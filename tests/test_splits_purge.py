"""时序切分 + purge gap 测试 —— 防 look-ahead 第二道防线.

核心断言（命门）：
    训练集最大日期与验证集最小日期之间，必须至少隔 `label_horizon` 个**交易日**。
    否则训练集尾部样本的「未来 N 日标签」会覆盖到验证集前段 —— 经典 label leakage。

本测试用合成 panel（不依赖 P2 真实数据缓存），所以 CI / 任何机器都能跑。
"""

from __future__ import annotations

import pandas as pd
import pytest

from astock_quant.models.splits import TimeSeriesSplit, time_series_split


# ---------------------------------------------------------------------------
# 共用 fixture：合成 panel 索引
# ---------------------------------------------------------------------------

@pytest.fixture
def panel_index() -> pd.MultiIndex:
    """120 个交易日 × 3 只票 = 360 行的 (date, ticker) MultiIndex."""
    dates = pd.date_range("2024-01-01", periods=120, freq="B")  # B = business day
    tickers = ["A", "B", "C"]
    return pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])


# ---------------------------------------------------------------------------
# 命门 1：gap_days < label_horizon → 必须 raise
# ---------------------------------------------------------------------------

def test_gap_smaller_than_horizon_raises(panel_index):
    """gap < horizon 是禁止的 —— 标签会从训练集泄漏到验证集。"""
    with pytest.raises(ValueError, match="purge_gap_days=3 小于 label_horizon=5"):
        time_series_split(
            panel_index,
            train_end="2024-03-15",
            valid_end="2024-06-01",
            purge_gap_days=3,
            label_horizon=5,
        )


def test_gap_equal_to_horizon_ok(panel_index):
    """gap == horizon 是允许的边界情况（恰好隔 horizon 个交易日）."""
    sp = time_series_split(
        panel_index,
        train_end="2024-03-15",
        valid_end="2024-06-01",
        purge_gap_days=5,
        label_horizon=5,
    )
    assert isinstance(sp, TimeSeriesSplit)
    assert sp.train_size > 0
    assert sp.valid_size > 0


# ---------------------------------------------------------------------------
# 命门 2：训练集最大日期 + gap_days >= 验证集最小日期 - label_horizon
# 等价于：valid_start 和 train_end 之间至少隔 horizon 个交易日
# ---------------------------------------------------------------------------

def test_train_valid_gap_satisfies_horizon(panel_index):
    """核心不变量：train 最大日 与 valid 最小日 之间至少隔 horizon 个交易日.

    具体表述（与 task 要求一致）：
        train_max_pos + horizon <= valid_min_pos - 1，即
        valid_min_pos - train_max_pos - 1 >= horizon
    """
    horizon = 5
    sp = time_series_split(
        panel_index,
        train_end="2024-03-15",
        valid_end="2024-06-01",
        purge_gap_days=horizon,  # 等于 horizon 是最紧约束
        label_horizon=horizon,
    )

    # 从 mask 反推实际的训练 / 验证 日期范围
    train_dates = panel_index[sp.train_mask].get_level_values("date").unique()
    valid_dates = panel_index[sp.valid_mask].get_level_values("date").unique()
    assert len(train_dates) > 0 and len(valid_dates) > 0

    # 按 panel 的实际交易日序列算"中间隔了多少个交易日"
    all_dates = pd.DatetimeIndex(sorted(panel_index.get_level_values("date").unique()))
    train_max_pos = all_dates.get_loc(train_dates.max())
    valid_min_pos = all_dates.get_loc(valid_dates.min())
    gap_actual = valid_min_pos - train_max_pos - 1  # 中间排除 train_max 和 valid_min 本身

    assert gap_actual >= horizon, (
        f"训练 / 验证 间隔 {gap_actual} 个交易日 < horizon {horizon}, "
        f"标签会泄漏。train_max={train_dates.max().date()}, "
        f"valid_min={valid_dates.min().date()}"
    )


# ---------------------------------------------------------------------------
# 命门 3：训练 / 验证集 不重叠
# ---------------------------------------------------------------------------

