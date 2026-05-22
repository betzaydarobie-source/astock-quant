# Stage 3 启动设计

> architect-2 产出 · 2026-05-16 · 基于 P1-架构设计.md + Stage1-收尾说明.md + Stage2-收尾说明.md + 实际代码结构
>
> 本文档定义 Stage 3（②③④ 三类预测目标）的子阶段拆解、接口设计、复用边界、测试预期、风险点和预算。

---

## 0. 前置状态确认

进入 Stage 3 时，基础设施已有：

| 层级 | 状态 | 文件 |
|---|---|---|
| 数据层 | Stage 1 已完整实现 | `data/` 全部 4 个文件 |
| 因子层 | 25 个量价/财务/资金流因子 + LLM 因子（负面结果） | `factors/` 全部 7 个文件 |
| 标签层 | direction 做透，②③④ 均为 stub | `labels/targets.py` |
| 模型层 | DirectionModel 做透，②③④ 均为 stub | `models/` ret_regression.py / ranking.py / trade_signal.py |
| 回测层 | 完整实现 + A股约束 + missing_prediction_action config | `backtest/` 全部 4 个文件 |
| 信号层 | generator.py 已实装 4 分支 stub 策略（direction 完整，②③④ 有基础逻辑待升级） | `signals/generator.py` |
| 管道层 | run_direction.py 做透 | `pipeline/run_direction.py` |
| 测试 | 97 个全过，ruff clean | `tests/` |

**关键起点**：③ 横截面排序（ranking）是 Stage 3 中 look-ahead bias 风险最高的目标，必须作为设计重心。

---

## 1. 子阶段拆解

### 1.1 总体三阶段

```
P9  ── 收益率回归（②） ── 最快落地，复用度最高，作为热身
P10 ── 横截面 Top N（③）── 重点/难点，横截面 rank 是新的 look-ahead 高风险区
P11 ── 信号扩展（④）    ── entry/exit 细化 + stop-loss/take-profit，收尾阶段
```

### 1.2 P9 —— 收益率回归（②）

**入口**：Stage 3 启动后立刻开始，依赖 Stage 2 收尾完成（97 测试全过）。

**出口**：
- `labels/targets.py` 的 `return_label()` 函数完整实现
- `models/ret_regression.py` 的 `ReturnRegressor` 完整实现
- `pipeline/run_return.py` 跑通端到端
- `signals/generator.py` 的 `return` 分支升级为真实策略（现有 stub 已有 `v > 0` 占位，升级为阈值可配版本）
- P9 新增测试全过，指标（RMSE/IC/rank-IC）可观测
- 审核 PASS + verifier 端到端 PASS

**依赖**：`data/` / `factors/` / `backtest/engine.py` 全部直接复用，不改动。

**主笔**：model-engineer（同 Stage 1 P3b，熟悉 LightGBM + splits）

**配角协助**：
- traditional-factor-engineer 确认 `factors/registry.py` 的 `FactorFrame` 输出对回归 label 无需改动
- factor-integrator 接线 `pipeline/run_return.py`

**时间预估**：3-4 天

---

### 1.3 P10 —— 横截面 Top N（③）

**入口**：P9 完成且 PASS（run_return.py 跑通是前置，证明基础设施可复用路径无误）。

**出口**：
- `labels/targets.py` 的 `ranking_label()` 完整实现，**横截面 rank 计算必须守住 look-ahead 红线**
- `models/ranking.py` 的 `RankingModel` 完整实现（LightGBM regressor 分数排序 OR LGBMRanker，选型见 §2）
- `pipeline/run_ranking.py` 跑通端到端
- `signals/generator.py` 的 `ranking` 分支升级为真实横截面排序策略（现有 stub 已有基础 score 排序逻辑）
- `backtest/engine.py` 已在 P4 实现「按 score 降序 Top-K 等权配资」逻辑，ranking 模式**不需要改引擎**，调整 buy_threshold 即可天然 Top N
- **P10 特有命门测试**：横截面 rank 不泄漏（详见 §4）
- 审核 PASS + verifier 端到端 PASS

**依赖**：P9 完成。`data/` / `factors/` / `backtest/engine.py` 全部直接复用，不改动（引擎 P4 已有 Top-K 逻辑，见 §2.3）。

