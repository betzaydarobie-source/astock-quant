"""A股 数据适配器 —— 实现 DataSource Protocol.

研读 .p0-repos/TradingAgents-astock/tradingagents/dataflows/a_stock.py 后用自己的话
重写的精简版：只留量化管道要的端点，返回 contracts.py 的 Pydantic 对象。
**不 import 那个框架** —— 它只是「标准答案」，理解后誊写。

────────────────────────────────────────────────────────────────────────
数据源分工（实测确认 2026-05-15 可用）
────────────────────────────────────────────────────────────────────────
  get_prices      mootdx (TCP 7709)   —— 日线 OHLCV，单次最多 800 根，分页拼接
  get_financials  akshare 同花顺源     —— stock_financial_abstract_ths（EPS/ROE/营收/净利…）
                  + mootdx finance     —— 总股本/流通股本快照
                  + 腾讯财经            —— 实时 PE/PB（附在最新一期）
  get_moneyflow   akshare             —— stock_individual_fund_flow（个股主力/超大/大单）
  get_news        akshare             —— stock_news_em（个股新闻，东财源）
────────────────────────────────────────────────────────────────────────

设计纪律：
- 任何一个端点挂了，**返回空 list / 跳过该字段，不抛异常**（Protocol 约定）。
  量化管道宁可少几个因子，也不能因为一个数据源抖动整条挂掉。
- mootdx 走 TCP 直连通达信服务器，海外环境可能需代理（用户有 VPN，非阻塞项）。
"""

from __future__ import annotations

import logging
import urllib.request

import pandas as pd

from astock_quant.contracts import (
    FinancialMetrics,
    MoneyFlowRecord,
    NewsItem,
    PriceBar,
)

logger = logging.getLogger(__name__)

# mootdx bars 单次返回上限（实测：offset 再大也只给 800）
_MOOTDX_PAGE = 800
# 分页保护上限 —— 800 * 12 ≈ 38 年日线，足够覆盖任何 Stage 1 区间
_MAX_PAGES = 12


# ---------------------------------------------------------------------------
# ticker 归一化 + 市场前缀（参考 a_stock.py 的 _normalize_ticker / _get_prefix）
# ---------------------------------------------------------------------------

def normalize_ticker(symbol: str) -> str:
    """各种输入格式 → 纯 6 位代码.

    支持 '600519' / 'SH600519' / '600519.SH' / 'sh600519' → '600519'。
    """
    s = symbol.strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def market_prefix(code: str) -> str:
    """6 位代码 → 交易所前缀（sh / sz / bj），腾讯/新浪等 HTTP 端点要用。"""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


def _parse_cn_number(val) -> float | None:
    """同花顺财务字符串 → float.

    同花顺 `stock_financial_abstract_ths` 返回的值是中文数字串，需解析：
        '1.47亿' → 1.47e8      '6.28万' → 6.28e4
        '23.38%' → 23.38（保留百分数口径，与 fundamental.ROE 因子注释一致）
        False / '--' / '' / NaN → None（同花顺用 False 表示该期缺失）
    """
    if val is None or val is False:
        return None
    if isinstance(val, (int, float)):
        return None if pd.isna(val) else float(val)
    s = str(val).strip()
    if s in ("", "False", "--", "nan", "None"):
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1])
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# A股 数据适配器
# ---------------------------------------------------------------------------

