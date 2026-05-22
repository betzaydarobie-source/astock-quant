# Stage 2 启动准备 —— 4 项债务清理

> 2026-05-15 · model-engineer 出品 · auditor 在 Stage 1 final 审里点名的 Stage 2 前必修项

Stage 1 最终审 PASS，但 auditor 在「给 Stage 2 的提醒」段抓了 4 条**虽不阻塞 Stage 1 收尾、但 Stage 2 启动前应优先处理**的债务。lead 决策 `C` —— Stage 2 启动前一并修干净。

## 总览

| # | 来源 | 严重性 | 一句话 | 状态 |
|---|---|---|---|---|
| M4 | reviewer | Medium | 持仓估值缺行情用 `avg_cost` 兜底，掩盖停牌风险 | ✅ 改用 `_last_seen_close` 兜底 + 标 `is_stale_price` |
| M3 | reviewer | Medium | `direction_label` 用 `groupby.apply`，慢 5-10x | ✅ 改 `groupby.transform`，bit-exact 一致 |
| H4 | reviewer / auditor | High | `missing_prediction_action` 默认 `liquidate` 会让 Stage 2 LLM 稀疏因子被无谓清仓 | ✅ 默认切 `hold`，保留 `liquidate` 选项与老调用兼容 |
| N1 | reviewer | Nit | `portfolio.py` 注释残留 `BacktestConfig`，与 P5 cleanup rename 不一致 | ✅ 改为「`config.settings.BacktestConfig` (项目级) / `BacktestRunConfig` (引擎级)」消歧 |

**测试 62 / 62 PASS**（60 旧 + 2 新增：`test_engine_missing_prediction_default_is_hold` + `test_portfolio_avg_cost_suspended`）。
**ruff clean**。
**端到端 metrics 不漂移**（Stage 1 蓝筹数据下 hold ≡ liquidate，详见 §H4 影响范围）。

---

## M4：停牌估值改「上一日 close」兜底

### 问题（reviewer P5-code-review §M4）

老逻辑：`holdings_value` / `position_snapshot` 在 prices 缺失某 ticker 时用 `avg_cost` 兜底。docstring 写「保守，避免净值跳变」，但实际是「假装没亏没赚」 —— 真实停牌 30 天后开盘可能 -50%，回测却像一池死水，**Sharpe 被人为拉高**。Stage 2 扩到全 A 股池后这条会显眼。

### 决策：选 reviewer 推荐的方案 ——「`_last_seen_close` 优先 + avg_cost 兜底」

reviewer 原文：
> 用「上一日 close」兜底（更接近真实），需要 `Portfolio` 持有「最后一次见过的有效 close」字典

加 `Portfolio._last_seen_close: dict[str, float]` + 公开方法 `update_last_seen_close(prices)`。engine 主循环每日 mark-to-market 前调一次更新内部字典。

### 改动

`astock_quant/backtest/portfolio.py`：

- 顶部 docstring 加「停牌估值」段，讲清新旧逻辑差异
- 加 `_last_seen_close: dict[str, float] = field(default_factory=dict)` 字段
- 新增 `update_last_seen_close(prices)` 方法 —— 只更新本次给到的 ticker，缺数据保留旧值（这正是「最近一次见过」语义）
- 新增 `_resolve_price(ticker, prices, pos) -> (price, is_stale)` 内部 helper，统一取价优先级：当日 close > `_last_seen_close` > `avg_cost`
- `holdings_value` / `position_snapshot` 改走 `_resolve_price`
- `position_snapshot` 多加一列 `is_stale_price: bool` —— 让分析者看得见停牌污染

`astock_quant/backtest/engine.py`：
- 主循环 mark-to-market 前加一行 `self.portfolio.update_last_seen_close(close_map)`

### 测试

`tests/test_backtest_engine.py::test_portfolio_avg_cost_suspended`：
- T1 买入 100 股 @ 100（avg_cost=100）
- T2 close=120，正常 mark-to-market；调 `update_last_seen_close`
- T3 停牌（prices 空），**估值应 = cash + 100×120**（不是 100×100），否则就回归 M4 bug
- 验证 `position_snapshot[0]["is_stale_price"] is True`（停牌日）
- 验证正常日 `is_stale_price is False`

