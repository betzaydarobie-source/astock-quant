# Stage 2 收尾说明 —— LLM 情绪因子扩展 + 团队重组 + 诚实的 negative finding

> 2026-05-16 · 多角色出品（factor-engineer 阶段中替换 / llm-factor-engineer / factor-integrator / verifier-2 / code-reviewer / auditor）
>
> Stage 2 把 P3a 留的 LLM 因子插槽真填实 + 跑了对照实验。**结论是诚实的 negative finding**：基础设施全过、LLM 真接通了 178 次真实调用花了 ¥0.0753，但 LightGBM 给 news_sentiment 打的 importance = 0.0，AUC delta = 0 bps。**根因是 akshare 新闻源对 4 年训练窗口的覆盖率 < 1%，不是 bug**。
>
> **lead 决策：用户选 A 接受现状收尾**（B 短窗口大 universe / C 换公告替代新闻 / D 付费数据源 留给 Stage 3+）。本文档既是 Stage 2 的交付清单 + 修法记录 + 团队事件复盘，也是 Stage 3 启动前的债务列表。

## 总览

Stage 2 = P6（LLM 因子模块）+ P7（对照验证）+ P8（深审 + 修复），中间穿插基础设施扩建（dotenv / DeepSeek provider / wiring）+ 一次团队重组。

| # | 来源 | 严重性 | 一句话 | 状态 |
|---|---|---|---|---|
| P6 | Stage 2 启动 | Feature | LLMNewsSentiment 实装 + Anthropic 默认 + Protocol 多供应商 | ✅ auditor PASS（85/85 测试，scope 严守）|
| Infra-1 | P6 → P7 之间 | Feature | python-dotenv 集成 + `__init__.py` load_dotenv + tests/conftest LLM env 隔离 | ✅ dotenv 集成审核 PASS |
| Infra-2 | P6 → P7 之间 | Feature | DeepSeek provider 实装（HTTP + 错误透传不带 key + httpx mock 测试）| ✅ DeepSeek 复审 PASS |
| P7-wiring | P7 v1 失败暴露 | Critical | `run_direction → compute_factor_frame → fac.compute` 5 处 wiring 接线缺失 → LLM 全 NaN → bit-identical | ✅ 5 处真接通 + 2 守护测试 + auditor PASS |
| P7 | Stage 2 验证 | Verification | 3 票 smoke + 30 票全量 A/B 对照（DeepSeek 真实调用 178 次）| ✅ p7_pass=true，但 `llm_factor_useful=false` |
| H1 | P8 review | High | `lookback` 用自然日算窗口（panel 是交易日），跨周末窗口缩水 + Sat 新闻丢失 | ✅ 改交易日 slice + 周末就近映射 + 未来新闻丢弃 + 1 新测试 |
| H2 | P8 review | High | `_news_id` 只用 title hash → 5-10% 缓存意外失效 | ✅ 加 content[:500] 进 hash + cache_stats 可观测性 + 2 新测试 |
| 团队 | 阶段中 | Process | factor-engineer 8 次装死（干完不发消息 / idle 心跳代替汇报）| ✅ shutdown + 拆 3 专职新人 |

**测试：97 / 97 PASS**（62 旧 + 35 新增） · **ruff：clean** · **关 LLM 时 metrics 字节级不漂移**（AUC=0.5131337782587783 与 Stage 1 完全一致）。

---

## P6：LLMNewsSentiment 因子实装

### 问题

P3a 留的 stub `factors/llm_factor.py`（11 行占位），需要在 P3a 时预留好的 LLM 因子插槽里真实现「让 AI 读新闻、把新闻转成情绪分」的因子，**和 25 个量价/财务/资金流因子完全平级**喂给 LightGBM。

### 决策：选「方案 A 瘦身版 + Protocol 多供应商」

理由：
- 研读 TradingAgents-astock 的 `llm_clients/`，发现它建立在 `langchain_anthropic` / `langchain_openai` 之上 —— 我们的场景只是「给单条新闻打个情绪分」，不需要 tool calls / multi-turn / 流式，langchain 是**负担**
- 用 `typing.Protocol`（不是 ABC）—— 任何实现 `chat` / `chat_json` 两个方法的类都自动满足 `LLMClient` 契约，**不强求继承**
- 默认 Anthropic Claude（Haiku 4.5，性价比）；多供应商可切换 env var `LLM_PROVIDER=openai/deepseek/kimi`，每个 implementer ~ 50 行

### 改动

