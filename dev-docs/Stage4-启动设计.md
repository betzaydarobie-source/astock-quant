# Stage 4 启动设计 —— 每日预测报告工具

> architect-2 产出 · 2026-05-16 · 基于实际代码 grep + Stage1/2/3 收尾说明
>
> Stage 4 把"训练好的模型"变成"每天能用的预测工具"。不实盘，出预测报告。

---

## 0. 前置状态确认（实际代码 grep 结果）

| 层级 | 状态 | 关键事实 |
|---|---|---|
| 4 个 pipeline 入口 | **全部真实存在** | `pipeline/run_direction.py` / `run_return.py` / `run_ranking.py` / `run_trade_signal.py` 均已实装 |
| 测试 | **212 / 212 PASS** | 97 旧 + 115 新（Stage 3 收尾）|
| 当前 universe | **30 只蓝筹** | `config/settings.py::STAGE1_UNIVERSE`，`Settings.universe` 默认指向它 |
| 信号生成 | **4 分支全实装** | `signals/generator.py` 含 `_generate_direction/return/ranking/trade_signal` |
| 数据拉取入口 | `data/dataset.py::prepare_stage1_data` | 支持 `universe` 参数 + `curr_date` look-ahead 截断 |
| 模型实测结果 | **5 次诚信弱基线** | AUC=0.513 / R²=-0.002 / rank-IC≈0 / macro-acc≈baseline —— 无 alpha，符合学习项目定位 |
| artifacts 目录 | `量化/artifacts/` | 模型文件存放位置，已 gitignore |

**Stage 4 的核心工作**：整合（不是重写）。4 个 pipeline 已存在，Stage 4 在它们之上加一层"每日调度 + 报告渲染 + 历史记录"。

---

## 1. 子阶段拆解

### 1.1 总体四阶段

```
P12 —— 每日预测报告（核心交付）    ← 最先做，用户最直接感受
P13 —— 自动跑（launchd + 通知）    ← P12 稳了再自动化
P14 —— 准确率追踪                  ← 依赖 P12 落盘历史预测
P15 —— 股票池扩到沪深 300          ← 最后做，data volume 最大，风险独立
```

### 1.2 P12 —— 每日预测报告

**入口**：Stage 3 收尾完成（212 测试 PASS）即开始。

**出口**：
- `astock_quant/predict/daily.py` —— `run_daily_predict(date, universe, targets)` 函数
- `scripts/daily_predict.py` —— 命令行薄壳，`uv run python scripts/daily_predict.py --date today`
- HTML 报告输出到 `artifacts/reports/YYYY-MM-DD.html`
- Markdown 报告同步输出到 `artifacts/reports/YYYY-MM-DD.md`
- 预测结果 JSON 存到 `data_cache/daily_predictions/YYYY-MM-DD.json`（P14 准确率追踪用）
- P12 新增测试全过（目标 ~15 个）
- 审核 PASS

**主笔**：factor-integrator（擅长接线和编排，P11 跑通了 `run_trade_signal.py`）

**配角协助**：
- model-engineer 确认 4 个 pipeline 的 `predict_only` 模式接口（只推理不训练）
- explainer 负责报告里的中文说明文字和诚信声明

**时间预估**：3-4 天

---

### 1.3 P13 —— 自动跑

**入口**：P12 完成且能稳定跑出报告。

**出口**：
- `scripts/install_launchd.py` —— 一键安装 launchd plist 到 `~/Library/LaunchAgents/`
- `scripts/uninstall_launchd.py` —— 一键卸载
- `com.astock.daily_predict.plist` 模板文件（项目内，安装时 copy）
- macOS `osascript` 通知：成功时"今日预测报告已生成"+ 自动 `open` 浏览器；失败时"预测失败，查看日志"
- 错误日志落 `~/.astock_quant/logs/YYYY-MM-DD.log`
- 手动测试（不写 pytest，launchd 行为难 mock）+ 文档说明
- 审核 PASS

**主笔**：factor-integrator（shell 脚本 + plist 配置）

**时间预估**：1-2 天

---

### 1.4 P14 —— 准确率追踪

**入口**：P12 完成（依赖 `data_cache/daily_predictions/` 的历史 JSON 积累；P14 实现后，P12 的 JSON 才有人读）。

