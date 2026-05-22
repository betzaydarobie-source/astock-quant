# P7 — LLM 因子对比验证报告（v2，wiring 修复后最终版）

> verifier-2 产出 · 2026-05-16 · Stage 2 P7 v2（最终）
>
> 前置：P6-LLM因子.md / P6-LLM因子-审核.md / P7 wiring 5 处修复 + max_tokens 256→512
>
> v1 教训：`compute_factor_frame` 未传 news_fetcher → LLM 列全 NaN → AUC bit-identical（无法判断效果）
> v2：wiring 修复后，178 次真实 DeepSeek 调用，结论如下。

---

## 0. 一句话结论

**P7 PASS（流程跑通，数字诚实）。LLM 因子 API 调通、wiring 5 处修复确认、178 次真实 DeepSeek 调用完成；但 AUC delta = 0.0 bps，LightGBM importance = 0.0。这是诚实的 negative finding，根因是 akshare 只返回最近 ~20 条新闻（集中 2026-04/05），对训练窗口 2022-2026 覆盖率 < 1%，LightGBM 在 99%+ NaN 列上正确打出 importance=0。**

---

## 0.5 v1 vs v2 对照（两次都 bit-identical，原因完全不同）

| 项目 | v1（2026-05-16 上午）| v2（2026-05-16 下午）|
|---|---|---|
| wiring 状态 | 未接线（bug）| 5 处修复，全通 |
| 实际 LLM 调用次数 | **0**（全 cache miss 但 news_fetcher=None）| **178**（真实 HTTP 调用）|
| news_sentiment NaN 率 | 100%（无新闻）| ~99%（akshare 数据稀疏）|
| AUC delta | 0.0 | 0.0 |
| importance | 不适用 | **0.0** |
| bit-identical 根因 | 代码 bug（接线缺失）| **数据源限制**（akshare 只返最近 ~20 条）|
| 能否判断 LLM 效果 | 否（bug 导致）| **否**（数据不够，fair test 未完成）|

关键区别：v1 是代码问题，v2 是数据问题。因子本身逻辑正确，DeepSeek API 工作，基础设施 PASS。

---

## 1. 预检

| 检查项 | 结果 |
|---|---|
| `DEEPSEEK_API_KEY`（via `.env` load_dotenv）| 已设置（len=35）|
| `LLM_PROVIDER` | `deepseek` |
| `ENABLE_LLM_FACTOR` | `1` |
| DeepSeek client 构造 | PASS（model=deepseek-v4-pro）|
| wiring: `prepare_stage1_data` 返回 `"source"` key | PASS |
| wiring: `run_direction` 注入 `news_fetcher=source.get_news` | PASS |
| wiring: `compute_factor_frame` 透传 `news_fetcher` | PASS |
| wiring: `LLMNewsSentiment.compute` 从 kwargs 拿 fetcher | PASS |
| wiring: `default_factors()` 返回 26 因子（含 news_sentiment）| PASS |
| max_tokens 256→512 | PASS |

---

## 2. Step 1 — 3 票 smoke（v2，通过 run_direction 路径）

### 2.1 配置

| 参数 | 值 |
|---|---|
| Universe | 600519（贵州茅台）/ 000858（五粮液）/ 600887（伊利股份）|
| 路径 | `run_direction(universe=TICKERS)` —— 完整 pipeline 验证 wiring |
| Provider | DeepSeek deepseek-v4-pro，max_tokens=512 |

### 2.2 wiring 验证结果

| 指标 | 值 |
|---|---|
| DeepSeek HTTP 调用次数 | 9（真实 cache miss，wiring 通路径）|
| 新 LLM 缓存文件写入 | 3（`data_cache/llm_factor/`，每票一个）|
| n_features | 26（含 news_sentiment）|
| LLM 打分失败条数 | 1（行业通稿无 json 字眼，正常降级）|
| 花费 | ¥0.0045 |

**wiring 验证：PASS**。`run_direction → compute_factor_frame → news_fetcher → AStockSource.get_news → DeepSeek` 完整链路真实调用。

---

## 3. Step 2 — 30 票回测对比（v2，真实 wiring）

### 3.1 配置

| 参数 | 值 |
|---|---|
| Universe | STAGE1_UNIVERSE 30 只（沪深蓝筹）|
| 训练区间 | ~ 2025-06-30（train_size=25,283）|
| 验证区间 | 2025-07-15 ~ 2026-05-01（valid_size=5,780）|
| Purge gap | 10 交易日，label horizon=5 |
| 模型 | LightGBM DirectionModel（默认超参）|
| Baseline A | `ENABLE_LLM_FACTOR=0`，25 因子 |
| Treatment B | `ENABLE_LLM_FACTOR=1`，26 因子，news_fetcher 注入 |

### 3.2 对比表