**主笔**：model-engineer（横截面排序需要理解 group-aware cross-validation）

**配角协助**：
- traditional-factor-engineer 负责 `ranking_label()` 横截面计算的 look-ahead 审计（**专门审计横截面泄漏问题，不由写代码的人自审**）
- factor-integrator 接线 `pipeline/run_ranking.py`

**时间预估**：4-6 天（比 P9 长：横截面 rank 的 look-ahead 检验复杂，必须慢下来守）

---

### 1.4 P11 —— 信号扩展（④）

**入口**：P10 完成且 PASS。

**出口**：
- `labels/targets.py` 的 `trade_signal_label()` 完整实现（买卖点标注策略，见 §2.4）
- `models/trade_signal.py` 完整实现
- `signals/generator.py` 全部 4 个分支升级为真实策略（direction/return/ranking P9/P10 已升级，P11 升级 trade_signal 分支）
- `pipeline/run_trade_signal.py` 跑通端到端
- Stage 3 最终审核 + verifier 全流程（①②③④ 可独立跑）
- Stage3-收尾说明.md

**依赖**：P10 完成。`backtest/engine.py` 可能需要补 stop-loss / take-profit 触发逻辑。

**主笔**：model-engineer + factor-integrator（信号层改动较多，两人协作）

**配角协助**：
- traditional-factor-engineer 验证买卖点标注不引入 look-ahead

**时间预估**：4-5 天

---

### 1.5 子阶段总览表

| 阶段 | 目标 | 新增文件/主改文件 | 直接复用（不改） | 主笔 | 时间 |
|---|---|---|---|---|---|
| P9 | ② 收益率回归 | `labels/targets.py` + `models/ret_regression.py` + `pipeline/run_return.py` + `signals/generator.py`(补分支) | `data/` / `factors/` / `backtest/` / `models/splits.py` | model-engineer | 3-4 天 |
| P10 | ③ 横截面 Top N | `labels/targets.py` + `models/ranking.py` + `pipeline/run_ranking.py` + `signals/generator.py`(升级分支) | `data/` / `factors/` / `backtest/engine.py`(P4已有Top-K) / `models/splits.py`(需加group-aware守护) | model-engineer | 4-6 天 |
| P11 | ④ 信号扩展 | `labels/targets.py` + `models/trade_signal.py` + `signals/generator.py`(完整) + `pipeline/run_trade_signal.py` + 可能补`backtest/engine.py`(stop-loss) | `data/` / `factors/` | model-engineer + factor-integrator | 4-5 天 |
| **合计** | | | | | **11-15 天** |

---

## 2. 接口设计

### 2.1 ② 收益率回归 —— labels 和 models 接口

**labels 层**：在 `labels/targets.py` 中新增 `return_label()` 函数。

```python
def return_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """② 未来 horizon 日累计收益率 —— 回归 target.

    公式：close[T+horizon] / close[T] - 1
    与 direction_label 共用同一个 shift(-horizon) 操作，
    只是不做二值化，直接返回 float。
    for_training=False 时末尾 horizon 行强制 NaN（同 direction_label）。

    返回：MultiIndex(date, ticker) → float（累计收益率）
    """
```

**不新写** `labels.py` 里的 `horizon_return` —— 直接在同一个 `targets.py` 文件里加函数，保持单一文件的设计哲学（4 类目标 label 都在 `targets.py`，按 `target_type` 分派）。

**models 层**：`ReturnRegressor` 复用 `DirectionModel` 的 save/load（Booster API + sidecar JSON），只换目标函数。

```python
class ReturnRegressor(BasePredictor):
    """② LightGBM 收益率回归器.

    复用 DirectionModel 骨架：
    - 同样的 BasePredictor 接口（fit/predict/save/load）
    - 同样的 Booster.save_model + sidecar JSON 持久化
    - 同样的 feature_names_ 对齐防漏接

    区别：
    - objective="regression"（或 "mse"），不是 "binary"
    - predict() 输出 float（收益率预测），不是 proba
    - 评估指标：RMSE / IC（信息系数）/ rank-IC
    """
```

**不另写** `ReturnModel`，直接填实 `ret_regression.py` 里的 `ReturnRegressor`（P1 骨架文件已存在）。

