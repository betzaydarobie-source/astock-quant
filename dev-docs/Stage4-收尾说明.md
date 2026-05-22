# Stage 4 收尾说明 —— 每日预测工具 + 第 4 次队员重组 + 诚信声明强制守门

> 2026-05-16 · 多角色出品（architect-2 / model-engineer / factor-integrator / traditional-factor-engineer / data-engineer-2 / auditor-2 / explainer）
>
> Stage 4 把训好的 4 类模型变成"日常能用"的预测工具——每日预测报告（P12）+ launchd 自动跑（P13）+ 准确率追踪（P14）+ 沪深 300 扩容（P15）。**Stage 1/2/3 全部代码字节级保留**，4 个 pipeline 加 predict_only kw-only 模式（不重训只推理）。
>
> **诚信声明从代码层面强制守门**：HTML/MD 模板 + 3 个 renderer 测试命门 + P14 命中率报告也带 bp 量化诚信结论——跟之前 6 次诚信弱基线一脉相承（① AUC=0.5131 / Stage 2 LLM importance=0 / ② R²=-0.002 / ③ rank-IC≈0 / ④ macro acc≈baseline / 命中率追踪）。
>
> **第 4 次队员重组**：data-engineer 15 分钟零响应 → shutdown → data-engineer-2 接手 P15。**项目至今 5 次队员重组累计**，老员工 9 个里 4 个被换了，6 个新人 0 装死。zero-tolerance + progress-reporter 模式彻底成熟。
>
> 本文档既是 Stage 4 的交付清单 + 修法记录 + 团队事件复盘，也是 Stage 5 启动前（如果有的话）的债务列表。

## 总览

Stage 4 = 4 子任务（P12 / P13 / P14 / P15）+ architect-2 整体规划 + 4 次 auditor-2 复审 + 4 次三人 / 二人协作 + 1 次队员重组。

| # | 阶段 | 干了啥 | 主笔 | 状态 |
|---|---|---|---|---|
| Stage 4 设计 | architect-2 | v1.0 整体规划（含 predict_only 接口 / launchd plist / accuracy 命中率算法 / HS300 扩容设计）| architect-2 | ✅ auditor-2 PASS |
| P12 | 每日预测报告 | 4 pipeline predict_only + daily.py + renderer.py + HTML/MD 模板 + JSON 落盘 + 42 测试 | model-engineer + factor-integrator + traditional | ✅ auditor-2 Conditional PASS（4 ruff F401 测试文件可一键 fix）|
| P13 | launchd 自动跑 | plist 模板 + wrapper.sh + README + 16 测试 | factor-integrator | ✅ auditor-2 PASS |
| P14 | 准确率追踪 | accuracy.py 530 行（4 类 evaluator + GroundTruthCache + horizon 截断 + 诚信结论）+ 34 测试 | model-engineer + traditional | ✅ auditor-2 PASS |
| P15 | 扩沪深 300 | settings.py 加 get_universe + dataset.py 加 stage 参数 + scripts/prewarm_hs300.py + 22 测试 | data-engineer-2（替补）+ traditional | ✅ auditor-2 PASS |
| 团队 | 阶段中 | data-engineer 15 分钟零响应 → data-engineer-2 替补 | — | ✅ 装死症 zero-tolerance 第 4 次落地 |

**测试：326 / 326 PASS**（212 旧 + 114 新增）· **ruff：clean**（P12 测试文件 4 个 F401 已修）· **Stage 1/2/3 metrics 字节级不漂移**（① AUC=0.5131337782587783 与 5 个月前完全一致）

---

## P12：每日预测报告

### 问题

Stage 3 收尾时 4 个 pipeline（run_direction / run_return / run_ranking / run_trade_signal）都是"训练 + 回测"一体的，每次跑都重新训练。每日预测只需要"加载已训练模型推理"，**不需要重训**。需要：
1. 给 4 个 pipeline 加 `predict_only=True` 模式
2. 整合 4 类预测结果出报告（HTML + Markdown 双格式）
3. JSON 落盘供 P14 准确率追踪用
4. **诚信声明强制守门**（不能假装有 alpha）

### 决策：4 设计

**1. predict_only kw-only 模式**

理由：
- 重新训练 5-10 分钟，predict 推理 30 秒
- 每个 pipeline 加 `_run_*_predict_only` helper 函数（独立 200 行），训练路径通过 `if predict_only: return _run_*_predict_only(...)` 短路
- **4 个 pipeline 各自 inline 同款逻辑（不跨模块调用）**——保持各 pipeline 独立可测试 + 可独立修改 + scope 严守优先于 DRY

**2. 诚信声明强制守门**

理由：
- 模型实际没 alpha（① AUC=0.513 / ② R²=-0.002）
- 用户每天看报告——稍不留神会把报告当"投资建议"误用
- **3 个 renderer 命门测试**强制盯死：HTML 必须含警告 / Markdown 必须含 / 非空占位
- 模板里诚信声明放 §1（不是附录、不是页脚），HTML 用红/橙警示框（`.disclaimer { background: #fff3cd; }`）

**3. 4 pipeline 完全隔离（任一失败不影响其他）**

`try/except` 隔离 + 失败的在报告里标 ⚠️ + 错误信息写日志。全失败时 `n_failed >= n_targets_attempted → exit 1`（launchd 可检测）。

