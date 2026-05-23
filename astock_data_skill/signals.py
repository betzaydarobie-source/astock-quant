"""Layer 3 信号层 —— 同花顺热点 / 北向 / 百度概念 / 资金流分钟 / 龙虎榜 / 解禁 / 行业.

8 个端点 (SKILL.md L566-1116):
- ths_hot_reason: 当日强势股 + 题材归因 reason tags
- hsgt_realtime / load_northbound_history / save_northbound_snapshot: 北向资金
- baidu_concept_blocks: 个股概念板块归属
- eastmoney_fund_flow_minute: 个股资金流向 (分钟级)
- dragon_tiger_board: 个股龙虎榜 (上榜 + 席位 + 机构动向)
- daily_dragon_tiger: 全市场龙虎榜
- lockup_expiry: 限售解禁日历
- industry_comparison: 行业板块排名

代码按 SKILL.md V3.1 抄, 失败一律降级.
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta
from pathlib import Path
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
# 3.1 同花顺热点 — 当日强势股 + 题材归因 reason tags
# ===========================================================================


def ths_hot_reason(date: str | None = None) -> "Any":
    """同花顺当日强势股归因.

    Args:
        date: 'YYYY-MM-DD'. None=今天.

    Returns:
        pandas.DataFrame, 列: 代码 / 名称 / 题材归因 / 涨幅% / 换手率% / ...
        失败返回空 DataFrame.
    """
    import pandas as pd

    if date is None:
        date = _date.today().strftime("%Y-%m-%d")

    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{date}/orderby/date/orderway/desc/charset/GBK/"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Chrome/117.0.0.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("ths_hot_reason(%s) failed: %s", date, e)
        return pd.DataFrame()

    if data.get("errocode", 0) != 0:
        logger.warning("ths_hot_reason errocode: %s", data.get("errormsg", ""))
        return pd.DataFrame()

    rows = data.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    rename_map = {
        "name": "名称", "code": "代码", "reason": "题材归因",
        "close": "收盘价", "zhangdie": "涨跌额", "zhangfu": "涨幅%",
        "huanshou": "换手率%", "chengjiaoe": "成交额",
        "chengjiaoliang": "成交量", "ddejingliang": "大单净量",
        "market": "市场",
    }
    return df.rename(columns=rename_map)


# ===========================================================================
# 3.2 同花顺北向资金 (hsgtApi 实时分钟流向 + 本地自缓存)
# ===========================================================================
#
# 行业性问题: eastmoney 北向数据自 2024-08 后净买额字段 NaN/0. 本地 CSV 自缓存.

HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}


def hsgt_realtime() -> "Any":
    """沪深股通当日实时分钟流向 (含集合竞价 09:10-15:00, 262 个时间点).

    返回 pandas.DataFrame: time / hgt_yi (沪股通累计) / sgt_yi (深股通累计), 亿元.
    """
    import pandas as pd

    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    try:
        r = requests.get(url, headers=HSGT_HEADERS, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("hsgt_realtime failed: %s", e)
        return pd.DataFrame()

    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])
    n = len(times)
    return pd.DataFrame({
        "time": times,
        "hgt_yi": hgt[:n] + [None] * (n - len(hgt)),
        "sgt_yi": sgt[:n] + [None] * (n - len(sgt)),
    })


def _northbound_cache_path() -> Path:
    """本地 CSV 缓存路径: ~/.tradingagents/cache/northbound_daily.csv."""
    p = Path.home() / ".tradingagents" / "cache" / "northbound_daily.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_northbound_snapshot(date: str, hgt: float, sgt: float) -> None:
    """写入/更新当天北向收盘数据到本地 CSV."""
    path = _northbound_cache_path()
    rows: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                rows[parts[0]] = line
    rows[date] = f"{date},{hgt},{sgt}"
    with open(path, "w") as f:
        f.write("date,hgt,sgt\n")
        for d in sorted(rows.keys()):
            f.write(rows[d] + "\n")


def load_northbound_history(n: int = 20) -> "Any":
    """读取最近 N 天本地北向历史."""
    import pandas as pd
    path = _northbound_cache_path()
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        return df.tail(n)
    except Exception as e:  # noqa: BLE001
        logger.warning("load_northbound_history failed: %s", e)
        return pd.DataFrame()


# ===========================================================================
# 3.3 百度股市通 — 概念板块归属
# ===========================================================================

_BAIDU_PAE_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0",
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


def baidu_concept_blocks(ticker: str) -> dict[str, Any]:
    """百度股市通概念板块归属.

    返回: {industry: [...], concept: [...], region: [...], concept_tags: [...]}.
    每项 dict: {name, change_pct, desc}.

    踩坑: 百度 ResultCode 类型不稳定(int/str), 用 str(...) 统一比较.
    """
    code = normalize_ticker(ticker)
    empty = {"industry": [], "concept": [], "region": [], "concept_tags": []}
    url = (
        f"https://finance.pae.baidu.com/api/getrelatedblock"
        f"?code={code}&market=ab&typeCode=all&finClientType=pc"
    )
    try:
        r = requests.get(url, headers=_BAIDU_PAE_HEADERS, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("baidu_concept_blocks(%s) failed: %s", code, e)
        return empty

    if str(d.get("ResultCode", -1)) != "0":
        logger.warning("baidu_concept_blocks bad ResultCode: %s", d)
        return empty

    result: dict[str, list] = {"industry": [], "concept": [], "region": [], "concept_tags": []}
    for block in d.get("Result", []):
        block_type = block.get("type", "")
        for item in block.get("list", []):
            entry = {
                "name": item.get("name", ""),
                "change_pct": item.get("increase", ""),
                "desc": item.get("desc", ""),
            }
            if "行业" in block_type:
                result["industry"].append(entry)
            elif "概念" in block_type:
                result["concept"].append(entry)
                result["concept_tags"].append(entry["name"])
            elif "地域" in block_type:
                result["region"].append(entry)
    return result


# ===========================================================================
# 3.4 东财 push2 — 个股资金流向 (分钟级)
# ===========================================================================


def eastmoney_fund_flow_minute(ticker: str) -> list[dict[str, Any]]:
    """个股资金流向 (分钟级, 当日盘中).

    单位: 元 (主力/超大单/大单/中单/小单).
    """
    code = normalize_ticker(ticker)
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": get_secid(code),
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("eastmoney_fund_flow_minute(%s) failed: %s", code, e)
        return []

    rows: list[dict[str, Any]] = []
    for line in d.get("data", {}).get("klines", []) or []:
        parts = line.split(",")
        if len(parts) >= 6:
            rows.append({
                "time": parts[0],
                "main_net": _float_or_zero(parts[1]),
                "small_net": _float_or_zero(parts[2]),
                "mid_net": _float_or_zero(parts[3]),
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


# ===========================================================================
# 3.5 龙虎榜席位 — 个股
# ===========================================================================


def dragon_tiger_board(
    ticker: str,
    trade_date: str,
    look_back: int = 30,
) -> dict[str, Any]:
    """龙虎榜数据聚合 (个股).

    Returns:
        {records: [...], seats: {buy: [...], sell: [...]}, institution: {...}}.
    """
    code = normalize_ticker(ticker)
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")

    # 1. 上榜记录
    records: list[dict[str, Any]] = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=(
            f'(TRADE_DATE>=\'{start_str}\')(TRADE_DATE<=\'{trade_date}\')'
            f'(SECURITY_CODE="{code}")'
        ),
        page_size=50,
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    for row in data:
        records.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "reason": row.get("EXPLANATION", ""),
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })

    # 2. 最近上榜的买卖席位
    seats: dict[str, list] = {"buy": [], "sell": []}
    buy_data: list[dict] = []
    sell_data: list[dict] = []
    if records:
        latest_date = records[0]["date"]
        buy_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str=f'(TRADE_DATE=\'{latest_date}\')(SECURITY_CODE="{code}")',
            page_size=10,
            sort_columns="BUY",
            sort_types="-1",
        )
        for row in buy_data[:5]:
            seats["buy"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })
        sell_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str=f'(TRADE_DATE=\'{latest_date}\')(SECURITY_CODE="{code}")',
            page_size=10,
            sort_columns="SELL",
            sort_types="-1",
        )
        for row in sell_data[:5]:
            seats["sell"].append({
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                "net": round((row.get("NET") or 0) / 10000, 1),
            })

    # 3. 机构买卖统计 (OPERATEDEPT_CODE=="0")
    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
    for detail_data, side in [(buy_data, "buy"), (sell_data, "sell")]:
        for row in detail_data:
            if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                if side == "buy":
                    institution["buy_amt"] += row.get("BUY") or 0
                else:
                    institution["sell_amt"] += row.get("SELL") or 0
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)

    return {"records": records, "seats": seats, "institution": institution}


# ===========================================================================
# 3.6 全市场龙虎榜
# ===========================================================================


def daily_dragon_tiger(
    trade_date: str | None = None,
    min_net_buy: float | None = None,
) -> dict[str, Any]:
    """全市场龙虎榜.

    Args:
        trade_date: YYYY-MM-DD (默认当日).
        min_net_buy: 净买入下限 (万元), None 不过滤.
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
    )
    if not data:
        return {
            "date": trade_date,
            "total_records": 0,
            "stocks": [],
            "note": "无数据 (非交易日或盘后未更新)",
        }

    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"date": actual_date, "total_records": len(stocks), "stocks": stocks}