新增 `astock_quant/llm/` 包（4 个文件）：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 对外暴露 `LLMClient` / `make_llm_client` / `NewsSentimentOutput` |
| `client.py` | **核心** —— `LLMClient` Protocol + `AnthropicClient` 默认实现 + `make_llm_client` 工厂 + `parse_json_to_schema` JSON 解析 helper |
| `schemas.py` | LLM 结构化输出 schema —— `NewsSentimentOutput`（sentiment / confidence / reason）|
| `prompts.py` | A股 新闻情绪打分的 system / user prompt 模板（中文，借鉴 social_media_analyst 的五级情绪框架）|

填实 `astock_quant/factors/llm_factor.py`（414 行）：
- `LLMNewsSentiment(BaseFactor)` 继承基类，产出 `pd.Series(MultiIndex=(date, ticker))`，**和量价因子完全平级**
- 五级情绪映射（-1 / -0.5 / 0 / +0.5 / +1）→ confidence 加权聚合 → 缓存键 `news_id`（url 优先 / title hash 后备，P8 H2 后再 + content）
- 缓存落 `data_cache/llm_factor/{ticker}-{date}.json`，**严格去重避免烧 token**
- 默认禁用 `ENABLE_LLM_FACTOR=0`，**不烧 token，不破 Stage 1 字节级一致**

改 `astock_quant/factors/registry.py`：加 `_llm_factor_enabled()` env var helper + 末尾按条件追加 `LLMNewsSentiment(lookback=1)`。

改 `pyproject.toml`：加 `anthropic>=0.40`（实测装 0.102.0）。

### 测试

`tests/test_llm_factor_with_mock_client.py`（23 个测试）：
- mock LLM 客户端不烧 token / 因子产出对齐 panel.index / 缓存命中（二次跑 0 次 LLM 调用） / 缓存文件 schema 校验 + **不泄漏 API key**（`assert "ANTHROPIC_API_KEY" not in serialized` + `assert "sk-" not in serialized`）
- LLM 失败 / 缺 key / 无新闻 / 空 panel → 全 NaN 不抛异常
- confidence 加权聚合数学正确性 / lookback 窗口聚合范围 / Protocol runtime_checkable 校验
- registry env var 三档（默认 25 / 启用 26 / 显式关 25）/ JSON 解析 4 种边界形式（纯 JSON / Markdown 包装 / 前后废话 / 垃圾 raise）

### 验证

auditor P6 PASS（85/85 测试，scope 严守只动 7 个文件，AUC 等 14 指标 bit-exact 与 Stage 1 一致，6 个工程亮点点赞）。

---

## Infra-1：python-dotenv 集成

### 问题

P7 验证时需要 `.env` 配置 `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `LLM_PROVIDER` 等，但：
- 手动 `export` 易遗漏
- CI / 生产环境需要禁用自动 load
- 测试用例本地有 `.env` + CI 没有 → 行为不一致

### 决策：包级 `__init__.py` 自动 load + `tests/conftest.py` autouse fixture 强制隔离

理由：
- 开发机有 `.env` + CI/test 必须基线干净 —— 两个矛盾的解决方式
- `try/except ImportError` 让 `python-dotenv` 缺失不报错（`pyproject.toml` 加 `python-dotenv` 为依赖）
- `tests/conftest.py` 用 autouse fixture 把 6 个 LLM 相关 env var 全 `monkeypatch.delenv`，**每个测试基线干净**

### 改动

`astock_quant/__init__.py`：
```python
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass
```

`tests/conftest.py`（新增）：autouse fixture 隔离 `ENABLE_LLM_FACTOR` / `LLM_PROVIDER` / `LLM_MODEL` / `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` 共 7 个 env var。

`pyproject.toml`：加 `python-dotenv>=1.0`。

### 验证

dotenv 集成审核 PASS（auditor 评「教科书写法」）。

---

## Infra-2：DeepSeek provider 实装

### 问题

P7 验证用 Anthropic 默认成本较高，且中国大陆访问需要 base_url 代理；用户希望优先试 DeepSeek（性价比 + 本地访问无障碍）。

### 决策：自写 `DeepSeekClient` 实现 `LLMClient` Protocol —— 50 行实装 + httpx mock 测试

### 改动

新增 `astock_quant/llm/deepseek.py`（~ 200 行）：
- `DeepSeekClient` 实现 `chat` / `chat_json`，直接走 OpenAI 兼容 HTTP（`/v1/chat/completions`）
- 默认模型 `deepseek-v4-pro`，env var `DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL`（可选代理）
- **错误透传不带 key**：`test_deepseek_4xx_extracts_error_message` 末尾 `assert "fake-test-key" not in msg` 守住

注册到 `client.py:_PROVIDERS["deepseek"] = DeepSeekClient`（文件末尾循环 import 安全模式，加了注释解释为啥放末尾）。

### 测试

httpx mock 覆盖：200 OK / 4xx 错误 / JSON 解析鲁棒（与 Anthropic 路径一致的 parse_json_to_schema）。

### 验证

DeepSeek provider 审核 + 复审两轮全 PASS。

---

## P7-wiring：5 处接线缺失 + 命门测试守护

### 问题

P7 v1 跑完发现 AUC bit-identical 到小数 16 位，且**DeepSeek 真实调用次数 = 0**。排查发现 P6 实装时漏了 5 处接线：

```
run_direction (pipeline 入口)
   ↓ ❌ 没把 news_fetcher 传下去
