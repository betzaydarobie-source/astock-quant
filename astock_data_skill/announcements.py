"""Layer 7 公告层 —— 巨潮公告全文检索 + mootdx F10 最新提示.

2 个端点 (SKILL.md L1596-1664):
- cninfo_announcements: 巨潮公告全文检索
- mootdx_latest_announcement: mootdx F10 最新提示 (公告/分红/股东大会摘要)
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from astock_data_skill._common import DEFAULT_TIMEOUT, UA, normalize_ticker

logger = logging.getLogger(__name__)


# ===========================================================================
# 7.1 巨潮公告 (cninfo.com.cn)
# ===========================================================================


def cninfo_announcements(ticker: str, page_size: int = 30) -> list[dict[str, Any]]:
    """巨潮公告全文检索.

    Returns:
        list[dict]: title / type / date / url.
    """
    code = normalize_ticker(ticker)
    if code.startswith("6"):
        org_id = f"gssh0{code}"
    elif code.startswith("8") or code.startswith("4"):
        org_id = f"gsbj0{code}"
    else:
        org_id = f"gssz0{code}"

    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("cninfo_announcements(%s) failed: %s", code, e)
        return []

    rows = []
    for item in d.get("announcements", []) or []:
        ann_id = item.get("announcementId", "")
        rows.append({
            "title": item.get("announcementTitle", ""),
            "type": item.get("announcementTypeName", ""),
            "date": item.get("announcementTime", ""),
            "url": (
                f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={ann_id}"
                if ann_id
                else ""
            ),
        })
    return rows


# ===========================================================================
# 7.2 mootdx F10 最新提示 (公告摘要)
# ===========================================================================


def mootdx_latest_announcement(ticker: str) -> str:
    """mootdx F10 最新提示 - 含最近的公告/分红/股东大会决议等摘要.

    复用 fundamentals.mootdx_f10, 这里只是绑定到 "最新提示" 类目.
    """
    from astock_data_skill.fundamentals import mootdx_f10
    code = normalize_ticker(ticker)
    return mootdx_f10(code, "最新提示")


__all__ = [
    "cninfo_announcements",
    "mootdx_latest_announcement",
]
