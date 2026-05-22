# P6 — LLM 情绪因子

> factor-engineer 产出 · 2026-05-16 · Stage 2 启动后第一个交付物
>
> 前置：`P1-架构设计.md` §5 (LLM 因子接口预留)、`P3-因子库.md` (`BaseFactor` 接口 +
> registry 模式)、`P2-数据管道.md` (`DataSource.get_news`)。本文档讲 P6 在 P3a 留好的
> 插槽里**填实**了什么。

---

## 0. 一句话结论

按计划「方案 A 瘦身版」研读 `.p0-repos/TradingAgents-astock/` 的多供应商 LLM 封装 +
A股 分析师 prompt，**不 import 任何框架**，用 typing.Protocol 自写一个最小客户端
接口，默认 provider 是 Anthropic Claude（直接走官方 `anthropic` SDK）。新增 1 个
LLM 因子 `LLMNewsSentiment`：消费 P2 `get_news` 的 NewsItem，按 A股 五级情绪框架打分，
confidence 加权聚合到 (ticker, date)，落 `data_cache/llm_factor/{ticker}-{date}.json`
**严格去重**避免烧 token。registry 在 env var `ENABLE_LLM_FACTOR=1` 时启用，**默认关**，
Stage 1 流水线 25 因子完全不变、62 旧测试 + 23 新测试 = 85/85 全过、ruff clean。

---

## 1. 建了什么

### 1.1 新增包 `astock_quant/llm/`

| 文件 | 职责 |
|---|---|
| `__init__.py` | 对外暴露 `LLMClient` / `make_llm_client` / `NewsSentimentOutput` 等 |
| `client.py` | **核心** —— `LLMClient` Protocol + `AnthropicClient` 默认实现 + `make_llm_client` 工厂 + `parse_json_to_schema` JSON 解析 helper |
| `schemas.py` | LLM 结构化输出 schema —— `NewsSentimentOutput`（sentiment / confidence / reason） |
| `prompts.py` | A股 新闻情绪打分的 system / user prompt 模板（中文，借鉴 social_media_analyst 的五级情绪框架） |

### 1.2 填实 `astock_quant/factors/llm_factor.py`

P3a 留的 stub 现在是真实现：

- `LLMNewsSentiment(BaseFactor)` —— 继承 `BaseFactor`，产出 `pd.Series(MultiIndex=(date, ticker))`，**和量价因子完全平级**
- `LLMFactor = LLMNewsSentiment` 别名 —— 老代码 / P3a 文档里写的 `LLMFactor` 仍能用
- `compute()` 接受 `news`（预拉好的字典） 或 `news_fetcher`（callable）；都没传则走全 NaN
- LLM client lazy 构造 —— 没 API key 时只在真正要打分（cache miss）时报错 + 跳过

### 1.3 改 `astock_quant/factors/registry.py`

加 1 行 import + 1 个 env var helper `_llm_factor_enabled()`；在 `default_factors()`
末尾按 env var 条件追加 `LLMNewsSentiment(lookback=1)`。**默认关闭**，所以现有 25 因
子集合与跑通行为完全不变。

### 1.4 改 `pyproject.toml`

加 `anthropic>=0.40`（实测装的是 0.102.0）。其它依赖不动。

### 1.5 新增测试 `tests/test_llm_factor_with_mock_client.py` —— 23 个测试

mock LLM 客户端、不烧 token，覆盖：

- 因子产出对齐 panel.index、聚合正确
- 缓存命中（二次跑 0 次 LLM 调用，结果 bit-exact 一致）
- 缓存文件 schema 校验 + 不泄漏 API key / prompt 原文
- LLM 失败 / 缺 key / 无新闻 / 空 panel → 全 NaN 不抛异常
- confidence 加权聚合的数学正确性
- lookback 窗口在多日新闻上的聚合范围
- registry env var 开关（默认 25 / 启用 26 / 显式关 25）
- 工厂错误处理（缺 key / 不支持 provider）
- Protocol runtime_checkable 校验
- JSON 解析 helper（纯 JSON / Markdown 包装 / 前后废话 / 垃圾输入）
- 内部 helper：`_news_id`（url 优先 / title hash 后备）、`_normalize_ticker`（zero-pad）
- `news_fetcher` 注入路径

