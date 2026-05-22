"""价值+质量综合打分（T2）测试.

覆盖三件事：
  1. 值域与方向：分数 ∈ [0,1]；因子方向正确（低 PE → 高价值分，高 ROE → 高质量分）。
  2. ★防 look-ahead★：所有标准化/排名按「每日横截面」—— 截断 panel 算出的分数，必须与
     全量 panel 切到同区间 bit-exact 相等（与 test_factors_no_lookahead 同款纪律）。
  3. 一致性：综合分 = 三维分项分按权重加权 + 归一化；维度缺失时按可用维度归一化。

打分用纯构造数据为主（不依赖网络），look-ahead 用真实 data_cache（缓存不在则 skip）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from astock_quant.config.settings import ValueScoreConfig
from astock_quant.contracts import FactorFrame
from astock_quant.factors.value_score import (
    COL_COMPOSITE,
    COL_GROWTH,
    COL_QUALITY,
    COL_VALUE,
    compute_value_scores,
)


# ===========================================================================
# 构造工具
# ===========================================================================

def _make_factor_frame(
    n_dates: int = 20,
    n_tickers: int = 10,
    seed: int = 42,
) -> FactorFrame:
    """构造一个含全部估值/盈利/成长因子的 FactorFrame（随机但可复现）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    tickers = [f"{600000 + i:06d}" for i in range(n_tickers)]
    idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])

    n = len(idx)
    data = pd.DataFrame(
        {
            "pe": rng.uniform(5, 60, n),
            "pb": rng.uniform(0.5, 8, n),
            "dividend_yield": rng.uniform(0, 0.06, n),
            "roe": rng.uniform(2, 30, n),
            "net_margin": rng.uniform(1, 40, n),
            "gross_margin": rng.uniform(10, 90, n),
            "revenue_growth_yoy": rng.uniform(-0.3, 0.5, n),
            "net_profit_growth_yoy": rng.uniform(-0.5, 0.8, n),
        },
        index=idx,
    )
    return FactorFrame(data=data, factor_names=list(data.columns))


# ===========================================================================
# 1. 值域与方向
# ===========================================================================

def test_scores_in_unit_range():
    """4 个分数列都必须落在 [0, 1]."""
    ff = _make_factor_frame()
    scores = compute_value_scores(ff)
    for col in [COL_VALUE, COL_QUALITY, COL_GROWTH, COL_COMPOSITE]:
        s = scores[col].dropna()
        assert len(s) > 0, f"{col} 全 NaN"
        assert s.min() >= 0.0 - 1e-9, f"{col} 有负值：{s.min()}"
        assert s.max() <= 1.0 + 1e-9, f"{col} 超过 1：{s.max()}"


def test_output_columns_and_index():
    """输出必须是 4 列、索引与输入因子一致."""
    ff = _make_factor_frame()
    scores = compute_value_scores(ff)
    assert list(scores.columns) == [COL_VALUE, COL_QUALITY, COL_GROWTH, COL_COMPOSITE]
    assert scores.index.equals(ff.data.sort_index().index)


def test_low_pe_gets_high_value_score():
    """方向：同一天里，PE 最低的票价值分应明显高于 PE 最高的票.

    构造一天 5 只票，PE 单调递增、其余价值因子（PB/股息）持平 ——
    则 PE 最低（最便宜）的票价值分最高。
    """
    dates = pd.bdate_range("2024-05-06", periods=1)
    tickers = [f"{600000 + i:06d}" for i in range(5)]
    idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    data = pd.DataFrame(
        {
            "pe": [10.0, 20.0, 30.0, 40.0, 50.0],   # 递增：600000 最便宜
            "pb": [3.0] * 5,
            "dividend_yield": [0.02] * 5,
            "roe": [15.0] * 5,
            "net_margin": [20.0] * 5,
            "gross_margin": [50.0] * 5,
            "revenue_growth_yoy": [0.1] * 5,
            "net_profit_growth_yoy": [0.1] * 5,
        },
        index=idx,
    )
    ff = FactorFrame(data=data, factor_names=list(data.columns))
    scores = compute_value_scores(ff)
    vs = scores[COL_VALUE].xs(dates[0], level="date")
    # 600000（PE=10，最便宜）价值分应 > 600004（PE=50，最贵）
    assert vs["600000"] > vs["600004"], (
        f"低 PE 的票价值分应更高：600000={vs['600000']}, 600004={vs['600004']}"
    )