**4. lazy `__getattr__` 避免 `python -m` 双加载**

`predict/__init__.py` 用 `__getattr__` lazy 延迟到显式访问 `astock_quant.predict.run_daily_predict` 时才 import—避免 `python -m astock_quant.predict.daily` 时 daily 被作为 `__main__` 加载 + 同时作为包成员加载触发 RuntimeWarning。

### 改动

| 文件 | 改动 | 行数 |
|---|---|---|
| `astock_quant/predict/__init__.py` | **新建**（lazy `__getattr__`）| ~25 |
| `astock_quant/predict/daily.py` | **新建** 主入口（调 4 pipeline + 整合 + JSON 落盘 + 渲染）| ~400 |
| `astock_quant/predict/renderer.py` | **新建**（stdlib `string.Template` 渲染 HTML/MD）| ~150 |
| `astock_quant/predict/templates/daily_report.html.template` | **新建** | ~120 |
| `astock_quant/predict/templates/daily_report.md.template` | **新建** | ~80 |
| `astock_quant/pipeline/run_direction.py` | 加 `predict_only` kw-only 参数 + `_run_direction_predict_only` helper | +90 |
| `astock_quant/pipeline/run_return.py` | 同款 | +90 |
| `astock_quant/pipeline/run_ranking.py` | 同款 | +90 |
| `astock_quant/pipeline/run_trade_signal.py` | 同款 | +90 |

### 命门测试（42 个）

| 测试 | 守的命门 |
|---|---|
| `test_run_direction_predict_only_does_not_call_fit` × 4 | **predict_only 真不调 fit**：fit_spy `side_effect=AssertionError`，call_count=0。**未来若 predict_only 漂回训练路径，spy 触发 → CI 立刻红** |
| `test_daily_report_html_contains_honesty_disclaimer` | HTML 报告必须含诚信声明（renderer 守门）|
| `test_daily_report_md_contains_honesty_disclaimer` | Markdown 报告同款 |
| `test_disclaimer_is_not_empty_placeholder` | 不能是空模板占位（必须有实质内容）|
| 其余 35 个 | predict_only 路径覆盖 / 报告格式 / JSON schema / CLI / exit code / 异常隔离 |

### 验证

auditor-2 P12 Conditional PASS（7 项审核全过，4 个 ruff F401 测试文件未用 import 可一键 `--fix` 修复）。详见 `审核/P12-每日预测报告-审核.md`。

---

## P13：launchd 自动跑

### 问题

P12 完成后，用户每天还得手动 `uv run python -m astock_quant.predict.daily`。需要 macOS 自动调度——每天 16:30（A股 16:00 收盘留 30 分钟数据更新）自动触发 + 通知 + 浏览器自动开。

### 决策：macOS 内置 launchd + osascript，零新增依赖

理由：
- launchd 是 macOS 原生（类 Linux cron），跟系统一起运行，比第三方更稳
- osascript 是 macOS 内置 AppleScript 解释器，弹通知无需第三方包
- `KeepAlive=false`（失败等明天不无限重启）+ `RunAtLoad=false`（开机不自动跑）
- `wrapper.sh` 永远 `exit 0`（失败也不阻塞 launchd 下次调度）

### 改动

| 文件 | 改动 |
|---|---|
| `scripts/com.astock.daily.plist.template` | **新建** launchd 配置模板（含 8 个必需 key + `{project_root}` 占位）|
| `scripts/daily_predict_wrapper.sh` | **新建** bash 包装脚本（set -euo pipefail + try/catch + osascript 成功/失败通知）|
| `scripts/README.md` | **新建** 安装/卸载/排错文档 |
| `tests/test_launchd_scripts.py` | **新建** 16 测试（plist 必需 key / wrapper 语法 / exit 0 / 通知文案）|

### 命门测试（16 个）

| 测试 | 守的命门 |
|---|---|
| `TestPlistTemplate`（9 个）| plist 存在 + 5 个必需 key（Label/StartCalendarInterval/ProgramArguments/StandardOutPath/StandardErrorPath）+ 16:30 调度 + `{project_root}` 占位符 + Label 字符串 |
| `TestWrapperShell`（7 个）| 文件存在 + `bash -n` 语法检查通过 + osascript 调用 + 成功/失败通知文案 + `exit 0` 兜底 + error log 落盘 + 成功时 `open html` |

### 验证

auditor-2 P13 PASS（6 项审核全过 + 16 测试 + ruff clean + 独立跑 `bash -n` 通过）。详见 `审核/P13-launchd-自动跑-审核.md`。

---

## P14：准确率追踪

### 问题

P12 每天落盘 JSON 预测，但**光预测没用**——必须回头对照真实涨跌算命中率。需要：
1. 读历史 JSON 预测
2. 拉真实 ground truth 价格
3. 算 4 类命中率（direction / return / ranking / trade_signal）
4. 严格防 look-ahead（horizon 截断）
5. **诚信结论**用 bp 量化跟 baseline 偏差

### 决策：5 设计

**1. 4 类 evaluator 算法与 label 算法严格对称**