| 指标 | Baseline A v2（25 因子）| Treatment B v2（26 因子）| Delta |
|---|---:|---:|---:|
| n_features | 25 | 26 | +1 |
| train_size | 25,283 | 25,283 | 0 |
| valid_size | 5,780 | 5,780 | 0 |
| **accuracy** | **0.5334** | **0.5334** | **0.0000** |
| **AUC** | **0.5131** | **0.5131** | **0.0 bps** |
| **log_loss** | **0.6906** | **0.6906** | **0.0000** |
| news_sentiment importance | — | **0.0** | — |

### 3.3 Treatment B v2 LLM API 实际使用量

| 指标 | 值 |
|---|---|
| DeepSeek HTTP 调用（cache miss）| 178 |
| Input tokens | 107,941 |
| Output tokens | 41,998 |
| 估算花费 | ¥0.0753 |
| 运行耗时 | 1,804s（~30 分钟）|

### 3.4 Treatment B v2 特征重要性 Top 10

| 排名 | 因子 | 重要性 |
|---:|---|---:|
| 1 | volatility_20d | 296.5 |
| 2 | roe | 253.7 |
| 3 | momentum_60d | 235.9 |
| 4 | amount_mean_20d | 230.8 |
| 5 | momentum_20d | 208.4 |
| 6 | eps | 203.4 |
| 7 | net_profit_growth_yoy | 177.7 |
| 8 | atr_pct_14 | 174.7 |
| 9 | macd_hist | 152.1 |
| 10 | zscore_50 | 128.9 |
| — | **news_sentiment** | **0.0** |

---

## 4. 诚信解读

### LLM 因子真本事 verdict：**在当前配置下无效（negative finding）**

**不是因子逻辑错误**：
- wiring 修复后，178 次真实 DeepSeek 调用，JSON 解析正确，缓存机制工作
- 因子值本身有语义（贵州茅台季报发布日 +0.5，行业资金流出日 0.0 或负分）
- 完整链路 `run_direction → compute_factor_frame → news_fetcher → DeepSeek` 确认通路

**是新闻覆盖率问题**：

```
akshare stock_news_em 限制：
  每次调用返回最近 ~20 条（东财默认翻页），全部集中在 2026-04 ~ 2026-05（最近 6 周）
  → 30 票 × ~20 条 = ~600 条新闻，占 2022-2026 约 1,000 个交易日的 < 3%
  → 训练集 25,283 样本中，news_sentiment 非 NaN 的比例 < 1%
  → LightGBM 在 99%+ NaN 的列上 importance=0，这是正确行为
```

**不能说「LLM 因子没用」**：数据覆盖不够，没有公平给它机会参与决策。

---

## 5. 建议下一步

### P7-next-1（关键）— 扩新闻覆盖

akshare 限制是硬约束，需换策略：
- **方案 A**：akshare 加翻页（如有）拉历史新闻
- **方案 B**：换数据源（同花顺 / 东方财富研报 API / Choice 数据）
- **方案 C**：只在 valid 集最后 6 周做子集对比（news 有覆盖的那段），绕开历史稀疏问题

### P7-next-2（可选）— lookback 扩窗

默认 `lookback=1`（仅当日），试 `lookback=7`，用滑动窗口聚合近期情绪，有效值密度可提升数倍。

### P7-next-3（可选）— 截断率监控

max_tokens=512 仍有少数截断（`{"sentiment": 0.` 这种约占 5-10%），调到 768 可进一步降低。

---

## 6. P7 状态汇总

| 项目 | 结论 |
|---|---|
| p7_pass | **true**（流程跑通 + 数字诚实）|
| llm_api_live | **true**（178 次 DeepSeek 调用确认）|
| wiring_confirmed | **true**（5 处修复，CI 守住）|
| llm_factor_useful | **false**（当前 akshare 数据源限制下）|
| Stage 2 算法 | 跑通 + 真测了 + 出了有价值的 negative finding |

**诚实结论**：
- LLM 因子在当前 akshare 数据源 + 4 年训练窗口下**无效**
- 基础设施全过：API 活、wiring 通、因子能落地、成本可控（¥0.08/30 票）
- 这是**有意义的 negative finding**，不是 bug，根因是数据源覆盖历史不够

---

## 7. 测试兜底（verifier 未改任何代码）

```
uv run pytest tests/ -q --tb=short  →  93 passed in 63.2s（含 wiring 新测试）
uv run ruff check astock_quant/ tests/  →  All checks passed!
```

---

## 8. 花费汇总

| 阶段 | 调用次数 | Input tokens | Output tokens | 花费（¥）|
|---|---:|---:|---:|---:|
| v1 smoke（3 票，旧版）| 26 | 15,609 | 5,572 | 0.01 |
| v2 smoke（3 票，run_direction）| 9 | 5,504 | 2,709 | 0.004 |
| v2 Treatment B（30 票全量）| 178 | 107,941 | 41,998 | 0.075 |
| **合计** | **213** | **129,054** | **50,279** | **~¥0.09** |

远低于预算上限 ¥10。

---

## 附：产出文件

- 本文档：`量化/P7-LLM对比验证.md`
- `量化/artifacts/p7_baseline_A_v2.json`
- `量化/artifacts/p7_treatment_B_v2.json`
- `量化/artifacts/p7_compare_v2.json`