### 注：测试名沿用 lead 给的 `test_portfolio_avg_cost_suspended`

lead 文案「停牌日下单时 avg_cost 用前一交易日 close」与 reviewer 原意有一处歧义 —— avg_cost 是**历史买入加权均价**，与停牌无关，停牌当天通常也无下单（panel 缺行 → constraints 拦截）。reviewer 真正修的是「**估值取价兜底**」（mark-to-market 时缺 close 用什么）。测试沿用 lead 命名以便他索引，但断言点对齐 reviewer 原意（估值层）。

---

## M3：`direction_label` `apply → transform`

### 问题（reviewer P5-code-review §M3）

`labels/targets.py:95-99`：

```python
future_ret = (
    price_panel[close_col]
    .groupby(level="ticker", group_keys=False)
    .apply(lambda s: s.pct_change(horizon).shift(-horizon))
)
```

- `apply` 走 Python 循环，`transform` 走 Cython 路径 —— 30 票 × 4 年 panel 上 transform 快 5-10x
- pandas 2.x 对 `apply` 在「输出形状与输入相同」的场景下抛 FutureWarning
- 项目其他地方（`price_volume.py` 整片代码、`moneyflow.py`）用的都是 `transform`，唯独 labels.targets 这一处用 apply —— 是统一过去的疏漏

### 决策：直接换 `transform`

`pct_change(horizon).shift(-horizon)` 输出形状与输入完全一致 —— 这正是 `transform` 的契约要求。两者功能等价。

### 改动

`astock_quant/labels/targets.py:95-103`：把 `.apply(lambda s: s.pct_change(horizon).shift(-horizon))` 换成 `.transform(lambda s: s.pct_change(horizon).shift(-horizon))`。注释里加 M3 修复说明。

### Bit-exact 验证

**合成数据**（3 票 × 30 日合成 panel）：
```
共有效样本数: 75
bit-exact 一致: True
NaN 位置一致: True
```

**真实数据**（30 只蓝筹 × 2022-01-01 ~ 2026-05-15）：

| 指标 | 修复前（apply）| 修复后（transform）| 一致 |
|---|---|---|---|
| train_size | 25283 | 25283 | ✅ |
| valid_size | 5780 | 5780 | ✅ |
| auc | 0.5131337782587783 | 0.5131337783 | ✅ bit-exact |
| accuracy | 0.5333910034602076 | 0.5333910035 | ✅ |
| log_loss | 0.6905634407838911 | 0.6905634408 | ✅ |

**所有训练 metrics 一字不差** —— 证明 transform 与 apply 在本场景完全等价，只是快。

---

## H4：`missing_prediction_action` 默认 `liquidate` → `hold`

### 问题（auditor Stage 1 final §给 Stage 2 的提醒 #1）

P5 cleanup 阶段加了 `missing_prediction_action: Literal["liquidate", "hold"] = "liquidate"` config —— 默认 `liquidate` 保留 P4 老行为不破坏 bc。但 auditor 提醒：

> Stage 2 LLM 因子稀疏数据会让大量日子缺 prediction，建议把默认 `missing_prediction_action` 切到 `"hold"`，避免 alpha 被「自动清仓」吃掉

### 决策：默认切 `hold`，保留 `liquidate` 选项

按 lead 指示：
- `BacktestRunConfig.missing_prediction_action` 默认从 `"liquidate"` 切到 `"hold"`
- **保留 `liquidate` 选项不删除** —— 老调用方（包括 P4 报告里的 14 笔交易 / +5.92% 实验）显式传 `missing_prediction_action="liquidate"` 即可复现
- 默认变更**不是漂移，是预期行为变化**

### 改动

`astock_quant/backtest/engine.py:88`：
```python
# Stage 2 prep 切换：默认从 "liquidate" → "hold"
missing_prediction_action: Literal["liquidate", "hold"] = "hold"
```

注释更新：讲清两种模式的语义 + 默认切换原因 + 复现 P4 老数字的方法。

### 测试

`tests/test_backtest_engine.py`：
- 新增 `test_engine_missing_prediction_default_is_hold` —— 断言 `BacktestRunConfig().missing_prediction_action == "hold"`，未来回退到 `liquidate` 立刻挂
- 旧 `test_engine_missing_prediction_liquidate_sells_position` docstring 更新：「显式传 liquidate 才能复现 P4 数字」
- 旧 `test_engine_missing_prediction_hold_keeps_position` docstring 更新：「Stage 2 prep 后这是默认行为」