compute_factor_frame (factors/registry.py)
   ↓ ❌ 函数签名没接收 news_fetcher
fac.compute(panel, ...)
   ↓ news_fetcher 永远是 None
LLMNewsSentiment.compute
   ↓ kwargs.get("news_fetcher") 总返回 None
   ↓ → 拿不到新闻 → 全 NaN → LLM 一次没被调用 → 因子列全 NaN → LightGBM 忽略 → bit-identical
```

**这是个纯 bug**，AI 设备插好了但电源线断了。

### 决策：5 处接线全修 + max_tokens 256→512 顺便修 Step 1 暴露的截断 + 2 守护测试 + P6 §9 文档复盘

### 改动

| # | 文件 | 改动 |
|---|---|---|
| 1 | `data/dataset.py` | `prepare_stage1_data` 返回值加 `"source"` key |
| 2 | `factors/registry.py` | `compute_factor_frame` 签名加 `news_fetcher: Callable | None = None` 形参 + 透传给 `fac.compute()` |
| 3 | `pipeline/run_direction.py` | `news_fetcher=source.get_news` 注入 |
| 4 | `factors/llm_factor.py` | `compute()` 从 `kwargs.get("news_fetcher")` 取，配合自身 `self._news_fetcher` fallback |
| 5 | `factors/llm_factor.py:294` | `max_tokens` override 256→512（修 v1 Step 1 暴露的 35% 中文 reason 截断）|

加 `P6-LLM因子.md §9` 文档：症状 + 根因 + 5 处修法表 + 量价无影响声明 + verifier-2 重跑预期。

### 测试

`tests/test_llm_factor_with_mock_client.py` 新增 2 个集成测试：

| # | 测试 | 守的命门 |
|---|---|---|
| 1 | `test_compute_factor_frame_wires_news_fetcher_to_llm` | wiring happy path：fetcher 被调用 ≥ 1 次 + 2 ticker 都被 fetch + news_sentiment ≥ 3 个非 NaN。**docstring 明示「如果未来回退把 news_fetcher 透传删掉，P7 bit-identical 会立刻在 CI 复现」** |
| 2 | `test_compute_factor_frame_without_news_fetcher_llm_returns_nan` | degraded path：无 client + 无 fetcher + 没 LLM key → 全 NaN + 不抛异常 |

**91 旧测试全过**证明 12 个非 LLM 因子的 `compute(**kwargs)` 用 `**kwargs` / `**_` 吸收 `news_fetcher` 无影响。

### 验证

P7 wiring 审核 PASS（auditor 评「把根因预防写进测试」）。

---

## P7：30 票 A/B 对照验证 —— 诚实的 negative finding

### 跑了什么

| 步骤 | 配置 | 真实 LLM 调用 | 花费 |
|---|---|---:|---:|
| v1 smoke | 3 票（旧版 wiring 不通） | 26 | ¥0.01 |
| v2 smoke | 3 票（run_direction 路径，wiring 已修）| 9 | ¥0.004 |
| v2 Treatment B | 30 票全量（DeepSeek deepseek-v4-pro，max_tokens=512）| 178 | ¥0.0753 |
| **合计** | | **213** | **~¥0.09** |

远低于预算 ¥10。

### 对照表

| 指标 | Baseline A v2（25 因子，关 LLM）| Treatment B v2（26 因子，开 LLM）| Delta |
|---|---:|---:|---:|
| n_features | 25 | 26 | +1 |
| train_size | 25,283 | 25,283 | 0 |
| valid_size | 5,780 | 5,780 | 0 |
| **AUC** | **0.5131337782587783** | **0.5131337782587783** | **0.0 bps** |
| **Accuracy** | **0.5333910034602076** | **0.5333910034602076** | **0.0** |
| **Log loss** | **0.6905634407838911** | **0.6905634407838911** | **0.0** |
| **news_sentiment importance** | — | **0.0** | — |

**bit-identical 到小数 16 位**。

### 诚信解读

**LLM 因子真本事 verdict：在当前配置下无效（negative finding）**。

不是因子逻辑错误（wiring 修了 + 178 次真实调用 + JSON 解析正确 + 缓存机制工作 + 因子值本身有语义），**是新闻覆盖率问题**：

```
akshare stock_news_em 限制：
  每次调用返回最近 ~20 条（东财默认翻页），全部集中在 2026-04 ~ 2026-05（最近 6 周）
  → 30 票 × ~20 条 = ~600 条新闻，占 2022-2026 约 1,000 个交易日的 < 3%
  → 训练集 25,283 样本中，news_sentiment 非 NaN 的比例 < 1%
  → LightGBM 在 99%+ NaN 的列上 importance=0，这是正确行为