**`contracts.py` 改动**：`Prediction.value` 已经是 float（Stage 1 设计），`target_type="return"` 即可，无需改数据契约。

---

### 2.2 信号层 —— ② 分支

`signals/generator.py` 已有 `_generate_return()` stub 方法（`v > 0` 占位逻辑）。P9 升级为真实策略：

```python
def _generate_return(self, predictions: list[Prediction]) -> SignalReport:
    # 升级：阈值从配置读取，而非硬编码 v > 0
    # buy  if prediction.value > return_buy_threshold（如 0.02 = 预期收益 2%）
    # sell if prediction.value < return_sell_threshold（如 -0.02）
    # hold 否则
```

策略语义：`direction` 是「概率 > X」，`return` 是「预期收益率 > Y%」——两者接口形状一致，由 `SignalReport` 统一输出。不改 `_generate_direction` 分支。

---

### 2.3 ③ 横截面 Top N —— 关键设计决策

**ranking_label 实现方案**（核心 look-ahead 高风险区）：

```python
def ranking_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    n_quantiles: int = 5,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """③ 横截面排序标签 —— look-ahead 高风险，必须严守.

    实现逻辑：
    1. 先算每只股票未来 horizon 日收益率（和 return_label 相同的 shift(-horizon)）
    2. 按日期做横截面 rank / qcut，输出 0~(n_quantiles-1) 的分位数标签

    CRITICAL look-ahead 防线（见 §5.1 横截面 rank 专项分析）：
    - step 1（算收益率）：shift(-horizon) 是标签定义允许的 forward-look
    - step 2（横截面 rank）：必须按日期 groupby，只用**同一日**的横截面，
      不能把全样本放一起 rank（等于混入未来的涨跌幅分布信息）
    - winsorize/标准化如果要做：必须按日期 groupby，不能用全样本统计量
    """
```

**RankingModel 选型决策**：选用 **LightGBM regressor 分数排序**，而非 LGBMRanker。

理由：
- LGBMRanker 需要 `group` 参数（每个交易日的股票数），在 panel 数据上配置复杂，且 A股 universe 每日股票数不稳定（停牌/退市），group 数组容易写错
- 回归分数排序在横截面预测任务上效果与 ranker 相当（多篇量化研究验证）
- **复用成本极低**：`ReturnRegressor` 改一行 objective 就是 `RankingModel` 的基础版
- 如果将来实验表明 ranker 更优，替换时只改 `ranking.py` 一个文件，下游不感知

```python
class RankingModel(BasePredictor):
    """③ 横截面 Top N 选股模型.

    实现：LightGBM regression + 横截面分数排序（非 LGBMRanker）。
    predict() 输出每只股票的预期收益率预测值，
    信号层 signals/generator.py 按日期横截面排序，取 Top N。
    """
```

**signals/generator.py 的 ranking 分支**：

`_generate_ranking()` 已有 stub。P10 升级为真实横截面排序策略：

```python
def _generate_ranking(self, predictions: list[Prediction]) -> SignalReport:
    # 按 prediction.value（预期收益率）降序排列所有股票
    # Top N → buy（N = max_positions from config）
    # 其余 → sell/hold
```

**回测层 `backtest/engine.py` 无需改动**：P4 的 `_process_buys`（L301-320）已实现「按 score 降序 Top-K 等权配资」，`K = max_positions - current_positions`，`score >= buy_threshold` 过滤后再取 Top-K。ranking 模式只需将 buy_threshold 调低（让更多票过滤），引擎天然执行 Top N 逻辑，**不需要新增 selection_mode 参数**。

---

### 2.4 ③ 如何接 pipeline

`pipeline/run_ranking.py` 结构与 `run_direction.py` / `run_return.py` 完全平行：

```python
def run_ranking(
    universe: list[str] | None = None,
    top_n: int = 10,
    ...
) -> dict:
    """③ 横截面 Top N 端到端管道.

    数据 → 因子 → ranking_label → RankingModel.fit → 回测(top_n持仓) → 信号
    """
```

---

### 2.5 ④ 信号扩展 —— labels 和 signals 接口

`trade_signal_label()` 的标注策略选**基于路径的规则标注**（不是简单的 N 日收益）：