**出口**：
- `astock_quant/predict/accuracy.py` —— `compute_accuracy(days_back)` 函数
- `scripts/predict_accuracy.py` —— `uv run python scripts/predict_accuracy.py --days 30`
- 输出：per-target 命中率表 + 按 ticker 命中率 + 过去 N 天趋势
- P14 新增测试全过（目标 ~10 个，主要是 JSON 解析 + 命中率计算逻辑）
- 审核 PASS

**主笔**：model-engineer（命中率统计逻辑涉及 label 语义理解）

**时间预估**：2-3 天

---

### 1.5 P15 —— 股票池扩到沪深 300

**入口**：P12 稳定（先在 30 只上验证报告流程，再扩）。P15 可与 P13/P14 并行，但需要先 prewarm 数据（约 1 小时拉数）。

**出口**：
- `config/settings.py` 新增 `STAGE4_UNIVERSE: list[str]`（沪深 300 成分，保留 `STAGE1_UNIVERSE` 兼容）
- `astock_quant/data/universe.py` —— `fetch_csi300_components()` 从 akshare 拉最新成分股
- `scripts/prewarm_data.py` —— 首次拉取 300 只数据（预计 ~60 分钟，带进度条）
- 验证：300 只 universe 下 run_direction 能跑通（不要求 AUC 提升，只要不 crash）
- 数据量 + 训练时间评估文档（`量化/人话报告/XX-沪深300扩容.md`）
- P15 新增测试全过（目标 ~5 个，主要是 `fetch_csi300_components` mock 测试）
- 审核 PASS

**主笔**：data-engineer（数据层专职）

**时间预估**：2-3 天（含等待数据 prewarm 时间）

---

### 1.6 子阶段总览表

| 阶段 | 目标 | 新增/主改文件 | 直接复用 | 主笔 | 时间 |
|---|---|---|---|---|---|
| P12 | 每日预测报告 | `predict/daily.py` + `scripts/daily_predict.py` + HTML/MD 模板 | 4 个 pipeline（不改）/ `signals/generator.py`（不改）| factor-integrator | 3-4 天 |
| P13 | 自动跑 | plist 模板 + `install_launchd.py` + `uninstall_launchd.py` | P12 的 `scripts/daily_predict.py` | factor-integrator | 1-2 天 |
| P14 | 准确率追踪 | `predict/accuracy.py` + `scripts/predict_accuracy.py` | P12 的 JSON 落盘 | model-engineer | 2-3 天 |
| P15 | 扩沪深 300 | `data/universe.py` + `config/settings.py`(加字段) + `scripts/prewarm_data.py` | `data/dataset.py`（universe 参数已支持）| data-engineer | 2-3 天 |
| **合计** | | | | | **8-12 天** |

---

## 2. 每日预测报告设计

### 2.1 报告结构

HTML 报告分 5 节，Markdown 同步输出相同内容：

```
📅 A股每日预测报告 — YYYY-MM-DD
══════════════════════════════════

§1 今日摘要
  - 预测日期 / 使用模型版本（artifacts/ 里最新的 .lgb 文件）
  - 4 类模型运行状态（✅ 成功 / ⚠️ 部分失败）
  - universe 规模（30 只 / 300 只）

§2 各模型今日信号
  ① 涨跌方向（DirectionModel）
     - 买入信号：{ticker} P(涨)=0.62、{ticker} P(涨)=0.58、...（Top 5，降序）
     - 卖出信号：{ticker} P(涨)=0.41、...（Bottom 5）
     - 中性持有：其余 N 只
  ② 预期收益（ReturnRegressor）
     - 预期收益 ≥ +2%：{ticker} +3.1%、...（Top 5）
     - 预期收益 ≤ -2%：{ticker} -2.4%、...（Bottom 5）
  ③ 横截面排名（RankingModel）
     - Top N 持仓候选：{ticker}（分位 0.92）、...
  ④ 买卖点信号（TradeSignalModel）
     - 买入点：{ticker}（TP 概率 0.55）、...
     - 止损点：{ticker}（SL 概率 0.61）、...

§3 诚信声明（显眼位置，不可省略）
  ⚠️ 本报告所有预测基于历史数据统计模型。
  - 当前模型验证集 AUC = 0.513（随机猜 AUC = 0.5）
  - R² = -0.002（收益率预测不如猜均值）
  - 以上信号不构成投资建议，仅供学习研究使用
  - 过去表现不代表未来收益

§4 历史准确率（如果 P14 已落盘数据）
  - 过去 N 天 direction 命中率：52%（近似随机）
  - 数据不足时显示"数据积累中（已有 X 天）"

§5 运行元数据
  - 生成时间 / 耗时 / 数据更新时间 / 模型文件路径
```