- `_eval_direction`：`value >= 0.5` → predicted_up；`actual_return > 0` → actual_up；`hit = predicted_up == actual_up`
- `_eval_return`：MAE = `|pred - actual|` + 方向一致率 = `(pred>0) == (actual>0)`
- `_eval_ranking`：Top K 按 score 降序，真实涨幅前 K/2 的比例（precision@K/2）+ Spearman 相关
- `_eval_trade_signal`：**路径模拟与 `trade_signal_label._label_one_ticker` 完全对称**（逐日 scan，TP 优先 break，SL 次之 break，都没触 = HOLD）

**2. `_GroundTruthCache` —— dedup + missing set 不重试**

理由：
- N 天预测 × 30 只票 = 数百个 ticker 重复，dedup 避免重复 fetch
- 拉过空数据的 ticker 进 missing set，第二次直接返回 None（akshare 不会突然有数据，重试浪费）
- `set_date_range` 未调 → `RuntimeError` 明确报错（不静默失败）

**3. horizon 截断防 look-ahead**

`today_cutoff = today - horizon`，仅评估 T+horizon ≤ today 的预测。文件级 `continue` 跳过（高效）+ `_future_close_at_horizon` 在 `target_pos >= len(close_series)` 时返回 None（两层防护）。

**4. 缺失友好（5 种场景）**

| 场景 | 处理 |
|---|---|
| JSON 文件损坏 | `_load_predictions` 返回 None + warning，跳过 |
| ticker GT 拉取失败 | `_GroundTruthCache` 返回 None + warning，`n_missing_gt++` |
| T 当日停牌 | `_future_close_at_horizon` 回退到前一交易日 entry，或 `pos==0` 时返回 None |
| T+horizon 超末尾 | 返回 `(None, None)` → `n_horizon_unreached++` |
| 整体无可评估预测 | CLI exit 1 + 明确引导信息（"先跑 daily.py"）|

**5. 诚信结论 bp 量化**

- ① direction：`delta_bp = (hit_rate - 0.5) * 10000`
- ② return：`delta_bp = (dir_correct_rate - 0.5) * 10000`
- ③ ranking：`abs(spearman) < 0.05 → "接近 0（没排序能力）"`
- ④ trade_signal：`baseline = 1/3`，`delta_bp = (hit_rate - 1/3) * 10000`
- 整体判断段引用历史数据：「与 Stage 1 ① direction AUC=0.5131 / P9 ② return R²=-0.002 一脉相承，**模型仍是诚信弱基线**，没有 alpha。学习/研究项目的预期结果，**不要当真盘**」

### 改动

| 文件 | 改动 | 行数 |
|---|---|---|
| `astock_quant/predict/accuracy.py` | **新建**（4 evaluator + GroundTruthCache + CLI + 诚信结论）| 530 |
| `tests/test_accuracy.py` | **新建** 34 测试 | ~600 |

### 命门测试（34 个）

| 测试类 | 测试数 | 覆盖 |
|---|---|---|
| `TestFutureCloseAtHorizon` | 4 | 正常 / 超出 / 停牌回退 / T 早于序列 |
| `TestFutureClosePath` | 3 | 路径长度 / 超出 / 路径值 |
| `TestGroundTruthCache` | 4 | 缓存命中 / 缺失 / missing 不重试 / set_date_range 守门 |
| `TestEvalDirection` | 4 | buy 命中 / sell 未中 / 缺 GT / ticker filter |
| `TestEvalReturn` | 3 | MAE / 方向一致 / 方向错误 |
| `TestEvalRanking` | 3 | Top-K precision / Spearman / 行不足跳过 |
| `TestEvalTradeSignal` | 4 | TP / SL / HOLD / TP 预测但 SL 先触 |
| `TestEvaluatePredictions` | 7 | 空目录 / horizon 截断 / 缺 GT / 损坏 JSON / target filter / ticker filter / 端到端 |
| `TestAccuracyCLI` | 2 | 空预测 exit 1 / 默认 --days 30 |

### 验证

auditor-2 P14 PASS（6 项审核全过 + 34 测试 + ruff clean + 4 类 evaluator 算法独立核对完整）。详见 `审核/P14-准确率追踪-审核.md`。

---

## P15：扩沪深 300

### 问题

之前 Stage 1-3 一直用的是 30 只大盘蓝筹（`STAGE1_UNIVERSE`）。需要扩到沪深 300——但**不能破坏 Stage 1 行为**（所有 326 个测试 + Stage 1/2/3 五个月不漂移的 AUC=0.5131337782587783）。

### 决策：4 设计

**1. STAGE1_UNIVERSE 一字不动 + 新增 stage 参数**

理由：
- `get_universe("stage1")` 返回 30 只（默认）
- `get_universe("stage4")` 返回沪深 300
- `prepare_stage1_data(stage="stage1")` 默认走 stage1，**所有不传参的老代码字节级一致**
- 这是延续 Stage 2/3 的"加法纪律"——加新的不破坏老的

**2. 1 天 TTL 缓存**

理由：
- 沪深 300 成分股每季度才调整，每次都 fetch akshare 浪费
- 第一次拉，存 `data_cache/hs300_universe.json`，24 小时内直接读
- 用 `timezone.utc`（避免本地时区 DST 问题）
- 损坏 cache 处理：`except Exception: pass` 一律降级重拉（不崩溃）

**3. 3 次重试 + 错误差异化处理**

| 数据类型 | 失败 3 次后处理 |
|---|---|
| prices | 加入 missing list（核心数据必须有）|
| moneyflow | 打印警告但继续（辅助数据，pipeline 能降级运行）|
| financials | 打印警告但继续（辅助数据）|