def test_high_roe_gets_high_quality_score():
    """方向：同一天里，ROE 最高的票质量分应明显高于 ROE 最低的票."""
    dates = pd.bdate_range("2024-05-06", periods=1)
    tickers = [f"{600000 + i:06d}" for i in range(5)]
    idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    data = pd.DataFrame(
        {
            "pe": [20.0] * 5,
            "pb": [3.0] * 5,
            "dividend_yield": [0.02] * 5,
            "roe": [5.0, 10.0, 15.0, 20.0, 25.0],   # 递增：600004 最赚钱
            "net_margin": [20.0] * 5,
            "gross_margin": [50.0] * 5,
            "revenue_growth_yoy": [0.1] * 5,
            "net_profit_growth_yoy": [0.1] * 5,
        },
        index=idx,
    )
    ff = FactorFrame(data=data, factor_names=list(data.columns))
    scores = compute_value_scores(ff)
    qs = scores[COL_QUALITY].xs(dates[0], level="date")
    assert qs["600004"] > qs["600000"], (
        f"高 ROE 的票质量分应更高：600004={qs['600004']}, 600000={qs['600000']}"
    )


# ===========================================================================
# 2. ★防 look-ahead★ —— 横截面排名，截断不变性
# ===========================================================================

def test_scores_bit_exact_under_truncation():
    """★核心★ 截断 panel 算的分数 = 全量 panel 切到同区间 —— bit-exact.

    打分全程按「每日横截面」标准化/排名。如果某步误用了全样本统计量（全样本分位、
    全样本 rank），截断 panel（少了后半段日期）会改变那个统计量 → 分数在重叠区间
    就会偏移。本测试构造全量 + 截断两套，断言重叠区间逐元素相等。
    """
    ff_full = _make_factor_frame(n_dates=30, n_tickers=12, seed=7)
    cut = ff_full.data.index.get_level_values("date").unique()[15]

    # 截断：只保留 date <= cut 的行
    mask = ff_full.data.index.get_level_values("date") <= cut
    ff_trunc = FactorFrame(
        data=ff_full.data[mask].copy(),
        factor_names=list(ff_full.factor_names),
    )

    scores_full = compute_value_scores(ff_full)
    scores_trunc = compute_value_scores(ff_trunc)

    # 在截断索引上对齐，逐列比对
    aligned_full = scores_full.reindex(scores_trunc.index)
    for col in scores_trunc.columns:
        a = aligned_full[col]
        b = scores_trunc[col]
        both_nan = a.isna() & b.isna()
        diff = (a - b).abs().where(~both_nan, 0.0)
        max_diff = float(diff.max(skipna=True))
        assert max_diff < 1e-12, (
            f"{col} 在截断 vs 全量出现 {max_diff} 的差异 —— 可能有全样本统计量"
            "（look-ahead）。打分必须全程按每日横截面。"
        )


def test_single_day_score_independent_of_other_days():
    """某一天的分数只取决于那天的横截面 —— 改动其它天的因子值，这天分数不变.

    这是「按日横截面」最直接的断言：横截面排名不跨日。
    """
    ff = _make_factor_frame(n_dates=10, n_tickers=8, seed=11)
    target_day = ff.data.index.get_level_values("date").unique()[5]
    scores_before = compute_value_scores(ff)
    day_before = scores_before.xs(target_day, level="date")

    # 篡改「除 target_day 外」所有日期的 pe（放大 100 倍）
    ff2_data = ff.data.copy()
    other_mask = ff2_data.index.get_level_values("date") != target_day
    ff2_data.loc[other_mask, "pe"] = ff2_data.loc[other_mask, "pe"] * 100.0
    ff2 = FactorFrame(data=ff2_data, factor_names=list(ff.factor_names))
    scores_after = compute_value_scores(ff2)
    day_after = scores_after.xs(target_day, level="date")

    pd.testing.assert_frame_equal(day_before, day_after)


# ===========================================================================
# 3. 综合分一致性
# ===========================================================================