### 2.2 入口函数设计

新建 `astock_quant/predict/` 包：

```
astock_quant/predict/
  __init__.py
  daily.py       ← 核心：调 4 个 pipeline → 整合结果 → 渲染报告
  accuracy.py    ← P14：读历史 JSON → 计算命中率
  renderer.py    ← HTML/MD 渲染（Jinja2 模板 or stdlib string.Template）
  templates/
    report.html  ← HTML 模板
    report.md    ← Markdown 模板
```

**`daily.py` 核心函数**：

```python
def run_daily_predict(
    date: str | None = None,          # None → 今天；"2026-05-15" → 指定日
    universe: list[str] | None = None, # None → SETTINGS.universe
    targets: list[str] | None = None,  # None → 全部 4 类；["direction","return"] → 指定
    save_json: bool = True,            # 落盘 JSON（P14 准确率追踪用）
    open_browser: bool = False,        # P13 launchd 调时传 True
) -> dict:
    """每日预测主入口.

    返回：{"date": ..., "predictions": {...}, "report_path": Path, "errors": [...]}
    """
```

**关键实现细节**：
- 4 个 pipeline 的 `predict_only` 模式：不重新训练，只加载 `artifacts/` 里的已训练模型做推理
- 如果模型文件不存在（首次跑或 artifacts 被清空），自动触发训练（带警告日志）
- 任一 pipeline 失败不影响其他（`try/except` 隔离，失败的在报告里标 ⚠️）
- `curr_date` 透传给 `prepare_stage1_data`，严格防 look-ahead

### 2.3 命令行用法

```bash
# 最简：跑今天
uv run python scripts/daily_predict.py

# 指定日期（回测某天）
uv run python scripts/daily_predict.py --date 2026-05-15

# 只跑部分模型
uv run python scripts/daily_predict.py --targets direction return

# 指定股票池
uv run python scripts/daily_predict.py --universe 600519,000858,600036

# 跑完自动开浏览器
uv run python scripts/daily_predict.py --open-browser

# 查帮助
uv run python scripts/daily_predict.py --help
```

### 2.4 依赖选型

**HTML 渲染**：优先用 `string.Template`（stdlib，零新增依赖）。如果模板复杂度超过 stdlib 能力，引入 `jinja2`（`pyproject.toml` 加 `jinja2>=3.0`，轻量）。

**图表**：报告里不生成 matplotlib 图（耗时 + 复杂度），用纯文字 ASCII 横条图表示信号强度。可选：P14 准确率追踪里加一张简单折线图。

**不引入**：`flask` / `fastapi`（不做 web server）/ `pandas-profiling` / 任何重框架。

### 2.5 复用 vs 新建边界

**直接复用（Stage 3 产物，一行不动）**：
- `pipeline/run_direction.py` / `run_return.py` / `run_ranking.py` / `run_trade_signal.py`
- `signals/generator.py`（4 分支全实装）
- `backtest/` 全部（每日预测不跑回测，但 pipeline 内部会调，不影响）
- `models/` 全部（只加载已训练模型，不改模型代码）
- `data/dataset.py`（`prepare_stage1_data` 支持 `universe` + `curr_date`，直接用）

**新建（Stage 4 专有）**：
- `astock_quant/predict/` 包（4 个文件）
- `scripts/daily_predict.py` / `predict_accuracy.py` / `install_launchd.py` / `uninstall_launchd.py` / `prewarm_data.py`
- `astock_quant/data/universe.py`（P15，沪深 300 成分拉取）
- HTML/MD 模板文件

**需要扩展（加法，不改已有逻辑）**：
- `config/settings.py`：加 `STAGE4_UNIVERSE` + `PredictConfig`（报告输出路径、通知开关等）
- `astock_quant/pipeline/run_*.py`：确认是否已支持 `predict_only` 模式（加载已有模型不重训）；如果没有，各加一个 `predict_only: bool = False` 参数分支

---

