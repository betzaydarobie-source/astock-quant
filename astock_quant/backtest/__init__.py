"""回测层 —— 逐日回测引擎 + A股约束 + 绩效指标 + 季度调仓.

engine 逐日推进、portfolio 管持仓现金、constraints 加 A股专属规则、metrics 算绩效。
核心逻辑重度参考 ai-hedge-fund v1 src/backtesting/（研读后用自己的话重写，不直接 import）。
产出 contracts.py 的 BacktestResult。

quarterly（T4 价值选股改造）：在 engine 之上加「季度调仓」策略层 —— 每自然季度初
按价值+质量综合分挑 Top-N 等权持有，复用 engine 的全部 A股 约束与成本模型。
"""

from astock_quant.backtest.quarterly import (
    QuarterlyBacktestConfig,
    build_quarterly_predictions,
    quarter_start_dates,
    run_quarterly_backtest,
    yearly_alpha_breakdown,
)

__all__ = [
    "QuarterlyBacktestConfig",
    "build_quarterly_predictions",
    "quarter_start_dates",
    "run_quarterly_backtest",
    "yearly_alpha_breakdown",
]