---

## 2. 关键决策

### 2.1 不引 langchain —— 自写最小 Protocol

参考的 `.p0-repos/TradingAgents-astock/llm_clients/` 全部建在 `langchain_anthropic` /
`langchain_openai` 之上 —— 这是它的 multi-agent 编排架构需要。我们的场景**只是给单
条新闻打个情绪分**，不需要 tool calls / multi-turn / 流式，langchain 是负担。

所以：

- 用 `typing.Protocol`（不是 ABC）—— 任何实现 `chat` / `chat_json` 两个方法的类都
  自动满足 `LLMClient`，不强求继承
- 默认 `AnthropicClient` 直接调 `anthropic.Anthropic` 官方 SDK 的 `messages.create()`
- 切其它 provider 的标准动作：新增一个 implementer class，在 `_PROVIDERS` 注册一行

代价：将来加 OpenAI / DeepSeek / Kimi 时各自要写 ~50 行的 client class（vs
TradingAgents-astock 一行 `from langchain_openai import ChatOpenAI`）。但因为我们的
Protocol 极小（只 2 个方法），每个 implementer 也就 50 行 —— 比扛 langchain 整套
框架划算。

### 2.2 结构化输出 = 让 LLM 吐 JSON + Pydantic 解析（不用 tool_choice）

TradingAgents-astock 的 `agents/utils/structured.py` 用 langchain 的
`with_structured_output()`（底层走 OpenAI function_calling 或 Anthropic tool_use）。
我们简化为：

- system prompt 里强制要求「严格按 JSON 格式输出，不要任何其它文字」
- 用正则抠最长 `{...}` 块兜底（应对 LLM 偶尔加 Markdown 代码块 / 前后废话）
- Pydantic `model_validate` 解析失败 → `LLMClientError` 抛上去

实测 3 种 LLM 输出格式（纯 JSON / Markdown 包装 / 前后带废话）全部能正确解析。

### 2.3 缓存键 = news_id（url 优先 / title hash 后备）

烧钱大头是 LLM 调用，必须严格去重：

- 有 `url` 的新闻：用 `"u:" + sha1(url)[:16]` 做 id（url 是 stable 的）
- 没 url（爬虫漏字段）：用 `"t:" + sha1(ticker|date|title)[:16]`

落盘：`data_cache/llm_factor/{ticker}-{date}.json`，结构 `{news_id: {sentiment,
confidence, reason, title, source}}`。同一 (ticker, date) 多条新闻共享一个文件。

**安全审计**：缓存里**不存** API key、不存原始 prompt、不存 raw LLM response（只存
最终情绪分 + 必要元数据）。测试断言 `"ANTHROPIC_API_KEY" not in serialized`。

### 2.4 聚合 = confidence 加权 mean

单条新闻 → `NewsSentimentOutput(sentiment, confidence, reason)`，sentiment 走五级
（-1 / -0.5 / 0 / +0.5 / +1）映射到 [-1, +1] 连续分。

对每个 (ticker, T) 聚合：

```
sentiment(T) = Σ(sent_i * conf_i) / Σ(conf_i)  for i in 窗口 [T-lookback+1, T]
```

- `confidence < 1e-3` 时被截断到 1e-3，防全 0 权重崩溃
- 没有新闻 / 全部失败 → 返回 NaN（**不强行填 0**，对齐 P3a 「因子层故意保留 NaN」纪律）
- 默认 `lookback=1`（仅当日新闻），可调成 3-7（短期记忆，缓解 A股 新闻稀疏）

### 2.5 prompt 设计 —— 借鉴 social_media_analyst 但瘦身

TradingAgents-astock 的 `social_media_analyst.py` 是写报告型，要 1000+ 字 Markdown
+ tool calls + BUY/HOLD/SELL。我们的场景只要 1 个 JSON，所以 prompt 要：

- 保留它的 A股 情绪分析框架（散户 > 60% / 政策市 / 板块联动 / 五级情绪）
- 砍掉 tool calls / 长报告 / 交易建议
- 强制 JSON 输出 + 信息不足时 confidence=0、sentiment=0 的兜底规则

