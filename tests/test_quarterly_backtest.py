"""T4 季度调仓回测 —— backtest/quarterly.py 单元测试.

诚信纪律（最重要）：
    本测试**绝不**断言「年化收益 > X%」「夏普 > 1」「超额 > 0」这类**期望策略赚钱**
    的命题。回测能不能跑赢沪深300 是一个待验证的经验问题，不是代码正确性问题 ——
    在测试里写死「应该赚钱」等于偷偷预设结论、伪造 alpha。
    本测试只验证**回测逻辑本身正确**：
      - 季度边界选对（每自然季度初一个调仓日）
      - Top-N 选股 / 综合分排序正确
      - 非调仓日不产出信号（换手率真实）
      - 按年超额分解的算术正确（用构造数据精确验证）
      - 回测能跑通、产出结构完整、诚实声明非空

全部合成数据，不依赖真实缓存 / 网络。
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from astock_quant.backtest.quarterly import (
    QuarterlyBacktestConfig,
    build_quarterly_predictions,
    quarter_start_dates,
    run_quarterly_backtest,
    yearly_alpha_breakdown,
)


# ===========================================================================
# helpers
# ===========================================================================

def _make_price_panel(
    start: str = "2022-01-01",
    end: str = "2024-12-31",
    tickers: list[str] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """合成 MultiIndex(date, ticker) 行情 panel，含 OHLC + volume + amount."""
    tickers = tickers or [f"T{i:02d}" for i in range(20)]
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    rows = []
    for t in tickers:
        p = 100.0
        for d in dates:
            p *= 1 + rng.normal(0.0003, 0.018)
            close = round(p, 2)
            rows.append({
                "date": d, "ticker": t,
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "volume": 1_000_000.0, "amount": close * 1_000_000.0,
            })
    return pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()


def _make_score_panel(price_panel: pd.DataFrame, seed: int = 7) -> pd.Series:
    """在行情 panel 的索引上合成「综合分」Series（每个 (date,ticker) 一个分数）."""
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.random(len(price_panel.index)),
        index=price_panel.index,
        name="value_score",
    )


# ===========================================================================
# quarter_start_dates —— 季度边界
# ===========================================================================

def test_quarter_start_dates_picks_four_per_year():
    """一整年的交易日 → 恰好 4 个季度初调仓日（1/4/7/10 月各一个）."""
    dates = list(pd.bdate_range("2024-01-01", "2024-12-31").date)
    qs = quarter_start_dates(dates)
    assert len(qs) == 4
    # 每个调仓日的月份应分别落在 Q1/Q2/Q3/Q4
    months = [d.month for d in qs]
    assert months == sorted(months)  # 升序
    assert (months[0] - 1) // 3 == 0  # Q1
    assert (months[1] - 1) // 3 == 1  # Q2
    assert (months[2] - 1) // 3 == 2  # Q3
    assert (months[3] - 1) // 3 == 3  # Q4


def test_quarter_start_dates_is_first_trading_day_of_quarter():
    """调仓日必须是该季度里**最早**的交易日（不是季度第一个自然日）."""
    # 2024-01-01 是元旦休市，第一个交易日是 2024-01-02
    dates = list(pd.bdate_range("2024-01-01", "2024-06-30").date)
    qs = quarter_start_dates(dates)
    q1_start = qs[0]
    # q1_start 应是 dates 里所有 Q1 日期的最小值
    q1_dates = [d for d in dates if (d.month - 1) // 3 == 0]
    assert q1_start == min(q1_dates)


def test_quarter_start_dates_spans_multiple_years():
    """跨年时每年每季度各一个调仓日."""
    dates = list(pd.bdate_range("2022-01-01", "2024-12-31").date)
    qs = quarter_start_dates(dates)
    # 3 年 × 4 季 = 12
    assert len(qs) == 12
    assert qs == sorted(qs)


def test_quarter_start_dates_empty():
    """空输入 → 空列表，不抛错."""
    assert quarter_start_dates([]) == []


# ===========================================================================
# build_quarterly_predictions —— 综合分稀释成季度调仓信号
# ===========================================================================

def test_predictions_only_on_quarter_starts():
    """命门：predictions 只在季度初出现，季度中的交易日不产出（保证换手率真实）."""
    panel = _make_price_panel("2022-01-01", "2023-12-31", tickers=["A", "B", "C", "D", "E"])
    scores = _make_score_panel(panel)
    preds = build_quarterly_predictions(scores, top_n=2)

    pred_dates = sorted({p.date for p in preds})
    all_dates = sorted({d.date() for d in panel.index.get_level_values("date")})
    expected = quarter_start_dates(all_dates)
    assert pred_dates == expected, (
        "predictions 的日期必须恰好等于季度初调仓日 —— 否则换手率 / 成本失真"
    )


def test_topn_get_high_score_rest_get_low():
    """调仓日：综合分 Top-N 的票 score=0.99，其余 score=0.01."""
    panel = _make_price_panel("2022-01-01", "2022-12-31", tickers=list("ABCDEFGH"))
    scores = _make_score_panel(panel)
    top_n = 3
    preds = build_quarterly_predictions(scores, top_n=top_n)

    # 取第一个调仓日
    first_day = min(p.date for p in preds)
    day_preds = [p for p in preds if p.date == first_day]
    high = [p for p in day_preds if p.score == 0.99]
    low = [p for p in day_preds if p.score == 0.01]
    assert len(high) == top_n, f"应有 {top_n} 只 Top-N（score 0.99）"
    assert len(low) == len(day_preds) - top_n, "其余票应为 score 0.01"


def test_topn_selection_matches_score_ranking():
    """命门：被选为 Top-N（score=0.99）的票，必须是当日综合分真正最高的 N 只."""
    # 构造一个综合分明确的调仓日
    d = pd.Timestamp("2022-01-03")  # 2022 Q1 第一个交易日附近
    tickers = ["A", "B", "C", "D", "E"]
    explicit_scores = {"A": 0.9, "B": 0.1, "C": 0.8, "D": 0.2, "E": 0.5}
    idx = pd.MultiIndex.from_tuples(
        [(d, t) for t in tickers], names=["date", "ticker"]
    )
    scores = pd.Series([explicit_scores[t] for t in tickers], index=idx, name="value_score")

    preds = build_quarterly_predictions(scores, top_n=2)
    high_tickers = {p.ticker for p in preds if p.score == 0.99}
    # Top-2 综合分是 A(0.9) 和 C(0.8)
    assert high_tickers == {"A", "C"}, f"Top-2 应是 A,C（最高分），实际 {high_tickers}"


def test_predictions_preserve_raw_score_in_value():
    """Prediction.value 保留原始综合分（可追溯），score 才是引擎看的阈值信号."""
    d = pd.Timestamp("2022-01-03")
    idx = pd.MultiIndex.from_tuples([(d, "A"), (d, "B")], names=["date", "ticker"])
    scores = pd.Series([0.73, 0.21], index=idx, name="value_score")
    preds = build_quarterly_predictions(scores, top_n=1)
    by_ticker = {p.ticker: p for p in preds}
    # value 是原始综合分
    assert by_ticker["A"].value == pytest.approx(0.73)
    assert by_ticker["B"].value == pytest.approx(0.21)
    # score 是阈值信号（A 是 Top-1）
    assert by_ticker["A"].score == 0.99
    assert by_ticker["B"].score == 0.01


def test_build_predictions_nan_scores_dropped():
    """综合分为 NaN 的票当日不参与排名（不产出 Prediction）."""
    d = pd.Timestamp("2022-01-03")
    idx = pd.MultiIndex.from_tuples(
        [(d, "A"), (d, "B"), (d, "C")], names=["date", "ticker"]
    )
    scores = pd.Series([0.9, np.nan, 0.5], index=idx, name="value_score")
    preds = build_quarterly_predictions(scores, top_n=2)
    tickers = {p.ticker for p in preds}
    assert tickers == {"A", "C"}, "NaN 分数的 B 不应出现在 predictions 里"


def test_build_predictions_dataframe_input():
    """score_panel 是 DataFrame 时，需指定 score_col."""
    d = pd.Timestamp("2022-01-03")
    idx = pd.MultiIndex.from_tuples([(d, "A"), (d, "B")], names=["date", "ticker"])
    df = pd.DataFrame({"value_score": [0.8, 0.3], "other": [1, 2]}, index=idx)
    preds = build_quarterly_predictions(df, top_n=1, score_col="value_score")
    assert {p.ticker for p in preds} == {"A", "B"}
    # 不传 score_col → 抛错
    with pytest.raises(ValueError, match="score_col"):
        build_quarterly_predictions(df, top_n=1)


def test_build_predictions_empty_panel():
    """空综合分 panel → 空列表，不抛错."""
    empty = pd.Series(
        dtype=float,
        index=pd.MultiIndex.from_arrays([[], []], names=["date", "ticker"]),
        name="value_score",
    )
    assert build_quarterly_predictions(empty, top_n=5) == []


# ===========================================================================
# yearly_alpha_breakdown —— 按年超额分解（用构造数据精确验证算术）
# ===========================================================================

def test_yearly_breakdown_arithmetic():
    """命门：按年超额分解的算术必须精确正确.

    构造一条「策略每天 +0.1%、基准每天 0%」的曲线，手算当年累计收益验证。
    """
    # 2023 全年交易日
    dates = pd.bdate_range("2023-01-01", "2023-12-31")
    n = len(dates)
    # 策略：每日 +0.1% → 净值复利
    strat_daily = 0.001
    equity = pd.Series(
        [1_000_000.0 * (1 + strat_daily) ** i for i in range(n)],
        index=dates,
    )
    # 基准：每日 0%
    bench = pd.Series([0.0] * n, index=dates)

    breakdown = yearly_alpha_breakdown(equity, bench)
    assert len(breakdown) == 1
    row = breakdown[0]
    assert row["year"] == 2023
    # 策略当年收益 = (1.001)^(n-1) - 1（pct_change 丢首日，n-1 个收益）
    expected_strat = (1 + strat_daily) ** (n - 1) - 1
    assert row["strategy_return"] == pytest.approx(expected_strat, rel=1e-9)
    # 基准当年收益 = 0
    assert row["benchmark_return"] == pytest.approx(0.0, abs=1e-12)
    # 超额 = 策略 - 基准
    assert row["excess_return"] == pytest.approx(expected_strat, rel=1e-9)


def test_yearly_breakdown_multi_year():
    """跨年时每个自然年一条记录，按年升序."""
    dates = pd.bdate_range("2022-01-01", "2024-12-31")
    equity = pd.Series(
        np.linspace(1_000_000.0, 1_200_000.0, len(dates)),
        index=dates,
    )
    bench = pd.Series(np.zeros(len(dates)), index=dates)
    breakdown = yearly_alpha_breakdown(equity, bench)
    years = [r["year"] for r in breakdown]
    assert years == [2022, 2023, 2024]


def test_yearly_breakdown_no_benchmark():
    """基准缺失（None）时，仍算策略各年收益，但基准 / 超额列为 None（如实缺失）."""
    dates = pd.bdate_range("2023-01-01", "2023-12-31")
    equity = pd.Series(
        np.linspace(1_000_000.0, 1_100_000.0, len(dates)),
        index=dates,
    )
    breakdown = yearly_alpha_breakdown(equity, None)
    assert len(breakdown) == 1
    row = breakdown[0]
    assert row["strategy_return"] is not None
    assert row["benchmark_return"] is None, "基准缺失时不应伪造基准收益"
    assert row["excess_return"] is None, "基准缺失时超额必须是 None，不能假装算出来"


def test_yearly_breakdown_empty_equity():
    """空净值曲线 → 空列表."""
    assert yearly_alpha_breakdown(pd.Series(dtype=float), None) == []


def test_yearly_breakdown_clips_benchmark_to_strategy_span():
    """命门：基准比策略覆盖更长时，按年表必须把基准裁到策略日期范围.

    bug 场景（verifier 实测）：基准数据拉到「今天」，但策略回测末日更早。
    末年若不裁剪 —— 策略末年只算到回测末日、基准末年却算到基准数据末日 ——
    末年超额会被算错（区间不一致）。

    本测试构造：策略只跑到 2026-04-01，基准一直延伸到 2026-05-15。
    末年（2026）的基准收益必须只统计 ≤ 2026-04-01 的部分，与策略同区间。
    """
    # 策略净值曲线：2025-01-01 ~ 2026-04-01
    strat_dates = pd.bdate_range("2025-01-01", "2026-04-01")
    equity = pd.Series(
        np.linspace(1_000_000.0, 1_050_000.0, len(strat_dates)),
        index=strat_dates,
    )

    # 基准日收益率：覆盖更长 —— 一直到 2026-05-15（比策略多一个多月）
    bench_dates = pd.bdate_range("2025-01-01", "2026-05-15")
    # 让 2026-04-01 之后的基准收益全是一个显眼的大正数；若没裁剪，2026 基准会被这段污染
    bench_vals = pd.Series(0.0, index=bench_dates)
    after_cut = bench_dates > pd.Timestamp("2026-04-01")
    bench_vals[after_cut] = 0.05  # 截断点后每天 +5% —— 没裁的话 2026 基准会暴涨
    bench = bench_vals

    breakdown = yearly_alpha_breakdown(equity, bench)
    row_2026 = next(r for r in breakdown if r["year"] == 2026)

    # 2026 策略只跑到 04-01，基准在 [2025/2026..04-01] 区间内全是 0 →
    # 正确的 2026 基准收益必须 ≈ 0（裁掉了 04-01 之后那段 +5%/天）
    assert abs(row_2026["benchmark_return"]) < 1e-9, (
        f"2026 基准收益={row_2026['benchmark_return']:.4f}，应≈0 —— "
        "说明基准没被裁到策略日期范围，末年区间不一致（verifier 揪出的 bug）"
    )


# ===========================================================================
# run_quarterly_backtest —— 端到端跑通 + 结构完整性
# ===========================================================================

def test_run_quarterly_backtest_smoke():
    """端到端跑通：合成数据上跑季度调仓回测，产出结构完整.

    注意：本测试**只验证回测能跑通、产出字段齐全**，绝不断言收益数字好坏。
    """
    panel = _make_price_panel("2022-01-01", "2024-06-30")
    scores = _make_score_panel(panel)
    # 合成一条基准日收益率
    all_dates = sorted({d for d in panel.index.get_level_values("date")})
    rng = np.random.default_rng(99)
    bench = pd.Series(rng.normal(0.0002, 0.012, len(all_dates)), index=pd.DatetimeIndex(all_dates))

    out = run_quarterly_backtest(
        scores, panel, benchmark_returns=bench,
        config=QuarterlyBacktestConfig(top_n=10),
    )

    # 结构完整性
    assert "result" in out and "metrics" in out
    assert "yearly_breakdown" in out and "config" in out and "disclaimers" in out
    # metrics 关键字段存在（值不做大小断言）
    m = out["metrics"]
    for key in ("total_return", "annualized_return", "max_drawdown", "sharpe", "trading_days"):
        assert key in m, f"metrics 缺字段 {key}"
    # 按年分解非空（回测跨 2022/2023/2024）
    years = {r["year"] for r in out["yearly_breakdown"]}
    assert years.issubset({2022, 2023, 2024})
    assert len(years) >= 1


def test_run_quarterly_backtest_config_echo():
    """回测结果回显配置：top_n / 调仓频率 / 调仓次数."""
    panel = _make_price_panel("2022-01-01", "2023-12-31")
    scores = _make_score_panel(panel)
    out = run_quarterly_backtest(scores, panel, config=QuarterlyBacktestConfig(top_n=15))
    cfg = out["config"]
    assert cfg["top_n"] == 15
    assert "quarterly" in cfg["rebalance_frequency"]
    # 2022-01 ~ 2023-12 = 2 年 × 4 季 = 8 个调仓日
    assert cfg["n_rebalances"] == 8


def test_run_quarterly_backtest_disclaimers_nonempty():
    """命门（诚信）：回测结果必须带非空的诚实局限声明.

    回测有幸存者偏差 / 区间短 / 成交假设乐观等局限 —— 这些必须随结果一起披露，
    不能让结果「裸奔」。
    """
    panel = _make_price_panel("2022-01-01", "2023-06-30")
    scores = _make_score_panel(panel)
    out = run_quarterly_backtest(scores, panel)
    disclaimers = out["disclaimers"]
    assert isinstance(disclaimers, list) and len(disclaimers) >= 3, (
        "回测必须附带诚实局限声明，且至少覆盖：区间短 / 幸存者偏差 / 成交假设"
    )
    joined = "".join(disclaimers)
    assert "幸存者偏差" in joined, "局限声明必须提到幸存者偏差"


def test_run_quarterly_backtest_no_benchmark_alpha_is_none():
    """命门（诚信）：基准缺失时，超额收益相关指标必须是 None，绝不伪造.

    沪深300 指数拿不到时，alpha 是「无法计算」，不是 0 也不是某个编出来的数。
    """
    panel = _make_price_panel("2022-01-01", "2023-12-31")
    scores = _make_score_panel(panel)
    out = run_quarterly_backtest(scores, panel, benchmark_returns=None)
    # 按年分解里超额必须全 None
    for row in out["yearly_breakdown"]:
        assert row["excess_return"] is None, "无基准时按年超额必须 None"
        assert row["benchmark_return"] is None, "无基准时按年基准收益必须 None"


def test_run_quarterly_backtest_costs_are_charged():
    """季度调仓回测确实计入了交易成本（佣金 / 印花税 / 滑点）.

    验证：成交流水里成本列之和 > 0（只要发生过交易）。不验证成本「应该是多少」——
    只确认成本不是 0（即真的扣了钱）。
    """
    panel = _make_price_panel("2022-01-01", "2024-12-31")
    scores = _make_score_panel(panel)
    out = run_quarterly_backtest(scores, panel, config=QuarterlyBacktestConfig(top_n=10))
    trades = out["result"].trades
    if trades is not None and len(trades) > 0:
        total_cost = 0.0
        for col in ("commission", "stamp_tax", "slippage_cost"):
            if col in trades.columns:
                total_cost += float(trades[col].sum())
        assert total_cost > 0, "发生了交易却没扣任何成本 —— 成本模型没生效"
    else:
        pytest.skip("本次回测未产生交易，无法验证成本（合成数据偶发）")
