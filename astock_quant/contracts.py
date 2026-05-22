"""全局数据契约 —— 模块之间传递的数据结构，单一事实来源.

设计原则（思路来自 ai-hedge-fund v2 models.py）：
- 跨模块边界传 Pydantic 对象或约定 schema，不传裸 dict；DataFrame 仅在模块内部用。
- `target_type` 是 4 类预测目标共用基础设施的锚点：同一套 Label / Prediction 契约，
  靠 target_type 区分，下游 models / signals 按 type 分派。

P2+ 各阶段按需补全字段；本文件 stub 阶段只列骨架与字段意图，不写校验/方法逻辑。
"""

from __future__ import annotations

from datetime import date as _date

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

# 4 类预测目标的类型标签 —— labels / models / signals 按此分派
TargetType = Literal["direction", "return", "ranking", "trade_signal"]


# ---------------------------------------------------------------------------
# 数据层契约（data/ 产出）
# ---------------------------------------------------------------------------

class PriceBar(BaseModel):
    """单根 OHLCV 日线 K 线 —— data 层产出的最小行情单元.

    一只票一个交易日一条。dataset 把多只票多日的 PriceBar 拼成 panel。
    `amount`（成交额，元）部分数据源可能缺，允许 None。
    """

    ticker: str  # 纯 6 位代码，如 "600519"
    date: _date  # 交易日
    open: float
    high: float
    low: float
    close: float
    volume: float  # 成交量（股；mootdx 的 vol 实为手，astock_source 内部已换算口径见注释）
    amount: float | None = None  # 成交额（元），可缺


class FinancialMetrics(BaseModel):
    """单只股票某报告期的财务指标 —— fundamental 因子的原料.

    A股 财务数据按报告期（季报/年报）发布，`report_period` 标识是哪一期。
    各字段口径：盈利能力来自季报快照，估值（pe/pb）来自实时行情快照。
    数据源拼不齐的字段一律允许 None —— fundamental 因子自己处理缺失。

    防 look-ahead 关键：`publish_date`（财报实际可见日）。报告期末日 != 可见日 ——
    年报 / 一季报最晚 4/30 才披露，半年报 8/31，三季报 10/31。因子层对齐到交易日 T
    时必须用 `publish_date <= T` 截断，而非 `report_period <= T`，否则会用到「当时
    还没公布」的财报（典型未来函数）。详见 data/fundamentals.py 的口径说明。
    """

    ticker: str
    report_period: str  # 报告期末日，如 "20251231"
    publish_date: str | None = None  # 财报实际可见日 "YYYYMMDD"（防 look-ahead 用，见类 docstring）
    eps: float | None = None  # 每股收益（同花顺口径为「累计 YTD」，TTM 换算见 fundamentals.py）
    roe: float | None = None  # 净资产收益率（%）
    revenue: float | None = None  # 营业总收入（元）
    net_profit: float | None = None  # 净利润（元）
    total_assets: float | None = None  # 总资产（元）
    net_assets: float | None = None  # 净资产 / 所有者权益（元）
    bvps: float | None = None  # 每股净资产
    eps_ttm: float | None = None  # 每股收益 TTM（滚动 4 季，由 fundamentals.py 算出）
    gross_margin: float | None = None  # 销售毛利率（%）
    net_margin: float | None = None  # 销售净利率（%）
    debt_ratio: float | None = None  # 资产负债率（%）
    dividend_per_share: float | None = None  # 近 12 个月每股现金分红（元，税前）
    dividend_yield: float | None = None  # 股息率（近 12 月每股分红 / 当时股价）
    pe: float | None = None  # 市盈率 TTM（来自实时行情快照）
    pb: float | None = None  # 市净率（来自实时行情快照）
    total_share: float | None = None  # 总股本（股）
    float_share: float | None = None  # 流通股本（股）


class MoneyFlowRecord(BaseModel):
    """单只股票某日的资金流记录 —— moneyflow 因子的原料.

    注意：P0 报告提醒部分资金流端点只有短历史（如北向自 2024-08 起断供、
    百度个股资金流仅最近 ~20 交易日）。所以除 ticker/date 外字段全部可缺，
    长周期回测里这些列会大面积为 None —— moneyflow 因子需对此鲁棒。
    """

    ticker: str
    date: _date
    main_inflow: float | None = None  # 主力净流入（元）
    super_inflow: float | None = None  # 超大单净流入（元）
    large_inflow: float | None = None  # 大单净流入（元）
    northbound: float | None = None  # 北向资金净买入（元；多为市场级，个股级常缺）
    dragon_tiger_net: float | None = None  # 当日龙虎榜净买额（元），未上榜为 None