content 截断 2000 字符 —— 单条新闻一般都够，防止极长稿件吃掉 8K+ token。

### 2.6 默认禁用 (`ENABLE_LLM_FACTOR=0`) —— 不烧 token，不破 Stage 1

注册到 `default_factors()` 但只在 env var = `1`/`true`/`yes`/`on` 时启用。这样：

- Stage 1 用户 / CI 跑 `run_direction()` 不会因缺 API key 报错
- 旧的 25 因子完全照旧（62 旧测试都通过）
- Stage 2 用户显式开启时多 1 列 LLM 情绪因子，下游模型 / 回测一行不用改

### 2.7 默认模型 = Claude Haiku 4.5（性价比）

因子打分是「对每条新闻独立判断 + 输出 30 字内 JSON」的简单任务，不需要 Opus 的
推理深度。默认 `claude-haiku-4-5`，可通过 env var `LLM_MODEL` 覆盖（如改成
`claude-opus-4-7` 做对比实验）。

---

## 3. 怎么用

### 3.1 启用 LLM 因子（端到端）

```bash
# 1. 装依赖（如果还没）
uv sync

# 2. 设环境变量
export ANTHROPIC_API_KEY=sk-ant-xxxxx          # 必需
export ENABLE_LLM_FACTOR=1                      # 开关
export LLM_MODEL=claude-haiku-4-5               # 可选，默认就是这个
export LLM_PROVIDER=anthropic                   # 可选，默认就是这个
# 中转站 / 自建代理（可选）
# export ANTHROPIC_BASE_URL=https://your-relay.example.com

# 3. 跑流水线 —— factor frame 自动多 1 列 news_sentiment
uv run python scripts/run_pipeline.py
```

### 3.2 单独用因子（debug / smoke test）

```python
from astock_quant.factors.llm_factor import LLMNewsSentiment
from astock_quant.contracts import NewsItem
from datetime import date

fac = LLMNewsSentiment(lookback=1)  # 默认 Anthropic / haiku 4.5
news = {
    "600519": [
        NewsItem(ticker="600519", date=date(2024, 1, 3),
                 title="茅台业绩超预期增长", content="...", url="..."),
    ]
}
# panel 是 P2 build_price_panel() 的产出
s = fac.compute(panel, news=news)
# s: pd.Series, MultiIndex=(date, ticker), 范围 [-1, +1]
```

### 3.3 测试模式（mock client，零 API 消耗）

```python
class MyMockClient:
    provider = "mock"
    model = "v1"
    def chat(self, *a, **k): raise NotImplementedError
    def chat_json(self, messages, schema, **k):
        return schema(sentiment=0.5, confidence=0.8, reason="test")

fac = LLMNewsSentiment(client=MyMockClient(), cache_dir="/tmp/test")
```

### 3.4 切换 provider（未来扩展）

```python
# 默认（env var 控制）
client = make_llm_client()

# 显式
client = make_llm_client("anthropic", model="claude-opus-4-7")

# Stage 2 后续加 OpenAI / DeepSeek 时：
# 1. 在 client.py 实现 OpenAIClient class
# 2. _PROVIDERS["openai"] = OpenAIClient
# 3. make_llm_client("openai", model="gpt-5-mini") —— 调用方完全无感
```

---

## 4. 测试

### 4.1 mock 单元测试 —— 23 项全过