class AStockSource:
    """A股 数据适配器 —— 实现 data/protocol.py 的 DataSource Protocol.

    无状态（除 mootdx 客户端单例懒加载），可直接 `AStockSource()` 实例化复用。
    """

    def __init__(self) -> None:
        self._mootdx_client = None  # 懒加载，避免 import 时就建 TCP 连接

    # -- mootdx 客户端（懒加载单例，TCP 连接复用） --
    def _mootdx(self):
        if self._mootdx_client is None:
            from mootdx.quotes import Quotes

            self._mootdx_client = Quotes.factory(market="std")
        return self._mootdx_client

    # ======================================================================
    # 1. get_prices —— 日线 OHLCV（mootdx，分页拼接）
    # ======================================================================
    def get_prices(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[PriceBar]:
        """拉 [start_date, end_date] 区间日线.

        mootdx 单次最多 800 根 → 用 `start` 参数往回翻页，直到覆盖 start_date
        或翻到没有更多数据。然后按区间过滤、转 PriceBar list（升序）。
        """
        code = normalize_ticker(ticker)
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)

        try:
            client = self._mootdx()
            frames: list[pd.DataFrame] = []
            offset_start = 0
            for _ in range(_MAX_PAGES):
                # category=4 是日线；start 往回偏移，offset 是本页根数
                df = client.bars(
                    symbol=code, category=4, offset=_MOOTDX_PAGE, start=offset_start
                )
                if df is None or df.empty:
                    break
                frames.append(df)
                if pd.to_datetime(df.index.min()) <= start_ts:
                    break  # 已经翻到比 start_date 更早，够了
                offset_start += _MOOTDX_PAGE

            if not frames:
                logger.warning("mootdx 无 K 线数据: %s", code)
                return []

            raw = pd.concat(frames)
            # mootdx 的 index 名是 'datetime'，同时还有一个同名 'datetime' 列 +
            # year/month/day/hour/minute 拆解列 —— reset_index 前先清掉重名列
            raw = raw.drop(
                columns=["datetime", "year", "month", "day", "hour", "minute"],
                errors="ignore",
            )
            raw = raw[~raw.index.duplicated(keep="first")].sort_index()
            raw = raw.reset_index()  # index 'datetime' → 列 'datetime'
            raw["datetime"] = pd.to_datetime(raw["datetime"]).dt.normalize()

            # 按请求区间过滤
            mask = (raw["datetime"] >= start_ts) & (raw["datetime"] <= end_ts)
            raw = raw[mask].sort_values("datetime")

            bars: list[PriceBar] = []
            for _, row in raw.iterrows():
                # mootdx: vol == volume（实测一致，已是统一口径）；amount 单位元
                bars.append(
                    PriceBar(
                        ticker=code,
                        date=row["datetime"].date(),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        amount=float(row["amount"]) if pd.notna(row.get("amount")) else None,
                    )
                )
            return bars

        except Exception as e:  # noqa: BLE001 —— Protocol 约定：失败返回 []
            logger.warning("get_prices 失败 %s: %s", code, e)
            return []

    # ======================================================================
    # 2. get_financials —— 财务指标（akshare 主 + mootdx 股本 + 腾讯 PE/PB）
    # ======================================================================
    def get_financials(
        self, ticker: str, end_date: str
    ) -> list[FinancialMetrics]:
        """拉截至 end_date 的财务指标，按报告期升序.

        防未来函数：只保留报告期 <= end_date 的列。
        akshare stock_financial_abstract 是宽表（指标 × 报告期），转置成「每期一条」。
        """
        code = normalize_ticker(ticker)
        cutoff = pd.to_datetime(end_date)

        # -- akshare 同花顺源：财务摘要（按报告期）--
        # P24：从 stock_financial_abstract（东财源，反爬已坏 —— 持续返回空 JSON）
        #      换成 stock_financial_abstract_ths（同花顺源，实测 HS300 10/10 稳定）。
        #      同花顺返回长表：每行一个报告期，值是中文数字串（'1.47亿' / '23.38%'）。
        period_metrics: dict[str, dict] = {}
        try:
            import akshare as ak

            fa = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
            if fa is not None and not fa.empty and "报告期" in fa.columns:
                # 同花顺中文列名 → FinancialMetrics 字段名
                col_map = {
                    "基本每股收益": "eps",
                    "净资产收益率": "roe",
                    "营业总收入": "revenue",
                    "净利润": "net_profit",
                    "每股净资产": "bvps",
                }
                for _, row in fa.iterrows():
                    # 报告期形如 '2025-12-31' → 归一成 '20251231'（FinancialMetrics
                    # 契约 + fundamental 因子的 align 都按 8 位数字 %Y%m%d 解析）
                    period = str(row["报告期"]).strip().replace("-", "")
                    if len(period) != 8 or not period.isdigit():
                        continue
                    # 防未来函数：报告期晚于 end_date 的丢弃
                    if pd.to_datetime(period, format="%Y%m%d") > cutoff:
                        continue
                    rec: dict = {}
                    for cn_col, field in col_map.items():
                        if cn_col in fa.columns:
                            parsed = _parse_cn_number(row[cn_col])
                            if parsed is not None:
                                rec[field] = parsed
                    if rec:
                        period_metrics[period] = rec
        except Exception as e:  # noqa: BLE001
            logger.warning("同花顺财务摘要失败 %s: %s", code, e)

        # -- mootdx: 股本快照（总股本/流通股本，无报告期维度，附到最新一期） --
        total_share = float_share = None
        try:
            fin = self._mootdx().finance(symbol=code)
            if fin is not None and not (isinstance(fin, pd.DataFrame) and fin.empty):
                frow = fin.iloc[0] if isinstance(fin, pd.DataFrame) else fin
                if "zongguben" in frow.index:
                    total_share = float(frow["zongguben"])
                if "liutongguben" in frow.index:
                    float_share = float(frow["liutongguben"])
        except Exception as e:  # noqa: BLE001
            logger.warning("mootdx 股本快照失败 %s: %s", code, e)

        # -- 腾讯: 实时 PE/PB（无报告期维度，附到最新一期） --
        pe = pb = None
        try:
            tq = _tencent_quote([code])
            if code in tq:
                pe = tq[code].get("pe_ttm") or None
                pb = tq[code].get("pb") or None
        except Exception as e:  # noqa: BLE001
            logger.warning("腾讯行情失败 %s: %s", code, e)

        if not period_metrics:
            # akshare 没数据时，至少用 mootdx 股本 + 腾讯估值兜一条「当前」记录
            if total_share or float_share or pe or pb:
                return [
                    FinancialMetrics(
                        ticker=code,
                        report_period=end_date.replace("-", ""),
                        pe=pe, pb=pb,
                        total_share=total_share, float_share=float_share,
                    )
                ]
            return []

        # 组装：每个报告期一条 FinancialMetrics，升序
        periods_sorted = sorted(period_metrics.keys())
        out: list[FinancialMetrics] = []
        for i, period in enumerate(periods_sorted):
            rec = period_metrics[period]
            is_latest = i == len(periods_sorted) - 1
            out.append(
                FinancialMetrics(
                    ticker=code,
                    report_period=period,
                    eps=rec.get("eps"),
                    roe=rec.get("roe"),
                    revenue=rec.get("revenue"),
                    net_profit=rec.get("net_profit"),
                    net_assets=rec.get("net_assets"),
                    bvps=rec.get("bvps"),
                    # 股本/估值是「当前」快照，只挂到最新一期，避免误用历史 PE
                    total_share=total_share if is_latest else None,
                    float_share=float_share if is_latest else None,
                    pe=pe if is_latest else None,
                    pb=pb if is_latest else None,
                )
            )
        return out

    # ======================================================================
    # 3. get_moneyflow —— 个股资金流（akshare）
    # ======================================================================
    def get_moneyflow(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[MoneyFlowRecord]:
        """拉 [start_date, end_date] 区间个股资金流.

        用 akshare stock_individual_fund_flow（东财源，主力/超大/大单净流入）。
        注意 P0 报告提醒：这类端点历史较短，返回可能远少于请求区间 —— 正常现象。
        """
        code = normalize_ticker(ticker)
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)

        try:
            import akshare as ak

            # 东财个股资金流要带 market 参数（sh/sz/bj）
            df = ak.stock_individual_fund_flow(
                stock=code, market=market_prefix(code)
            )
            if df is None or df.empty:
                return []

            # 列名（东财中文）：日期 / 主力净流入-净额 / 超大单净流入-净额 / 大单净流入-净额 ...
            col_date = "日期"
            col_main = "主力净流入-净额"
            col_super = "超大单净流入-净额"
            col_large = "大单净流入-净额"
            if col_date not in df.columns:
                return []

            df[col_date] = pd.to_datetime(df[col_date])
            mask = (df[col_date] >= start_ts) & (df[col_date] <= end_ts)
            df = df[mask].sort_values(col_date)

            records: list[MoneyFlowRecord] = []
            for _, row in df.iterrows():
                records.append(
                    MoneyFlowRecord(
                        ticker=code,
                        date=row[col_date].date(),
                        main_inflow=_safe_float(row.get(col_main)),
                        super_inflow=_safe_float(row.get(col_super)),
                        large_inflow=_safe_float(row.get(col_large)),
                        # 北向个股级 / 龙虎榜 Stage 1 不接，留 None
                        northbound=None,
                        dragon_tiger_net=None,
                    )
                )
            return records

        except Exception as e:  # noqa: BLE001
            logger.warning("get_moneyflow 失败 %s: %s", code, e)
            return []

    # ======================================================================
    # 4. get_news —— 个股新闻（akshare，Stage 2 LLM 因子入口）
    # ======================================================================
    def get_news(
        self, ticker: str, start_date: str, end_date: str
    ) -> list[NewsItem]:
        """拉个股新闻 —— Stage 1 就实现，Stage 2 LLM 因子消费.

        akshare stock_news_em 返回最近一批个股新闻（东财源），按日期区间过滤。
        端点不支持自定义区间，只能拉「最近的」再筛 —— 这是 akshare 的限制。
        """
        code = normalize_ticker(ticker)
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)

        try:
            import akshare as ak

            df = ak.stock_news_em(symbol=code)
            if df is None or df.empty:
                return []

            items: list[NewsItem] = []
            for _, row in df.iterrows():
                pub = row.get("发布时间", "")
                try:
                    pub_ts = pd.to_datetime(pub)
                except (ValueError, TypeError):
                    continue
                if pub_ts < start_ts or pub_ts > end_ts:
                    continue
                items.append(
                    NewsItem(
                        ticker=code,
                        date=pub_ts.date(),
                        title=str(row.get("新闻标题", "")),
                        content=str(row.get("新闻内容", "") or ""),
                        source=str(row.get("文章来源", "") or ""),
                        url=str(row.get("新闻链接", "")) or None,
                    )
                )
            return items

        except Exception as e:  # noqa: BLE001
            logger.warning("get_news 失败 %s: %s", code, e)
            return []