# ===========================================================================
# 3.7 限售解禁日历
# ===========================================================================


def lockup_expiry(
    ticker: str,
    trade_date: str,
    forward_days: int = 90,
) -> dict[str, Any]:
    """限售解禁日历.

    Returns:
        {history: [...], upcoming: [...]}. 每项: date / type / shares / ratio.
    """
    code = normalize_ticker(ticker)

    # 1. 历史
    history_data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=15,
        sort_columns="FREE_DATE",
        sort_types="-1",
    )
    history = []
    for row in history_data:
        history.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("LIMITED_STOCK_TYPE", ""),
            "shares": row.get("FREE_SHARES_NUM", 0),
            "ratio": row.get("FREE_RATIO", 0),
        })

    # 2. 未来
    end_date = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)
    end_str = end_date.strftime("%Y-%m-%d")
    upcoming_data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str=(
            f'(SECURITY_CODE="{code}")(FREE_DATE>=\'{trade_date}\')'
            f'(FREE_DATE<=\'{end_str}\')'
        ),
        page_size=20,
        sort_columns="FREE_DATE",
        sort_types="1",
    )
    upcoming = []
    for row in upcoming_data:
        upcoming.append({
            "date": str(row.get("FREE_DATE", ""))[:10],
            "type": row.get("LIMITED_STOCK_TYPE", ""),
            "shares": row.get("FREE_SHARES_NUM", 0),
            "ratio": row.get("FREE_RATIO", 0),
        })

    return {"history": history, "upcoming": upcoming}


