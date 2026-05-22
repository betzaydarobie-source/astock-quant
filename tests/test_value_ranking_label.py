"""T3 价值选股 —— value_ranking_label 季度级横截面排名标签 单元测试 + 命门.

value_ranking_label 是 ranking_label 的「季度尺度」版本（默认 horizon=60 而非 5）。
底层完全复用 ranking_label，所以本测试聚焦三件事：

1. 命门：横截面 rank 必须只用「当日」数据 —— 在 **季度尺度** 重跑「前 N 天 vs 全样本」
   双跑断言，确保 horizon 放大到 60 后 look-ahead 命门依然成立。
2. 默认 horizon 必须是 SETTINGS.label.value_horizon（60）—— 价值因子只在季度尺度有效，
   这是 T3 改造的核心，若有人把默认值改回 5 立刻挂。
3. value_ranking_label 与 ranking_label 在「同一 horizon」下必须 bit-exact 一致 ——
   证明它只是个换默认参数的薄包装，没有偷偷改计算逻辑（没有新增 look-ahead 面）。

不依赖真实缓存，全部合成 panel。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from astock_quant.config.settings import SETTINGS
from astock_quant.labels.targets import ranking_label, return_label, value_ranking_label


# ===========================================================================
# helpers —— 与 test_ranking_label.py 同款合成 panel
# ===========================================================================

def _make_panel(
    n_dates: int = 200,
    tickers: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """合成 MultiIndex(date, ticker) panel，含 close 列.

    n_dates 默认 200 —— 季度 horizon=60 需要足够长的时间轴才有非 NaN 中段样本。
    """
    tickers = tickers or ["A", "B", "C", "D", "E"]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    rng = np.random.default_rng(seed)
    rows = []
    for t in tickers:
        p = 100.0
        for d in dates:
            p *= 1 + rng.normal(0.001, 0.02)
            rows.append({"date": d, "ticker": t, "close": p})
    return pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()


def _split_panel_by_date(panel: pd.DataFrame, n_first: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按前 n_first 个交易日切分 panel，返回 (front, full) 两份."""
    all_dates = panel.index.get_level_values("date").unique().sort_values()
    cutoff = all_dates[n_first - 1]
    front = panel[panel.index.get_level_values("date") <= cutoff]
    return front, panel


# ===========================================================================
# 命门 1：季度尺度下横截面 rank 仍只用「当日」数据
# ===========================================================================

def test_value_ranking_label_no_full_sample_rank_quarterly():
    """命门：季度 horizon 下，横截面 rank 必须只用「当日」数据，不能用全样本.

    设计：构造长 panel（5 只票 × 200 日），horizon=60（一个季度），跑两次：
      - 一次只用前 130 天数据（保证 130-60=70 天有非 NaN 标签）
      - 一次用全 200 天数据

    断言：前 130 天里的有效 rank，在两次结果中必须 bit-exact 相同。
    如果实现错误地用了全样本 .rank()（把全 200 天一起 rank），前段的 rank
    会因为后段数据引入而漂移 → 测试挂。

    这是 test_ranking_label.py 同款命门，但在 **季度尺度** 重做一遍 ——
    确保 horizon 从 5 放大到 60 后，groupby(date) 横截面隔离依然成立。
    """
    panel = _make_panel(n_dates=200, tickers=["A", "B", "C", "D", "E"], seed=0)
    horizon = 60

    front, full = _split_panel_by_date(panel, n_first=130)

    label_front = value_ranking_label(front, horizon=horizon)
    label_full = value_ranking_label(full, horizon=horizon)

    common_idx = label_front.index.intersection(label_full.index)
    valid_front = label_front.loc[common_idx].dropna()
    valid_full = label_full.loc[common_idx].dropna()
    common_valid = valid_front.index.intersection(valid_full.index)

    assert len(common_valid) > 0, (
        "没有共同的非 NaN 样本 —— 测试设计有问题（panel 太短或 horizon 太大）"
    )

    front_vals = valid_front.loc[common_valid].sort_index()
    full_vals = valid_full.loc[common_valid].sort_index()
    if not front_vals.equals(full_vals):
        mismatches = (front_vals - full_vals).abs()
        pytest.fail(
            f"命门失败：季度 horizon={horizon} 下，前段 rank 在「全样本」结果里漂移了 —— "
            f"说明 value_ranking_label 用了全样本 rank，存在 look-ahead bias。"
            f"正确实现应是 groupby(date).rank()，只用当日截面。"
            f"最大差异: {mismatches.max():.6f}"
        )