```
tests/test_llm_factor_with_mock_client.py::test_compute_basic_alignment_and_aggregation PASSED
tests/test_llm_factor_with_mock_client.py::test_cache_hits_on_second_compute PASSED
tests/test_llm_factor_with_mock_client.py::test_cache_file_layout PASSED
tests/test_llm_factor_with_mock_client.py::test_compute_returns_nan_when_llm_fails PASSED
tests/test_llm_factor_with_mock_client.py::test_compute_returns_nan_when_no_api_key PASSED
tests/test_llm_factor_with_mock_client.py::test_compute_with_empty_news PASSED
tests/test_llm_factor_with_mock_client.py::test_compute_with_empty_panel PASSED
tests/test_llm_factor_with_mock_client.py::test_confidence_weighted_mean PASSED
tests/test_llm_factor_with_mock_client.py::test_lookback_window PASSED
tests/test_llm_factor_with_mock_client.py::test_registry_disabled_by_default PASSED
tests/test_llm_factor_with_mock_client.py::test_registry_enabled_with_env_var PASSED
tests/test_llm_factor_with_mock_client.py::test_make_llm_client_missing_api_key PASSED
tests/test_llm_factor_with_mock_client.py::test_make_llm_client_unknown_provider PASSED
tests/test_llm_factor_with_mock_client.py::test_mock_satisfies_llm_client_protocol PASSED
tests/test_llm_factor_with_mock_client.py::test_parse_json_to_schema_clean PASSED
tests/test_llm_factor_with_mock_client.py::test_parse_json_to_schema_markdown_wrapped PASSED
tests/test_llm_factor_with_mock_client.py::test_parse_json_to_schema_with_chatter PASSED
tests/test_llm_factor_with_mock_client.py::test_parse_json_to_schema_raises_on_garbage PASSED
tests/test_llm_factor_with_mock_client.py::test_parse_json_to_schema_raises_on_empty PASSED
tests/test_llm_factor_with_mock_client.py::test_news_id_uses_url_when_present PASSED
tests/test_llm_factor_with_mock_client.py::test_news_id_falls_back_to_title_hash PASSED
tests/test_llm_factor_with_mock_client.py::test_normalize_ticker_pads_zeros PASSED
tests/test_llm_factor_with_mock_client.py::test_news_fetcher_injection PASSED

============================== 23 passed in 0.18s ==============================
```

### 4.2 全套兼容性 —— 85/85 全过

```
tests/test_align_xy_determinism.py            5 passed
tests/test_backtest_engine.py                18 passed
tests/test_constraints_astock.py             18 passed
tests/test_direction_model_roundtrip.py       6 passed
tests/test_factors_no_lookahead.py            4 passed
tests/test_llm_factor_with_mock_client.py    23 passed   ← 新增
tests/test_splits_purge.py                   11 passed
============================== 85 passed in 51.96s ==============================
```

旧的 62 个测试一项不破。

### 4.3 ruff —— clean

```
$ uv run ruff check astock_quant/ tests/
All checks passed!
```

### 4.4 Stage 1 流水线 —— 默认行为完全不变

```python
import os; os.environ.pop("ENABLE_LLM_FACTOR", None)
from astock_quant.factors.registry import default_factors
facs = default_factors()
assert len(facs) == 25  # 与 P3a 完全一致
assert "news_sentiment" not in [f.name for f in facs]
```

✓ 确认默认 pipeline 25 因子集合不变。

---

## 5. 烟雾验证（mock，无 API 调用）

构造 4 日 × 2 票的迷你 panel + 3 条新闻 + rule-based mock client：

```
input news:
  600519 2024-01-03 "茅台业绩超预期"    → mock 打 +1.0
  600519 2024-01-04 "茅台监管处罚暴雷"  → mock 打 -1.0
  000858 2024-01-02 "五粮液中性消息"    → mock 打  0.0

LLMNewsSentiment(lookback=1).compute(panel, news=...) 输出：

date        ticker
2024-01-02  600519    NaN     ← 无新闻
            000858    0.0     ← 中性
2024-01-03  600519    1.0     ← +1
            000858    NaN
2024-01-04  600519   -1.0     ← -1
            000858    NaN
2024-01-05  600519    NaN
            000858    NaN
```

- 索引与 panel.index 严格对齐
- 聚合分数符合规则
- 二次 compute() 命中缓存：mock 调用次数从 3 → 不再增加，pd.testing.assert_series_equal 通过

---

## 6. 已知 TODO（Stage 2 后续）

1. **真实 API smoke test**：留了 mock 测试，没自动跑真实 LLM 端到端（用户需要时
   `export ANTHROPIC_API_KEY=... && uv run python -c "..."` 即可）。下个阶段
   P7 对比验证时可顺便跑一次。