### H4 默认变更的影响范围（核心）

**端到端实测**（30 只蓝筹 × P3b 模型 prediction）：

| 场景 | 默认 hold | 显式 liquidate | 差异 |
|---|---|---|---|
| 0.55/0.45 默认阈值 | 193d / 0 trades / 0.0% | 193d / 0 trades / 0.0% | **无差异** |
| 0.51/0.49 放宽阈值 | 14 trades / +5.92% / Sharpe 1.10 / 回撤 -2.18% | 14 trades / +5.92% / Sharpe 1.10 / 回撤 -2.18% | **无差异** |

**为什么完全一致？** Stage 1 universe 是 30 只蓝筹流动性好停牌少，**所有 (date, ticker) 都被 prediction 覆盖**（factor / label 极少被 `drop_all_nan_rows` 扔掉），永远不会触发「没 prediction → 怎么办」分支。所以 hold / liquidate 在 Stage 1 数据下行为完全相同。

**真正差异只在 Stage 2 才显现** —— LLM 因子按日新闻产出，大量交易日某只票根本没新闻 → prediction 大量缺失 → hold 维持持仓 vs liquidate 清仓的差距才会拉开。

### 让老调用方继续用 liquidate

如果你想 bit-exact 复现 P4 报告里的 14 笔 / +5.92% 数字（或在 Stage 2 中按 P4 阶段口径做对照实验），显式传：

```python
from astock_quant.backtest.engine import BacktestRunConfig
from astock_quant.pipeline.run_direction import run_direction

cfg = BacktestRunConfig(
    buy_threshold=0.51, sell_threshold=0.49,
    missing_prediction_action="liquidate",  # ← 显式锁定老行为
)
r = run_direction(backtest_config=cfg)
```

或命令行：

```bash
uv run python scripts/run_pipeline.py \
  --buy-threshold 0.51 --sell-threshold 0.49 \
  --missing-prediction-action liquidate
```

（`scripts/run_pipeline.py` 在 P5 cleanup 阶段已加 `--missing-prediction-action {liquidate,hold}` 参数，本轮不动）

---

## N1：`portfolio.py` 注释 `BacktestConfig` 消歧

### 问题（reviewer P5-code-review §N1）

P5 cleanup 把 `engine.BacktestConfig` 重命名为 `BacktestRunConfig`，但 `portfolio.py` 顶部 docstring L12 + L86（成本参数注释）残留「与 BacktestConfig 一致」，读者无法判断指的是哪个 config。

### 改动

`astock_quant/backtest/portfolio.py`：

- L12 顶部 docstring「交易成本模型（与 BacktestConfig 一致）」改为「交易成本模型（与 `config.settings.BacktestConfig` 默认值一致；engine 用 `BacktestRunConfig` 时按需覆盖）」
- L99 成本参数注释「与 BacktestConfig 默认值一致」改为「默认与 `config.settings.BacktestConfig` 一致；引擎构造 `BacktestRunConfig` 时按需覆盖」

`grep "BacktestConfig\|BacktestRunConfig" portfolio.py` 验证仅 L12 / L99 两处出现，且都已正确消歧。

---

## 验证

### pytest

```
$ uv run pytest tests/ -q
..............................................................           [100%]
62 passed in 51.04s
```

按文件分布：

| 文件 | 测试数 | 备注 |
|---|---|---|
| tests/test_factors_no_lookahead.py | 4 | P3a 旧 |
| tests/test_splits_purge.py | 11 | P3b 旧 |
| tests/test_constraints_astock.py | 18 | P4 旧 |
| tests/test_backtest_engine.py | **18** | P4 旧 13 + P5 cleanup Sortino 1 + Stage1 H4 missing_pred 2 + **Stage2 prep H4 默认 1 + M4 1** |
| tests/test_direction_model_roundtrip.py | 6 | Stage1 H1 旧 |
| tests/test_align_xy_determinism.py | 5 | Stage1 H2 旧 |
| **合计** | **62** | 60 旧 + 2 新（test_engine_missing_prediction_default_is_hold + test_portfolio_avg_cost_suspended）|