def test_composite_is_weighted_average_of_dimensions():
    """综合分 = 价值×w_v + 质量×w_q + 成长×w_g，再除以权重和."""
    ff = _make_factor_frame(seed=3)
    cfg = ValueScoreConfig(value_weight=0.5, quality_weight=0.3, growth_weight=0.2)
    scores = compute_value_scores(ff, cfg)

    valid = scores.dropna()
    assert len(valid) > 0
    manual = (
        valid[COL_VALUE] * 0.5
        + valid[COL_QUALITY] * 0.3
        + valid[COL_GROWTH] * 0.2
    ) / (0.5 + 0.3 + 0.2)
    np.testing.assert_allclose(valid[COL_COMPOSITE].values, manual.values, atol=1e-12)


def test_composite_renormalizes_when_dimension_missing():
    """某维度因子全缺 → 综合分按「可用维度」的权重重新归一化，不整票 NaN.

    构造：成长因子两列全 NaN → 成长分 NaN → 综合分应 = (价值×0.4 + 质量×0.4) / 0.8。
    """
    ff = _make_factor_frame(seed=5)
    data = ff.data.copy()
    data["revenue_growth_yoy"] = np.nan
    data["net_profit_growth_yoy"] = np.nan
    ff2 = FactorFrame(data=data, factor_names=list(ff.factor_names))

    cfg = ValueScoreConfig(value_weight=0.4, quality_weight=0.4, growth_weight=0.2)
    scores = compute_value_scores(ff2, cfg)

    assert scores[COL_GROWTH].isna().all(), "成长因子全缺，成长分应全 NaN"
    # 综合分仍应有值（用价值 + 质量），且 = (v×0.4 + q×0.4)/0.8
    valid = scores.dropna(subset=[COL_VALUE, COL_QUALITY, COL_COMPOSITE])
    assert len(valid) > 0, "价值/质量维度应仍可算出综合分"
    manual = (valid[COL_VALUE] * 0.4 + valid[COL_QUALITY] * 0.4) / 0.8
    np.testing.assert_allclose(valid[COL_COMPOSITE].values, manual.values, atol=1e-12)


def test_all_dimensions_missing_gives_nan_composite():
    """三维因子全缺 → 综合分 NaN（没东西可算）."""
    ff = _make_factor_frame(seed=9)
    data = ff.data.copy()
    for col in data.columns:
        data[col] = np.nan
    ff2 = FactorFrame(data=data, factor_names=list(ff.factor_names))
    scores = compute_value_scores(ff2)
    assert scores[COL_COMPOSITE].isna().all(), "因子全 NaN 时综合分应全 NaN"


def test_min_cross_section_guard():
    """某日有效票数 < min_cross_section → 该日该维度分 NaN（横截面太小无意义）."""
    # 构造一天只有 3 只票，min_cross_section 默认 5
    dates = pd.bdate_range("2024-05-06", periods=1)
    tickers = ["600000", "600001", "600002"]
    idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    data = pd.DataFrame(
        {
            "pe": [10.0, 20.0, 30.0],
            "pb": [2.0, 3.0, 4.0],
            "dividend_yield": [0.01, 0.02, 0.03],
            "roe": [10.0, 15.0, 20.0],
            "net_margin": [10.0, 20.0, 30.0],
            "gross_margin": [40.0, 50.0, 60.0],
            "revenue_growth_yoy": [0.0, 0.1, 0.2],
            "net_profit_growth_yoy": [0.0, 0.1, 0.2],
        },
        index=idx,
    )
    ff = FactorFrame(data=data, factor_names=list(data.columns))
    scores = compute_value_scores(ff)  # 默认 min_cross_section=5
    assert scores[COL_VALUE].isna().all(), "3 只票 < min_cross_section=5，价值分应全 NaN"


# ===========================================================================
# 4. 边界
# ===========================================================================

def test_empty_input_returns_empty():
    """空输入 → 空 DataFrame（不抛异常）."""
    empty = FactorFrame(
        data=pd.DataFrame(index=pd.MultiIndex.from_tuples([], names=["date", "ticker"])),
        factor_names=[],
    )
    scores = compute_value_scores(empty)
    assert scores.empty


def test_accepts_raw_dataframe():
    """既能吃 FactorFrame，也能吃裸 DataFrame."""
    ff = _make_factor_frame()
    scores_from_ff = compute_value_scores(ff)
    scores_from_df = compute_value_scores(ff.data)
    pd.testing.assert_frame_equal(scores_from_ff, scores_from_df)
