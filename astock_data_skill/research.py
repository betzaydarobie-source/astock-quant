"""Layer 2 研报层 —— 东财 + 同花顺 EPS + iwencai 语义搜索.

3 个端点:
- eastmoney_reports: 个股研报列表 (HTTP JSON, 免费)
- download_research_pdf: 单份研报 PDF 下载
- ths_eps_forecast: 同花顺一致预期 EPS (HTML 表格解析)
- iwencai_search / iwencai_query: NL 语义搜索 (需 API Key + X-Claw headers)
- dedup_articles: iwencai 结果去重

代码按 SKILL.md L345-562 抄.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import requests

from astock_data_skill._common import DEFAULT_TIMEOUT, UA, normalize_ticker

logger = logging.getLogger(__name__)


# ===========================================================================
# 2.1 东财研报 API
# ===========================================================================

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"


def eastmoney_reports(ticker: str, max_pages: int = 5) -> list[dict[str, Any]]:
    """拉取个股研报列表.

    返回 list[dict], 关键字段:
    - title / publishDate / orgSName (机构简称) / infoCode (拼 PDF URL)
    - predictThisYearEps / predictNextYearEps / predictNextTwoYearEps
    - emRatingName (买入/增持) / indvInduName (行业)
    """
    code = normalize_ticker(ticker)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/",
    })
    all_records: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2000-01-01", "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        try:
            r = session.get(REPORT_API, params=params, timeout=30)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("eastmoney_reports(%s) page=%d failed: %s", code, page, e)
            break

        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
        time.sleep(0.3)
    return all_records


def download_research_pdf(record: dict[str, Any], target_dir: str = "./reports") -> str | None:
    """下载单份研报 PDF, 返回保存路径或 None.

    输入: eastmoney_reports() 单个 record.
    """
    info_code = record.get("infoCode", "")
    if not info_code:
        return None
    date = (record.get("publishDate") or "")[:10]
    org = record.get("orgSName") or "未知"
    title = re.sub(r'[\\/:*?"<>|]', "_", record.get("title", ""))[:80]
    fname = f"{date}_{org}_{title}.pdf"
    target = Path(target_dir) / fname
    if target.exists():
        return str(target)

    url = PDF_TPL.format(info_code=info_code)
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("download_research_pdf(%s) failed: %s", info_code, e)
        return None

    if r.status_code == 200 and len(r.content) >= 1024:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        return str(target)
    return None


# ===========================================================================
# 2.2 同花顺一致预期 EPS (HTML 表格解析)
# ===========================================================================


def ths_eps_forecast(ticker: str) -> "Any":
    """同花顺机构一致预期 EPS.

    直连 basic.10jqka.com.cn, 解析 HTML 表格.
    返回 pandas.DataFrame: 年度 / 预测机构数 / 最小值 / 均值 / 最大值.
    "均值" = 机构一致预期 EPS. 预测机构数 < 3 谨慎.
    无机构覆盖时返回空 DataFrame.
    """
    import pandas as pd  # 延迟 import: skill 库可能没装 pandas

    code = normalize_ticker(ticker)
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    headers = {
        "User-Agent": UA,
        "Referer": "https://basic.10jqka.com.cn/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        r.encoding = "gbk"
        dfs = pd.read_html(r.text)
    except Exception as e:  # noqa: BLE001
        logger.warning("ths_eps_forecast(%s) failed: %s", code, e)
        return pd.DataFrame()

    # 找含"每股收益"或"均值"的表格
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any("每股收益" in c or "均值" in c for c in cols):
            return df
    return dfs[0] if dfs else pd.DataFrame()


# ===========================================================================
# 2.3 iwencai NL 语义搜索 (需 API Key)
# ===========================================================================

IWENCAI_BASE = os.environ.get("IWENCAI_BASE_URL", "https://openapi.iwencai.com")


def _iwencai_key() -> str:
    """每次调用都从 env 读, 让测试可 monkeypatch."""
    return os.environ.get("IWENCAI_API_KEY", "")


def _claw_headers(call_type: str = "normal") -> dict[str, str]:
    """SkillHub 2.0 必须的 X-Claw 鉴权头."""
    return {
        "X-Claw-Call-Type": call_type,
        "X-Claw-Skill-Id": "report-search",
        "X-Claw-Skill-Version": "2.0.0",
        "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none",
        "X-Claw-Trace-Id": secrets.token_hex(32),
    }


def iwencai_search(
    query: str,
    channel: str = "report",
    size: int = 50,
) -> list[dict[str, Any]]:
    """iwencai 语义搜索.

    channel: "report" / "announcement" / "news".
    size: 默认 10, 实测可调到 50 (隐藏参数).
    无 IWENCAI_API_KEY 时返回 [] (优雅降级, 不抛).
    """
    key = _iwencai_key()
    if not key:
        logger.info("iwencai_search: IWENCAI_API_KEY 未设置, 跳过")
        return []
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **_claw_headers(),
    }
    payload = {
        "channels": [channel],
        "app_id": "AIME_SKILL",
        "query": query,
        "size": size,
    }
    try:
        r = requests.post(
            f"{IWENCAI_BASE}/v1/comprehensive/search",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning("iwencai_search HTTP %s: %s", r.status_code, r.text[:200])
            return []
        data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("iwencai_search failed: %s", e)
        return []

    if data.get("status_code", 0) != 0:
        logger.warning("iwencai_search err: %s", data.get("status_msg", ""))
        return []
    return data.get("data") or []


def iwencai_query(
    query: str,
    page: int = 1,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """iwencai NL 数据查询 (结构化字段, 如「贵州茅台 ROE」)."""
    key = _iwencai_key()
    if not key:
        return []
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **_claw_headers(),
    }
    payload = {
        "query": query,
        "page": str(page),
        "limit": str(limit),
        "is_cache": "1",
        "expand_index": "true",
    }
    try:
        r = requests.post(
            f"{IWENCAI_BASE}/v1/query2data",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("iwencai_query failed: %s", e)
        return []

    if data.get("status_code", 0) != 0:
        return []
    return data.get("datas") or []


def dedup_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 uid 仅保留 score 最高的段落, 按发布日期降序."""
    best: dict[str, dict[str, Any]] = {}
    for a in articles:
        uid = a.get("uid", "") or f"{a.get('title','')}|{a.get('publish_date','')}"
        score = _safe_score(a.get("score", 0))
        if uid not in best or score > _safe_score(best[uid].get("score", 0)):
            best[uid] = a
    return sorted(best.values(), key=lambda x: x.get("publish_date", ""), reverse=True)


def _safe_score(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# 测试需要 import json,  re —— 保持 import 显式 (避免被 ruff 误删)
_ = json
_ = re


__all__ = [
    "eastmoney_reports",
    "download_research_pdf",
    "ths_eps_forecast",
    "iwencai_search",
    "iwencai_query",
    "dedup_articles",
]