## 3. 自动跑方案

### 3.1 launchd plist 设计

文件路径：`~/Library/LaunchAgents/com.astock.daily_predict.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.astock.daily_predict</string>

    <key>ProgramArguments</key>
    <array>
        <!-- uv 绝对路径：install_launchd.py 运行时自动探测 `which uv` 填入 -->
        <string>/Users/USERNAME/.local/bin/uv</string>
        <string>run</string>
        <string>python</string>
        <string>/Users/USERNAME/claude code/量化/scripts/daily_predict.py</string>
        <string>--open-browser</string>
    </array>

    <!-- 每天 16:30 触发（A股 16:00 收盘，留 30 分钟数据更新） -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>16</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>

    <!-- 工作目录设为项目根 -->
    <key>WorkingDirectory</key>
    <string>/Users/USERNAME/claude code/量化</string>

    <!-- 日志 -->
    <key>StandardOutPath</key>
    <string>/Users/USERNAME/.astock_quant/logs/daily_predict.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/USERNAME/.astock_quant/logs/daily_predict_err.log</string>

    <!-- 不自动重启（失败了等明天）-->
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
```

**`scripts/install_launchd.py` 做的事**：
1. `which uv` 探测 uv 路径
2. 用实际用户路径替换模板里的 `USERNAME` 和项目根路径
3. 写到 `~/Library/LaunchAgents/com.astock.daily_predict.plist`
4. `launchctl load` 注册
5. 打印确认信息 + "第一次运行时间：今天 16:30（如果现在 < 16:30）或明天 16:30"

### 3.2 通知方式

**成功时**：
```bash
# macOS 系统通知
osascript -e 'display notification "今日预测报告已生成，点击查看" with title "A股预测" subtitle "2026-05-16"'
# 自动打开浏览器
open artifacts/reports/2026-05-16.html
```

**失败时**：
```bash
osascript -e 'display notification "预测脚本失败，查看日志: ~/.astock_quant/logs/" with title "A股预测 ⚠️"'
```

两种通知都通过 `daily_predict.py` 内部 `subprocess.run(["osascript", ...])` 调用，**不依赖第三方库**。

### 3.3 错误处理策略

| 场景 | 处理方式 |
|---|---|
| 某个 pipeline 抛异常 | `try/except` 隔离，其余 pipeline 继续跑；报告里标 ⚠️；错误信息写日志 |
| 所有 pipeline 失败 | 发失败通知 + 写错误日志；不生成报告 |
| 数据拉取超时 | `astock_source` 已有 retry 逻辑；超时后 pipeline 标 ⚠️ |
| 模型文件不存在 | 自动触发训练（可能耗时 ~2 分钟）+ 警告日志；首次跑可能到 16:32 才出报告 |
| 节假日（A股休市）| 脚本仍跑，但数据层不会有新 T 日数据；报告里注明"今日为非交易日，使用最近交易日数据" |
| `launchd` 系统休眠跳过 | 用户唤醒后补跑：`uv run python scripts/daily_predict.py --date 2026-05-15` |

---

## 4. 准确率追踪

### 4.1 历史预测 JSON 格式

`data_cache/daily_predictions/YYYY-MM-DD.json`：

```json
{
  "date": "2026-05-16",
  "generated_at": "2026-05-16T16:32:15",
  "universe": ["600519", "000858", ...],
  "predictions": {
    "direction": [
      {"ticker": "600519", "value": 0.62, "signal": "buy"},
      {"ticker": "000858", "value": 0.41, "signal": "sell"},
      ...
    ],
    "return": [...],
    "ranking": [...],
    "trade_signal": [...]
  },
  "errors": []
}
```

### 4.2 命中率计算逻辑

**direction 命中率**：
- 预测日 T 发出信号 → T+5 日（horizon=5）真实收益率 > 0 为"涨"，否则为"跌"
- `hit = (signal == "buy" and actual_return > 0) or (signal == "sell" and actual_return <= 0)`
- 命中率 = `sum(hits) / n_predictions`

**return 命中率**：
- 预测收益率 > 0 对应真实收益率 > 0，方向一致即命中（不要求预测值精确）

**ranking 命中率**：
- Top N 里真实涨幅排前 N/2 的比例（precision@N/2）