def test_train_valid_disjoint(panel_index):
    """训练集和验证集索引不能有任何重叠。"""
    sp = time_series_split(
        panel_index, train_end="2024-03-15", valid_end="2024-06-01",
        purge_gap_days=5, label_horizon=5,
    )
    overlap = sp.train_mask & sp.valid_mask
    assert overlap.sum() == 0, "训练集和验证集出现重叠样本"


def test_train_dates_before_valid_dates(panel_index):
    """所有训练集日期严格早于所有验证集日期（时序约束）。"""
    sp = time_series_split(
        panel_index, train_end="2024-03-15", valid_end="2024-06-01",
        purge_gap_days=5, label_horizon=5,
    )
    train_max = panel_index[sp.train_mask].get_level_values("date").max()
    valid_min = panel_index[sp.valid_mask].get_level_values("date").min()
    assert train_max < valid_min, (
        f"训练集尾 {train_max} 不早于验证集首 {valid_min}"
    )


# ---------------------------------------------------------------------------
# 命门 4：purged 样本数 = train_end 后 gap 个交易日 × ticker 数
# ---------------------------------------------------------------------------

def test_purged_count_matches_gap(panel_index):
    """中间被丢弃的样本数 = gap 个交易日 × ticker 数（panel 全覆盖时）."""
    gap = 5
    sp = time_series_split(
        panel_index, train_end="2024-03-15", valid_end="2024-06-01",
        purge_gap_days=gap, label_horizon=5,
    )
    n_tickers = len(panel_index.get_level_values("ticker").unique())
    assert sp.purged_count == gap * n_tickers, (
        f"purged={sp.purged_count}, 期望 {gap} * {n_tickers} = {gap * n_tickers}"
    )


# ---------------------------------------------------------------------------
# 边界：train_end 落在非交易日 → 应对齐到前一个交易日
# ---------------------------------------------------------------------------

def test_train_end_aligns_to_prior_trading_day(panel_index):
    """传周末的 train_end → 自动对齐到前一个交易日."""
    # 2024-03-16 是周六，前一个交易日是 2024-03-15 (周五)
    sp = time_series_split(
        panel_index, train_end="2024-03-16", valid_end="2024-06-01",
        purge_gap_days=5, label_horizon=5,
    )
    assert sp.train_end == pd.Timestamp("2024-03-15"), (
        f"train_end 周六应对齐到周五，实际 {sp.train_end}"
    )


# ---------------------------------------------------------------------------
# 边界：train_end >= valid_end → raise
# ---------------------------------------------------------------------------

def test_train_end_after_valid_end_raises(panel_index):
    with pytest.raises(ValueError, match="train_end.*必须早于"):
        time_series_split(
            panel_index, train_end="2024-06-01", valid_end="2024-03-15",
            purge_gap_days=5, label_horizon=5,
        )


# ---------------------------------------------------------------------------
# 边界：mask 长度与输入 index 长度一致
# ---------------------------------------------------------------------------

def test_mask_lengths_match_index(panel_index):
    """mask 长度必须等于输入 index 长度；总和 <= n（valid_end 之后的样本不归类）。"""
    sp = time_series_split(
        panel_index, train_end="2024-03-15", valid_end="2024-06-01",
        purge_gap_days=5, label_horizon=5,
    )
    n = len(panel_index)
    assert len(sp.train_mask) == n
    assert len(sp.valid_mask) == n
    # 三个集合互不重叠
    assert (sp.train_mask & sp.valid_mask).sum() == 0
    # 训练 + 验证 + purged <= n（valid_end 之后的样本不归任何集合，是合法行为）
    assert sp.train_size + sp.valid_size + sp.purged_count <= n


def test_full_range_fully_classified():
    """当 valid_end 恰好覆盖到 panel 最后一日时，train+valid+purged == n（无尾部裁剪）。"""
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["date", "ticker"])
    last_day = dates.max().strftime("%Y-%m-%d")
    sp = time_series_split(
        idx, train_end="2024-03-15", valid_end=last_day,
        purge_gap_days=5, label_horizon=5,
    )
    n = len(idx)
    assert sp.train_size + sp.valid_size + sp.purged_count == n


# ---------------------------------------------------------------------------
# 集成：与 SETTINGS 默认值的兼容性（不挂网络，纯参数校验）
# ---------------------------------------------------------------------------