```

**不能说「LLM 因子没用」**：数据覆盖不够，没有公平给它机会参与决策。**也不装作「LLM 因子初步有效」**：importance=0 是事实，AUC bit-identical 是事实，¥0.09 也是事实。

### v1 vs v2 区别

| 维度 | v1（上午）| v2（下午）|
|---|---|---|
| wiring 状态 | ❌ 未接线（bug）| ✅ 5 处修复，全通 |
| 实际 LLM 调用 | **0** 次 | **178** 次 |
| bit-identical 根因 | **代码 bug**（接线缺失）| **数据源限制**（akshare 硬上限）|
| 能否判断 LLM 效果 | 否（bug 导致）| 否（数据不够，fair test 未完成）|

**两次都失败但失败类型完全不同** —— v1 能修，v2 改不了（除非换数据源）。这种诚信汇报正是 auditor 评「P3a 重做之后诚信深度最高的一份阶段验证报告」的原因。

### 验证

P7 报告 v2 审核 PASS（auditor 评「数字诚实 + 完整 + 不夸大」，6 条核对全过）。

---

## H1：lookback 自然日 → 交易日

### 问题

P8 code review 抓出：`_aggregate_to_dates` (`llm_factor.py:246-248`) 用 `pd.Timedelta(days=offset)` 算窗口，但 panel 上的 date 是**交易日**（A股 周一~周五，节假日跳）：

```python
for offset in range(self.lookback):
    t_minus = pd.Timestamp(t) - pd.Timedelta(days=offset)
    samples.extend(by_date.get(t_minus.date(), []))
```

**Bug 表现**：
- `lookback=3` 配周一的 T → 窗口 = {周一, 周日, 周六}，但**周六/周日 panel 上不存在交易日**
- 即使周末有新闻，`by_date.get(Sat)` 也拿不到（by_date 按 item.date 分桶，没有「就近映射」）→ **Sat 新闻被静默丢失**
- 周一 `lookback=3` 与 `lookback=1` 对周一行的产出**完全等价**
- 旧测试 `test_lookback_window` 所有新闻都放在 1/1（周一）+ panel 全是工作日 —— **碰巧不触发自然日 vs 交易日的不一致**，所以测试过但语义错

**为什么算 High**：P7 报告 §5「下一步」明确写了「扩 lookback 到 3~5」，作者会直接踩这个坑。

### 决策：按 panel 实际交易日 slice + 周末新闻就近映射到下个交易日

### 改动

`astock_quant/factors/llm_factor.py:223-286` 关键改动：

```python
# L244：从 panel.xs(ticker).index 拿实际交易日集合
trading_days = sorted({_to_date(d) for d in dates})
# L247：O(1) 找下标
day_to_pos = {d: i for i, d in enumerate(trading_days)}

for item in news:
    news_d = _to_date(item.date)
    # L258：新闻就近映射到下个交易日（周末/节假日新闻不丢）
    bucket = _map_to_next_trading_day(news_d, trading_days)
    # L259-262：未来新闻丢弃（防 look-ahead 渗透）
    if bucket is None:
        continue
    by_trading_day.setdefault(bucket, []).append(...)

# L276：交易日窗口 slice
for raw_d in dates:
    t = _to_date(raw_d)
    pos = day_to_pos[t]
    window = trading_days[max(0, pos - self.lookback + 1) : pos + 1]
    samples = [s for d in window for s in by_trading_day.get(d, [])]
    ...