2. **多 provider 实现**：当前只有 `AnthropicClient`。计划支持 OpenAI / DeepSeek /
   Kimi —— 各 ~50 行 implementer 即可，调用方无感。等 P7 有对比需要再加。
3. **lookback 调优**：默认 1，没有 grid search 过最优值（依赖真实数据）。P7 对比
   验证可以做 1/3/7 三档对照。
4. **新闻源扩展**：目前消费 `get_news`（akshare 个股新闻，东财源）。研报 / 公告
   / 大盘新闻是后续扩 P2 时再接入 —— DataSource Protocol 已留位（`get_news` 的
   ticker=None 是大盘）。
5. **prompt 调优**：现在是单一打分 prompt。未来可分政策 / 业绩 / 监管等子类，参考
   TradingAgents-astock 的 7 分析师细分（lockup_watcher / policy_analyst /
   hot_money_tracker 等）。

---

## 7. 给后续阶段的提醒

### 给 P7 对比验证（verifier）

1. **加 LLM 因子前后对比**：环境变量切换非常简单 —— `ENABLE_LLM_FACTOR=0 / 1`
   重跑同一个 `run_direction()` pipeline，看 AUC / 回测指标差异。
2. **样本可比性**：注意 LLM 因子在历史早期可能因为新闻源覆盖少 → 大量 NaN。LightGBM
   原生支持 NaN，但要看「有效样本量」别从 25k 掉到 5k。
3. **token 成本**：每条新闻 ~ 500-800 input tokens + 50 output tokens；haiku 4.5
   按当时单价估算成本，建议小 universe + 短时段先跑。

### 给 P8 审查（code-reviewer）

重点看：

- `llm/client.py` 的 `parse_json_to_schema` —— 正则抠 JSON 的兜底逻辑是否有边界 case
- `factors/llm_factor.py` 的 `_aggregate_to_dates` —— lookback 窗口对当 lookback > 1
  跨 ticker 边界的处理（已通过 lookback=3 测试，确认不串 ticker）
- 缓存文件是否真的不存敏感信息（测试里有断言，但 review 时再确认 schema）
- env var 处理是否有大小写 / 空格的边界 case

### 给 explainer

报告 06 的「人话版」核心要点：

- LLM 因子 = 让 Claude 看每条新闻、给一个 -1 ~ +1 的情绪分，再把当天的多条加权平均
- 跟「茅台今天涨没涨」「PE 高不高」这些数字因子是平等关系，喂给 LightGBM 一起决策
- 默认关掉，避免新人不知道烧 token；想试的话设两个环境变量就行
- 缓存严格去重：同一条新闻一辈子只调一次 LLM

---

## 附：交付物清单

- `astock_quant/llm/__init__.py` —— LLM 包入口（含 `DeepSeekClient` export，P7 补）
- `astock_quant/llm/client.py` —— `LLMClient` Protocol + `AnthropicClient` + 工厂 + JSON 解析 helper（末尾 lazy 注册 `deepseek` provider）
- `astock_quant/llm/schemas.py` —— `NewsSentimentOutput`
- `astock_quant/llm/prompts.py` —— A股 情绪分析 prompt 模板
- `astock_quant/llm/deepseek.py` —— DeepSeek 客户端（OpenAI 兼容协议；P7 启用，2026-05-16 补）
- `astock_quant/factors/llm_factor.py` —— 从 stub → 实现 `LLMNewsSentiment`
- `astock_quant/factors/registry.py` —— 1 行 import + env var 开关
- `pyproject.toml` —— 加 `anthropic>=0.40`（DeepSeek 复用 `httpx`，是 anthropic SDK 的传递依赖，**不加新顶层依赖**）
- `tests/test_llm_factor_with_mock_client.py` —— 29 项 mock 测试（24 P6 + 5 DeepSeek，P7 补）
- 缓存目录：`data_cache/llm_factor/`（运行时创建，gitignore）
- 本文档：`P6-LLM因子.md`

---

## 8. 多 provider 切换：DeepSeek 使用（P7 启用，2026-05-16 补）

### 8.1 为什么追加 DeepSeek