def test_default_settings_satisfy_constraint():
    """SETTINGS 默认的 purge_gap 必须 >= label.horizon —— 永久不变量."""
    from astock_quant.config.settings import SETTINGS
    assert SETTINGS.split.purge_gap >= SETTINGS.label.horizon, (
        f"SETTINGS 配错了：purge_gap={SETTINGS.split.purge_gap} < "
        f"label.horizon={SETTINGS.label.horizon}，跑 run_direction 会触发 ValueError"
    )


# ---------------------------------------------------------------------------
# 命门：价值标签（季度 horizon）的 purge gap 不变量（T3 价值改造）
# ---------------------------------------------------------------------------

def test_value_purge_gap_satisfies_value_horizon():
    """命门：SETTINGS 的 value_purge_gap 必须 >= label.value_horizon —— 永久不变量.

    value_ranking_label 的 horizon 是 60（一个季度），标签语义是「T 日标签 = T+60 日的
    相对涨幅」。切训练/验证集时若 purge gap < 60，训练集尾部样本的 60 日未来窗口会跨
    切线泄漏进验证集（look-ahead）。short-window 任务用的 purge_gap=10 对价值标签远远不够。

    本断言守住：value_purge_gap 这条配置一旦被误改到 < value_horizon，CI 立刻红。
    """
    from astock_quant.config.settings import SETTINGS
    assert SETTINGS.split.value_purge_gap >= SETTINGS.label.value_horizon, (
        f"SETTINGS 配错了：value_purge_gap={SETTINGS.split.value_purge_gap} < "
        f"label.value_horizon={SETTINGS.label.value_horizon} —— 季度价值标签会从训练集"
        f"泄漏到验证集（look-ahead bias）。"
    )


def test_value_horizon_split_with_short_gap_raises(panel_index_large):
    """命门：价值标签 horizon=60 时，若误用短窗 purge_gap（如 10）→ 必须 raise.

    这是「horizon 从 5 变 60，gap 没跟着变大」的典型 look-ahead 陷阱。
    time_series_split 的 gap >= horizon 校验必须在季度尺度同样生效。
    """
    with pytest.raises(ValueError, match="purge_gap_days=10 小于 label_horizon=60"):
        time_series_split(
            panel_index_large,
            train_end="2024-05-31",
            valid_end="2024-10-01",
            purge_gap_days=10,  # 短窗 gap —— 对季度标签远远不够
            label_horizon=60,   # 价值标签 horizon
            group_by="date",
        )


def test_value_horizon_split_with_value_gap_ok(panel_index_large):
    """价值标签 horizon=60 配 value_purge_gap=60 → 切分成功，gap 恰好满足约束.

    用 SETTINGS 的真实 value 配置跑一遍 group_by='date' 切分，确认：
      - gap >= horizon 命门放行（60 >= 60 边界成立）
      - 切分结果非空且同 date 不跨集
    """
    from astock_quant.config.settings import SETTINGS

    sp = time_series_split(
        panel_index_large,
        train_end="2024-05-31",
        valid_end="2025-03-01",  # 拉长 valid_end，给 60 日 gap 留足空间
        purge_gap_days=SETTINGS.split.value_purge_gap,
        label_horizon=SETTINGS.label.value_horizon,
        group_by="date",
    )
    assert sp.train_size > 0 and sp.valid_size > 0
    assert sp.gap_days >= sp.label_horizon

    # 同 date 不跨集（group_by='date' 内部已校验，这里再断言一次）
    train_dates = set(panel_index_large[sp.train_mask].get_level_values("date").unique())
    valid_dates = set(panel_index_large[sp.valid_mask].get_level_values("date").unique())
    assert len(train_dates & valid_dates) == 0


# ---------------------------------------------------------------------------
# 命门：group_by="date" —— 横截面 ranking 场景 look-ahead 防线（Stage 3 §5.4）
# ---------------------------------------------------------------------------

@pytest.fixture
def panel_index_large() -> pd.MultiIndex:
    """200 个交易日 × 5 只票 = 1000 行的 (date, ticker) MultiIndex."""
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    tickers = ["A", "B", "C", "D", "E"]
    return pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])