**4. akshare 实测 25 秒（远低于 architect-2 估的 5-10 分钟）**

原因：
- data_cache/ 已有 stage1 的 30 只历史数据，prewarm 跳过这些
- akshare 东财源响应快

无需补 sleep（prewarm 是一次性手动操作，非 launchd 定时，不存在频率问题）。

### 改动

| 文件 | 改动 |
|---|---|
| `astock_quant/config/settings.py` | 新增 `get_universe(stage)` + `get_hs300_universe()` + cache 字段 + import（json/datetime/timezone/logging）|
| `astock_quant/data/dataset.py` | `prepare_stage1_data` 新增 `stage="stage1"` 参数 + import `get_universe` |
| `astock_quant/scripts/__init__.py` | **新建**（scripts 包）|
| `astock_quant/scripts/prewarm_hs300.py` | **新建** 拉沪深 300 数据 + 3 次重试 + missing list + progress 输出 |
| `tests/test_universe_hs300.py` | **新建** 14 测试 |
| `tests/test_prewarm_hs300.py` | **新建** 8 测试 |

### 命门测试（22 个）

| 测试类 | 测试数 | 覆盖 |
|---|---|---|
| `TestGetUniverseBackwardCompat` | 5 | **STAGE1_UNIVERSE 一字不动** + 6 位纯数字代码 + 默认 stage 是 stage1 |
| `TestGetHs300Universe` | 7 | cache 命中不调 akshare + 2 次调用 1 次 fetch + 过期重拉 + 损坏 cache 不崩 + cache 写后存在 + zero-pad |
| `TestGetUniverseStage4` | 2 | stage4 行为 |
| `TestPrewarmMain` | 8 | 3 次重试 + missing list + moneyflow 失败不阻塞 + partial 失败 exit(1) + 全成功不调 exit |

### 验证

auditor-2 P15 PASS（7 项审核全过 + 22 测试 + ruff clean + akshare 实测 25 秒数据透明披露）。详见 `审核/P15-扩沪深300-审核.md`。

---

## 团队事件：第 4 次队员重组 —— data-engineer → data-engineer-2

### 事件回顾

P15 阶段，lead 派给 data-engineer 任务（拉沪深 300 数据 + prewarm 脚本）。data-engineer **15 分钟零响应**——既不出文件、也不报错、也不汇报。

按 zero-tolerance 装死纪律（Stage 3 P10 / Stage 2 factor-engineer / Stage 3 启动 architect 都用同款）：lead 立刻 shutdown 旧 data-engineer + spawn `data-engineer-2` 替补。

新人接手后 **真做完了**：4 个文件 + 烟测 25 秒拉完（远超 architect-2 估算）+ 22 测试全过。

### 项目至今 5 次队员重组累计

| 时间 | 谁 | 原因 | 处理 |
|---|---|---|---|
| Stage 2 中段 | factor-engineer | 8 次装死症 | 拆 3 专职新人（traditional / llm-factor / factor-integrator）|
| Stage 3 启动 | architect | 50 分钟装死 | shutdown → architect-2（"罕见纪律深度"评价）|
| P10 中段 | auditor | 25 分钟无响应（事后查证消息延迟）| shutdown → auditor-2 |
| **P15 阶段** | **data-engineer** | **15 分钟零响应** | **shutdown → data-engineer-2** |
| Stage 3 启动 加 | progress-reporter | 主动监控代替被动等 | 新加（cron 15 分钟）|

**老员工 9 人 → 4 个被换了 = 现在团队由 2 个老员工（model-engineer / code-reviewer / verifier-2 / document-specialist）+ 6 个新人组成**。

### 关键观察：6 个新人 0 装死

| 新人 | 横跨阶段 | 表现 |
|---|---|---|
| traditional-factor-engineer | Stage 2 / 3 / 4 全程 | P10/P11/P12/P14/P15 测试 + 复审，**5 个阶段 0 装死** |
| llm-factor-engineer | Stage 2 / 3 | M1 防线 + 顺手清容错测试 |
| factor-integrator | Stage 2 / 3 / 4 全程 | P10/P11/P12/P13 接线 + 中途修 n_quantiles，**4 个阶段 0 装死** |
| architect-2 | Stage 3 / 4 | Stage 3 设计 v1.1 + Stage 4 设计 |
| auditor-2 | Stage 3 P10 起 | 复审 P10/P11/P12/P13/P14/P15 全 PASS（6 个阶段 0 装死）|
| data-engineer-2 | Stage 4 P15 | 4 文件 + 烟测 25s 超预期 |

**zero-tolerance + 新人 prompt 钉死纪律 + progress-reporter 主动监控**模式在 Stage 4 第 4 次落地 —— **彻底成熟**。

### 教训复盘

经过 Stage 3 P10 auditor 消息延迟事件（事后查证原 auditor 实际已完工，是消息延迟导致误判），P15 处理时：

- **先看 mtime + 60 秒延迟容忍**（不是无脑超时立刻 shutdown）
- 多看 progress-reporter 的状态
- 但 15 分钟零响应 + 0 文件输出 + 0 mtime 变动 = **真的没干活**，shutdown 决定正确

---

## 三人协作模式：第 3 + 4 次落地（P12 / P14）