P7 对比验证要在更大 universe / 更长时段上做「加 LLM 因子前 vs 后」回测，新闻打分
量会上一个数量级。DeepSeek API 价格比 Anthropic 便宜约 **~10×**（同等数量级 token
成本下），所以 P7 默认切到 DeepSeek 跑长样本。

P6 设计的多 provider 抽象正好兑现：新增 1 个 `DeepSeekClient` implementer + 工厂
注册 1 行，**调用方完全无感**（`LLMNewsSentiment` 不动、prompt 不动、cache 不动、
聚合不动、registry 开关不动）。

### 8.2 一键切换

```bash
# 1. 设环境变量
export DEEPSEEK_API_KEY=sk-xxxxx          # 必需
export LLM_PROVIDER=deepseek               # 切换 provider（默认仍是 anthropic，向后兼容）
export ENABLE_LLM_FACTOR=1                 # registry 开关（同 P6）

# 可选：
# export LLM_MODEL=deepseek-v4-pro         # 默认就是 -v4-pro；便宜版用 deepseek-v4-flash
# export DEEPSEEK_BASE_URL=https://your-relay   # 自建代理 / 中转站

# 2. 跑流水线 —— FactorFrame 自动多 1 列 news_sentiment（由 DeepSeek 打分）
uv run python scripts/run_pipeline.py
```

### 8.3 价格对比（粗略数量级，2026-05 价目）

| Provider | 模型 | 输入价（$/M tok） | 输出价（$/M tok） | 备注 |
|---|---|---:|---:|---|
| Anthropic | claude-haiku-4-5 | ~$1 | ~$5 | P6 默认 |
| Anthropic | claude-sonnet-4-6 | ~$3 | ~$15 | 中端 |
| DeepSeek | deepseek-v4-flash | ~$0.07 | ~$0.28 | 最便宜 |
| **DeepSeek** | **deepseek-v4-pro** | **~$0.27** | **~$1.10** | **P7 默认** |

P7 单条新闻打分 ~ 500-800 input + 50 output tokens；deepseek-v4-pro 单条成本约
$0.0002，1 万条新闻 ~ $2，比 Haiku 同体量低约 1 个数量级。具体单价以 DeepSeek
官方为准。

### 8.4 代码示例 —— 切换 provider，调用方完全相同

```python
from astock_quant.factors.llm_factor import LLMNewsSentiment
from astock_quant.llm import make_llm_client

# 显式构造 DeepSeek client
client = make_llm_client("deepseek")  # 走 env DEEPSEEK_API_KEY，默认 model=deepseek-v4-pro

# 或注入到因子（便于 A/B 测试不同 provider）
fac = LLMNewsSentiment(client=client, lookback=1)
s = fac.compute(panel, news=news_dict)
```

### 8.5 实现要点（不引 langchain / 不加新顶层依赖）

- DeepSeek API 完全 OpenAI 兼容（`POST /v1/chat/completions`，body 同 OpenAI schema）
- 直接 `httpx.Client` 调用 —— `httpx` 是 `anthropic` SDK 的传递依赖，已经在装好的环境里，**不加新顶层依赖**
- JSON 模式：`response_format={"type": "json_object"}`（DeepSeek 官方支持，与 OpenAI 一致）
- 错误透传：`_safe_extract_error` 把 `{"error": {"message": "model_not_found"}}` 这种 DeepSeek 错误透传，方便用户排错（设错 `LLM_MODEL` 时立刻看到具体模型名问题）
- API key 安全：与 Anthropic 同模式（env var 唯一读取点 / 缺失 fail loud / 错误信息不泄漏 key），mock 测试 `test_deepseek_4xx_extracts_error_message` 有断言

### 8.6 工厂行为速查

| 调用 | 行为 |
|---|---|
| `make_llm_client()` | 走 `LLM_PROVIDER` env var；缺省默认 `anthropic`（向后兼容 P6） |
| `make_llm_client("anthropic")` | Anthropic Claude，需要 `ANTHROPIC_API_KEY` |
| `make_llm_client("deepseek")` | DeepSeek，需要 `DEEPSEEK_API_KEY`，默认 `deepseek-v4-pro` |
| `make_llm_client("foobar")` | `LLMClientError: 不支持的 LLM provider`，info 列出已支持的 |
| 缺对应 API key | `LLMClientError`，明确告诉用户 export 哪个 env var |