def test_splits_group_aware_for_ranking(panel_index_large):
    """命门：group_by='date' 时，同一 date 的所有 (date, ticker) 行必须全在训练集 OR 全在验证集.

    横截面 ranking 场景下，同一日的 N 只股票共享同一日市场环境，ranking_label 是相对值。
    若同日不同 ticker 被切到不同集 = 「看着今天涨没涨预测今天哪些票会涨」= look-ahead。

    此命门测试守住：未来任何人改 splits 逻辑导致行级随机切 → CI 立刻红。
    """
    sp = time_series_split(
        panel_index_large,
        train_end="2024-05-31",
        valid_end="2024-10-01",
        purge_gap_days=5,
        label_horizon=5,
        group_by="date",
    )

    # 获取训练集和验证集各自的 date 集合
    train_dates = set(panel_index_large[sp.train_mask].get_level_values("date").unique())
    valid_dates = set(panel_index_large[sp.valid_mask].get_level_values("date").unique())

    # 核心断言：同一 date 不能同时出现在训练集和验证集
    overlap = train_dates & valid_dates
    assert len(overlap) == 0, (
        f"group_by='date' 命门失败：以下 {len(overlap)} 个 date 同时出现在训练集和验证集，"
        f"违反横截面 look-ahead 防线：{sorted(overlap)[:3]}..."
    )

    # 额外断言：每个 date 下的 ticker 数在训练/验证集中完整（5 只，不缺票）
    train_idx = panel_index_large[sp.train_mask]
    valid_idx = panel_index_large[sp.valid_mask]

    if len(train_dates) > 0:
        sample_train_date = sorted(train_dates)[-1]  # 训练集最后一天
        tickers_on_day = train_idx[
            train_idx.get_level_values("date") == sample_train_date
        ].get_level_values("ticker").tolist()
        assert len(tickers_on_day) == 5, (
            f"训练集 {sample_train_date.date()} 应有 5 只 ticker，实际 {len(tickers_on_day)}"
        )

    if len(valid_dates) > 0:
        sample_valid_date = sorted(valid_dates)[0]  # 验证集第一天
        tickers_on_day = valid_idx[
            valid_idx.get_level_values("date") == sample_valid_date
        ].get_level_values("ticker").tolist()
        assert len(tickers_on_day) == 5, (
            f"验证集 {sample_valid_date.date()} 应有 5 只 ticker，实际 {len(tickers_on_day)}"
        )


def test_splits_group_aware_default_is_safe(panel_index):
    """验证默认行为（不传 group_by）下，time_series_split 同样满足 group-aware 约束.

    time_series_split 按日期连续切，同一 date 的所有 ticker 天然全在同一集。
    此测试证明：旧行为本身就是 group-safe 的，group_by='date' 只是明确化 + 加验证层。
    """
    sp = time_series_split(
        panel_index,
        train_end="2024-03-15",
        valid_end="2024-06-01",
        purge_gap_days=5,
        label_horizon=5,
        # 不传 group_by —— 默认 None
    )
    train_dates = set(panel_index[sp.train_mask].get_level_values("date").unique())
    valid_dates = set(panel_index[sp.valid_mask].get_level_values("date").unique())
    overlap = train_dates & valid_dates
    assert len(overlap) == 0, (
        f"默认切分下仍出现 date 跨集，说明 splits.py 底层逻辑被破坏：{sorted(overlap)[:3]}"
    )


def test_splits_group_by_invalid_value_raises(panel_index):
    """group_by 传不支持的值应立刻抛 ValueError."""
    with pytest.raises(ValueError, match="group_by 只支持"):
        time_series_split(
            panel_index,
            train_end="2024-03-15",
            valid_end="2024-06-01",
            purge_gap_days=5,
            label_horizon=5,
            group_by="ticker",
        )


def test_splits_group_by_date_requires_multiindex():
    """group_by='date' 传 DatetimeIndex 应抛 ValueError（需要 MultiIndex）."""
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    with pytest.raises(ValueError, match="MultiIndex"):
        time_series_split(
            dates,
            train_end="2024-03-15",
            valid_end="2024-06-01",
            purge_gap_days=5,
            label_horizon=5,
            group_by="date",
        )