```

docstring L230-238 例子讲清「lookback=3 + T=周一 → 窗口 = {上周三, 上周四, 上周五, 本周一}」。

附加加分：L209-216 `cache_stats` hit/miss/fallback_id 三段计数 + debug log，**reviewer 没要求但 llm-factor-engineer 顺手做的可观测性增强**。

### 测试

`tests/test_llm_factor_with_mock_client.py:315-397` 新增 `test_lookback_window_crosses_weekend`：
- panel trading_days = [Wed/Thu/Fri/Mon]（**真实 A 股 panel 跳过周末**）
- 3 条新闻覆盖 [Wed/Fri/Sat]
- 断言「周一 T 非 NaN（周五在窗口 + 周六映射到周一也在窗口）」+「周三自身有分」
- docstring 推演详细，**未来 lookback 改回自然日，CI 立刻挂**

### 验证

P8 修复复审 PASS（auditor 评「不只『按交易日 slice』，还加了周末新闻就近映射让 Sat 新闻不丢」）。

---

## H2：缓存键加 content，防 5-10% 意外失效

### 问题

P8 code review 抓出：`_news_id` 在 url 缺失时只用 `title` hash：

```python
def _news_id(ticker: str, item: NewsItem) -> str:
    if item.url:
        return "u:" + hashlib.sha1(item.url.encode("utf-8")).hexdigest()[:16]
    raw = f"{ticker}|{item.date.isoformat()}|{item.title}"
    return "t:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
```

akshare 实测 5-10% 条目 url 为空，走 title fallback。但：
- 同一事件不同新闻源标题略不同 → 2 个不同 nid → **重复打分**
- 标题带时间戳（「【06:30 早盘速递】XX 股涨停」），同一新闻被微小差异分成多份缓存
- **更严重**：title 是 LLM 看到的内容之一，标题变化必然导致打分输出可能不同，**缓存键和打分内容耦合得不够紧**

**为什么算 High**：5-10% 缓存意外失效 → 长期成本爆炸，且**作者看不见**（没有 hit/miss 计数）。

### 决策：fallback hash 把 content[:500] 也纳入 + 加 cache_stats 可观测性

### 改动

`astock_quant/factors/llm_factor.py:404-418`：

```python
def _news_id(ticker: str, item: NewsItem) -> str:
    if item.url:
        return "u:" + hashlib.sha1(item.url.encode("utf-8")).hexdigest()[:16]
    content_head = (item.content or "")[:500]
    raw = f"{ticker}|{item.date.isoformat()}|{item.title}|{content_head}"
    return "t:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
```

docstring L407-411 写清两种漏洞场景 + 500 字符边界选择理由：
- 防 hash 抖动（超长稿件不爆涨计算时间）
- 区分度对短摘要 / 详细全文都够
- trade-off：极端长稿件**有可能**两条不同新闻前 500 字相同 → 撞 ID（已知 trade-off，**实际场景不阻塞**）

### 测试

2 个新测试：
- `test_news_id_same_title_different_content_gives_different_id` —— 同 title 不同 content 必须不同 ID
- `test_news_id_same_title_and_content_gives_same_id` —— 同 title 同 content 必须同 ID（缓存命中确定性）

### 验证

P8 修复复审 PASS（auditor 评「不只『加 content 到 hash』，还加了 cache_stats 可观测性 + debug log 让运行时能监控撞 ID 风险」）。

---

## 团队事件：factor-engineer 装死 → 拆 3 专职新人

### 背景

Stage 2 中段（2026-05-16），factor-engineer 出现**8 次装死症**：
- 干完不发完工消息（4 次）
- 用 idle 心跳代替进度报告（3 次）
- 派活后过夜不动（1 次）

**症状**：lead 派任务后没回应；spawn 新 worker 也没收到完工通知。lead 数次催进度，回应只有 idle ping。

### 决策：用户 2026-05-16 拍板拆 3 专职新人 + prompt 钉死纪律

### 重组前后对比

| | 重组前（9 人）| 重组后（11 人）|
|---|---|---|
| factor-engineer | 1 人扛 P3 量价 + P6 LLM + 接线 | shutdown |
| traditional-factor-engineer（新）| | 接管 volume_price / financial / moneyflow 25 因子 |
| llm-factor-engineer（新）| | 接管 `factors/llm_factor.py` + `llm/` 全目录 |
| factor-integrator（新）| | 接管 `registry.py` + pipeline + dataset 接线 + conftest |

### 教训

- **worker 装死 + 沟通失灵也是工程风险**，跟代码 bug 同样需要被工程化处理
- 新团队 prompt 钉死：「每完成一个子任务立刻 SendMessage」、「不要发 idle ping 代替进度报告」、「失败 / 卡住要直接发事实」
- explainer 在写报告 07 时也踩了一次这个坑（90 分钟前完工但通知未到 lead），新流程下重发事实

### 收尾时新团队就位

P8 修复（H1 / H2 / 3 新测试）由 **llm-factor-engineer** 独立完成、auditor PASS。新团队工作正常，**已等 Stage 3**。

---

## 验证

### pytest

```
$ uv run pytest tests/ -q
.................................................................................................             [100%]
97 passed in 58.74s
```

按文件分布：

| 文件 | 用例数 | 备注 |
|---|---|---|
| tests/test_factors_no_lookahead.py | 4 | P3a 旧 |
| tests/test_splits_purge.py | 11 | P3b 旧 |
| tests/test_constraints_astock.py | 18 | P4 旧 |
| tests/test_backtest_engine.py | 18 | P4 旧 13 + Stage2-prep 5 |
| tests/test_direction_model_roundtrip.py | 6 | Stage1 H1 旧 |
| tests/test_align_xy_determinism.py | 5 | Stage1 H2 旧 |
| tests/test_llm_factor_with_mock_client.py | **35** | **本轮 Stage 2 新增**（P6 23 + P7-wiring 4 + P8 fetcher 异常 3 + P8 H1 1 + P8 H2 2 + DeepSeek httpx 2）|
| **合计** | **97** | 62 旧 + 35 新 ✅ |

### ruff

```
$ uv run ruff check astock_quant/ tests/ scripts/
All checks passed!
```

### 端到端 metrics 字节级不漂移（关 LLM 时）

```
train metrics (默认，关 LLM):
  train_size: 25283        ← Stage 1 字节级一致 ✓
  valid_size: 5780         ← ✓
  auc: 0.5131337782587783  ← 小数 16 位完全一致 ✓
  accuracy: 0.5333910034602076  ← ✓
  log_loss: 0.6905634407838911  ← ✓

