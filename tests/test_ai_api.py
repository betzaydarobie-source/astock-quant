"""services/ai_api/app.py 测试 —— 用 FastAPI TestClient + mock LLM.

不打真网络. 验证:
- /api/health 返回 ok + 模型名 + 版本
- /api/analyze 代码查询 / 中文名查询 / 简称模糊
- 404 (找不到 ticker) / 400 (参数非法) / 502 (LLM 失败)
- in-memory cache: 同 key 第二次 cached=True
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """每个测试拿干净的 client + 清空 cache."""
    from services.ai_api import app as app_mod
    app_mod._cache.clear()
    return TestClient(app_mod.app)


@pytest.fixture
def mock_analyze_success():
    """mock analyze_stock 返回成功结果."""
    def fake_analyze(ticker, *, perspective="value", depth="summary",
                     factor_context=None, pre_fetched=None, client=None):
        return {
            "ticker": ticker,
            "name": "测试公司",
            "markdown": "AI 解读内容\n\n不构成投资建议",
            "fetched_endpoints": ["quote", "info"],
            "tokens_used": 500,
            "generated_at": "2026-05-23T16:00:00",
            "perspective": perspective,
            "depth": depth,
        }
    with patch("astock_quant.llm.stock_analyst.analyze_stock", side_effect=fake_analyze):
        yield


@pytest.fixture
def mock_analyze_fail():
    def fake_analyze(ticker, **kw):
        return {
            "ticker": ticker, "name": "", "markdown": None,
            "fetched_endpoints": [], "tokens_used": 0,
            "generated_at": "2026-05-23T16:00:00",
            "error": "LLM 调用失败: API down",
        }
    with patch("astock_quant.llm.stock_analyst.analyze_stock", side_effect=fake_analyze):
        yield


# ===========================================================================
# /api/health
# ===========================================================================


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"]
        assert body["model"]


# ===========================================================================
# /api/analyze
# ===========================================================================


class TestAnalyzeByCode:
    def test_six_digit_code(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "600519"})
        assert r.status_code == 200
        body = r.json()
        assert body["ticker"] == "600519"
        assert body["name"]
        assert "不构成投资建议" in body["markdown"]
        assert body["cached"] is False

    def test_sh_prefix(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "SH600519"})
        assert r.status_code == 200
        assert r.json()["ticker"] == "600519"

    def test_dot_suffix(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "600519.SH"})
        assert r.status_code == 200
        assert r.json()["ticker"] == "600519"


class TestAnalyzeByName:
    def test_full_chinese_name(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "贵州茅台"})
        assert r.status_code == 200
        assert r.json()["ticker"] == "600519"

    def test_short_name_alias(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "茅台"})
        assert r.status_code == 200
        assert r.json()["ticker"] == "600519"


class TestAnalyzeErrors:
    def test_unknown_ticker_404(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "ZZZZNotExist"})
        assert r.status_code == 404

    def test_invalid_perspective_400(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "600519", "perspective": "xxx"})
        assert r.status_code == 400

    def test_invalid_depth_400(self, client, mock_analyze_success):
        r = client.get("/api/analyze", params={"q": "600519", "depth": "ultra"})
        assert r.status_code == 400

    def test_llm_failure_502(self, client, mock_analyze_fail):
        r = client.get("/api/analyze", params={"q": "600519"})
        assert r.status_code == 502
        assert "AI 分析失败" in r.json()["detail"]


class TestAnalyzeCache:
    def test_second_call_cached(self, client, mock_analyze_success):
        r1 = client.get("/api/analyze", params={"q": "600519"})
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        r2 = client.get("/api/analyze", params={"q": "600519"})
        assert r2.status_code == 200
        assert r2.json()["cached"] is True

    def test_different_perspective_separate_cache(self, client, mock_analyze_success):
        r1 = client.get("/api/analyze", params={"q": "600519", "perspective": "value"})
        r2 = client.get("/api/analyze", params={"q": "600519", "perspective": "general"})
        assert r1.status_code == 200 and r1.json()["cached"] is False
        assert r2.status_code == 200 and r2.json()["cached"] is False


# ===========================================================================
# CORS preflight
# ===========================================================================


class TestCORS:
    def test_github_pages_origin_allowed(self, client):
        r = client.options(
            "/api/health",
            headers={
                "Origin": "https://betzaydarobie-source.github.io",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}