**trade_signal 命中率**：
- buy=1 且真实路径先触 TP 命中；sell=-1 且真实路径先触 SL 命中；hold=0 且两者都没触 命中

**注意**：命中率计算需要"T+horizon 日的真实价格"，所以 P14 只能看 N 天前的预测，当天和最近 5 天无法评估（horizon=5）。`predict_accuracy.py` 自动过滤掉还没到期的预测。

### 4.3 命令行用法

```bash
# 查过去 30 天准确率
uv run python scripts/predict_accuracy.py --days 30

# 查某个模型
uv run python scripts/predict_accuracy.py --days 30 --target direction

# 查某只股票
uv run python scripts/predict_accuracy.py --days 30 --ticker 600519

# 输出 CSV（方便自己分析）
uv run python scripts/predict_accuracy.py --days 30 --output csv > accuracy.csv
```

---

## 5. 股票池扩到沪深 300

### 5.1 数据源选型

**akshare `ak.index_stock_cons_csindex("000300")`** —— 拉沪深 300 当前成分股，返回 DataFrame 含 `成分券代码`（6 位）。

实测可用，不需要付费 API。成分股每季度调整（3/6/9/12 月），Stage 4 用**快照式**（写死一份列表，下次调整时手动更新 `STAGE4_UNIVERSE`），不做自动跟踪成分调整。

```python
# astock_quant/data/universe.py
import akshare as ak

def fetch_csi300_components() -> list[str]:
    """从 akshare 拉沪深 300 当前成分股（6 位代码）."""
    df = ak.index_stock_cons_csindex("000300")
    return df["成分券代码"].tolist()  # 约 300 只（含 A+H 双计，实际约 300）
```

### 5.2 STAGE4_UNIVERSE 设计

`config/settings.py` 新增：

```python
# 沪深 300 成分股（Stage 4 快照，2026-05 季调版本）
# 通过 `uv run python scripts/prewarm_data.py --fetch-universe` 更新
STAGE4_UNIVERSE: list[str] = [...]  # 由 data-engineer 用 fetch_csi300_components() 生成并硬编码

@dataclass(frozen=True)
class Settings:
    universe: list[str] = field(default_factory=lambda: list(STAGE1_UNIVERSE))  # 默认保持 30 只
    # Stage 4 用法：SETTINGS_300 = Settings(universe=STAGE4_UNIVERSE)
    ...
```

**不改 `SETTINGS` 默认值**（保持向后兼容，30 只 universe 的 212 个测试不漂移）。Stage 4 的 daily_predict.py 用 `--universe csi300` 参数切换。

### 5.3 数据量评估

| 维度 | Stage 1（30 只）| Stage 4（300 只）| 倍数 |
|---|---|---|---|
| 行情 CSV 数量 | 30 个 | 300 个 | 10x |
| 数据行数（4年×240交易日）| ~28,800 行 | ~288,000 行 | 10x |
| data_cache 磁盘 | ~50 MB | ~500 MB | 10x |
| 首次 prewarm 时间 | ~30 秒（实测）| ~300 秒（估算）| 约 5-10 分钟 |
| run_direction 训练时间 | ~32 秒（实测）| ~5-8 分钟（估算）| ~10x |
| 每日 4 类 pipeline 总时间 | ~2 分钟（估算）| ~20 分钟（估算）| ~10x |

**关键判断**：20 分钟对于"每天 16:30 跑"完全可接受（16:30 触发，16:50 出报告，用户 17:00 看）。如果超过预期，P15 收尾时报告实测时间，lead 决策是否需要优化（并行 pipeline / 增量训练）。

### 5.4 prewarm 脚本

```bash
# 首次拉取 300 只数据（约 5-10 分钟，带进度条）
uv run python scripts/prewarm_data.py

# 只更新最近 30 天（日常增量更新）
uv run python scripts/prewarm_data.py --days 30

# 顺便更新沪深 300 成分股列表并打印
uv run python scripts/prewarm_data.py --fetch-universe
```

---

## 6. 风险点

### 6.1 4 个 pipeline 的 predict_only 模式（P12 最高优先级）

**问题**：Stage 3 的 `run_direction.py` / `run_return.py` / `run_ranking.py` / `run_trade_signal.py` 都是"训练 + 回测"一体的，每次跑都重新训练。每日预测只需要"加载已有模型推理"，不需要重训。