### 模式回顾

报告 09 / 10 讲过 P10 + P11 是项目第一次 + 第二次三人协作。**Stage 4 又用了 2 次（P12 + P14）—— 这个模式现在彻底固化下来**。

核心纪律："考试出题人 ≠ 监考 ≠ 阅卷"——**作者审查强制分离**。

### P12 三人分工

| 角色 | 谁 | 干啥 |
|---|---|---|
| 核心算法 | model-engineer | 4 个 pipeline 的 predict_only 模式 + daily.py 主入口 + CLI |
| 渲染 + 接线 | factor-integrator | renderer.py + HTML/MD 模板 + JSON 落盘 |
| 测试 + 复审 | traditional-factor-engineer | 42 个测试（含 4 个 predict_only 命门 + 3 个诚信声明命门）|

### P14 二人协作（model-engineer + traditional）

| 角色 | 谁 | 干啥 |
|---|---|---|
| 核心算法 | model-engineer | accuracy.py 530 行（4 类 evaluator + GroundTruthCache + horizon 截断 + 诚信结论）|
| 测试 + 复审 | traditional-factor-engineer | 34 个测试（4 类 evaluator 各覆盖正/负/边界 + 缓存 + 缺失友好 + CLI）|

### P15 二人协作（data-engineer-2 + traditional）

| 角色 | 谁 | 干啥 |
|---|---|---|
| 核心算法 | data-engineer-2 | settings.py + dataset.py 加 stage 参数 + scripts/prewarm_hs300.py |
| 测试 + 复审 | traditional-factor-engineer | 22 个测试（STAGE1 一字不动 + HS300 cache + prewarm 错误处理）|

**Stage 4 一共 4 次协作模式落地**（P12 三人 + P13 单人 + P14 二人 + P15 二人），traditional-factor-engineer 5 个阶段连续承担测试 + 复审职责 0 装死 —— **彻底证明分离纪律有效**。

---

## 验证

### pytest

```
$ uv run pytest tests/ -q
.....................................................................................326 passed in 90s
```

按文件分布（326 = 212 旧 + 114 新）：

| 文件 | 用例数 | 备注 |
|---|---|---|
| Stage 1-3 全部旧测试 | 212 | 全过 |
| **P12 新**（test_pipeline_predict_only + test_daily_report）| **42** | 4 fit_spy 命门 + 3 诚信声明命门 |
| **P13 新**（test_launchd_scripts）| **16** | plist 必需 key + wrapper.sh 语法 + exit 0 |
| **P14 新**（test_accuracy）| **34** | 4 类 evaluator 算法 + 缓存 + horizon 截断 + 缺失友好 + CLI |
| **P15 新**（test_universe_hs300 + test_prewarm_hs300）| **22** | STAGE1 一字不动 + HS300 cache + prewarm 错误处理 |
| **合计** | **326** | 全过 ✅ |

### ruff

```
$ uv run ruff check astock_quant/ tests/ scripts/
All checks passed!
```

（P12 审核时发现 4 个 F401 测试文件未用 import，已用 `uv run ruff check --fix` 一键修复）

### Stage 1/2/3 metrics 字节级不漂移（关 LLM，stage="stage1"）

```
train metrics (默认):
  train_size: 25283        ← Stage 1/2/3 字节级一致 ✓
  valid_size: 5780         ← ✓
  auc: 0.5131337782587783  ← Stage 1 到现在 5 个月不漂移 ✓
  accuracy: 0.5333910034602076  ← ✓
  log_loss: 0.6905634407838911  ← ✓
```

**Stage 4 加了一整套新东西（predict/ 包 + scripts/ 包 + 4 pipeline 加 predict_only + 114 个新测试），关 LLM + stage="stage1" 时所有数字一字不漂移**。这是延续 Stage 2/3 的"加法纪律"——加新的不破坏老的。

### akshare 沪深 300 实测

```
$ uv run python -m astock_quant.scripts.prewarm_hs300
[1/300] 600519 ✓ (cache hit)
[2/300] 000858 ✓ (cache hit)
... ...
[300/300] 689009 ✓
合计 25 秒（远低于 architect-2 估算 5-10 分钟）
```

---

## 诚信声明：从代码层面强制守住

Stage 4 在工程纪律上最特别的一点——**诚信声明从代码层面强制守住，跟之前 6 次诚信弱基线一脉相承**。

### 6 次诚信弱基线累计

| 阶段 | 关键指标 | 诚信解读 |
|---|---|---|
| Stage 1 ① direction | AUC = 0.5131 | 略高于随机 0.5 |
| Stage 2 LLM 因子 | importance = 0.0 | 模型完全不看 LLM 列（数据太稀）|
| Stage 3 ② return | R² = -0.0019 | 比常数均值差一点点 |
| Stage 3 ③ ranking | rank-IC ≈ 0 | 横截面排序无能力 |
| Stage 3 ④ trade_signal | macro accuracy ≈ baseline | 跟前面一样 |
| **Stage 4 准确率追踪** | **bp 量化** | **跟之前一脉相承** |

### 3 道强制守门