# ===========================================================================
# 命门 2：默认 horizon 必须是季度（value_horizon=60），不是短窗 5
# ===========================================================================

def test_value_ranking_label_default_horizon_is_quarterly():
    """命门：value_ranking_label 不传 horizon 时，必须用 SETTINGS.label.value_horizon.

    价值因子只在季度/年尺度有效，5 日尺度已被项目反复证明接近随机。
    若有人把默认 horizon 改回 5（或漏接 value_horizon），这条测试立刻挂。

    验证手法：不传 horizon 的输出，必须与显式传 value_horizon 的输出 bit-exact 一致；
    且必须与显式传 5 的输出 **不一致**（否则默认值退回了短窗）。
    """
    panel = _make_panel(n_dates=200, seed=7)

    y_default = value_ranking_label(panel)
    y_explicit_quarter = value_ranking_label(panel, horizon=SETTINGS.label.value_horizon)
    y_explicit_short = value_ranking_label(panel, horizon=5)

    # 默认值 == 季度
    pd.testing.assert_series_equal(y_default, y_explicit_quarter)

    # 季度尺度 != 短窗尺度（NaN 行数不同 + 有效值不同 —— 至少 NaN 数量必然不同）
    assert y_explicit_quarter.isna().sum() != y_explicit_short.isna().sum(), (
        "季度 horizon 与 5 日 horizon 的 NaN 数量竟然相同 —— "
        "说明默认 horizon 没切到季度尺度，价值标签退化成短窗。"
    )


def test_value_ranking_label_uses_60_trading_days():
    """value_horizon 配置值应为 60（一个季度 ≈ A股 每年 242 交易日 ÷ 4）.

    这是 T3 价值改造的核心常量，写死一条断言守住，避免被误改。
    """
    assert SETTINGS.label.value_horizon == 60, (
        f"value_horizon 应为 60（一个季度的交易日数），实际 {SETTINGS.label.value_horizon}"
    )


# ===========================================================================
# 命门 3：value_ranking_label 只是 ranking_label 的换参薄包装
# ===========================================================================

def test_value_ranking_label_equals_ranking_label_same_horizon():
    """命门：同一 horizon 下，value_ranking_label 与 ranking_label 必须 bit-exact 一致.

    value_ranking_label 设计上只是 ranking_label 的「换默认 horizon」薄包装 ——
    底层计算（shift 链 + groupby(date).rank）完全复用。这条测试证明它没有偷偷
    改任何计算逻辑：若有人在 value_ranking_label 里加了 winsorize / 改了 rank 方式 /
    引入了新的 look-ahead 面，同 horizon 双跑会立刻不一致。

    （唯一允许的差异是 Series.name：ranking_label vs value_ranking_label。）
    """
    panel = _make_panel(n_dates=200, tickers=[f"T{i}" for i in range(6)], seed=11)
    horizon = 60

    y_ranking = ranking_label(panel, horizon=horizon)
    y_value = value_ranking_label(panel, horizon=horizon)

    # 值必须完全一致（忽略 name 差异）
    pd.testing.assert_series_equal(
        y_value.rename("ranking_label"),
        y_ranking,
    )


def test_value_ranking_label_series_name():
    """输出 Series 的 name 应为 'value_ranking_label'（下游区分 ③-价值 与 ③ 短窗）."""
    panel = _make_panel(n_dates=200)
    y = value_ranking_label(panel, horizon=60)
    assert y.name == "value_ranking_label"


# ===========================================================================
# 横截面正确性 + 值域（季度尺度）
# ===========================================================================

def test_value_ranking_label_value_range():
    """value_ranking_label 输出值域应在 [0, 1] 内（横截面百分位 rank）."""
    panel = _make_panel(n_dates=200, tickers=["A", "B", "C", "D", "E"])
    y = value_ranking_label(panel, horizon=60)
    valid = y.dropna()
    assert len(valid) > 0, "季度尺度下应有非 NaN 中段样本（panel 长 200 > horizon 60）"
    assert valid.min() >= 0.0
    assert valid.max() <= 1.0