class NewsItem(BaseModel):
    """单条新闻 / 研报 / 公告文本 —— Stage 2 LLM 因子的输入.

    Stage 1 只固定形状（供 DataSource.get_news 返回、astock_source 已可实现），
    Stage 2 的 llm_factor 消费 title + content 抽取情绪/事件因子。
    `ticker` 为 None 表示大盘级新闻（非个股相关）。
    """

    ticker: str | None = None  # 关联个股代码；大盘新闻为 None
    date: _date  # 发布日期
    title: str
    content: str = ""  # 正文 / 摘要，可能为空（只有标题）
    source: str = ""  # 来源，如 "东方财富" / "财联社"
    url: str | None = None  # 原文链接


# ---------------------------------------------------------------------------
# 因子层契约（factors/ 产出）
# ---------------------------------------------------------------------------

class FactorValue(BaseModel):
    """单个因子在 单 ticker × 单日 的值.

    量价因子 / 财务因子 / 资金流因子 / Stage 2 LLM 因子产出的都是这个契约 ——
    对下游（registry / labels / models）无差别。

    本契约用于「单点查询」（如某个调试场景看 ticker T 在日期 D 的 RSI），
    批量计算的产出走 FactorFrame（更高效，直接 DataFrame 操作）。
    """

    factor_name: str  # 因子标识，如 'momentum_20d' / 'roe' / 'main_inflow_5d'
    ticker: str
    date: _date
    value: float | None  # NaN / 数据缺失时为 None
    z_score: float | None = None  # 可选：横截面 z-score
    percentile: float | None = None  # 可选：横截面分位（0-100）


class FactorFrame(BaseModel):
    """一批因子值的集合（对应 v2 的 QuantSignals 思路）.

    内部持有 DataFrame：MultiIndex=(date, ticker)，columns=因子名。
    registry 批量计算后产出此对象 —— 作为模型层的输入特征矩阵 X。

    用法：
        ff = FactorFrame(data=df, factor_names=["momentum_20d", "rsi_14", ...])
        ff.data        # 拿到底层 DataFrame
        ff.factor_names  # 当前包含的因子名列表
        ff.align_with(label_frame)  # 与 LabelFrame 对齐（按 (date, ticker)）

    设计：DataFrame 是模块内部的高效格式，FactorFrame 是跨模块边界传递的契约容器。
    Pydantic v2 需 model_config 允许 arbitrary types 才能持有 DataFrame。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: pd.DataFrame = Field(description="MultiIndex=(date, ticker), columns=因子名")
    factor_names: list[str] = Field(
        default_factory=list, description="因子名列表（= data.columns 的快照，可读性 / schema 校验用）"
    )

    @property
    def shape(self) -> tuple[int, int]:
        """(行数, 因子数)。"""
        return self.data.shape

    @property
    def n_observations(self) -> int:
        """总 (date, ticker) 观测数（行数）。"""
        return len(self.data)

    def nan_ratio(self) -> "pd.Series":
        """每个因子的 NaN 比例（columns → ratio）。资金流因子大量 NaN 是正常的（见 P2 说明）。"""
        if self.data.empty:
            return pd.Series(dtype=float)
        return self.data.isna().mean()


# ---------------------------------------------------------------------------
# 标签层契约（labels/ 产出）
# ---------------------------------------------------------------------------

class Label(BaseModel):
    """单个训练标签 —— labels/ 层产出，按 target_type 喂给对应模型.

    字段意图（与 dev-docs/P1-架构设计.md 3.1 节一致）：
        - direction:    value 为 0/1（涨/跌二分类，由 horizon + threshold 决定）
        - return:       value 为 float（未来 N 日收益率，扩展点 stub）
        - ranking:      value 为横截面排序 / 分位（扩展点 stub）
        - trade_signal: value 为买卖点标注（扩展点 stub）

    Stage 1 只产出 direction 的实际数据；②③④ 的接入位在 labels/targets.py 已留 stub。
    """

    ticker: str  # 纯 6 位代码，如 "600519"
    date: _date  # 标签观察日 T（基于 T 的因子去预测 T 之后 horizon 日的表现）
    target_type: TargetType  # 4 类目标共用契约的分派锚点
    value: float  # direction: 0.0 或 1.0；return: 未来收益率；ranking: 分位；trade_signal: 标签编码


# ---------------------------------------------------------------------------
# 模型层契约（models/ 产出）
# ---------------------------------------------------------------------------

class Prediction(BaseModel):
    """模型单条预测输出 —— models/ 层产出，下游 backtest / signals 按 target_type 分派.

    字段意图（与 dev-docs/P1-架构设计.md 3.1 节一致）：
        - direction:    value 为 0/1（硬分类结果）；score 为 P(涨)（默认阈值 0.5）；
                        proba 是 (P(跌), P(涨)) 元组，便于做不同阈值的 backtest 实验
        - return:       value 为预测收益率（float）；score 同 value（扩展点 stub）
        - ranking:      value 为模型分数；score 是同日横截面的分位 / rank（扩展点 stub）
        - trade_signal: value 为信号枚举的数值编码（扩展点 stub）

    Stage 1 只产出 direction 的实际数据。score 是「连续可比」字段，下游做阈值调优 /
    选 Top N 都看 score，不直接看 value（value 是离散硬分类，丢信息）。
    """

    ticker: str
    date: _date  # 预测发出日 T —— 基于截至 T 的因子（含 T 当日）预测 T 之后的表现
    target_type: TargetType
    value: float  # 硬预测（direction 时是 0/1，其它见上）
    score: float | None = None  # 连续分数：direction 时是 P(涨)，ranking 时是 rank/分位
    proba: tuple[float, float] | None = None  # 二分类时的 (P(跌), P(涨))；其它目标不用


# ---------------------------------------------------------------------------
# 回测 / 信号层契约
# ---------------------------------------------------------------------------

class BacktestResult(BaseModel):
    """回测结果 —— backtest/engine.py 产出.

    设计：把「时序数据」（净值曲线 / 成交流水 / 持仓快照）放在 DataFrame 里
    （MultiIndex / 列名约定见字段 docstring），把「单点指标」放在 metrics dict 里。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    equity_curve: pd.DataFrame = Field(
        description=(
            "净值曲线 DataFrame，index=date（DatetimeIndex），列：\n"
            "  - portfolio_value: 当日组合总市值（现金 + 持仓市值）\n"
            "  - cash: 当日现金余额\n"
            "  - holdings_value: 当日持仓市值\n"
            "  - daily_return: 当日净值收益率（pct_change）\n"
            "  - n_positions: 当日持仓股票数"
        )
    )
    metrics: dict[str, float | int | str | None] = Field(
        default_factory=dict,
        description=(
            "汇总指标：\n"
            "  - total_return / annualized_return：累计收益 / 年化收益（小数）\n"
            "  - sharpe / sortino：年化 Sharpe / Sortino（无风险利率默认 0.02）\n"
            "  - max_drawdown：最大回撤（负数小数，如 -0.18 表示 -18%）\n"
            "  - max_drawdown_date：最大回撤发生日（ISO 字符串）\n"
            "  - win_rate：盈利交易占比（已平仓交易统计）\n"
            "  - profit_loss_ratio：平均盈利 / 平均亏损\n"
            "  - n_trades / n_buy_orders / n_sell_orders：交易统计\n"
            "  - n_rejected_constraint：被 A股 约束拦截的下单数（涨跌停 / T+1 / 手数等）\n"
            "  - start_date / end_date / trading_days：回测区间与交易日数"
        )
    )
    trades: pd.DataFrame = Field(
        description=(
            "成交流水 DataFrame，列：date, ticker, action(buy/sell), quantity, "
            "price, gross_amount, commission, stamp_tax, slippage_cost, "
            "net_cash_flow（卖入账 / 买出账，含手续费）, reason"
        )
    )
    positions: pd.DataFrame = Field(
        description=(
            "持仓时序快照 DataFrame，MultiIndex=(date, ticker)，列：\n"
            "  - quantity: 持股数（股）\n"
            "  - avg_cost: 持仓均价\n"
            "  - close: 当日收盘价\n"
            "  - market_value: 持仓市值\n"
            "  - unrealized_pnl: 浮动盈亏"
        )
    )

    @property
    def n_days(self) -> int:
        return len(self.equity_curve)


