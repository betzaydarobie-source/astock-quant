"""因子层 look-ahead 防线测试 —— bit-exact 复测.

P3a auditor 反馈：必须把「截断 panel vs 全量 panel」的 bit-exact 复测固化到
pytest，避免「单因子样本验证 → 推广全 25 因子」的过度 claim。每次改因子代码就跑。

核心断言：
    对 default_factors() 的全部因子，
    用 curr_date=CUT 截断 panel 算出来的因子值，
    必须与「全量 panel 算完后切到 ≤ CUT 的子集」逐元素 bit-exact 相等。

如果某因子在切点出现非零差异，说明它的实现里有「数据依赖型」操作（如整列分位、
全样本归一化、横截面 rank 等），属于 look-ahead bias，必须修复或下沉到 labels
层。这是 P3 「头号大敌」的第一道自动防线。

依赖 P2 的 CSV 缓存（`data_cache/`）+ SETTINGS.universe。
缓存不存在 / 资金流为空时直接 skip —— 不让 CI 因外部数据状态挂掉。
"""

from __future__ import annotations

import pandas as pd
import pytest

from astock_quant.data.dataset import (
    build_moneyflow_panel,
    build_price_panel,
    load_financials,
)
from astock_quant.factors.registry import compute_factor_frame, default_factors

CUT_DATE = "2024-06-30"  # 切点：与 auditor 复测脚本一致，留有足够「未来」可对照


@pytest.fixture(scope="module")
def panels():
    """加载一次全量 + 截断两套 panel，整个 module 共享."""
    price_full = build_price_panel()
    if price_full is None or price_full.empty:
        pytest.skip("price panel 为空，跳过（CI 无 data_cache 时正常）")

    price_trunc = build_price_panel(curr_date=CUT_DATE)
    if price_trunc.empty:
        pytest.skip("截断 panel 为空，切点早于数据起始")

    mf_full = build_moneyflow_panel()
    mf_trunc = build_moneyflow_panel(curr_date=CUT_DATE)
    # 资金流 panel 可能为空（数据源历史短），允许 None 传入 compute_factor_frame
    if mf_full is None or mf_full.empty:
        mf_full = None
    if mf_trunc is None or mf_trunc.empty:
        mf_trunc = None

    fins_full = load_financials()
    fins_trunc = load_financials(curr_date=CUT_DATE)

    return {
        "full": (price_full, mf_full, fins_full),
        "trunc": (price_trunc, mf_trunc, fins_trunc),
    }


@pytest.fixture(scope="module")
def factor_frames(panels):
    """跑两次 compute_factor_frame，返回 (ff_full, ff_trunc)."""
    price_full, mf_full, fins_full = panels["full"]
    price_trunc, mf_trunc, fins_trunc = panels["trunc"]

    # drop_nan_threshold=1.1 关闭 drop-NaN：no-lookahead 测试要在「固定 26 因子集」
    # 下验证截断 vs 全量的 bit-exact 一致性。drop-NaN 会随数据量波动（全量数据
    # drop 8 列、截断数据 drop 13 列），破坏「因子集稳定」这一测试前提，故在此
    # 测试套件里关闭它，单独测纯因子计算的 no-lookahead 正确性。
    ff_full = compute_factor_frame(
        price_panel=price_full,
        moneyflow_panel=mf_full,
        financials=fins_full,
        drop_nan_threshold=1.1,
    )
    ff_trunc = compute_factor_frame(
        price_panel=price_trunc,
        moneyflow_panel=mf_trunc,
        financials=fins_trunc,
        drop_nan_threshold=1.1,
    )
    return ff_full, ff_trunc


# ---------------------------------------------------------------------------
# 健康检查 —— 先确认实验前提
# ---------------------------------------------------------------------------

def test_truncation_takes_effect(panels):
    """截断 panel 的最大日期必须 <= CUT_DATE."""
    price_trunc, _, _ = panels["trunc"]
    max_date = price_trunc.index.get_level_values("date").max()
    assert max_date <= pd.Timestamp(CUT_DATE), (
        f"截断失效：max date {max_date} > {CUT_DATE}"
    )


def test_factor_frame_shapes(factor_frames, panels):
    """两个 FactorFrame 都应非空 + 因子数一致."""
    ff_full, ff_trunc = factor_frames
    assert not ff_full.data.empty, "全量 FactorFrame 为空"
    assert not ff_trunc.data.empty, "截断 FactorFrame 为空"
    assert ff_full.factor_names == ff_trunc.factor_names, (
        "因子列不一致 —— 默认因子集应稳定"
    )
    # 全量行数 >= 截断行数（截断只裁掉了未来数据）
    assert len(ff_full.data) >= len(ff_trunc.data)


# ---------------------------------------------------------------------------
# 核心：bit-exact 复测（25/25 因子）
# ---------------------------------------------------------------------------

def _diff_one_factor(ff_full, ff_trunc, factor: str) -> tuple[int, float]:
    """对单个因子，对齐到截断 panel 的索引后算 max abs diff.

    返回 (非零差异行数, max abs diff)。两侧都 NaN 视为相等（不计入差异）。
    """
    s_full = ff_full.data[factor].reindex(ff_trunc.data.index)
    s_trunc = ff_trunc.data[factor]
    # 两侧都 NaN 视为相等
    both_nan = s_full.isna() & s_trunc.isna()
    diff = (s_full - s_trunc).abs()
    diff = diff.where(~both_nan, 0.0)
    nz = diff.fillna(0.0) > 0
    return int(nz.sum()), float(diff.max(skipna=True) if not diff.dropna().empty else 0.0)


def test_all_factors_bit_exact_at_truncation(factor_frames):
    """全部因子的 截断 vs 全量 在切点 bit-exact 一致.

    这是 P3a auditor 反馈的核心硬性要求 —— 不再只测 momentum_20d 一个样本，
    必须跑遍 default_factors() 全部因子，否则 winsorize 类问题会漏检。
    """
    ff_full, ff_trunc = factor_frames

    failures: list[tuple[str, int, float]] = []
    for factor in ff_trunc.factor_names:
        n_nonzero, max_abs = _diff_one_factor(ff_full, ff_trunc, factor)
        if n_nonzero > 0 or max_abs > 0.0:
            failures.append((factor, n_nonzero, max_abs))

    if failures:
        msg_lines = [
            f"{len(failures)} 个因子在切点 {CUT_DATE} 出现「截断 vs 全量」非零差异，"
            "可能存在数据依赖型 look-ahead："
        ]
        for fac, n, m in failures:
            msg_lines.append(f"  - {fac}: 非零差异 {n} 行，max abs = {m:.6g}")
        msg_lines.append(
            "请检查相关因子的实现，是否用了 winsorize / 全样本归一化 / 横截面 rank "
            "等数据依赖型变换。"
        )
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# 守护：default_factors 因子数 = 27（任何 PR 改这条会触发显式 review）
# ---------------------------------------------------------------------------

def test_default_factor_count_is_27():
    """default_factors 数量稳定在 27 —— 改动需有意识地更新 P3 报告.

    T1（价值选股改造）把财务因子从 7 扩到 9：估值 PE/PB 重建 + 新增股息率、毛利率。
    """
    factors = default_factors()
    names = [f.name for f in factors]
    assert len(names) == 27, (
        f"default_factors 数量从 27 变成 {len(names)}，"
        f"请同步更新 P3-因子库.md。当前清单：{names}"
    )
    assert len(set(names)) == len(names), "因子 name 重复"