def test_value_ranking_label_cross_section_mean_half():
    """每日横截面 label 均值应约为 0.5（百分位 rank 的期望值）."""
    panel = _make_panel(n_dates=220, tickers=[f"T{i}" for i in range(10)])
    y = value_ranking_label(panel, horizon=60)
    y_valid = y.dropna()

    all_dates = y_valid.index.get_level_values("date").unique()
    daily_means = [y_valid.xs(d, level="date").mean() for d in all_dates]
    overall_mean = np.mean(daily_means)
    assert 0.3 <= overall_mean <= 0.7, (
        f"季度横截面 label 均值偏离 0.5 过多：{overall_mean:.3f}"
    )


def test_value_ranking_label_consistent_with_return_label():
    """命门：同一日横截面，value_ranking_label 高分位对应 return_label 高值（单调关系）.

    value_ranking_label 就是「未来一季度收益」在横截面上的百分位排名，因此与
    同 horizon 的 return_label 在每个交易日内必须单调一致（Spearman rho ≈ 1）。
    """
    from scipy.stats import spearmanr

    panel = _make_panel(n_dates=220, tickers=[f"T{i}" for i in range(8)])
    horizon = 60
    y_rank = value_ranking_label(panel, horizon=horizon)
    y_ret = return_label(panel, horizon=horizon)

    valid_dates = []
    all_dates = y_rank.index.get_level_values("date").unique()
    for d in all_dates:
        r_day = y_rank.xs(d, level="date").dropna()
        ret_day = y_ret.xs(d, level="date").dropna()
        common = r_day.index.intersection(ret_day.index)
        if len(common) >= 3:
            valid_dates.append(d)

    assert len(valid_dates) > 0, "没有足够的有效日期做横截面 correlation 检验"

    for d in valid_dates[:5]:
        r_day = y_rank.xs(d, level="date").dropna()
        ret_day = y_ret.xs(d, level="date").dropna()
        common = r_day.index.intersection(ret_day.index)
        rho, _ = spearmanr(r_day.loc[common].values, ret_day.loc[common].values)
        assert rho > 0.99, (
            f"日期 {d}：value_ranking_label 与 return_label 横截面 Spearman rho={rho:.4f} "
            "< 0.99 —— 价值标签的计算基础和 return_label 不一致。"
        )


# ===========================================================================
# 末尾 NaN —— 季度 horizon 下尾部 60 行 NaN
# ===========================================================================

def test_value_ranking_label_tail_nan_per_ticker():
    """每只票最后 horizon(=60) 行 label 应为 NaN（shift(-horizon) 自然结果）."""
    panel = _make_panel(n_dates=200, tickers=["A", "B", "C"])
    horizon = 60
    y = value_ranking_label(panel, horizon=horizon)
    for ticker in ["A", "B", "C"]:
        ticker_y = y.xs(ticker, level="ticker").sort_index()
        assert ticker_y.iloc[-horizon:].isna().all(), (
            f"{ticker} 末尾 {horizon} 行应全为 NaN"
        )
        # 中段有有效值（200 - 60 = 140 行 > 0）
        assert ticker_y.iloc[:-(horizon + 1)].notna().any()


def test_value_ranking_label_for_training_false_same_as_true():
    """for_training=True/False 尾部 NaN 行为一致（与 ranking_label 语义对齐）."""
    panel = _make_panel(n_dates=200)
    y_train = value_ranking_label(panel, horizon=60, for_training=True)
    y_infer = value_ranking_label(panel, horizon=60, for_training=False)
    pd.testing.assert_series_equal(y_train, y_infer)


# ===========================================================================
# 边界：空 panel / 缺列
# ===========================================================================

def test_value_ranking_label_empty_panel():
    """空 panel → 返回空 Series，不抛错."""
    empty = pd.DataFrame(
        columns=["close"],
        index=pd.MultiIndex.from_arrays([[], []], names=["date", "ticker"]),
    )
    y = value_ranking_label(empty)
    assert isinstance(y, pd.Series)
    assert y.empty


def test_value_ranking_label_missing_close_raises():
    """缺少 close 列 → 抛 ValueError."""
    panel = _make_panel(n_dates=100)
    panel_no_close = panel.rename(columns={"close": "price"})
    with pytest.raises((ValueError, KeyError)):
        value_ranking_label(panel_no_close, horizon=60)
