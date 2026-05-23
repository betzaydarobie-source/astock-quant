"""Layer 6 基础数据层 —— mootdx 财务/F10 + 东财个股信息 + 新浪三表.

4 个端点 (SKILL.md L1472-1592):
- mootdx_finance: 季报快照 (37 字段)
- mootdx_f10: 公司 9 大类文本 (mootdx TCP)
- eastmoney_stock_info: 个股基本面 (行业/股本/市值/上市日期)
- sina_financial_report: 资产负债表/利润表/现金流量表
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from astock_data_skill._common import DEFAULT_TIMEOUT, UA, get_secid, normalize_ticker

logger = logging.getLogger(__name__)


# mootdx F10 的 9 大类
F10_CATEGORIES = [
    "最新提示", "公司概况", "财务分析",
    "股东研究", "股本结构", "资本运作",
    "业内点评", "行业分析", "公司大事",
]


# ===========================================================================
# 6.1 mootdx 财务快照 (37 字段)
# ===========================================================================


def mootdx_finance(ticker: str) -> dict[str, Any]:
    """mootdx 季报快照. 37 字段.

    包含 eps / bvps / roe / profit / income 等核心财务. 失败返回 {}.
    """
    code = normalize_ticker(ticker)
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        df = client.finance(symbol=code)
        if df is None or len(df) == 0:
            return {}
        # mootdx 返回 DataFrame, 取第一行
        row = df.iloc[0].to_dict() if hasattr(df, "iloc") else df.to_dict()
        return {k: row[k] for k in row if not k.startswith("_")}
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx_finance(%s) failed: %s", code, e)
        return {}


# ===========================================================================
# 6.2 mootdx F10 (9 大类文本)
# ===========================================================================


def mootdx_f10(ticker: str, category: str) -> str:
    """mootdx F10 公司文本资料.

    Args:
        ticker: 股票代码.
        category: "最新提示" / "公司概况" / "财务分析" / "股东研究" /
                  "股本结构" / "资本运作" / "业内点评" / "行业分析" / "公司大事".
    """
    code = normalize_ticker(ticker)
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        text = client.F10(symbol=code, name=category)
        return text or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx_f10(%s,%s) failed: %s", code, category, e)
        return ""


# ===========================================================================
# 6.3 东财个股基本面 (push2 API)
# ===========================================================================


def eastmoney_stock_info(ticker: str) -> dict[str, Any]:
    """东财个股基本面信息.

    Returns:
        {code, name, industry, total_shares, float_shares, mcap(元),
         float_mcap, list_date(YYYYMMDD), price}. 失败返回 {}.
    """
    code = normalize_ticker(ticker)
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": get_secid(code),
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=10)
        d = r.json().get("data") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("eastmoney_stock_info(%s) failed: %s", code, e)
        return {}

    if not d:
        return {}
    return {
        "code": d.get("f57", ""),
        "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0),
        "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
    }


# ===========================================================================
# 6.4 新浪财报三表
# ===========================================================================


def sina_financial_report(
    ticker: str,
    report_type: str = "lrb",
) -> list[dict[str, Any]]:
    """新浪财报三表.

    Args:
        ticker: 6 位代码.
        report_type: "fzb" 资产负债表 / "lrb" 利润表 / "llb" 现金流量表.

    Returns:
        最近 20 期财务数据列表, 字段是中文 (报告日 / 净利润 / ...).
    """
    if report_type not in ("fzb", "lrb", "llb"):
        raise ValueError(f"report_type 必须是 fzb/lrb/llb, 实际: {report_type}")

    code = normalize_ticker(ticker)
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    paper_code = f"{prefix}{code}"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": paper_code,
        "source": report_type,
        "type": "0",
        "page": "1",
        "num": "20",
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=DEFAULT_TIMEOUT)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("sina_financial_report(%s,%s) failed: %s", code, report_type, e)
        return []

    result = d.get("result", {}).get("data", {})
    items = result.get(report_type, [])
    return items if isinstance(items, list) else []


__all__ = [
    "F10_CATEGORIES",
    "mootdx_finance",
    "mootdx_f10",
    "eastmoney_stock_info",
    "sina_financial_report",
]
