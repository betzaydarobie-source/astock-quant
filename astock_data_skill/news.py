"""Layer 5 新闻层 —— 东财个股新闻 + 财联社快讯 + 东财全球资讯.

3 个端点 (SKILL.md L1349-1466):
- eastmoney_stock_news: 个股相关新闻 (JSONP)
- cls_telegraph: 财联社全市场实时电报
- eastmoney_global_news: 7x24 全球财经资讯
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import requests

from astock_data_skill._common import DEFAULT_TIMEOUT, UA, normalize_ticker

logger = logging.getLogger(__name__)


# ===========================================================================
# 5.1 东财个股新闻 (search-api-web JSONP)
# ===========================================================================


def eastmoney_stock_news(ticker: str, page_size: int = 20) -> list[dict[str, Any]]:
    """东财个股新闻 (JSONP 接口).

    Returns:
        list[dict]: title / content (前 200 字) / time / source / url.
    """
    code = normalize_ticker(ticker)
    cb = "jQuery_news"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner_params = json.dumps({
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default", "sort": "default",
                "pageIndex": 1, "pageSize": page_size,
                "preTag": "", "postTag": "",
            }
        },
    }, separators=(',', ':'))
    params = {"cb": cb, "param": inner_params}
    headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
        text = r.text
        # 解析 JSONP: cb(....)
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        d = json.loads(json_str)
    except Exception as e:  # noqa: BLE001
        logger.warning("eastmoney_stock_news(%s) failed: %s", code, e)
        return []

    rows = []
    articles = d.get("result", {}).get("cmsArticleWebOld", {}).get("list", []) or []
    for a in articles:
        rows.append({
            "title": re.sub(r'<[^>]+>', '', a.get("title", "")),
            "content": re.sub(r'<[^>]+>', '', a.get("content", ""))[:200],
            "time": a.get("date", ""),
            "source": a.get("mediaName", ""),
            "url": a.get("url", ""),
        })
    return rows


# ===========================================================================
# 5.2 财联社快讯 (cls.cn)
# ===========================================================================


def cls_telegraph(page_size: int = 50) -> list[dict[str, Any]]:
    """财联社电报 (全市场实时快讯).

    Returns:
        list[dict]: title / content / time.
    """
    url = "https://www.cls.cn/nodeapi/telegraphList"
    params = {"rn": str(page_size), "page": "1"}
    headers = {"User-Agent": UA, "Referer": "https://www.cls.cn/"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("cls_telegraph failed: %s", e)
        return []

    rows = []
    for item in d.get("data", {}).get("roll_data", []) or []:
        rows.append({
            "title": item.get("title", "") or item.get("brief", ""),
            "content": item.get("content", "") or item.get("brief", ""),
            "time": item.get("ctime", ""),
        })
    return rows


# ===========================================================================
# 5.3 东财全球资讯 (np-weblist)
# ===========================================================================


def eastmoney_global_news(page_size: int = 50) -> list[dict[str, Any]]:
    """东方财富全球财经资讯 (7x24 滚动).

    Returns:
        list[dict]: title / summary (前 200 字) / time.
    """
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web", "biz": "web_724",
        "fastColumn": "102", "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    headers = {"User-Agent": UA, "Referer": "https://kuaixun.eastmoney.com/"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("eastmoney_global_news failed: %s", e)
        return []

    rows = []
    for item in d.get("data", {}).get("fastNewsList", []) or []:
        rows.append({
            "title": item.get("title", ""),
            "summary": (item.get("summary", "") or "")[:200],
            "time": item.get("showTime", ""),
        })
    return rows


__all__ = [
    "eastmoney_stock_news",
    "cls_telegraph",
    "eastmoney_global_news",
]