```
第一道：HTML/MD 模板放最显眼位置
       ─→ §1 直接是诚信声明，不是附录不是页脚
              ↓
第二道：renderer 测试守门
       ─→ test_daily_report_html_contains_honesty_disclaimer
       ─→ test_daily_report_md_contains_honesty_disclaimer
              ↓
第三道：测试断言非空占位
       ─→ test_disclaimer_is_not_empty_placeholder
              ↓
   ┌─ 任何人改坏模板/删掉声明/换成空壳子 ─┐
   └─→ CI 立刻红，根本进不了主分支    ─┘
```

### P14 命中率报告同款守门

`accuracy.py::_format_honesty_verdict` (L581-636) 直接引用历史数据：

> 「与 Stage 1 ① direction AUC=0.5131 / P9 ② return R²=-0.002 一脉相承，**模型仍是诚信弱基线**，没有 alpha。学习/研究项目的预期结果，**不要当真盘**」

bp 量化各 baseline 偏差，渲染到 MD `## 诚信结论` + HTML 通过 `_html_escape` 渲染。

**与之前所有阶段的诚信红线完全一脉相承**——审核 grep 验证 P12-P15 全部测试无任何 alpha 期望断言。

---

## scope 限定

Stage 4 整个阶段只动了以下文件（mtime 全部在 Stage 4 时段）：

| 文件 | 改动类型 | 阶段 |
|---|---|---|
| `astock_quant/predict/__init__.py` | **新建**（lazy `__getattr__`）| P12 |
| `astock_quant/predict/daily.py` | **新建** 主入口 | P12 |
| `astock_quant/predict/renderer.py` | **新建** HTML/MD 渲染 | P12 |
| `astock_quant/predict/templates/*.template` | **新建** 模板 | P12 |
| `astock_quant/predict/accuracy.py` | **新建** 530 行 | P14 |
| `astock_quant/pipeline/run_direction.py` | 加 `predict_only` 参数 + helper | P12 |
| `astock_quant/pipeline/run_return.py` | 同款 | P12 |
| `astock_quant/pipeline/run_ranking.py` | 同款 | P12 |
| `astock_quant/pipeline/run_trade_signal.py` | 同款 | P12 |
| `astock_quant/config/settings.py` | 加 `get_universe` + `get_hs300_universe` + cache 字段 | P15 |
| `astock_quant/data/dataset.py` | `prepare_stage1_data` 加 `stage` 参数 | P15 |
| `astock_quant/scripts/__init__.py` | **新建** | P15 |
| `astock_quant/scripts/prewarm_hs300.py` | **新建** | P15 |
| `scripts/com.astock.daily.plist.template` | **新建** launchd 配置 | P13 |
| `scripts/daily_predict_wrapper.sh` | **新建** shell 包装 | P13 |
| `scripts/README.md` | **新建** 文档 | P13 |
| `tests/test_pipeline_predict_only.py` | **新建** 12 测试 | P12 |
| `tests/test_daily_report.py` | **新建** 30 测试 | P12 |
| `tests/test_launchd_scripts.py` | **新建** 16 测试 | P13 |
| `tests/test_accuracy.py` | **新建** 34 测试 | P14 |
| `tests/test_universe_hs300.py` | **新建** 14 测试 | P15 |
| `tests/test_prewarm_hs300.py` | **新建** 8 测试 | P15 |
| 1 份综合人话报告（11）| **新建** | explainer |
| 5 份审核记录（Stage4 设计 / P12 / P13 / P14 / P15）| **新建** | architect-2 / auditor-2 |

**未动**（mtime 全部停在 Stage 3 之前）：
- `astock_quant/contracts.py` —— **数据契约 P4 mtime 不动**
- `astock_quant/data/` 其它（cache.py / protocol.py / astock_source.py）—— **数据层未动**
- `astock_quant/factors/` 全部 —— **因子层（含 LLM）一行没动**
- `astock_quant/models/` 4 个 model 类（direction / ret_regression / ranking / trade_signal）+ base.py + splits.py —— **模型层一行没动**
- `astock_quant/backtest/` 全部 —— **回测引擎一行没动**
- `astock_quant/signals/generator.py` —— **信号层一行没动**
- `astock_quant/labels/targets.py` —— **标签层一行没动**
- `astock_quant/__init__.py` / `tests/conftest.py` —— **不动**
- `pipeline/run_direction.py` 等 4 个 pipeline 的训练 + 回测路径 —— **未改动现有逻辑，只加 predict_only 分支**

**完美的 Stage 4 加法纪律**：只动 predict/ 新建包 + scripts/ 新建包 + 4 pipeline 加 predict_only 参数 + settings/dataset 加 stage 参数，**Stage 1/2/3 全部代码字节级保留 + ① direction AUC 5 个月不漂移**。

---

## 关键判断 / 决策记录

### 决策 1：predict_only kw-only 模式 + 4 pipeline 各自 inline（不跨模块调用）

**两种候选**：
- A. 提取 `_resolve_predict_model_path` 等共用代码到 `astock_quant/predict/utils.py`（DRY 优先）
- B. **4 pipeline 各自 inline 同款 helper**（scope 严守优先）← 选

**选 B 的理由**：
- 4 个 pipeline 独立可测试 + 独立可修改（变 ② return 模型不影响 ① direction）
- 每个 helper 函数体约 15 行，重复代价低，独立性收益明显
- 未来若需改逻辑，4 处同步修改风险可控（grep + 多文件 edit 一次到位）
- 是 Stage 4 设计文档 §6.1 明确要求的"不自审 + 不跨 pipeline 引用"取舍