```python
def trade_signal_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.10,
    for_training: bool = True,
) -> pd.Series:
    """④ 买卖点标注.

    标注逻辑（三元分类）：
    - buy  (1)：未来 horizon 日内先触达 take_profit，且未先触达 stop_loss
    - sell (-1)：未来 horizon 日内先触达 stop_loss
    - hold (0)：未来 horizon 日内既未触 TP 也未触 SL

    这是比 direction 更精细的标注，要求逐日路径数据。
    look-ahead 防线：同样只用 T+1~T+horizon 的已发生数据作为 y，
    推理时（for_training=False）末尾 horizon 行强制 NaN。
    """
```

`signals/generator.py` 的 `trade_signal` 分支产出含 entry/exit 语义的信号，以及 stop-loss/take-profit 触发价位：

```python
elif prediction.target_type == "trade_signal":
    # prediction.value in {-1, 0, 1}
    signal = {1: "buy", 0: "hold", -1: "sell"}[round(prediction.value)]
    # 附加：止损价 = current_price * (1 - stop_loss_pct)
    #       止盈价 = current_price * (1 + take_profit_pct)
```

`backtest/engine.py` 补 stop-loss / take-profit 触发逻辑：当持仓价格达到触发条件时，不等信号层主动发 sell，引擎主动平仓。这是 P11 的主要 backtest 改动。

---

## 3. 复用 vs 新写边界

### 3.1 直接复用（Stage 1/2 产物，Stage 3 一行不动）

| 模块 | 文件 | 复用理由 |
|---|---|---|
| 数据层 | `data/protocol.py` / `astock_source.py` / `cache.py` / `dataset.py` | ② ③ ④ 和 ① 用完全相同的 OHLCV + 财务 + 资金流 panel 数据 |
| 因子层 | `factors/base.py` / `price_volume.py` / `fundamental.py` / `moneyflow.py` / `llm_factor.py` / `registry.py` | `FactorFrame` 是统一的特征输入，4 类目标共用 |
| 模型基类 | `models/base.py` | `BasePredictor` ABC 已设计好 fit/predict/save/load |
| 时序切分 | `models/splits.py` | purge gap 逻辑对 ② ③ ④ 同样有效（③ 需要额外 group-aware 检查，见 §5） |
| 配置 | `config/settings.py` | 新的 horizon / threshold / top_n 参数扩充配置，不改已有字段 |
| 数据契约 | `contracts.py` | `Prediction.target_type` 已有 `Literal["direction","return","ranking","trade_signal"]`，`value: float` 够用 |
| A股约束 | `backtest/constraints.py` | T+1 / 涨跌停 / 最小手数 规则对 ② ③ ④ 完全一致 |
| 绩效指标 | `backtest/metrics.py` | Sharpe / Sortino / 最大回撤 等对 ② ③ ④ 一致；② 补 IC / rank-IC |
| 持仓管理 | `backtest/portfolio.py` | 持仓 + 现金模型无需改 |

**完全不动的文件清单**（Stage 3 期间 mtime 应停在 Stage 2）：
`data/` 全部 · `factors/` 全部 · `models/base.py` · `models/splits.py` · `models/direction.py` · `backtest/constraints.py` · `backtest/portfolio.py` · `backtest/metrics.py`（仅补 IC 函数，不改已有） · `contracts.py` · `config/settings.py`（仅加新字段，不改已有）

---

### 3.2 需要扩展的文件（改，但严守加法纪律）

| 文件 | 扩展内容 | 风险级别 |
|---|---|---|
| `labels/targets.py` | 补 `return_label()` / `ranking_label()` / `trade_signal_label()` 三个函数 | 中（横截面 rank 逻辑是 high risk，见 §5） |
| `signals/generator.py` | 升级 `return` / `ranking` / `trade_signal` 三个现有 stub 为真实策略（dispatch 结构已有，只填实现） | 低（不改 direction 分支，只填 stub 方法体） |
| `backtest/engine.py` | P10 **不动**（已有 Top-K 逻辑）；P11 补 stop-loss/take-profit 收盘价触发 | 低-中（P10 不改；P11 只在引擎末尾加触发检查，不动主循环）|
| `backtest/metrics.py` | 补 IC / rank-IC 计算函数（② 需要） | 低（新增函数，不改已有） |
| `config/settings.py` | 补 `ReturnConfig` / `RankingConfig` / `TradeSignalConfig` 三个 dataclass | 低（加法，不改 `LabelConfig` / `BacktestRunConfig` 已有字段） |

