"""季度调仓回测 —— 价值选股策略的回测引擎.

T4 价值选股改造的回测层。回答的核心问题：
**按季度持有「价值+质量综合分最高的一篮子好公司」，扣掉真实交易成本后，
能不能跑赢躺平买沪深300指数？跑赢多少？**

────────────────────────────────────────────────────────────────────────
为什么是「季度调仓」而不是「逐日 / 周频」
────────────────────────────────────────────────────────────────────────
价值因子（便宜 + 能赚钱的好公司）的逻辑是「market 长期会修复错误定价」——
这个修复以季度 / 年为单位发生（与 T3 的 value_ranking_label horizon=60 同源）。
逐日 / 周频调仓只会把价值信号淹没在噪音里，还白交一堆手续费。所以：
  - 每个**自然季度初**（1/4/7/10 月第一个交易日）按综合分挑 Top-N 只
  - 等权买入，持有整个季度
  - 下个季度初再调一次，一年换手 4 次

────────────────────────────────────────────────────────────────────────
实现：复用 BacktestEngine，不重写撮合 / 成本 / A股 约束
────────────────────────────────────────────────────────────────────────
现有 `BacktestEngine` 已经把 A股 的硬规则做对了 —— T+1、涨跌停、100 股整手、
ST 过滤、佣金 / 印花税 / 滑点。季度调仓**没有理由重写这些**，只是「下单节奏」
不同。所以本模块的做法（与 scripts/realistic_backtest.py 的 build_topn_predictions
同款手法，但调仓周期换成季度）：

  把「每个交易日每只票的综合分」稀释成「只在季度初出现」的离散买卖信号：
    - 季度初那天：综合分 Top-N 的票 score 改写 0.99（≥ buy_threshold → 买入）；
      其余票 score 改写 0.01（< sell_threshold → 卖出 / 不买）
    - 季度中的其它日子：不产出 prediction → 引擎 missing_prediction_action="hold"
      下维持持仓不动

引擎照常逐日推进、逐日 mark-to-market，但实际成交只发生在季度初。

────────────────────────────────────────────────────────────────────────
三道防 look-ahead（与项目全局防线对齐）
────────────────────────────────────────────────────────────────────────
1. 数据层：score_panel 的每个 (date, ticker) 分数必须是「截至 date 的信息」算出的
   —— 这是上游 T2 综合打分 + T1 财务数据（带 publish_date 保守可见日）的职责。
   本模块不重新算分，只消费传入的 score_panel；但要求传入方已守住这条。
2. 撮合层：BacktestEngine 在交易日 T 用 close[T-1] 判涨跌停、close[T] 成交，
   绝不用未来价格。季度初下单时只看「当天及之前」的综合分。
3. 调仓日选取：季度边界由日历（1/4/7/10 月）决定，是**确定性、与数据无关**的，
   不存在「挑了事后看最好的调仓点」这种 look-ahead。

────────────────────────────────────────────────────────────────────────
诚实边界（写进结果，不藏）
────────────────────────────────────────────────────────────────────────
- 回测区间只有 ~4 年（2022-2026），是单一市场环境，结论是「参考」不是「铁证」。
- 选股池若是「今日沪深300成分」，自带幸存者偏差（退市 / 被剔除的输家缺席）。
- 这些局限由调用方 / 报告层如实披露；本模块的指标计算不做任何粉饰。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any

import pandas as pd

from astock_quant.backtest.engine import BacktestEngine, BacktestRunConfig
from astock_quant.contracts import BacktestResult, Prediction

logger = logging.getLogger(__name__)


# ===========================================================================
# 配置
# ===========================================================================

@dataclass
class QuarterlyBacktestConfig:
    """季度调仓回测参数.

    与 BacktestRunConfig（引擎级撮合参数）的关系：本类只管「季度调仓策略」
    特有的参数（持仓只数），撮合 / 成本参数透传给底层 BacktestRunConfig。
    """

    top_n: int = 15  # 每季度持有的股票数（任务要求 15-20，默认 15）
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003  # 佣金（双边，万 3）
    stamp_tax_rate: float = 0.0005  # 印花税（卖出单边，千 0.5）
    slippage_bps: float = 5.0  # 滑点（万 5）
    skip_st: bool = True  # 过滤 ST 股
    st_set: set[str] = field(default_factory=set)  # 已知 ST 名单
    # A股 每年约 242 个交易日（与 settings / labels 全项目口径一致）—— 年化收益折算的分母。
    annual_trading_days: int = 242
    annual_rf_rate: float = 0.02  # 年化无风险利率（夏普计算用）

    def to_run_config(self) -> BacktestRunConfig:
        """转成底层引擎的 BacktestRunConfig.

        关键设置：
          - buy_threshold / sell_threshold 固定 0.55 / 0.45 —— 季度调仓时
            Top-N 的票 score 被改写成 0.99（> 0.55 触发买），其余 0.01（< 0.45 触发卖）
          - max_positions = top_n
          - missing_prediction_action = "hold" —— 季度中非调仓日不产出 prediction，
            必须 hold 才不会误清仓
        """
        return BacktestRunConfig(
            initial_cash=self.initial_cash,
            commission_rate=self.commission_rate,
            stamp_tax_rate=self.stamp_tax_rate,
            slippage_bps=self.slippage_bps,
            buy_threshold=0.55,
            sell_threshold=0.45,
            max_positions=self.top_n,
            skip_st=self.skip_st,
            st_set=set(self.st_set),
            annual_trading_days=self.annual_trading_days,
            annual_rf_rate=self.annual_rf_rate,
            missing_prediction_action="hold",
        )


# ===========================================================================
# 季度边界 —— 调仓日选取
# ===========================================================================

def quarter_start_dates(all_dates: list[_date]) -> list[_date]:
    """从一组交易日里挑出「每个自然季度的第一个交易日」.

    自然季度 = (年, 季)，季 ∈ {1,2,3,4} 对应 1-3 / 4-6 / 7-9 / 10-12 月。
    每个 (年, 季) 桶里日期最小的那天就是该季度的调仓日。

    为什么用日历季度而不是 `dates[::60]`：
      - 「每季度初调仓」字面意思就是日历季度，直观、可解释
      - dates[::60] 是「按位置每 60 个交易日」，季度长度不均（有的季度交易日多 /
        少），位置切法会逐渐和真实季度错开
      - 日历季度是**确定性**的，与回测数据无关 → 不存在「挑调仓点」的 look-ahead

    参数：
        all_dates: 交易日列表（datetime.date），无需预先排序。

    返回：
        升序的调仓日列表（每个自然季度一个）。空输入返回空列表。
    """
    if not all_dates:
        return []
    # (year, quarter) → 该桶最早的交易日
    bucket: dict[tuple[int, int], _date] = {}
    for d in all_dates:
        q = (d.month - 1) // 3 + 1  # 1..12 月 → 1..4 季
        key = (d.year, q)
        if key not in bucket or d < bucket[key]:
            bucket[key] = d
    return sorted(bucket.values())


# ===========================================================================
# 综合分 panel → 季度调仓 predictions
# ===========================================================================

def build_quarterly_predictions(
    score_panel: pd.Series | pd.DataFrame,
    top_n: int,
    *,
    score_col: str | None = None,
) -> list[Prediction]:
    """把「每日每票综合分」稀释成「只在季度初出现」的 Top-N 调仓信号.

    这是季度调仓回测的命门函数。现有 BacktestEngine 是「阈值穿越驱动」，不是
    「周期调仓驱动」—— 不做这步稀释，引擎会每天按分数买卖，换手率 / 成本完全失真。

    做法（纯数据变换，不碰引擎）：
      1. 取所有交易日，用 quarter_start_dates 找出每个自然季度的第一个交易日
      2. 季度初那天：当日综合分 Top-N（降序）的票 → Prediction(score=0.99)；
         其余票 → Prediction(score=0.01)
      3. 非季度初的日子：不产出 Prediction → 引擎 hold 维持持仓

    参数：
        score_panel:  综合分。支持两种形态：
                        - pd.Series，MultiIndex=(date, ticker)，值为综合分
                        - pd.DataFrame，MultiIndex=(date, ticker)，取 score_col 列
                      综合分越大越好（越「便宜 + 能赚钱」）。NaN 分数的票当日不参与排名。
        top_n:        每季度选的股票数。
        score_col:    score_panel 是 DataFrame 时，综合分所在列名（必填）。

    返回：
        list[Prediction]，target_type="ranking"。只在季度初的交易日有数据。
        每个季度初产出当日全部有分数的票（Top-N 是 0.99，其余 0.01）。
    """
    # 统一成 Series
    if isinstance(score_panel, pd.DataFrame):
        if score_col is None:
            raise ValueError("score_panel 是 DataFrame 时必须指定 score_col")
        if score_col not in score_panel.columns:
            raise ValueError(
                f"score_panel 缺少列 '{score_col}'，可用列: {list(score_panel.columns)}"
            )
        scores = score_panel[score_col]
    elif isinstance(score_panel, pd.Series):
        scores = score_panel
    else:
        raise TypeError(f"score_panel 必须是 pd.Series 或 pd.DataFrame，收到 {type(score_panel)}")

    if scores.empty:
        return []
    if not isinstance(scores.index, pd.MultiIndex):
        raise ValueError("score_panel 必须是 MultiIndex=(date, ticker)")

    # 丢掉 NaN 分数（当日该票无综合分 → 不参与排名）
    scores = scores.dropna()
    if scores.empty:
        return []

    # 所有交易日 → 季度初调仓日
    all_dates = sorted({_to_date(d) for d in scores.index.get_level_values("date")})
    rebal_dates = set(quarter_start_dates(all_dates))
    if not rebal_dates:
        return []

    out: list[Prediction] = []
    # 按 date 分组，只处理调仓日
    for date_key, group in scores.groupby(level="date"):
        d = _to_date(date_key)
        if d not in rebal_dates:
            continue  # 非季度初 → 不产出，引擎 hold
        # group 是该日所有 (date, ticker) 的分数；降序取 Top-N
        day_scores = group.droplevel("date").sort_values(ascending=False)
        topn_tickers = set(day_scores.index[:top_n])
        for ticker, raw_score in day_scores.items():
            is_top = ticker in topn_tickers
            out.append(
                Prediction(
                    ticker=str(ticker),
                    date=d,
                    target_type="ranking",
                    # value 保留原始综合分（可追溯）；score 是引擎实际看的阈值信号
                    value=float(raw_score),
                    score=0.99 if is_top else 0.01,
                )
            )
    return out


# ===========================================================================
# 按年份的超额收益分解
# ===========================================================================

def yearly_alpha_breakdown(
    strategy_equity: pd.Series,
    benchmark_returns: pd.Series | None,
) -> list[dict[str, Any]]:
    """把策略 vs 沪深300 的超额收益按自然年分解 —— 看「哪年赢、哪年输」.

    诚实地展示策略不是每年都赢 —— 单一区间的总超额可能是某一年的运气堆出来的。
    按年拆开，读者能看到 alpha 的稳定性。

    参数：
        strategy_equity:   策略净值曲线（pd.Series，DatetimeIndex，值=组合总市值）。
        benchmark_returns: 沪深300 日收益率（pd.Series，DatetimeIndex）。None 时
                           只算策略各年收益，超额列为 None。

    返回：
        list[dict]，每个自然年一条，按年升序，字段：
          - year:              年份（int）
          - strategy_return:   策略当年收益率（小数）
          - benchmark_return:  沪深300 当年收益率（小数；基准缺失为 None）
          - excess_return:     超额 = 策略 - 基准（基准缺失为 None）
          - n_trading_days:    当年纳入计算的交易日数

    ────────────────────────────────────────────────────────────────────────
    区间对齐（命门）—— benchmark 必须 clip 到策略净值曲线的日期范围
    ────────────────────────────────────────────────────────────────────────
    benchmark_returns 可能比策略净值曲线覆盖更长（典型：基准数据拉到「今天」，
    但策略回测只跑到某个更早的末日）。若不裁剪，按年分解时**末年**会出现区间不一致：
    策略末年只算到回测末日，基准末年却算到基准数据末日 —— 末年超额被算错。
    （verifier 实测：策略 2026 跑到 04-01，基准若不裁会算到 05-15，2026 超额失真。）

    所以这里先把 benchmark_returns 裁剪到 [策略首日, 策略末日]，保证每个自然年里
    策略与基准用的是**同一段交易日**。headline 指标（compute_metrics 里的
    _benchmark_compare）本来就按两序列交集 dropna，不受此 bug 影响；受影响的只有
    本函数的按年表 —— 故在此处修。
    """
    if strategy_equity is None or strategy_equity.empty:
        return []

    eq = strategy_equity.sort_index()
    eq.index = pd.to_datetime(eq.index)
    # 策略日收益率
    strat_ret = eq.pct_change().dropna()

    bench_ret = None
    if benchmark_returns is not None and not benchmark_returns.empty:
        bench_ret = benchmark_returns.sort_index()
        bench_ret.index = pd.to_datetime(bench_ret.index)
        # ★ 命门：把基准裁到策略净值曲线的日期范围 —— 否则末年区间不一致、超额算错。
        if not strat_ret.empty:
            span_lo, span_hi = strat_ret.index.min(), strat_ret.index.max()
            bench_ret = bench_ret[(bench_ret.index >= span_lo) & (bench_ret.index <= span_hi)]

    out: list[dict[str, Any]] = []
    years = sorted({ts.year for ts in strat_ret.index})
    for year in years:
        year_strat = strat_ret[strat_ret.index.year == year]
        if year_strat.empty:
            continue
        # 当年累计收益 = (1+r) 连乘 - 1
        strat_year_ret = float((1.0 + year_strat).prod() - 1.0)

        bench_year_ret: float | None = None
        excess: float | None = None
        if bench_ret is not None:
            year_bench = bench_ret[bench_ret.index.year == year]
            if not year_bench.empty:
                bench_year_ret = float((1.0 + year_bench).prod() - 1.0)
                excess = strat_year_ret - bench_year_ret

        out.append(
            {
                "year": int(year),
                "strategy_return": strat_year_ret,
                "benchmark_return": bench_year_ret,
                "excess_return": excess,
                "n_trading_days": int(len(year_strat)),
            }
        )
    return out


# ===========================================================================
# 主入口：跑一次季度调仓回测
# ===========================================================================

def run_quarterly_backtest(
    score_panel: pd.Series | pd.DataFrame,
    price_panel: pd.DataFrame,
    benchmark_returns: pd.Series | None = None,
    config: QuarterlyBacktestConfig | None = None,
    *,
    score_col: str | None = None,
) -> dict[str, Any]:
    """跑一次完整的季度调仓回测，输出策略指标 + 沪深300 对照 + 按年超额分解.

    参数：
        score_panel:        价值+质量综合分。Series(MultiIndex date,ticker) 或
                            DataFrame(取 score_col)。综合分越大越好。
        price_panel:        行情 panel，MultiIndex=(date, ticker)，含 close 列。
                            必须覆盖回测区间所有 (date, ticker)。
        benchmark_returns:  沪深300 指数日收益率（pd.Series）。建议用
                            `data.benchmark.csi300_daily_returns()` 取真指数。
                            None 时仍能回测，但超额收益 / alpha 标 None（如实缺失）。
        config:             QuarterlyBacktestConfig；缺省走默认（Top-15）。
        score_col:          score_panel 是 DataFrame 时综合分的列名。

    返回 dict：
        {
          "result":            BacktestResult（底层引擎原始产出，含净值 / 成交 / 持仓）,
          "metrics":           策略指标 dict（年化 / 回撤 / 夏普 / 超额 …，来自引擎 metrics）,
          "yearly_breakdown":  list[dict]，按年的 策略 / 基准 / 超额 收益,
          "config":            本次回测用的配置摘要,
          "disclaimers":       list[str]，诚实局限声明（回测期短 / 幸存者偏差 …）,
        }

    诚实：返回的 disclaimers 必须随结果一起展示。回测数字是「参考」不是「实盘背书」。
    """
    config = config or QuarterlyBacktestConfig()

    # 1. 综合分 → 季度调仓 predictions
    predictions = build_quarterly_predictions(score_panel, config.top_n, score_col=score_col)
    if not predictions:
        logger.warning("季度调仓回测：build_quarterly_predictions 产出为空（综合分 panel 无有效数据）")

    # 2. 跑底层引擎（A股 约束 / 成本 / 撮合全复用）
    engine = BacktestEngine(
        price_panel=price_panel,
        config=config.to_run_config(),
        benchmark_returns=benchmark_returns,
    )
    result: BacktestResult = engine.run(predictions)

    # 3. 按年超额分解
    equity = result.equity_curve
    strategy_equity = (
        equity["portfolio_value"] if (equity is not None and "portfolio_value" in equity.columns)
        else pd.Series(dtype=float)
    )
    yearly = yearly_alpha_breakdown(strategy_equity, benchmark_returns)

    # 4. 调仓次数（= 季度数）
    n_rebalances = len({p.date for p in predictions})

    disclaimers = [
        "回测区间约 4 年（2022-2026），是单一市场环境 —— 结论是「参考」，不是「铁证」。"
        "换一段历史（牛市 / 熊市 / 震荡市）结果可能完全不同。",
        "若选股池是「今日沪深300成分股」，存在幸存者偏差：这 300 只是「涨上来 + 被指数"
        "纳入」的赢家，退市 / 被剔除的输家不在池子里，会让回测系统性偏乐观。",
        "交易成本已计入（佣金 / 印花税 / 滑点），但用收盘价成交、滑点固定、未建模冲击"
        "成本 —— 真实实盘成本通常更高。",
        "财务数据若按报告期末日而非实际披露日对齐，会有轻微 look-ahead；本回测依赖上游"
        "T1/T2 用 publish_date 保守对齐来规避，最终以代码审查（T6）结论为准。",
    ]

    return {
        "result": result,
        "metrics": dict(result.metrics),
        "yearly_breakdown": yearly,
        "config": {
            "top_n": config.top_n,
            "initial_cash": config.initial_cash,
            "commission_rate": config.commission_rate,
            "stamp_tax_rate": config.stamp_tax_rate,
            "slippage_bps": config.slippage_bps,
            "n_rebalances": n_rebalances,
            "rebalance_frequency": "quarterly (自然季度初)",
        },
        "disclaimers": disclaimers,
    }


# ===========================================================================
# helpers
# ===========================================================================

def _to_date(d) -> _date:
    """pandas Timestamp / np.datetime64 / date / str → datetime.date."""
    if isinstance(d, _date) and not isinstance(d, pd.Timestamp):
        return d
    return pd.Timestamp(d).date()


__all__ = [
    "QuarterlyBacktestConfig",
    "quarter_start_dates",
    "build_quarterly_predictions",
    "yearly_alpha_breakdown",
    "run_quarterly_backtest",
]
