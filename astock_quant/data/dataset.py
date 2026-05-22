"""数据集构建 —— 多股票 × 长时间的 panel 数据.

P2 的主要工作量。P0 报告明确指出 a-stock-data / a_stock.py 的设计重心是「查单只
股票当前状态」—— 没有现成的「批量 universe + 增量更新」能力。这一层把单股票查询
扩展成量化建模要的 panel：

    单股票查询（astock_source）
         │  遍历 UNIVERSE
         ▼
    逐只拉取 + CSV 落地（cache）
         │  拼接
         ▼
    panel DataFrame，index=(date, ticker)   ← factors 层 + labels 层的共同输入

────────────────────────────────────────────────────────────────────────
缓存 + 增量更新策略
────────────────────────────────────────────────────────────────────────
- 每只票的行情/资金流单独存一份「全量历史」CSV（cache.write_cache）。
- 构建 panel 时：
    · 缓存新鲜（今天更新过）→ 直接读 CSV，不打网络
    · 缓存过期 / 不存在     → 走 astock_source 重新拉，落地，再读
  `force_refresh=True` 可强制全部重拉（换 universe、怀疑数据有问题时用）。
- 「增量」体现在「按天有效」：A股 日线一天一更，当天内重复构建 panel 复用缓存；
  跨天后自动重拉当天的新数据。这对 Stage 1 的训练/回测节奏够用 ——
  不做「只追加尾部 N 天」那种精细增量（实测重拉 30 只票 ~30s，不值当复杂化）。

────────────────────────────────────────────────────────────────────────
look-ahead 防护
────────────────────────────────────────────────────────────────────────
- 缓存里存的是「全量历史」，本身不截断。
- `build_price_panel(..., curr_date=X)` 给定 curr_date 时，每只票读缓存都过
  cache.truncate_by_date —— panel 里不会出现 X 之后的行。
- curr_date=None 时返回全量 panel（给「一次性切训练/验证集」这种离线用途；
  逐日回测必须传 curr_date）。
"""

from __future__ import annotations

import logging

import pandas as pd

from astock_quant.config.settings import SETTINGS, get_universe
from astock_quant.data import cache
from astock_quant.data.astock_source import AStockSource, normalize_ticker
from astock_quant.data.protocol import DataSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 单只票：拉取 + 落地（带缓存判断）
# ---------------------------------------------------------------------------

def _bars_to_df(bars: list) -> pd.DataFrame:
    """PriceBar list → DataFrame（行情缓存的存储格式）。"""
    if not bars:
        return pd.DataFrame(
            columns=["ticker", "date", "open", "high", "low", "close", "volume", "amount"]
        )
    return pd.DataFrame([b.model_dump() for b in bars])


def _moneyflow_to_df(records: list) -> pd.DataFrame:
    """MoneyFlowRecord list → DataFrame（资金流缓存的存储格式）。"""
    if not records:
        return pd.DataFrame(
            columns=[
                "ticker", "date", "main_inflow", "super_inflow",
                "large_inflow", "northbound", "dragon_tiger_net",
            ]
        )
    return pd.DataFrame([r.model_dump() for r in records])