class SignalItem(BaseModel):
    """单条信号 —— SignalReport 的元素."""

    date: _date
    ticker: str
    action: Literal["buy", "sell", "hold"]  # A股 只做多，没有 short / cover
    strength: float = 0.0  # 信号强度（0~1）。direction 时 = |score - 0.5| × 2，越偏离 0.5 越强
    score: float | None = None  # 透传 Prediction.score，便于排序 / 调阈值
    reason: str = ""  # 人话解释（"模型预测涨概率 0.62，超过买入阈值 0.55"）


class SignalReport(BaseModel):
    """信号层产出 —— 人可读的买卖 / 持仓信号.

    设计：每条 signal 一行（date × ticker × action），独立成 SignalItem；
    SignalReport 持一个 list。生成时机为「模型给出 Prediction 之后、回测下单之前」，
    所以同一份 SignalReport 既可以让用户读、也可以直接喂回测引擎。
    """

    target_type: TargetType  # 哪一类预测目标产出的信号
    items: list[SignalItem] = Field(default_factory=list)
    notes: str = ""  # 可选的整体说明（如阈值、用了哪个模型）

    def to_dataframe(self) -> pd.DataFrame:
        """把 items 展平成 DataFrame，便于交付给回测引擎或写 CSV."""
        if not self.items:
            return pd.DataFrame(columns=["date", "ticker", "action", "strength", "score", "reason"])
        return pd.DataFrame([i.model_dump() for i in self.items])
