"""因子注册表 + 批量计算编排.

职责：
- 维护一份「启用的因子」清单（量价 / 财务 / 资金流 / Stage 2 的 LLM 因子）
- 跑一遍所有因子，产出 FactorFrame（MultiIndex=(date, ticker)，columns=因子名）
- FactorFrame 是模型层的输入特征 X

扩展性（这是 4 类目标共用基础设施 + Stage 2 LLM 因子平滑接入的关键）：
- 新增因子 → 在 `default_factors()` 里 append 一行
- 下游 labels / models / backtest / signals **一行都不用改**

LLM 情绪因子开关（Stage 2 P6）：
- 默认 **不启用** —— 因为要 LLM API key 且每次跑都烧 token。
- 环境变量 `ENABLE_LLM_FACTOR=1` 时，default_factors() 会在尾部追加 LLMNewsSentiment()。
- 启用前还要设 `ANTHROPIC_API_KEY`（默认 provider）或切到自己的 provider。详见
  `factors/llm_factor.py` 模块 docstring 的「启用方式」。

使用：
    from astock_quant.factors.registry import compute_factor_frame
    from astock_quant.data.dataset import prepare_stage1_data

    data = prepare_stage1_data()
    ff = compute_factor_frame(
        price_panel=data["prices"],
        moneyflow_panel=data["moneyflow"],
        financials=data["financials"],
    )
    ff.data       # → DataFrame, MultiIndex=(date, ticker), columns=因子名
    ff.shape      # → (n_obs, n_factors)
    ff.nan_ratio()  # → 每个因子的 NaN 比例

逐日回测场景：在外层 build_*_panel 时传 `curr_date`，panel 已截断；
registry 这一层不再管 look-ahead（数据进来时已干净）。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence

import pandas as pd

from astock_quant.contracts import FactorFrame
from astock_quant.factors.base import BaseFactor
from astock_quant.factors.fundamental import (
    EPS,
    PB,
    PE,
    ROE,
    DividendYield,
    GrossMargin,
    NetMargin,
    NetProfitGrowthYoY,
    RevenueGrowthYoY,
)
from astock_quant.factors.llm_factor import LLMNewsSentiment
from astock_quant.factors.moneyflow import (
    LargeInflowRolling,
    MainInflowRolling,
    MainInflowToAmount,
    SuperInflowRolling,
)
from astock_quant.factors.price_volume import (
    RSI,
    ATRPct,
    AmountMean,
    BollingerPosition,
    MACDHist,
    Momentum,
    PriceToMA,
    Volatility,
    VolumeRatio,
    ZScore,
)

logger = logging.getLogger(__name__)


def _llm_factor_enabled() -> bool:
    """读 env var `ENABLE_LLM_FACTOR` —— 1/true/yes 视为开启，其它 / 缺省视为关闭."""
    val = os.environ.get("ENABLE_LLM_FACTOR", "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ===========================================================================
# 启用的因子清单 —— Stage 1 默认集
# ===========================================================================

def default_factors() -> list[BaseFactor]:
    """Stage 1 默认启用的因子集合.

    分类（共 27 个）：
      量价（13）：动量 5/20/60 + 价格相对均线 5/20 + ZScore 50 + RSI 14 +
                  波动率 20 + ATR% 14 + 量比 20 + 成交额均 20 + MACD柱 + 布林位置 20
      财务（9）：PE / PB / 股息率 / ROE / 净利率 / 毛利率 / EPS / 营收同比 / 净利同比
                 —— T1 重建：PE/PB 改用日线股价 + 重建财报自己算（原腾讯快照历史
                 95% NaN）；新增股息率 + 毛利率两个价值/质量因子。
      资金流（5）：主力 5/20 日 + 超大 5 日 + 大单 5 日 + 主力净流入占比

    新增因子在这里 append 一行即可 —— 下游无感。Stage 2 加 LLM 因子也是同样的位置：
        from astock_quant.factors.llm_factor import LLMSentiment
        return [..., LLMSentiment(...)]
    """
    factors: list[BaseFactor] = [
        # ----- 量价（13） -----
        Momentum(5),
        Momentum(20),
        Momentum(60),
        PriceToMA(5),
        PriceToMA(20),
        ZScore(50),
        RSI(14),
        Volatility(20),
        ATRPct(14),
        VolumeRatio(20),
        AmountMean(20),
        MACDHist(),
        BollingerPosition(20),
        # ----- 财务（9）—— T1 重建估值因子 -----
        PE(),
        PB(),
        DividendYield(),
        ROE(),
        NetMargin(),
        GrossMargin(),
        EPS(),
        RevenueGrowthYoY(),
        NetProfitGrowthYoY(),
        # ----- 资金流（5） -----
        MainInflowRolling(5),
        MainInflowRolling(20),
        SuperInflowRolling(5),
        LargeInflowRolling(5),
        MainInflowToAmount(),
    ]
    # ----- LLM 情绪（Stage 2 P6，env var 开关）-----
    # 默认关闭：缺 API key 时 pipeline 不会因构造失败而中断。
    # 启用：export ENABLE_LLM_FACTOR=1 + 设好 provider 的 API key。
    if _llm_factor_enabled():
        factors.append(LLMNewsSentiment(lookback=1))
    return factors


# ===========================================================================
# 批量计算编排
# ===========================================================================

def compute_factor_frame(
    price_panel: pd.DataFrame,
    moneyflow_panel: pd.DataFrame | None = None,
    financials: dict | None = None,
    factors: Sequence[BaseFactor] | None = None,
    *,
    news_fetcher: Callable[[str, str, str], list] | None = None,
    drop_nan_threshold: float = 0.95,
    verbose: bool = False,
) -> FactorFrame:
    """跑一遍所有因子，拼成 FactorFrame.

    参数：
        price_panel:      行情 panel（必传，决定输出索引）
        moneyflow_panel:  资金流 panel（资金流因子用；None 则资金流因子全 NaN）
        financials:       {ticker: list[FinancialMetrics]}（财务因子用；None 则财务因子全 NaN）
        factors:          因子实例列表；None 用 default_factors()
        news_fetcher:     callable(ticker, start, end) -> list[NewsItem] —— LLM 因子按需拉新闻
                          的入口（典型传 `AStockSource().get_news`）。
                          量价 / 财务 / 资金流因子的 compute 用 `**kwargs` 吸收，**完全忽略此参**。
                          P7 wiring：之前 LLMNewsSentiment 始终全 NaN 的根因就是这个参没接通。
        drop_nan_threshold: 列 NaN 比例 >= 此阈值就 drop（默认 0.95）。
                          P22 fix：akshare 财务/资金流接口经常 100% 失败 →
                          特征全 NaN → LightGBM 早停在 1 棵树（退化）。drop 后
                          训练能用剩下的弱特征训出多棵树，避免模型坍缩。
                          设 1.0 关闭此行为（只 drop 完全 NaN 的列）；设 0.0 全 drop。
        verbose:          True 时每个因子打印一行耗时 + NaN 比例

    返回：
        FactorFrame —— .data 是 DataFrame[MultiIndex=(date, ticker), factor_name 列]
                       .factor_names 是因子名 list（已 drop 高 NaN 列后的子集）

    某个因子计算失败：打 warning 跳过，不阻断整体（其它因子继续算）。
    """
    if price_panel is None or price_panel.empty:
        logger.error("compute_factor_frame: price_panel 为空，返回空 FactorFrame")
        return FactorFrame(data=pd.DataFrame(index=pd.MultiIndex.from_tuples(
            [], names=["date", "ticker"]
        )), factor_names=[])

    factors = list(factors or default_factors())

    series_list: list[pd.Series] = []
    names: list[str] = []
    for fac in factors:
        t0 = time.time()
        try:
            s = fac.compute(
                price_panel,
                moneyflow=moneyflow_panel,
                financials=financials or {},
                news_fetcher=news_fetcher,
            )
            if s is None:
                logger.warning("factor %s 返回 None，跳过", fac.name)
                continue
            # 统一输出索引为 price_panel.index —— 因子可能返回子集索引（如资金流），
            # reindex 后行情有但因子没有的位置自然为 NaN
            if not s.index.equals(price_panel.index):
                s = s.reindex(price_panel.index)
            s.name = fac.name
            series_list.append(s)
            names.append(fac.name)
            if verbose:
                dt = time.time() - t0
                nan_pct = s.isna().mean()
                logger.info("  %s: %.2fs, NaN=%.1f%%", fac.name, dt, nan_pct * 100)
        except Exception as e:  # noqa: BLE001 —— 单个因子挂掉不应该拖垮整体
            logger.warning("factor %s 计算失败，跳过：%s", fac.name, e)

    if not series_list:
        logger.error("compute_factor_frame: 所有因子都失败了")
        return FactorFrame(
            data=pd.DataFrame(index=price_panel.index),
            factor_names=[],
        )

    df = pd.concat(series_list, axis=1)
    df.columns = names

    # P22 fix：drop 高 NaN 列。akshare 财务/资金流偶尔/经常全失败 → 整列 NaN →
    # LightGBM 早停在 1 棵树（degenerate model，所有票预测分一样）。drop 后
    # 剩下的弱特征能训出多棵树，模型不再坍缩。落盘的 feature_names 自动跟随，
    # 后续 predict 路径按 feature_names_ 取列 → 自动跳过被 drop 的列。
    nan_ratio = df.isna().mean()
    drop_cols = nan_ratio[nan_ratio >= drop_nan_threshold].index.tolist()
    if drop_cols:
        logger.warning(
            "compute_factor_frame: drop %d 个高 NaN 列（>= %.0f%% NaN）：%s",
            len(drop_cols),
            drop_nan_threshold * 100,
            ", ".join(f"{c}({nan_ratio[c] * 100:.0f}%%)" for c in drop_cols),
        )
        df = df.drop(columns=drop_cols)
        dropped_set = set(drop_cols)
        names = [n for n in names if n not in dropped_set]

    return FactorFrame(data=df, factor_names=names)


# ===========================================================================
# 单点查询（debug / notebook 用）
# ===========================================================================

def compute_single(
    factor: BaseFactor,
    price_panel: pd.DataFrame,
    moneyflow_panel: pd.DataFrame | None = None,
    financials: dict | None = None,
) -> pd.Series:
    """跑单个因子（debug 用 —— 看某个因子值的分布）."""
    return factor.compute(
        price_panel,
        moneyflow=moneyflow_panel,
        financials=financials or {},
    )


__all__ = ["default_factors", "compute_factor_frame", "compute_single"]