def load_prices(
    ticker: str,
    source: DataSource,
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """拿到单只票的「全量历史」行情 DataFrame，带缓存.

    缓存新鲜且非强制刷新 → 读 CSV；否则走 source 重拉并落地。
    返回的是全量历史（未按 curr_date 截断）—— 截断由 build_*_panel 统一做。
    """
    code = normalize_ticker(ticker)
    path = cache.cache_path("prices", code)

    if not force_refresh and cache.is_fresh(path):
        df = cache.read_cache("prices", code)  # 不传 curr_date = 全量
        if df is not None and not df.empty:
            return df

    # 重拉 + 落地
    bars = source.get_prices(code, start_date, end_date)
    df = _bars_to_df(bars)
    if not df.empty:
        cache.write_cache(df, "prices", code)
        df["date"] = pd.to_datetime(df["date"])
    return df


def load_moneyflow(
    ticker: str,
    source: DataSource,
    start_date: str,
    end_date: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """拿到单只票的资金流 DataFrame，带缓存（逻辑同 load_prices）.

    注意：资金流端点历史短，返回的区间可能远小于 [start_date, end_date]。
    """
    code = normalize_ticker(ticker)
    path = cache.cache_path("moneyflow", code)

    if not force_refresh and cache.is_fresh(path):
        df = cache.read_cache("moneyflow", code)
        if df is not None and not df.empty:
            return df

    records = source.get_moneyflow(code, start_date, end_date)
    df = _moneyflow_to_df(records)
    if not df.empty:
        cache.write_cache(df, "moneyflow", code)
        df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------------
# panel 构建 —— universe 循环 + 拼接
# ---------------------------------------------------------------------------

def build_price_panel(
    universe: list[str] | None = None,
    source: DataSource | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    curr_date: str | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """构建多股票行情 panel —— factors / labels 层的核心输入.

    参数（全部可选，缺省走 config.SETTINGS）：
        universe:      股票池，默认 SETTINGS.universe。
        source:        数据源，默认 AStockSource()。
        start_date:    历史起始，默认 SETTINGS.history_start。
        end_date:      历史结束，默认 SETTINGS.history_end。
        curr_date:     look-ahead 截断时点。给定则每只票都截断到 <= curr_date；
                       None 返回全量 panel（仅离线切分用，逐日回测必须传）。
        force_refresh: True 则忽略缓存全部重拉。

    返回：
        panel DataFrame，MultiIndex=(date, ticker)，列=[open, high, low, close,
        volume, amount]，按 (date, ticker) 升序。某只票无数据则跳过（不报错）。
    """
    universe = universe or SETTINGS.universe
    source = source or AStockSource()
    start_date = start_date or SETTINGS.history_start
    end_date = end_date or SETTINGS.history_end

    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for ticker in universe:
        code = normalize_ticker(ticker)
        df = load_prices(code, source, start_date, end_date, force_refresh)
        if df is None or df.empty:
            skipped.append(code)
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        # look-ahead 截断 —— 第一道防线
        if curr_date is not None:
            df = cache.truncate_by_date(df, curr_date, date_col="date")
        if df.empty:
            skipped.append(code)
            continue
        frames.append(df)

    if skipped:
        logger.warning("build_price_panel: %d 只票无数据，已跳过: %s",
                       len(skipped), skipped)
    if not frames:
        logger.error("build_price_panel: universe 全部无数据，返回空 panel")
        return pd.DataFrame()

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    return panel


def build_moneyflow_panel(
    universe: list[str] | None = None,
    source: DataSource | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    curr_date: str | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """构建多股票资金流 panel —— moneyflow 因子的输入.

    结构同 build_price_panel：MultiIndex=(date, ticker)。
    注意资金流历史短，panel 的日期覆盖会明显短于行情 panel —— moneyflow
    因子需对此鲁棒（与行情 panel 对齐时大量 NaN 是正常的）。
    """
    universe = universe or SETTINGS.universe
    source = source or AStockSource()
    start_date = start_date or SETTINGS.history_start
    end_date = end_date or SETTINGS.history_end

    frames: list[pd.DataFrame] = []
    for ticker in universe:
        code = normalize_ticker(ticker)
        df = load_moneyflow(code, source, start_date, end_date, force_refresh)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        if curr_date is not None:
            df = cache.truncate_by_date(df, curr_date, date_col="date")
        if df.empty:
            continue
        frames.append(df)

    if not frames:
        logger.warning("build_moneyflow_panel: 无任何资金流数据，返回空 panel")
        return pd.DataFrame()

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    return panel


# ---------------------------------------------------------------------------
# 财务数据 —— 不做 panel（按 ticker 取，因子层自己对齐到交易日）
# ---------------------------------------------------------------------------

def load_financials(
    universe: list[str] | None = None,
    source: DataSource | None = None,
    curr_date: str | None = None,
    force_refresh: bool = False,
) -> dict[str, list]:
    """拉 universe 的财务指标 —— 返回 {ticker: list[FinancialMetrics]}.

    财务数据按报告期发布（季度粒度），不像行情是规整的日频，做成 panel 反而别扭。
    这里返回「每只票一串报告期记录」，由 fundamental 因子层负责按交易日对齐
    （某交易日用「最近一期已披露财报」）。

    ★ T1 重建 ★：数据源从 astock_source.get_financials（只给最新一期估值快照、
    无缓存、PE/PB 历史 95% NaN）换成 `data/fundamentals.load_financial_history`
    —— 后者从同花顺财报 + 东财分红重建全历史，每只票一份 CSV 缓存，回测可复现，
    且每条记录带 `publish_date`（保守可见日）供因子层防 look-ahead 对齐。

    防未来函数：
      - 每条 FinancialMetrics 带 `publish_date`（财报实际/保守可见日）。
      - curr_date 给定时，过滤掉 `publish_date > curr_date` 的记录 —— 即「站在
        curr_date 这个时点还没披露」的财报一律不返回。curr_date=None 返回全历史
        （由因子层 align_by_publish_date 逐日按 publish_date 对齐，同样安全）。

    参数 source 保留仅为向后兼容签名 —— T1 起本函数不再用它（财报走 fundamentals
    模块）。force_refresh=True 时强制重拉财报网络（季度有新财报后用）。
    """
    from astock_quant.data import fundamentals as _fundamentals

    universe = universe or SETTINGS.universe

    result: dict[str, list] = {}
    for ticker in universe:
        code = normalize_ticker(ticker)
        records = _fundamentals.load_financial_history(code, force_refresh=force_refresh)
        if curr_date is not None:
            cutoff = pd.to_datetime(curr_date)
            records = [
                r for r in records
                if r.publish_date is not None
                and pd.to_datetime(r.publish_date, format="%Y%m%d") <= cutoff
            ]
        result[code] = records
    return result


# ---------------------------------------------------------------------------
# 便捷入口 —— 一次性把 Stage 1 要的数据都准备好
# ---------------------------------------------------------------------------

def prepare_stage1_data(
    universe: list[str] | None = None,
    force_refresh: bool = False,
    stage: str = "stage1",
) -> dict:
    """一键准备数据集 —— 行情 panel + 资金流 panel + 财务字典.

    参数：
        universe:       股票池。None 时按 stage 参数决定（向后兼容）；
                        传入则直接用此池子，忽略 stage。
        force_refresh:  忽略缓存全部重拉。
        stage:          "stage1" → 30 只蓝筹（默认，向后兼容）；
                        "stage4" → 沪深 300 全量（lazy 拉取）。
                        universe 显式传入时本参数无效。

    其余配置（起止日期）走 SETTINGS。curr_date=None（全量，给离线训练）。

    返回 dict:
        {
            "prices":     行情 panel  DataFrame,
            "moneyflow":  资金流 panel DataFrame,
            "financials": {ticker: list[FinancialMetrics]},
            "source":     AStockSource 实例 —— pipeline 拿它的 get_news 喂给 LLM 因子
                          （P7 wiring：让 compute_factor_frame 能按需拉新闻给 LLMNewsSentiment）。
        }
    """
    source = AStockSource()
    effective_universe = universe if universe is not None else get_universe(stage)
    logger.info("准备数据集：stage=%s，universe=%d 只，区间 %s ~ %s",
                stage, len(effective_universe), SETTINGS.history_start, SETTINGS.history_end)

    prices = build_price_panel(universe=effective_universe, source=source, force_refresh=force_refresh)
    moneyflow = build_moneyflow_panel(universe=effective_universe, source=source, force_refresh=force_refresh)
    financials = load_financials(universe=effective_universe, source=source)

    return {
        "prices": prices,
        "moneyflow": moneyflow,
        "financials": financials,
        "source": source,
    }
