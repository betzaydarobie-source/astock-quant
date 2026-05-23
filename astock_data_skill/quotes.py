"""Layer 1 行情层 —— mootdx + 腾讯财经 + 百度股市通.

3 个端点:
- mootdx_klines: K线 (TCP, mootdx 库)
- mootdx_quotes: 五档实时报价 (TCP, mootdx 库)
- mootdx_transactions: 逐笔成交 (TCP, mootdx 库)
- tencent_quote: 批量实时行情 + PE/PB/市值/换手 (HTTP GBK)
- baidu_kline_with_ma: K线自带 MA5/10/20 (HTTP JSON)

代码按 SKILL.md L173-341 抄, 失败一律返回空容器.
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Any

import requests

from astock_data_skill._common import DEFAULT_TIMEOUT, normalize_ticker

logger = logging.getLogger(__name__)


# ===========================================================================
# 1.1 mootdx — K线 + 五档盘口 + 逐笔成交
# ===========================================================================
#
# mootdx 走 TCP 通达信协议, 不封 IP. 调用前需 pip install mootdx>=0.10.
# market: 0=深圳 1=上海(SKILL.md 标注)
# category: 4=日线 5=周线 6=月线 7-11=分钟线


def _mootdx_client():
    """延迟 import mootdx, 失败抛 ImportError —— 测试用 mock 可跳过."""
    from mootdx.quotes import Quotes
    return Quotes.factory(market="std")


def mootdx_klines(
    ticker: str,
    category: int = 4,
    offset: int = 100,
) -> list[dict[str, Any]]:
    """K线数据.

    category: 4=日线/5=周线/6=月线/7=1分钟/8=5分钟/9=15分钟/10=30分钟/11=60分钟.
    返回每根 K 线: open / close / high / low / vol / amount / datetime.
    """
    code = normalize_ticker(ticker)
    try:
        client = _mootdx_client()
        df = client.bars(symbol=code, category=category, offset=offset)
        if df is None or len(df) == 0:
            return []
        return df.to_dict(orient="records")
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx_klines(%s) failed: %s", code, e)
        return []


def mootdx_quotes(tickers: list[str]) -> list[dict[str, Any]]:
    """五档实时报价 (批量).

    返回 46 字段: price/open/high/low/last_close/bid1~5/ask1~5/vol/amount/servertime.
    """
    codes = [normalize_ticker(t) for t in tickers]
    try:
        client = _mootdx_client()
        df = client.quotes(symbol=codes)
        if df is None or len(df) == 0:
            return []
        return df.to_dict(orient="records")
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx_quotes failed: %s", e)
        return []


def mootdx_transactions(ticker: str, date: str) -> list[dict[str, Any]]:
    """逐笔成交. date: YYYYMMDD. 非交易时间返回空.

    返回字段: time / price / vol / num / buyorsell (0买 / 1卖 / 2中性).
    """
    code = normalize_ticker(ticker)
    try:
        client = _mootdx_client()
        df = client.transaction(symbol=code, date=date)
        if df is None or len(df) == 0:
            return []
        return df.to_dict(orient="records")
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx_transactions(%s,%s) failed: %s", code, date, e)
        return []


# ===========================================================================
# 1.2 腾讯财经 —— 实时行情 + PE/PB/市值/换手率/涨跌停 (HTTP GBK)
# ===========================================================================
#
# 字段索引按 SKILL.md L278-302 实测校准 (2026-05-03):
#   index 39 = PE(TTM), 43 = 振幅%(不是PB!), 44 = 总市值(亿), 46 = PB, 52 = PE(静)


def tencent_quote(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯财经批量实时行情.

    支持: 个股 / 指数 (000001 上证, 000300 沪深300, 399006 创业板指) / ETF.
    返回: {code: {name, price, pe_ttm, pb, mcap_yi, ...}}
    """
    if not tickers:
        return {}
    prefixed: list[str] = []
    for t in tickers:
        c = normalize_ticker(t)
        if c.startswith(("6", "9")):
            prefixed.append(f"sh{c}")
        elif c.startswith("8"):
            prefixed.append(f"bj{c}")
        else:
            prefixed.append(f"sz{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("gbk")
    except Exception as e:  # noqa: BLE001
        logger.warning("tencent_quote failed: %s", e)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        try:
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            code = key[2:]
            result[code] = {
                "name":          vals[1],
                "price":         _safe_float(vals[3]),
                "last_close":    _safe_float(vals[4]),
                "open":          _safe_float(vals[5]),
                "change_amt":    _safe_float(vals[31]),
                "change_pct":    _safe_float(vals[32]),
                "high":          _safe_float(vals[33]),
                "low":           _safe_float(vals[34]),
                "amount_wan":    _safe_float(vals[37]),
                "turnover_pct":  _safe_float(vals[38]),
                "pe_ttm":        _safe_float(vals[39]),
                "amplitude_pct": _safe_float(vals[43]),
                "mcap_yi":       _safe_float(vals[44]),
                "float_mcap_yi": _safe_float(vals[45]),
                "pb":            _safe_float(vals[46]),
                "limit_up":      _safe_float(vals[47]),
                "limit_down":    _safe_float(vals[48]),
                "vol_ratio":     _safe_float(vals[49]),
                "pe_static":     _safe_float(vals[52]),
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("tencent_quote parse line skipped: %s", e)
            continue
    return result


def _safe_float(s: str) -> float:
    """空字符串/非数字 → 0.0."""
    try:
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


# ===========================================================================
# 1.3 百度股市通 K线 (带 MA5/MA10/MA20)
# ===========================================================================


def baidu_kline_with_ma(ticker: str, start_time: str = "") -> dict[str, Any]:
    """百度股市通 K线 — 独有能力: 返回时自带 ma5/ma10/ma20 均价.

    返回 {keys: [...], rows: [...]}.
    keys 包含: time, open, close, high, low, volume, amount,
              ma5avgprice, ma10avgprice, ma20avgprice.
    """
    code = normalize_ticker(ticker)
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
        "isFutures": "false", "isStock": "true", "newFormat": "1",
        "group": "quotation_kline_ab", "finClientType": "pc",
        "code": code, "start_time": start_time, "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("baidu_kline_with_ma(%s) failed: %s", code, e)
        return {"keys": [], "rows": []}

    result = d.get("Result", {})
    md = result.get("newMarketData", {})
    keys = md.get("keys", [])
    rows = md.get("marketData", "").split(";") if md.get("marketData") else []
    return {"keys": keys, "rows": rows}


__all__ = [
    "mootdx_klines",
    "mootdx_quotes",
    "mootdx_transactions",
    "tencent_quote",
    "baidu_kline_with_ma",
]
