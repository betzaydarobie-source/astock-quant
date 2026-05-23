# ai_api —— LLM 个股分析 FastAPI 后端

为前端 GitHub Pages 提供 LLM 个股分析能力。前端是静态 HTML(`docs/ai-analysis/`)，
通过 HTTPS 调用本服务的 `/api/analyze` 端点拿 markdown。

## 启动方式

```bash
# 主项目 .env 里需有 DEEPSEEK_API_KEY
cd "/Users/wujiangjingcai/claude code/量化"

# 加载 env (项目 .env)
set -o allexport && source .env && set +o allexport

# 启动 (开发模式 reload)
uv run uvicorn services.ai_api.app:app --host 127.0.0.1 --port 8000 --reload

# 启动 (生产模式, 1 worker; LLM 是 I/O bound, 1 worker 足够)
uv run uvicorn services.ai_api.app:app --host 127.0.0.1 --port 8000
```

## 端点

### `GET /api/health`

健康检查 + 模型版本。

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","model":"deepseek-v4-pro","version":"1.0.0"}
```

### `GET /api/analyze`

对单只票生成 LLM 分析。

参数：
- `q` (必需): 股票代码或中文名 (e.g. `600519` / `贵州茅台` / `茅台`)
- `perspective` (可选): `value` (默认) / `general` / `multi`
- `depth` (可选): `summary` (默认, ~200 字) / `full` (~600 字)

```bash
# 用代码
curl "http://127.0.0.1:8000/api/analyze?q=600519"

# 用中文名
curl "http://127.0.0.1:8000/api/analyze?q=贵州茅台"

# 用简称 + 完整深度
curl "http://127.0.0.1:8000/api/analyze?q=茅台&depth=full"
```

返回 schema:
```json
{
  "ticker": "600519",
  "name": "贵州茅台",
  "markdown": "...AI 生成的 markdown...",
  "fetched_endpoints": ["quote", "info", "dividends", "holders", "fund_flow", "news"],
  "tokens_used": 750,
  "generated_at": "2026-05-23T16:30:00",
  "perspective": "value",
  "depth": "summary",
  "cached": false
}
```

错误码:
- `400` 参数非法
- `404` 找不到 ticker
- `502` LLM 调用失败
- `500` 其它内部错误

## CORS

允许:
- `https://betzaydarobie-source.github.io` (GitHub Pages)
- `http://localhost:*` / `http://127.0.0.1:*` (本地开发)

## In-memory cache

5 分钟 TTL, key = `ticker|perspective|depth`. 进程重启清空, 单进程。

## 诚信红线

所有 `markdown` 输出末尾自动追加 `DISCLAIMER`(由 `astock_quant.llm.stock_analyst`
保证): 「本分析为 AI 生成, 不构成投资建议」。前端必须保留显示, 不可剥离。

## launchd 常驻

参考 `services/ai_api/com.astock.ai-api.plist.template` —— 由 lead 配 LaunchAgent
让服务开机自启 + 故障重启。
