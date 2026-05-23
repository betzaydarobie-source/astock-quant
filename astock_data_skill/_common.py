"""共用 helper —— ticker 归一化 / 市场前缀 / 东财 datacenter 统一查询.

代码严格按 ~/.claude/skills/a-stock-data/SKILL.md V3.1 抄, 保证与原 skill 行为一致.

约定:
- 所有公开函数都吃归一化后的 6 位代码; 上层可调 normalize_ticker 一次拿到.
- 所有 requests.get/post 加 timeout=15(资金面/datacenter)或 timeout=10(行情).
- 失败一律优雅兜底(返回空 list/None), 不抛崩 —— 上层用 dict.get 拿值不会炸.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/117.0.0.0 Safari/537.36"
)

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

DEFAULT_TIMEOUT = 15.0


def normalize_ticker(code: str | int) -> str:
    """把多种 ticker 输入归一化为纯 6 位数字字符串.

    支持: '688017' / 'SH688017' / 'sh688017' / '688017.SH' / '688017.sh' /
          'SZ000001' / 'BJ832000' / 688017(int).
    """
    s = str(code).strip().upper()
    # 切掉前缀 SH/SZ/BJ
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # 切掉后缀 .SH/.SZ/.BJ
    if "." in s:
        s = s.split(".", 1)[0]
    s = s.strip()
    if not s.isdigit():
        raise ValueError(f"无法归一化 ticker: {code!r}")
    return s.zfill(6)


def get_prefix(code: str) -> str:
    """6 位代码 → 市场前缀 (sh/sz/bj). 必须先 normalize_ticker."""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


def get_secid(code: str) -> str:
    """6 位代码 → 东财 secid 格式 (1.code 沪市 / 0.code 深市).

    SKILL.md 的 push2 / push2his 端点用这个格式.
    """
    return f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict[str, Any]]:
    """东财数据中心统一查询 —— 龙虎榜/解禁/融资融券/大宗交易/股东户数/分红 共用.

    严格按 SKILL.md L150-168 抄, 加 timeout 和优雅 fail.

    Returns:
        list[dict]: result.data 数组, 失败/无数据返回 [].
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    try:
        r = requests.get(
            DATACENTER_URL,
            params=params,
            headers={"User-Agent": UA},
            timeout=DEFAULT_TIMEOUT,
        )
        d = r.json()
    except Exception as e:  # noqa: BLE001 —— 网络/JSON 失败一律降级返回 []
        logger.warning("eastmoney_datacenter(%s) failed: %s", report_name, e)
        return []

    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


__all__ = [
    "UA",
    "DATACENTER_URL",
    "DEFAULT_TIMEOUT",
    "normalize_ticker",
    "get_prefix",
    "get_secid",
    "eastmoney_datacenter",
]