---

### 3.3 真正新建的文件

| 文件 | 内容 |
|---|---|
| `models/ret_regression.py` | `ReturnRegressor` 完整实现（Stage 1 有 stub，P9 填实） |
| `models/ranking.py` | `RankingModel` 完整实现（Stage 1 有 stub，P10 填实） |
| `models/trade_signal.py` | `TradeSignalModel` 完整实现（Stage 1 有 stub，P11 填实） |
| `pipeline/run_return.py` | ② 端到端管道 |
| `pipeline/run_ranking.py` | ③ 端到端管道 |
| `pipeline/run_trade_signal.py` | ④ 端到端管道 |
| `tests/test_return_label.py` | P9 标签测试 |
| `tests/test_ranking_label_no_lookahead.py` | P10 命门测试（横截面泄漏防护）|
| `tests/test_ranking_model_topn.py` | P10 Top N 持仓逻辑测试 |
| `tests/test_trade_signal_label.py` | P11 买卖点标注测试 |
| `tests/test_backtest_engine_topn.py` | P10 回测引擎 Top N 模式测试 |
| `tests/test_backtest_engine_stoploss.py` | P11 止损止盈触发测试 |

---

## 4. 测试预期

### 4.1 P9 测试（② 收益率回归）

**目标新增**：~12-15 个测试

| 文件 | 测试 | 类型 |
|---|---|---|
| `test_return_label.py` | `return_label` 值域是 float，不是 0/1 | 单元 |
| | `return_label` 末尾 horizon 行为 NaN（for_training=False）| 单元 |
| | `return_label` 与 `direction_label` 单调关系（同一 horizon，label>0 对应 direction=1）| 单元（验证语义一致）|
| | `return_label` 不同 horizon 的 shift 正确性 | 单元 |
| `test_ret_regression.py` | `ReturnRegressor` save/load bit-exact roundtrip | 集成 |
| | `ReturnRegressor` predict 输出 float，不是 0/1 | 单元 |
| | `ReturnRegressor.feature_names_` 对齐 | 单元 |
| `test_pipeline_run_return.py` (或集成进 e2e) | run_return 端到端，IC > -1 且 < 1（sanity check）| 集成 |
| | 关闭 LLM 时，run_return metrics 与 run_direction 训练数据完全一致（同一 FactorFrame）| 集成（字节级校验 feature matrix）|

**命门测试**：`test_return_label_consistent_with_direction` —— 同一 (date, ticker) 行，`return_label > 0` 等价于 `direction_label = 1`（数学恒等式，防止两个 label 函数实现不一致导致幽灵 bug）。

---

### 4.2 P10 测试（③ 横截面 Top N）—— 命门重点

**目标新增**：~15-20 个测试

#### 横截面 look-ahead 命门测试（P10 最高优先级）

```
tests/test_ranking_label_no_lookahead.py
```

| 测试 | 守的不变量 |
|---|---|
| `test_ranking_label_groupby_date_only` | `ranking_label` 按日期 groupby rank，同一股票不同日期的 label 不相互依赖 |
| `test_ranking_label_no_full_sample_rank` | **命门**：把 label 生成分成「前一半时间」和「全样本」，前一半的 rank 在两种情况下**必须不同**（证明没有用全样本做 rank） |
| `test_ranking_label_winsorize_per_date_not_global` | 如果做 winsorize，必须按日期做，不能用全 panel 统计量 |
| `test_ranking_label_tail_nan` | 末尾 horizon 行强制 NaN，与 direction/return 行为一致 |
| `test_ranking_label_consistent_with_return_label` | 同一日横截面，`ranking_label` 的高分位对应 `return_label` 的高值（单调关系，防实现不一致）|

#### ranking 模式接入验证测试

引擎 P4 已有 Top-K 逻辑，测试目的是验证 ranking pipeline 正确接入引擎（wiring），而非验证引擎新逻辑。

```
tests/test_ranking_model_topn.py
```