# ===========================================================================
# 3.8 行业板块排名 (V3.0 改用东财)
# ===========================================================================


def industry_comparison(top_n: int = 20) -> dict[str, Any]:
    """全行业涨跌幅排名 (东财行业板块, ~100 个).

    Returns:
        {top: [...], bottom: [...], total: int}.
        每项: rank / name / change_pct / code / up_count / down_count / leader.
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=DEFAULT_TIMEOUT)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("industry_comparison failed: %s", e)
        return {"top": [], "bottom": [], "total": 0}

    items = d.get("data", {}).get("diff", []) or []
    if not items:
        return {"top": [], "bottom": [], "total": 0}

    rows = []
    for i, item in enumerate(items):
        rows.append({
            "rank": i + 1,
            "name": item.get("f14", ""),
            "change_pct": item.get("f3", 0),
            "code": item.get("f12", ""),
            "up_count": item.get("f104", 0),
            "down_count": item.get("f105", 0),
            "leader": item.get("f140", ""),
            "leader_change": item.get("f136", 0),
        })
    return {
        "top": rows[:top_n],
        "bottom": rows[-top_n:],
        "total": len(rows),
    }


__all__ = [
    "ths_hot_reason",
    "hsgt_realtime",
    "save_northbound_snapshot",
    "load_northbound_history",
    "baidu_concept_blocks",
    "eastmoney_fund_flow_minute",
    "dragon_tiger_board",
    "daily_dragon_tiger",
    "lockup_expiry",
    "industry_comparison",
]