**实测现状**：grep `run_direction.py` 没有 `predict_only` 参数（实测 4 个 pipeline 都没有），需要 P12 加入。

**风险**：如果 P12 factor-integrator 在 pipeline 里随意加 `if predict_only: skip_train()`，可能引入条件分支 bug（类似 P7 wiring bit-identical 教训）。

**防护措施**：
- `predict_only` 模式的逻辑改动必须有命门测试：`test_run_direction_predict_only_does_not_call_fit`（mock `DirectionModel.fit`，验证调用次数 = 0）
- model-engineer 负责设计 predict_only 接口，factor-integrator 负责接线，不自审
- 模型文件不存在时 predict_only 必须抛清晰错误（不能静默返回空预测）

### 6.2 akshare 数据拉取时间窗口

**问题**：A股 16:00 收盘，但 akshare / mootdx 的当日数据通常 16:00-16:30 才更新完。launchd 设 16:30 触发，可能有概率拉到 T-1 日数据而不是当日。

**风险**：报告日期显示"今天"，但实际是用昨天的数据预测"明天"——在信息上没问题（这本来就是合理的预测模式），但用户体验上需要在报告里明确写"基于 YYYY-MM-DD 收盘数据"。

**防护措施**：
- 报告 §5 元数据里写"数据截至 YYYY-MM-DD"（从 `cache.py` 读最新日期）
- 如果当日数据未就绪，用最近交易日数据并在报告里注明

### 6.3 诚信声明放显眼位置（工程纪律红线）

**背景**：5 次诚信弱基线（AUC=0.513 / R²=-0.002 / rank-IC≈0）。任何报告都不能暗示模型有 alpha。

**强制要求**：
- 诚信声明是 §3，不是附录，不是页脚
- HTML 报告里用红色/橙色警示框渲染（`background: #fff3cd`）
- Markdown 里用 `> ⚠️` 引用块
- **代码审查时必须验证诚信声明存在**（reviewer 在审核清单里加这一项）

### 6.4 沪深 300 第一次跑的 cold start

**问题**：data_cache 只有 30 只数据，300 只首次拉数需要 5-10 分钟。如果用户在 P15 安装完直接跑 `daily_predict.py --universe csi300`，可能等很久。

**防护措施**：
- P15 必须提供 `scripts/prewarm_data.py`，用户先跑 prewarm 再开 launchd
- prewarm 脚本要有进度条（`tqdm` 或 print 手动计数）+ 时间估算
- README 里写清楚"首次使用沪深 300 需先运行 prewarm，约 XX 分钟"

### 6.5 Stage 2 遗留债务与 Stage 4 的交叉

Stage2-收尾说明留的 M3（LLM_MODEL env var 拆分）、M4（FactorContext 抽象）、L2-L5 在 Stage 4 中**不影响**（Stage 4 默认关 LLM 因子）。可延后到 Stage 5 或用户明确要重做 LLM 实验时再处理。

---

## 附：Stage 4 启动 Checklist

P12 开工前，lead 确认：

- [ ] **pipeline predict_only 接口设计**（lead + model-engineer 对齐）：4 个 `run_*.py` 需要加 `predict_only: bool` 参数；model-engineer 给出接口草案，lead 批准后 factor-integrator 实装
- [ ] **模型文件命名约定**（lead 决策）：`artifacts/` 下模型文件命名规则（`direction_model.lgb` 还是带日期 `direction_20260516.lgb`？带日期更安全，daily_predict 加载最新）
- [ ] **报告输出路径确认**：`artifacts/reports/` 已在 gitignore，confirm `SETTINGS.artifacts_dir` 路径正确

> 已由 architect-2 自查确认（无需 lead 操作）：
> - 4 个 pipeline 文件全部真实存在（ls 确认）✅
> - `STAGE1_UNIVERSE` = 30 只，`Settings.universe` 默认指向它 ✅
> - `signals/generator.py` 4 分支全实装（grep 确认）✅
> - `dataset.py::prepare_stage1_data` 支持 `universe` + `curr_date` 参数 ✅
> - `config/settings.py` 无 `STAGE4_UNIVERSE`（待 P15 新增）✅
> - `astock_quant/predict/` 目录不存在（待 P12 新建）✅

---

## 变更记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| v1.0 | 2026-05-16 | architect-2 | 初版，Stage 4 整体规划 |