| 测试 | 守的不变量 |
|---|---|
| `test_ranking_pipeline_wires_label_function` | wiring 命门：`run_ranking()` 真实调用了 `ranking_label()`（mock 计次 ≥ 1）|
| `test_ranking_engine_respects_max_positions` | `max_positions=5` 时，回测任意一天持仓数 ≤ 5（引擎已有逻辑的 regression test）|
| `test_ranking_t1_constraint_preserved` | ranking 路径下 T+1 约束仍然守住（不因 label 类型变化而失效）|
| `test_ranking_score_determines_position_selection` | score 高的股票优先被买入（验证引擎 Top-K 降序逻辑与 ranking predict 输出语义一致）|

---

### 4.3 P11 测试（④ 信号扩展）

**目标新增**：~12-15 个测试

| 文件 | 测试 | 类型 |
|---|---|---|
| `test_trade_signal_label.py` | buy=1 / sell=-1 / hold=0 三类都能生成 | 单元 |
| | TP 先于 SL 触达时标注 buy=1 | 单元 |
| | SL 先于 TP 触达时标注 sell=-1 | 单元 |
| | horizon 内无触达标注 hold=0 | 单元 |
| | 末尾 horizon 行强制 NaN | 单元 |
| `test_backtest_engine_stoploss.py` | 持仓价格跌破止损线时引擎自动平仓 | 集成 |
| | 持仓价格涨过止盈线时引擎自动平仓 | 集成 |
| | 止损止盈平仓后，当日不重新开仓（防 look-ahead 渗透）| 集成 |

---

### 4.4 Stage 3 整体测试目标

| 阶段结束 | 测试总数目标 | 新增 |
|---|---|---|
| P9 完成 | ~112（97 + 15）| 15 |
| P10 完成 | ~132（112 + 20）| 20 |
| P11 完成 | ~147（132 + 15）| 15 |

**不变式**：每 P 完成时，ruff clean + 全量 97 旧测试必须不漂移（direction 的 AUC=0.5131337782587783 字节级保持）。

---

## 5. 风险点

### 5.1 横截面 rank 的 look-ahead（P10 最高风险）

**背景教训**：P3a 的 winsorize bug —— 对因子值做全样本 winsorize（用了未来数据的分位数做截尾），导致因子值「偷看了未来」。虽然是数据依赖型而非代码 bug，但在审计中才被发现。

**横截面 rank 的特殊风险**：

```
ranking_label 的计算步骤：
  step 1：future_return[T] = close[T+horizon] / close[T] - 1    ← 允许（标签定义）
  step 2：rank = future_return.groupby(date).rank()              ← 必须严守「按日期」
```

如果 step 2 写成 `future_return.rank()`（全样本 rank），等于每个 T 时刻的排名用了**整个时间轴**上的涨跌幅分布——这把未来的市场表现分布泄漏给了训练样本。

**具体防护措施（P10 必须）**：

1. `ranking_label()` 实现时，step 2 强制写 `groupby(level="date").rank(pct=True)` 或 `groupby(level="date").transform(lambda x: x.rank(pct=True))`，绝不允许全样本 `.rank()`
2. **作者-审计分离**：model-engineer 写 `ranking_label()`，traditional-factor-engineer 专门做横截面泄漏审计（不自审）
3. `test_ranking_label_no_full_sample_rank`（§4.2 命门测试）在 CI 守住

**winsorize 同款风险**：如果 P10 在 ranking_label 里做标准化，必须：
- `per_date_zscore = groupby(date).transform(lambda x: (x - x.mean()) / x.std())`
- 绝不允许 `zscore = (series - series.mean()) / series.std()`

---

### 5.2 P7 wiring bit-identical 教训 —— ② ③ ④ 防漏接

**教训**：P7 v1 bit-identical 根因是 `news_fetcher` 透传链路有 5 处断开，LLM 因子安装好了但电源线断了，LLM 一次没被调用。

**Stage 3 ② ③ ④ 的对应风险**：新的 label 函数 / 新的 pipeline 接线，同样可能出现「代码写了但没接进 run_*.py」的隐性断线。

**具体防护措施**：