---

## 9. Pipeline 接线集成（P7 修复，2026-05-16 补）

### 9.1 P7 揭示的问题：bit-identical 回测

P6 验证了 LLM 因子**独立可调用**（cache + smoke + mock 测试都用 `compute(panel, news=dict)` 直传字典）。但 P7 verifier-2 跑 30 票全量回测时发现：

- baseline AUC = 0.5131 / treatment AUC = 0.5131 / **bit-identical**（小数 16 位完全相同）
- `n_features=26` 但 LLM 因子那列 importance = 0
- LightGBM 完全忽略了 LLM 因子

### 9.2 根因：`compute_factor_frame` 没有 news 路由参数

`run_direction()` 调 `compute_factor_frame(panel, mf, fin)` —— 传了 moneyflow / financials，**但没传任何 news 来源**。

`LLMNewsSentiment.compute()` 拿不到 `news` 也拿不到 `news_fetcher` → 当成「无新闻」处理 → 全 NaN → 该列被 LightGBM 当成无信息列。**LLM 因子从未真正被 pipeline 调用过 LLM API**。

这是 P6 的接线遗漏：LLM 因子的运行期注入路径（`kwargs["news_fetcher"]`）从未被 pipeline 入口接通。

### 9.3 修法（5 处 / 不破 P6 任何承诺）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `astock_quant/data/dataset.py::prepare_stage1_data` | 返回 dict 加 `"source": AStockSource()` —— 之前内部用完就丢，现在让上层复用同一实例拉新闻 |
| 2 | `astock_quant/factors/registry.py::compute_factor_frame` | 签名加 `news_fetcher: Callable | None`，透传给 `fac.compute(..., news_fetcher=news_fetcher)`。量价/财务/资金流因子 compute 用 `**kwargs` / `**_` 吸收无关 kwarg，**完全无影响** |
| 3 | `astock_quant/pipeline/run_direction.py` | 取 `source = data["source"]` + 注入 `news_fetcher=source.get_news` |
| 4 | `astock_quant/factors/llm_factor.py:290` | `max_tokens=256 → 512`（附带 fix：P7 Step 1 smoke 揭示中文 reason 在 256 时 35% 被截断 → JSON 解析失败 → 跳过；即使接线通了截断率也得修） |
| 5 | 测试 + 本节文档 | 见 9.4 |

### 9.4 守护用 2 个集成测试

`tests/test_llm_factor_with_mock_client.py` §10 节追加：

1. **`test_compute_factor_frame_wires_news_fetcher_to_llm`** —— 验证 wiring 通了
   - 构造 2 ticker × 5 day panel + mock news_fetcher（每 ticker 2 条新闻 = 4 条）+ mock LLM client
   - 调 `compute_factor_frame(panel, factors=[LLMNewsSentiment(client=mock)], news_fetcher=mock_fetcher)`
   - 断言：`fetcher.call_count ≥ 1`、`news_sentiment` 列 **≥ 3** 个非 NaN
   - **这条测试如果未来回退**（有人把 news_fetcher 透传删掉），P7 bit-identical 会立刻在 CI 复现

2. **`test_compute_factor_frame_without_news_fetcher_llm_returns_nan`** —— 守护退路
   - `LLMNewsSentiment()`（无 client、无 fetcher）+ 不传 news_fetcher
   - 断言：`news_sentiment` 列全 NaN、不抛异常、索引仍与 panel 对齐
   - 守住「老调用方不传 news_fetcher 不能崩」的兼容性承诺

### 9.5 verifier-2 重跑预期

修完后 verifier-2 用真实 DeepSeek API 重跑 P7 时应能看到：
- LLM 因子有效率从 35% 显著上升（512 的 reason 不再被截断 + wiring 通了的双重效果）
- `treatment AUC ≠ baseline AUC`（具体差多少由市场 / 新闻信号强度决定，不强求正向）
- LLM 因子 importance > 0

如果重跑后仍 bit-identical，那是另一类问题（新闻覆盖太稀 / prompt 校准等），**不再是 wiring 问题**。