### ruff

```
$ uv run ruff check astock_quant/ tests/ scripts/
All checks passed!
```

### 端到端 metrics（默认 hold 下）

```
默认 hold 0.55/0.45：trading_days=193 / n_trades=0 / total_return=0.0 / sortino=0.0
默认 hold 0.51/0.49：14 trades / +5.92% / Sharpe 1.10 / 回撤 -2.18%
显式 liquidate 0.51/0.49（P4 旧数字）：14 trades / +5.92% / Sharpe 1.10 / 回撤 -2.18%
```

**Stage 1 蓝筹数据下 hold ≡ liquidate**（解释见 §H4）。所有 Stage 1 既有报告里的数字依然可复现。

---

## scope 限定

本轮只动 4 个文件 + 2 个新测试用例（lead 限定的 portfolio / engine / labels/targets.py + 测试新增）：

| 文件 | 改动类型 |
|---|---|
| `astock_quant/backtest/portfolio.py` | _last_seen_close 字段 + update_last_seen_close + _resolve_price + holdings_value/position_snapshot 改走兜底 + 注释消歧（M4 + N1）|
| `astock_quant/backtest/engine.py` | 主循环加 update_last_seen_close + 默认 missing_prediction_action 切 hold + 注释更新（M4 + H4）|
| `astock_quant/labels/targets.py` | apply → transform + 注释（M3）|
| `tests/test_backtest_engine.py` | 加 test_engine_missing_prediction_default_is_hold（H4）+ test_portfolio_avg_cost_suspended（M4）+ 旧 2 个测试 docstring 更新|

**未动**：
- `data/` 全部
- `factors/` 全部
- `models/` 全部
- `signals/` 全部
- `config/settings.py`
- `contracts.py`
- `backtest/constraints.py / metrics.py`
- `pipeline/run_direction.py`
- `scripts/run_pipeline.py`
- README / 各阶段技术文档

---

## 给 Stage 2 启动 sprint 的清单

剩余 reviewer 提到的 9 项 M/L/N 债务（auditor「给 Stage 2 的提醒」也提了一部分）建议 sprint 第一周用半天扫一遍：

| # | 严重性 | 一句话 | 备注 |
|---|---|---|---|
| M1 | Medium | `_FundamentalBase / _MoneyflowBase` 中间层 overkill | 接 LLM 因子时顺手简化 |
| M2 | Medium | `compute_factor_frame` 的 `except Exception` 改 `logger.exception` 带 traceback | LLM API 超时调试时会感谢 |
| M5 | Medium | `BacktestRunConfig` docstring 显式列 Stage 1 策略约束 | 不加仓 / 不止损止盈 / max_positions 满不开新仓 |
| L1 | Low | `Position.realized_pnl` 写入未读取 → 用或删 |  |
| L2 | Low | `astock_source._tencent_quote` 加 retry | Stage 2 复用 HTTP 模式时 |
| L3 | Low | `metrics._trade_stats` docstring 写 FIFO 但实际是 VWAP → 改 docstring 或改实现 |  |
| L4 | Low | 因子测试 fixture 用小 universe 加速 | CI 时间 < 10s |
| N2 | Nit | `price_volume.py` 注释「不！」式草稿语 |  |
| N3 | Nit | 文档/docstring 混用「Stage 1 做透 / 主路径 / 实现」 |  |

reviewer 还提了 4 项**测试覆盖空缺**（Stage 2 启动前补）：

- 多次回测稳定性（引擎 instance 状态可能脏）
- 跨年边界（春节假期处理）
- 停牌日整链路（M4 修复后端到端 cover）
- `run_direction` 端到端冒烟测试（合成数据，5 秒级）

---

## 进度更新

- ✅ 4 项 auditor 点名债务全修
- ✅ 62 / 62 测试全过（含 2 个新增命门测试）
- ✅ ruff clean
- ✅ 端到端 metrics 不漂移；hold/liquidate 在 Stage 1 数据下等价（预期）
- ✅ scope 严守只动 lead 限定的 4 个文件 + 测试
- 等 auditor 复审 → Stage 2 正式启动（factor-engineer LLM 情绪因子接入）