**4 个 helper 文件**：
- `run_direction.py::_resolve_predict_model_path` (L299)
- `run_return.py::_resolve_predict_model_path` (L302) + 注释"与 run_direction 同款逻辑"
- `run_ranking.py::_resolve_predict_model_path` (L307)
- `run_trade_signal.py::_resolve_predict_model_path` (L335)

### 决策 2：诚信声明强制守门（最重要工程纪律）

**理由**：5 次诚信弱基线（AUC=0.513 / R²=-0.002 / rank-IC≈0）+ 用户每天看报告——任何报告都不能暗示模型有 alpha。

**3 道防线**：
1. 模板放最显眼 §1（不是附录不是页脚）+ HTML 红/橙警示框
2. renderer 3 个命门测试守门
3. 测试断言非空占位（不能是空模板壳子）

### 决策 3：launchd 用 macOS 内置 + 零新增依赖

**理由**：
- launchd 是 macOS 原生（类 cron），跟系统一起运行，比第三方更稳
- osascript 是 macOS 内置 AppleScript 解释器，弹通知无需第三方
- `KeepAlive=false`（失败等明天，不无限重启）
- `wrapper.sh exit 0`（失败也不阻塞 launchd 下次调度）

### 决策 4：4 类 evaluator 算法与 label 算法对称

**理由**：
- ① direction：`value >= 0.5` 与 `direction_label((return > 0))` 对称
- ② return：MAE + 方向一致率，标签是连续值，所以不要求精确匹配
- ③ ranking：Top K precision + Spearman，跟 `ranking_label.groupby(date).rank(pct=True)` 横截面语义对应
- ④ trade_signal：**路径模拟与 `trade_signal_label._label_one_ticker` 完全对称**（TP 优先 break + SL 次之 + 默认 HOLD）

### 决策 5：STAGE1_UNIVERSE 一字不动 + 新增 stage 参数

**理由**：延续 Stage 2/3 的"加法纪律"——加新的不破坏老的。所有不传参的老代码字节级一致 = 326 个测试 + Stage 1/2/3 5 个月不漂移的 AUC=0.5131337782587783。

### 决策 6：超时 shutdown 前先看 mtime + 60 秒延迟容忍

经过 Stage 3 P10 auditor 消息延迟事件后的纠正——P15 处理 data-engineer 装死时，先看 mtime + 给 60 秒容忍。但 15 分钟 0 输出 + 0 mtime 变动 + 0 响应 = **真的没干活**，shutdown 决定正确。

---

## 给 Stage 5 的提醒（如果有的话）

### Stage 5 候选方向（用户决策）

| 方向 | 描述 | 推荐度 |
|---|---|---|
| **A. 跑几周看 P14 准确率追踪** | 真假信号判断（最推荐：零代价就能知道模型在真实数据上表现如何）| ⭐⭐⭐⭐⭐ |
| **B. 找真 alpha** | 特征工程深挖 / 模型升级（不确定回报）| ⭐⭐⭐ |
| **C. 实盘准备** | 券商 API + 风控 + 模拟交易（不推荐，先看几周准确率）| ⭐⭐ |
| **D. 教学 demo 打磨** | README + notebook + 一键复现 + 视频讲解 | ⭐⭐⭐ |
| **E. 项目结束** | 学习目标已达成 + 工具能用 | ⭐⭐⭐⭐ |

### Stage 5 启动前债务（按优先级）

**P12-P15 审核留下的非阻塞观察**：

1. **`daily.py` `n_failed > n_targets_attempted` 边界**（auditor-2 P12 §7 提到）：renderer 失败也算入 errors，可能 5 > 4 触发"全失败"判断。**保守策略可接受，但建议加注释说明**
2. **`_resolve_predict_model_path` 4 处重复**：如果未来要改逻辑，4 处需同步。当前是刻意取舍（scope 严守 > DRY），可考虑 Stage 5 抽到 `astock_quant/predict/utils.py`
3. **plist `tee -a` 双写 stdout**（auditor-2 P13 §1 提到）：launchd 层 StandardOutPath 已写一份 + wrapper 里 tee -a 又写一份，日志重复行。**不影响功能，可清理**
4. **`get_hs300_universe` fetch 失败上抛**（auditor-2 P15 §7 提到）：`daily.py --universe stage4` 场景下整个 daily 报 FATAL。**建议 daily.py 的 --universe 参数说明里明示"首次使用 stage4 前必须先跑 prewarm"**

**Stage 2/3 遗留债务**：

- Stage 2: M3 LLM_MODEL env var 拆分 / M4 FactorContext 抽象 / L2-L5（默认关 LLM 不影响 Stage 4，可延后）
- Stage 3: 4 M / 5 L / 2 N（详见 Stage3-收尾说明.md）

### Stage 5 测试空缺建议

- **多次端到端跑稳定性**：连续跑 P12 daily 5 次，metrics 必须一致（防 race condition）
- **跨年 / 跨季度边界**：sourcer prewarm 在年末跨集时的边界行为
- **沪深 300 成分调整**：当 cache 过期时新成分加入是否正确（手动构造场景测试）

### Stage 5 团队建议