1. **每条新 pipeline 必须有「wiring 命门测试」**：类似 P7 的 `test_compute_factor_frame_wires_news_fetcher_to_llm`，验证 `run_return()` / `run_ranking()` 真的调用了新的 label 函数（mock label 函数，计次验证被调用 ≥ 1 次）
2. **factor-integrator 负责接线**，model-engineer 负责 label + model，两人分工不重叠 —— 接线由专人做，降低「写了但没接进去」的风险
3. **verifier 端到端验证时**，要在 log 里确认新 label 函数的调用次数 > 0（不能只看 metrics 合理）

---

### 5.3 回测引擎改动风险（P11 止损止盈）

**教训**：Stage 1 H4 发现引擎的「缺 prediction → 清仓」是隐性策略，在 `missing_prediction_action` config 后才变显性。回测引擎有很多「看起来合理但语义模糊」的地方。

**P10 无需改引擎**：`backtest/engine.py:_process_buys`（L301-320）P4 已实现「按 score 降序 Top-K 等权配资」。ranking 模式直接复用，**不存在 P10 引擎改动风险**。

**P11 的风险**：止损止盈是「盘中价格触发」vs「收盘价触发」的语义选择，两者回测结果差异大。A股 T+1 下止损还有「今日买、明日才能止损」的约束。

**防护措施**：
- P11 止损止盈用收盘价触发（不用日内价格），避免引入 OHLC 盘中逻辑的复杂性；`dataset.py:59` 确认 panel 已含 `high/low` 列，若需日内触发可升级
- P11 引擎改动必须有独立测试守门（`test_backtest_engine_stoploss.py`），且旧测试（18 个 `test_backtest_engine.py`）必须不漂移
- P11 开始前明确 lead 决策：收盘价触发还是日内价格触发（两者语义不同，不能由 model-engineer 自行决定）

---

### 5.4 Group-aware Split 风险（P10 专项）

**问题**：`models/splits.py` 的时序切分按行顺序（date 从早到晚）把数据切成训练/验证集。对于 direction/return 任务这是正确的——每行是独立的 (date, ticker) 样本。

但对于 ranking 任务，**同一 date 的所有 ticker 在横截面上不独立**：它们共享同一日的市场环境，ranking_label 的分位数是相对值（A 在上四分位是因为 B/C/D 表现差）。

**具体风险**：若 2022-01-05 这天的 30 只股票被随机分配到训练集 15 只 / 验证集 15 只——训练集看到「这 15 只今天排第 1-15」，验证集看到「另 15 只今天排第 16-30」。但**实际排名是在全 30 只里算的**，切开后训练的模型相当于「看着今天哪 15 只涨来预测今天哪些票会涨」——这是横截面维度的 look-ahead。

**现有 `splits.py` 是否有问题**：`time_series_split` 按日期切，同一日的所有 ticker 要么全在训练集、要么全在验证集（因为 date 维度是连续的）。**默认行为是安全的**——但需要一个测试明确守住这个不变量，防止将来有人改 splits 逻辑时破坏它。

**防护措施**：

1. P10 开始前，在 `tests/test_splits_purge.py`（或新建 `tests/test_splits_group_aware.py`）加命门测试：

```python
def test_splits_group_aware_for_ranking():
    """同一 date 的所有 ticker 必须在同一个集（训练或验证），不能跨集。"""
    # 构造 MultiIndex(date, ticker) panel，每日 N 只股票
    # 经过 time_series_split 后
    # 断言：train_idx 和 val_idx 按 date 分组后，
    #       任意一个 date 的所有 ticker 全在 train 或全在 val，不出现同日跨集
```

2. 若 `splits.py` 将来需要支持 GroupKFold（cross-validation 场景），必须以 date 为 group，不能以 ticker 为 group。

---

### 5.5 Stage 2 遗留债务（Stage 3 启动前宜处理）

Stage2-收尾说明.md 留了 4M / 5L / 2N 债务，其中 **Stage 3 直接相关的**：

| 债务 | 影响 Stage 3 的方式 | 建议 |
|---|---|---|
| M1 future news 防线 | ③ ranking_label 横截面泄漏同款风险 | P10 开始前补（30 分钟内可加）|
| M4 FactorContext 抽象 | P9 开始后 `compute_factor_frame` 的 `**kwargs` 越来越脏 | P9 开始前讨论，若不抽象先记录技术债 |
| M3 LLM_MODEL env var 拆分 | Stage 3 不直接用 LLM，可延后 | P11 后修 |