backtest metrics (默认 0.55/0.45):
  trading_days: 193  ← ✓
  n_trades: 0        ← ✓
  total_return: 0.0  ← ✓
  sortino: 0.0       ← ✓ Stage1-cleanup 修复后不回归
```

**Stage 2 加了一整套新东西（4 个新模块 + 1 个新因子 + 35 个新测试），关 LLM 时所有数字一字不差**。

### 端到端 metrics 真有 LLM 时（开 LLM）

```
treatment B v2（开 LLM）：
  n_features: 26  ← +1 LLM 因子 ✓
  auc: 0.5131337782587783  ← 与 baseline bit-identical（importance=0 → 模型完全忽略 LLM 列）
  llm_calls: 178
  llm_cost_rmb: 0.0753
  news_sentiment_importance: 0.0  ← 核心 negative finding
```

---

## 不在本轮 scope 的 reviewer 建议（留给 Stage 3 启动前再扫）

P8 code review 抓的 0 critical / 2 high / 4 medium / 5 low / 2 nit 中，**2 High 已修**（H1 + H2），4 M / 5 L / 2 N 留 Stage 3：

### Medium（值得修）

- **M1**：`_aggregate_to_dates` 缺 future news 防线 —— Stage 3 是 look-ahead 防线第三道补丁，外部数据塞进来时关键
- **M2**：~~`LLMFactor` 别名 + `_example_iter_tickers` 假函数~~ **本轮已确认 grep 全 repo 0 引用 → 无需操作**（auditor 已确认）
- **M3**：`LLM_MODEL` env var 在 Anthropic / DeepSeek 共用但语义不可比 —— 建议拆 `ANTHROPIC_MODEL` / `DEEPSEEK_MODEL`
- **M4**：`compute_factor_frame` 对所有因子传 `news_fetcher` 是隐性 API 污染 —— 建议引入 `FactorContext` 抽象，Stage 3 加更多 fetcher 时不补会越积越乱

### Low

- **L1**：`make_llm_client` provider 名 lowercase 约定写到 docstring
- **L2**：`DeepSeekClient` 默认模型硬编码 `deepseek-v4-pro`（一年后可能改名）
- **L3**：`__init__.py` 包级 `load_dotenv` 加 `ASTOCK_QUANT_SKIP_DOTENV` escape hatch
- **L4**：`_isolate_llm_env` fixture 改成 scan 所有 `LLM_` / `ANTHROPIC_` / `DEEPSEEK_` / `OPENAI_` 前缀
- **L5**：`pyproject.toml` 加 `anthropic` 上限 `<2`

### Nit

- **N1**：`registry.py` docstring 写「共 24 个」实际 25 个
- **N2**：`llm_factor.py` 头部 docstring 启用方式用 `ANTHROPIC_API_KEY`，但 P7 后默认 DeepSeek

---

## scope 限定

Stage 2 整个阶段只动了以下文件（mtime 全部对得上）：

| 文件 | 改动类型 |
|---|---|
| `astock_quant/llm/__init__.py` | **新建**（P6）|
| `astock_quant/llm/client.py` | **新建**（P6）+ 末尾循环 import 注册 DeepSeekClient（Infra-2）|
| `astock_quant/llm/deepseek.py` | **新建**（Infra-2）|
| `astock_quant/llm/schemas.py` | **新建**（P6）|
| `astock_quant/llm/prompts.py` | **新建**（P6）|
| `astock_quant/factors/llm_factor.py` | stub → 414 行实现（P6）+ wiring（P7-wiring）+ H1 + H2 + cache_stats（P8）|
| `astock_quant/factors/registry.py` | env var 开关 + `news_fetcher` 透传参数（P7-wiring）|
| `astock_quant/data/dataset.py` | 返回值加 `source` key（P7-wiring）|
| `astock_quant/pipeline/run_direction.py` | 注入 `source.get_news`（P7-wiring）|
| `astock_quant/__init__.py` | try/except load_dotenv（Infra-1）|
| `tests/conftest.py` | **新建** autouse fixture LLM env 隔离（Infra-1）|
| `tests/test_llm_factor_with_mock_client.py` | **新建** 35 个测试 |
| `pyproject.toml` | `anthropic>=0.40` + `python-dotenv>=1.0` |
| `P6-LLM因子.md` | 技术文档 + §9 wiring 复盘 |
| `P7-LLM对比验证.md` v2 | 技术文档 |
| 8 份审核记录 | `审核/P6-LLM因子-审核.md` / `审核/DeepSeek-provider-审核.md` + 复审 / `审核/dotenv-集成-审核.md` / `审核/P7-wiring-接线-审核.md` / `审核/P7-报告v2-审核.md` / `审核/P8-代码总审.md` / `审核/P8-修复复审.md` |
| 2 份人话报告 | `人话报告/06-P6LLM因子.md` / `人话报告/07-P7对比验证.md` |

**未动**（mtime 停在前几轮）：
- `backtest/` 全部 P4 / Stage2-prep mtime —— **回测引擎一行没动**
- `models/` 全部 P3b / Stage1-final mtime —— **模型 / 切分 / 标签一行没动**
- `signals/generator.py` P4 mtime —— **信号一行没动**
- `factors/` 其他（base.py / price_volume.py / fundamental.py / moneyflow.py）P3a mtime —— **量价 / 财务 / 资金流因子一行没动**
- `config/settings.py` P2 mtime —— **配置一行没动**
- `contracts.py` P4 mtime —— **数据契约一行没动**

**完美的 Stage 2 加法纪律：只动 LLM 相关 + 接线 + 测试**，Stage 1 全部代码字节级保留。

---

## 关键判断 / 决策记录

### 决策 1：用户选 A 接受现状收尾，不继续追逐 LLM alpha

**4 条候选路**：

| 路 | 投入 | 预期回报 | 风险 | 用户选择 |
|---|---|---|---|---|
| **A**（接受 negative finding 进 P8 收尾）| 极低 | 明确的 negative finding 已落袋 | 0 | ✅ **已选** |
| B（短窗口大 universe 重测）| 几小时 | 可能看到小幅 AUC 变化 | 样本太小（< 1000 行）结论不可靠 | ❌ |
| C（换文本源：akshare 公告 / 研报）| 1 周 | 可能终于看到 LLM 因子有效 | 也可能换了还是 0 | ❌ |
| D（付费数据源 Wind / Tushare Pro）| 1-2 周 + 付费 | 公认数据质量高 | 投入大 + 可能 LLM 因子还是无效 | ❌ |

**为什么 A 是合理选择**：
- Stage 2 验收条件（⑥ + ⑦）实际上已达成 —— LLM 因子模块能从文本产出因子并接入因子层 ✅；加 LLM 因子前后回测对比已做完 ✅（对比结果是 0，但**对比本身完整跑完**）
- 「学习型项目」核心目的本来就不是「出 alpha」，是「跑通 + 守纪律」（报告 05 反复强调）
- 用 ¥0.09 + 半小时算力，**买到了一个真知识**：当前数据条件下 LLM 因子无用，瓶颈在数据源
- 留下完整的 P7 文档，未来要做 B/C/D 任一时「从哪开始」一目了然

### 决策 2：H1 周末新闻「就近映射到下个交易日」而非「丢弃」

**两种语义选择**：
- **A**（采用）：周末新闻 → 映射到下一个交易日的窗口
- B：周末新闻 → 直接丢弃

**为什么选 A**：公司公告 / 监管通报常在周末发，**丢弃 = 损失真实信号**。映射到周一与「这些信息在周一开盘前已被市场吸收」的现实一致。

### 决策 3：cache_stats 可观测性增强，reviewer 没要求但顺手做

**理由**：H2 修法只解决 5-10% title-only 缓存失效，但**作者本身看不见运行时撞 ID 率**。加 3 段计数 + debug log 让运行时能监控 fallback_id 比例。这是 P3 winsorize bug 教训的延伸 ——「**别等到回归再发现**，把可观测性自动化」。

---

## 给 Stage 3 ②③④ 实现者的提醒（按优先级）

### Stage 3 启动前必修（P8 reviewer 留的 M）

1. **M1 future news 防线**：第三道 look-ahead 防线补丁，外部研究员塞数据进来时关键
2. **M4 FactorContext 抽象**：加 `event_fetcher` / `announcement_fetcher` 之前不抽象，registry 的 `compute(**kwargs)` 会越来越脏
3. **M3 LLM_MODEL env var 拆分**：切 provider 时不会带过去导致 400 错误

### Stage 3 时机加的事

- LLM 因子缓存 hit/miss 计数 → 真正接入 dashboard / 长期监控
- 把 DeepSeek 默认模型挪到 `config.settings`（与 BacktestConfig 等项目级配置看齐）
- 给 `LLMNewsSentiment` 加 `min_news_per_day` 阈值参数 —— 当某日新闻太少时直接 NaN，省 token 提升信号质量

### Stage 3 测试空缺（启动前建议补）

当前 35 个 LLM 测试覆盖：mock client / 缓存 / 安全审计 / DeepSeek httpx mock / wiring 通路 / fetcher 异常隔离 / H1 周末跨窗口 / H2 hash 区分度。

**Stage 3 启动前建议补**：
- future news 防线测试（守 M1 修复）：故意传 `item.date > panel.max(date)` 的新闻，断言不污染任何 T 行
- cache hit/miss 统计正确性（守 H2 修法的可观测性）
- provider env var 切换的端到端：mock `LLM_PROVIDER=deepseek` + `LLM_MODEL=xxx`，断言 client.model 与预期一致
- 跨年 / 跨季度边界 + 多次回测稳定性（P5 reviewer 上一轮留的债）

### 数据源问题（Stage 3 真要拿 LLM 出 alpha 必须解决）

**P7 negative finding 的根因不在 algo 在 data**。Stage 3 真想验证 LLM 因子有用与否，必须先解决：

- akshare 翻页拉历史新闻（看是否支持）
- 换数据源（同花顺 / 东方财富研报 API / Choice / Wind / Tushare Pro）
- 或公告替代新闻（公告历史覆盖一般比新闻好，Stage 2 没来得及试）

**关键认知**：再多调 prompt / 换模型 / 加 lookback 都改不了「99% NaN → importance=0」这个事实。**先把数据源覆盖率提到 30%+，再讨论 LLM 因子有用没用**。

---

## Stage 2 最终评价

### 工程层面

✅ **基础设施全 PASS**：API 真接通 / wiring 全链路 / 因子算法正确 / 缓存工作 / 成本可控（¥0.09 << 预算 ¥10）/ API key 安全 / 不破坏老成果 / 97 测试全过 / ruff clean / scope 严守只动 LLM 相关。

### 算法层面

❌ **LLM 因子在当前 akshare 数据源 + 4 年训练窗口下未对模型产生贡献**（importance = 0.0）。

### 研究层面

✅ **诚实的 negative finding** —— 揭示真正瓶颈在数据源，**这个负面发现的价值远超 ¥0.09**。避免未来花一周去调 prompt / 换模型，先去解决新闻覆盖问题。

### 团队层面

✅ **作者审查分离守住** —— factor-engineer / llm-factor-engineer 写代码，code-reviewer 深审，verifier-2 端到端跑，auditor 复审。**作者从不自审。**

✅ **诚信红线 0 次被踩**（跟 Stage 1 一脉相承）—— AUC bit-identical 不藏、importance=0 摆显眼位置、v1/v2 区分讲透、¥0.09 财务诚信。

⚠️ **一次团队事件**：factor-engineer 8 次装死 → 拆 3 专职。教训：worker 装死 + 沟通失灵也是工程风险，需要被工程化处理。新团队 prompt 钉死纪律，到收尾时工作正常。

✅ **auditor 第 11 次实战 PASS** —— 从 P3a winsorize bug 到 Stage 2 收尾，跑稳 11 轮（含 P6 / DeepSeek 双轮 / dotenv / P7-wiring / P7 报告 v2 / P8 代码总审 / P8 修复复审）。这是 P3a 装上后**最稳定的工程角色**。

### 收尾结论

**Stage 2 可正式收尾**。后续启动 Stage 3 时再扫一遍 4 M / 5 L / 2 N 债务 + 数据源问题即可，无任何阻塞项。

**用一句话总结 Stage 2**：

> 我们花了 ¥0.09 学到一个真知识 —— **当前数据条件下 LLM 因子无用，瓶颈在数据**。这比假装 LLM 有用、继续投入更值。
