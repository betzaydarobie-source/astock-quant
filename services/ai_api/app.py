"""FastAPI 应用 —— 暴露 stock_analyst.analyze_stock 给前端 GitHub Pages.

启动:
    uv run uvicorn services.ai_api.app:app --host 127.0.0.1 --port 8000

端点:
    GET /api/health     → {status, model, version}
    GET /api/analyze    → {ticker, name, markdown, fetched_endpoints, tokens_used, generated_at}

CORS:
    允许 https://betzaydarobie-source.github.io + http://localhost:*

诚信红线: markdown 总含「AI 生成 / 不构成投资建议」(由 stock_analyst.DISCLAIMER 保证).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

app = FastAPI(
    title="A股 AI 个股分析 API",
    version=VERSION,
    description="基于 DeepSeek + SKILL adapter 的个股价值分析后端. 不构成投资建议.",
)

# CORS —— 允许 GitHub Pages + 本地开发
ALLOWED_ORIGINS = [
    "https://betzaydarobie-source.github.io",
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # 用 regex 兜底任意 localhost 端口 (开发常用 5xxx / 8xxx / 3xxx)
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ===========================================================================
# Schemas
# ===========================================================================


class HealthResponse(BaseModel):
    status: str
    model: str
    version: str


class AnalyzeResponse(BaseModel):
    ticker: str
    name: str
    markdown: str
    fetched_endpoints: list[str]
    tokens_used: int
    generated_at: str
    perspective: str = "value"
    depth: str = "summary"
    cached: bool = False


# ===========================================================================
# In-memory cache (5 分钟, key = ticker + perspective)
# ===========================================================================

_CACHE_TTL_SEC = 5 * 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(key: str) -> dict[str, Any] | None:
    """命中 + TTL 内 → 返回 cached dict; 否则 None (顺手清掉过期)."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    _cache[key] = (time.time(), payload)


# ===========================================================================
# Ticker 解析: 代码 / 中文名 双向
# ===========================================================================


def _resolve_ticker(q: str) -> tuple[str, str] | None:
    """把用户输入归一化为 (ticker, name).

    1. 纯 6 位数字 (或带前缀) → 用 normalize_ticker, 查名字
    2. 中文名 → 先查 ticker_names mapping 反查, 再退到 mootdx F10 (慢, 兜底)
    """
    from astock_quant.predict.ticker_names import (
        STAGE1_NAMES,
        STAGE1_SHORT_NAMES,
        get_ticker_name,
    )

    q = (q or "").strip()
    if not q:
        return None

    # 1) 数字代码 (含前缀): 走 normalize
    digits_only = q.replace(".", "").replace("sh", "").replace("SH", "").replace(
        "sz", "").replace("SZ", "").replace("bj", "").replace("BJ", "")
    if digits_only.isdigit():
        try:
            from astock_data_skill import normalize_ticker
            code = normalize_ticker(q)
            name = get_ticker_name(code)
            return code, name
        except Exception as e:  # noqa: BLE001
            logger.warning("_resolve_ticker(%s) normalize failed: %s", q, e)
            return None

    # 2) 中文名/简称 反查
    # 全名优先
    for code, name in STAGE1_NAMES.items():
        if name == q:
            return code, name
    # 简称次之
    for code, short in STAGE1_SHORT_NAMES.items():
        if short == q:
            return code, STAGE1_NAMES.get(code, q)
    # 模糊包含 (e.g. "茅台" → 贵州茅台)
    matches = [
        (code, name) for code, name in STAGE1_NAMES.items()
        if q in name or q in STAGE1_SHORT_NAMES.get(code, "")
    ]
    if len(matches) == 1:
        return matches[0]

    # 3) 退到 mootdx F10 反查 (生产环境慢, 仅在前两步失败时调)
    try:
        import json as _json
        from pathlib import Path
        # 复用 daily 的 cache 文件
        cache_path = Path("data_cache") / "ticker_names_cache.json"
        if cache_path.exists():
            mapping: dict[str, str] = _json.loads(cache_path.read_text(encoding="utf-8"))
            for code, name in mapping.items():
                if name == q:
                    return code, name
            # 模糊
            fuzzy = [(c, n) for c, n in mapping.items() if q in n]
            if len(fuzzy) == 1:
                return fuzzy[0]
    except Exception as e:  # noqa: BLE001
        logger.debug("_resolve_ticker fallback cache miss: %s", e)

    return None


# ===========================================================================
# Endpoints
# ===========================================================================


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """健康检查 + 模型版本."""
    model = os.environ.get("LLM_MODEL") or "deepseek-v4-pro"
    return HealthResponse(status="ok", model=model, version=VERSION)


@app.get("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    q: str = Query(..., description="股票代码或中文名 (e.g. 600519 / 贵州茅台 / 茅台)"),
    perspective: str = Query("value", description="value / general / multi"),
    depth: str = Query("summary", description="summary / full"),
) -> AnalyzeResponse:
    """对单只票生成 LLM 分析.

    错误码:
        - 400: 参数非法 (perspective / depth)
        - 404: 找不到 ticker (代码错 / 名字查不到)
        - 502: LLM 调用失败
        - 500: 其它内部错误
    """
    if perspective not in ("value", "general", "multi"):
        raise HTTPException(
            status_code=400,
            detail=f"perspective must be value/general/multi, got: {perspective}",
        )
    if depth not in ("summary", "full"):
        raise HTTPException(
            status_code=400,
            detail=f"depth must be summary/full, got: {depth}",
        )

    resolved = _resolve_ticker(q)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"找不到该股票: '{q}'. 请用 6 位代码 (如 600519) 或中文名 (如 贵州茅台).",
        )
    ticker, _expected_name = resolved

    # cache key
    key = f"{ticker}|{perspective}|{depth}"
    cached = _cache_get(key)
    if cached is not None:
        return AnalyzeResponse(**{**cached, "cached": True})

    try:
        from astock_quant.llm.stock_analyst import analyze_stock
        result = analyze_stock(
            ticker,
            perspective=perspective,
            depth=depth,
            factor_context=None,
            pre_fetched=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("analyze_stock crashed for %s", ticker)
        raise HTTPException(status_code=500, detail=f"内部错误: {e}") from e

    if not result.get("markdown"):
        # LLM 失败兜底: stock_analyst._fallback_result 会把 markdown 设为 None
        err = result.get("error", "LLM 调用失败 (markdown 为空)")
        raise HTTPException(status_code=502, detail=f"AI 分析失败: {err}")

    payload = {
        "ticker": result["ticker"],
        "name": result.get("name", ""),
        "markdown": result["markdown"],
        "fetched_endpoints": result.get("fetched_endpoints", []),
        "tokens_used": result.get("tokens_used", 0),
        "generated_at": result.get("generated_at", ""),
        "perspective": perspective,
        "depth": depth,
        "cached": False,
    }
    _cache_put(key, payload)
    return AnalyzeResponse(**payload)


@app.get("/")
async def root() -> dict:
    """根路径 —— 简单提示."""
    return {
        "service": "astock-ai-api",
        "version": VERSION,
        "endpoints": ["/api/health", "/api/analyze?q=<code or name>"],
        "disclaimer": "本服务返回的所有分析均由 AI 生成, 不构成任何投资建议.",
    }