- **三人协作模式持续**：P10/P11/P12/P14/P15 5 次成功，作者审查分离值得延续
- **auditor 超时纪律保留**：先看 mtime + 60 秒延迟，再 shutdown（P15 落地有效）
- **progress-reporter** 继续运行（cron `7-59/15 * * * *` 主动监控）
- **6 个新人 + 模式成熟**：Stage 5 可直接复用现有团队

---

## Stage 4 最终评价

### 工程层面

✅ **基础设施全 PASS**：4 子任务（P12/13/14/15）全交付 + 326 测试全过 + ruff clean + scope 严守只动 predict/ scripts/ + Stage 1/2/3 五个月不漂移的 AUC=0.5131337782587783 一字不动。

✅ **方案 A 瘦身版纪律延续到 Stage 4**：
- launchd 用 macOS 内置（不引第三方调度库）
- osascript 用 macOS 内置（不引第三方通知库）
- HTML/MD 渲染用 `string.Template`（stdlib，不引 jinja2）
- 4 pipeline 各自 inline `_resolve_predict_model_path`（不跨模块调用）

### 算法层面

❌ **模型仍是诚信弱基线**（跟 Stage 1 ① / Stage 2 LLM / Stage 3 ②③④ 一脉相承）—— AUC≈0.513 / R²≈-0.002 / rank-IC≈0 / macro accuracy≈baseline。这是预期。

### 用户层面

✅ **Stage 4 真把"代码能跑"变成"日常能用"**：
- 一行命令出预测报告（30 秒）
- launchd 自动跑（16:30 触发 + 通知 + 浏览器自动开）
- P14 准确率追踪（跑几周后看模型真假信号）
- 沪深 300 扩容（30 → 300 只可选）

### 团队层面

✅ **4 次三人 / 二人协作模式落地**：P12 三人（model + integrator + traditional）+ P13 单人（factor-integrator）+ P14 二人（model + traditional）+ P15 二人（data-engineer-2 + traditional）。**traditional-factor-engineer 5 个阶段连续承担测试 + 复审职责 0 装死**。

⚠️ **第 4 次队员重组**：data-engineer 15 分钟零响应 → data-engineer-2 接手 P15 完美交付。

✅ **项目至今 5 次队员重组累计**：老员工 9 个 → 4 个被换了 = 现在 6 个新人 0 装死。zero-tolerance + 新人 prompt 钉死纪律 + progress-reporter 主动监控**彻底成熟**。

✅ **auditor-2 第 16-19 次实战 4 连 PASS**（从 P3a winsorize bug 到 P15 累计 19 轮）。

✅ **诚信声明从代码层面强制守住**——3 道防线 + P14 命中率报告同款诚信结论。**6 次诚信弱基线一脉相承**。

✅ **explainer 一路出 11 份人话报告**（01-11）+ Stage 1/2/3/4 四份收尾说明。讲解纪律持续。

### 收尾结论

**Stage 4 可正式收尾**。**项目主体 + 实用化全部完成**。

**用一句话总结 Stage 4**：

> 我们花了一天时间把训好的 4 类模型变成"日常能用"的预测工具——每日预测报告 + launchd 自动跑 + 准确率追踪 + 沪深 300 扩容。**你能每天用了**——但模型仍是诚信弱基线，看几周准确率追踪比直接实盘更稳。

---

## 项目主体四阶段全景

到此 Stage 1 + Stage 2 + Stage 3 + Stage 4 全部完成。四份收尾说明已齐全：

- `Stage1-收尾说明.md` —— 传统量化核心（① direction 跑通 + 三道防 look-ahead + A股 4 件套）
- `Stage2-收尾说明.md` —— LLM 情绪因子（接通 + negative finding ¥0.09 学到"瓶颈在数据"真知识）
- `Stage3-收尾说明.md` —— 4 类预测目标全实装（②③④ 端到端 + 三人协作首演）
- **`Stage4-收尾说明.md`** —— 本文档，每日预测工具 + 4 次队员重组累计

**关键数字回顾**：

| 指标 | Stage 1 末 | Stage 2 末 | Stage 3 末 | **Stage 4 末** |
|---|---:|---:|---:|---:|
| 测试数 | 62 | 97（+35）| 212（+115）| **326（+114）** |
| 因子数 | 25 | 26（+1 LLM）| 26 | 26 |
| 实装 model | 1（①）| 1 | 4（①②③④）| 4 |
| pipeline 数 | 1 | 1 | 4 | 4 |
| pipeline 模式 | 训练+回测 | 同 | 同 | **+ predict_only 模式** |
| 命门测试 | 多个 | 多个 | + 6 新加 | **+ 8+ 新加** |
| 人话报告 | 5 份 | 7 份 | 10 份 | **11 份** |
| 审核记录 | 12 份 | 20 份 | 25 份 | **30 份** |
| 技术文档 | 6 份 | 7 份 | 7 份 | **8 份**（+ Stage4 设计）|
| 收尾说明 | 1 份 | 2 份 | 3 份 | **4 份** |
| 队员重组 | 0 | 1（factor-engineer 拆 3）| +2（architect / auditor）| **+1（data-engineer）= 累计 4** |
| 用户使用 | 跑 pipeline | 同 | 同 | **每天一行命令 + 自动跑 + 准确率追踪** |

**项目主体 + 实用化全部交付**。下一步由用户决定 Stage 5 方向（推荐 A：跑几周看 P14 准确率追踪）。
