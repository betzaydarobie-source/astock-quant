"""Layer 4 资金面 / 筹码层 —— 融资融券 / 大宗交易 / 股东户数 / 分红 / 资金流 120 日.

5 个端点 (SKILL.md L1152-1341):
- margin_trading: 融资融券明细 (日级)
- block_trade: 大宗交易记录
- holder_num_change: 股东户数变化 (季度级)
- dividend_history: 分红送转历史
- stock_fund_flow_120d: 个股资金流 (最近 120 个交易日, 日级)
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from astock_data_skill._common import (
    DEFAULT_TIMEOUT,
    UA,
    eastmoney_datacenter,
    get_secid,
    normalize_ticker,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 4.1 融资融券明细
# ===========================================================================


def margin_trading(ticker: str, page_size: int = 30) -> list[dict[str, Any]]:
    """融资融券明细 (日级).

    Returns:
        list[dict]: date / rzye(融资余额, 元) / rzmre(融资买入) / rzche(融资偿还)
                    / rqye(融券余额, 元) / rqmcl / rqchl / rzrqye.
    """
    code = normalize_ticker(ticker)
    data = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns="DATE",
        sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("DATE", ""))[:10],
            "rzye": row.get("RZYE", 0),
            "rzmre": row.get("RZMRE", 0),
            "rzche": row.get("RZCHE", 0),
            "rqye": row.get("RQYE", 0),
            "rqmcl": row.get("RQMCL", 0),
            "rqchl": row.get("RQCHL", 0),
            "rzrqye": row.get("RZRQYE", 0),
        })
    return rows


# ===========================================================================
# 4.2 大宗交易
# ===========================================================================


def block_trade(ticker: str, page_size: int = 20) -> list[dict[str, Any]]:
    """大宗交易记录.

    Returns:
        list[dict]: date / price / close / premium_pct / vol / amount / buyer / seller.
    """
    code = normalize_ticker(ticker)
    data = eastmoney_datacenter(
        "RPT_DATA_BLOCKTRADE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    rows = []
    for row in data:
        close = row.get("CLOSE_PRICE") or 0
        deal_price = row.get("DEAL_PRICE") or 0
        try:
            premium = ((deal_price / close - 1) * 100) if close else 0
        except (TypeError, ZeroDivisionError):
            premium = 0
        rows.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "price": deal_price,
            "close": close,
            "premium_pct": round(premium, 2),
            "vol": row.get("DEAL_VOLUME", 0),
            "amount": row.get("DEAL_AMT", 0),
            "buyer": row.get("BUYER_NAME", ""),
            "seller": row.get("SELLER_NAME", ""),
        })
    return rows


# ===========================================================================
# 4.3 股东户数变化
# ===========================================================================


def holder_num_change(ticker: str, page_size: int = 10) -> list[dict[str, Any]]:
    """股东户数变化 (季度级).

    解读: 户数持续减少 = 筹码集中 = 主力吸筹信号.

    Returns:
        list[dict]: date / holder_num / change_num / change_ratio(环比%) / avg_shares.
    """
    code = normalize_ticker(ticker)
    data = eastmoney_datacenter(
        "RPT_HOLDERNUMLATEST",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="END_DATE",
        sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("END_DATE", ""))[:10],
            "holder_num": row.get("HOLDER_NUM", 0),
            "change_num": row.get("HOLDER_NUM_CHANGE", 0),
            "change_ratio": row.get("HOLDER_NUM_RATIO", 0),
            "avg_shares": row.get("AVG_FREE_SHARES", 0),
        })
    return rows


# ===========================================================================
# 4.4 分红送转历史
# ===========================================================================


def dividend_history(ticker: str, page_size: int = 20) -> list[dict[str, Any]]:
    """分红送转历史.

    Returns:
        list[dict]: date / bonus_rmb(每股派息税前) / transfer_ratio(每10股转增)
                    / bonus_ratio(每10股送股) / plan(进度).
    """
    code = normalize_ticker(ticker)
    data = eastmoney_datacenter(
        "RPT_SHAREBONUS_DET",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="EX_DIVIDEND_DATE",
        sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
            "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
            "transfer_ratio": row.get("TRANSFER_RATIO", 0),
            "bonus_ratio": row.get("BONUS_RATIO", 0),
            "plan": row.get("ASSIGN_PROGRESS", ""),
        })
    return rows


# ===========================================================================
# 4.5 个股资金流 (120 日, 日级)
# ===========================================================================


def stock_fund_flow_120d(ticker: str) -> list[dict[str, Any]]:
    """个股资金流 (日级, 最近 120 个交易日).

    Returns:
        list[dict]: date / main_net / small_net / mid_net / large_net / super_net.
        单位: 元.
    """
    code = normalize_ticker(ticker)
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": get_secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("stock_fund_flow_120d(%s) failed: %s", code, e)
        return []

    klines = d.get("data", {}).get("klines", []) or []
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "main_net":  _float_or_zero(parts[1]),
                "small_net": _float_or_zero(parts[2]),
                "mid_net":   _float_or_zero(parts[3]),
                "large_net": _float_or_zero(parts[4]),
                "super_net": _float_or_zero(parts[5]),
            })
    return rows


def _float_or_zero(s: str) -> float:
    if s in (None, "", "-"):
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "margin_trading",
    "block_trade",
    "holder_num_change",
    "dividend_history",
    "stock_fund_flow_120d",
]