---

## 6. 预算 & 时间预估

### 6.1 各 P 时间估算

| 阶段 | 核心工作 | 时间 | 说明 |
|---|---|---|---|
| Stage 3 启动准备 | Stage2 债务 M1（future news 防线）+ M4 讨论 + 读本设计文档 | 0.5 天 | 在 P9 开始前，快速清理遗留高优债务 |
| **P9**（② 收益率回归）| return_label + ReturnRegressor + run_return + 信号分支 + 15 测试 + 审核 | **3-4 天** | 技术难度低，复用度 > 90% |
| **P10**（③ 横截面 Top N）| ranking_label + RankingModel + engine Top N 分支 + run_ranking + 20 测试 + 审核 | **4-6 天** | 横截面 look-ahead 审计是慢的地方；引擎改动需要谨慎 |
| **P11**（④ 信号扩展）| trade_signal_label + TradeSignalModel + 止损止盈 engine + generator 完整 + 15 测试 + Stage3 收尾 | **4-5 天** | 止损止盈语义选择需要 lead 决策 |
| **Stage 3 总计** | | **12-16 天** | |

**乐观路径**（各环节顺利 + 无重大 bug）：12 天
**正常路径**（P10 横截面审计需反复迭代）：14 天
**保守路径**（引擎改动触发回归 + 审核返工）：16 天

### 6.2 数据 & API 预算

**不需要新数据源**（除非 Stage 3 计划验证 LLM 因子有效性）：
- ②③ 使用同一套 OHLCV + 财务 + 资金流 panel 数据，已有缓存
- ④ 买卖点标注基于 OHLC 路径（高低价），需确认 `price_panel` 是否包含 `high/low` 列（`contracts.py` 的 `PriceBar` 有 `high/low`，但 `dataset.py` 是否全量写入需检查）

**唯一潜在新增**：若 P11 止损止盈要用**日内最高/最低价**触发（而非收盘价），需要确认 `astock_source.py` 的 `high/low` 是否已在 panel 里（大概率是有的，Stage 1 `PriceBar` 定义了）。

**API 成本**：0（Stage 3 默认关 LLM 因子，不烧 token）。如果 Stage 3 想重做 LLM 对比验证，需要先解决数据覆盖率问题（见 Stage2-收尾说明.md §关键判断）—— 但这不是 Stage 3 ②③④ 的核心任务。

---

## 附：Stage 3 启动 Checklist

在 P9 开工前，lead 确认以下事项（已由 architect-2 自查的项已注明）：

- [ ] **M1 future news 防线**（30 分钟内可加）：`llm_factor.py` 里补「新闻日期 > 当前 T → 丢弃」防线，守住 look-ahead 第三道闸
- [ ] **P11 止损止盈触发语义决策**（lead 决策）：收盘价触发还是日内最高/最低价触发？两者回测结果差异大，需 lead 在 P11 开始前拍板（`dataset.py:59` 已确认 panel 含 high/low，两种方案都可实现）
- [ ] **P10 横截面审计分工确认**（lead 决策）：P10 横截面 label 审计必须是 model-engineer 写、traditional-factor-engineer 专项审，不能自审；lead 确认两人都已收到该纪律

> 已由 architect-2 自查确认（无需 lead 操作）：
> - `signals/generator.py`：4 分支 dispatch 已存在，②③④ 为 stub 待升级 ✅
> - `backtest/engine.py`：P4 已有 Top-K 降序持仓逻辑，P10 无需改引擎 ✅
> - `dataset.py:59`：`build_price_panel` 输出含 `open/high/low/close/volume/amount` ✅

---

## 变更记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| v1.0 | 2026-05-16 | architect-2 | 初版，接管前任 architect 未完成任务 |
| v1.1 | 2026-05-16 | architect-2 | auditor Conditional PASS 修 3 处事实错位：①信号层 4 分支已存在(升级非新建) ②engine P4 已有 Top-K 无需改(去掉 selection_mode) ③去除 signals/engine 两项 checklist 自查项；新增 §5.4 group-aware split 命门测试 |