# ---------------------------------------------------------------------------
# 模块级辅助
# ---------------------------------------------------------------------------

def _safe_float(val) -> float | None:
    """宽松转 float：None / NaN / 空串 / 转换失败都返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if pd.isna(f):
        return None
    return f


def _tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯财经实时行情（PE/PB/市值/换手率）—— 参考 a_stock.py 的 _tencent_quote.

    HTTP GET，GBK 编码，`~` 分隔字段。字段索引按 SKILL.md 实测校准表：
    39=PE(TTM), 44=总市值(亿), 46=PB, 38=换手率%。返回 {code: {...}}。
    """
    if not codes:
        return {}
    prefixed = [f"{market_prefix(c)}{c}" for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    raw = resp.read().decode("gbk")

    result: dict[str, dict] = {}
    for line in raw.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]  # 去掉 sh/sz/bj 前缀
        result[code] = {
            "name": vals[1],
            "price": _safe_float(vals[3]) or 0.0,
            "turnover_pct": _safe_float(vals[38]) or 0.0,
            "pe_ttm": _safe_float(vals[39]) or 0.0,
            "mcap_yi": _safe_float(vals[44]) or 0.0,
            "pb": _safe_float(vals[46]) or 0.0,
        }
    return result


__all__ = ["AStockSource", "normalize_ticker", "market_prefix"]
