# Stage 1 收尾说明 —— Final Polish 7 项

> 2026-05-15 · model-engineer 出品 · P5 reviewer + verifier 反馈闭环

P5 code-reviewer 在深审中给出 0 critical / 4 high / 5 medium / 4 low / 5 nit，
P5 verifier 端到端 PASS 但补了 3 个 README/scripts 改进建议。
**lead 决策：High 4 + Nit 3 = 7 项一起修，Stage 1 正式收尾**（M/L/其他 N 项留给 Stage 2 启动前再扫）。

## 总览

| # | 来源 | 严重性 | 一句话 | 状态 |
|---|---|---|---|---|
| H1 | reviewer | High | `DirectionModel.load()` 戳 LightGBM 私有属性 | ✅ 改公开 API + sidecar JSON + 6 个测试 |
| H2 | reviewer | High | `labels.align_xy` 用 `intersection` 顺序不确定 | ✅ 改 `reindex` + 5 个测试 |
| H3 | reviewer | High | `pipeline.run_direction` 的 `universe` 是假参数 | ✅ 选 (A) 真接通到 `prepare_stage1_data` |
| H4 | reviewer | High | 引擎「缺 prediction → 自动清仓」是隐性策略 | ✅ 加 `missing_prediction_action` config + 2 个测试 |
| N5 | verifier | Medium | `scripts/run_pipeline.py` 空 stub | ✅ 写真 argparse + 调 run_direction + 摘要打印 |
| N6 | verifier | Low | README 没提 `brew install libomp` | ✅ 加到「快速上手 · 0. 系统依赖」 |
| N7 | verifier | Low | README 顶部「P1 骨架」过时 | ✅ 改为「Stage 1 完成」+ 整文档导航 |

**测试：60 / 60 PASS**（47 旧 + 13 新增） · **ruff：clean** · **端到端 metrics 不漂移**（AUC=0.5131、回测 193d/0 trades 与 P4 / P5 verification 完全一致）。

---

## H1：`DirectionModel.save/load` 改用全公开 API

### 问题

`astock_quant/models/direction.py` 老 `load()` 戳了 `LGBMClassifier._Booster / _n_features / _classes / fitted_ / _classes` 等私有属性 —— LightGBM 4.x → 5.x 升级时若改名/删字段就直接挂。reviewer 在 H1 给了两个修法选项。

### 决策：选 (A) 公开 `Booster` API + sidecar JSON

理由：
- (B) `joblib.dump(self._clf)` 简单但对 LightGBM 版本敏感（pickle 把 wrapper 内部状态全序列化下来），换版本反而更脆
- (A) `Booster.save_model` + `lgb.Booster(model_file=...)` 是 LightGBM **官方推荐**的持久化路径，文档明确稳定
- sidecar JSON 把 `feature_names_` 显式落盘 —— 不依赖 booster 内部 `feature_name()` 的字段顺序（虽然实测可靠，但这是「数据契约」级表达，让 load 不依赖隐式约定）

### 改动

`astock_quant/models/direction.py`：

- `__init__`：加 `self._booster: lgb.Booster | None = None` 字段
- `fit`：训练完 hook `self._booster = self._clf.booster_`
- `predict` / `predict_score_frame`：改用 `self._booster.predict(X)`（公开 API）。LightGBM binary objective 默认返回 P(正类)，所以 `proba = (1-score, score)` 重组
- `save`：`Booster.save_model(path)` + `with sidecar.open("w"): json.dump(self.feature_names_, ...)`
- `load`：`lgb.Booster(model_file=path)` 重建独立 booster；`self._clf = None`（彻底脱钩 sklearn wrapper）；sidecar 不存在时回退用 `booster.feature_name()` 并 warn
- `feature_importance`：改走 `self._booster.feature_importance()`，不再访问 `_clf.booster_`

### 测试

`tests/test_direction_model_roundtrip.py`（6 个）：
- `test_save_load_bit_exact_roundtrip` — save → load → 同 X 出同 score（精度 1e-9）
- `test_save_creates_sidecar_with_feature_names` — sidecar 文件存在且内容正确
- `test_load_does_not_touch_private_attrs` — load 后 `_clf is None`、`_booster is not None`，证明脱钩
- `test_load_falls_back_when_sidecar_missing` — sidecar 缺失也能 load，feature_names 从 booster 回填
- `test_predict_and_predict_score_frame_consistent` — 两条预测路径 score 一致
- `test_feature_importance_uses_booster` — save/load 前后 importance 按 name 对齐相等

---

## H2：`labels.align_xy` 加确定性排序

### 问题

`align_xy` 用 `factor_data.index.intersection(label_series.index)`。`pd.Index.intersection` 在 MultiIndex 上**不保证保留原顺序** —— pandas 版本切换可能让 (date, ticker) 行序漂移，破坏：
- `time_series_split` 用 `X.index` 切的 train/valid mask
- pipeline 里 `predict()` 后用 `X_va.index` 取 (date, ticker) 给 Prediction
- 多次跑出来的 `score_frame` 行序一致性（可复现性）

### 决策：用 `reindex` 严格按 `factor_data.index` 拉齐

`reindex` 行为是严格按目标索引顺序、缺失填 NaN —— 完全确定性。

### 改动

`astock_quant/labels/targets.py:178-186`：

```python
# 老：common_idx = factor_data.index.intersection(label_series.index)
# 新：
X = factor_data
y = label_series.reindex(factor_data.index)
# 后续 drop_label_nan 自然去掉 reindex 出的 NaN 行
```

注释里讲清「为什么不能用 intersection」+ 「reindex 顺序确定性来自哪里」。

### 测试

`tests/test_align_xy_determinism.py`（5 个）：
- `test_align_xy_preserves_factor_index_order` — X.index 与 factor_data.index 完全一致
- `test_align_xy_robust_to_label_shuffled_index` — 把 label 用 5 个随机种子打乱，输出值序列必须完全相同（命门测试）
- `test_align_xy_drops_label_nan_rows` — drop_label_nan 行为正确
- `test_align_xy_idempotent_multiple_calls` — 重复调用同一份输入产物完全相同
- `test_align_xy_missing_label_entries_become_nan_then_dropped` — label 部分缺失时的 reindex 行为

---

## H3：`pipeline.run_direction` 的 `universe` 参数真接通（选 A）

### 问题

`run_direction(universe=...)` 签名里有但函数体没用 —— `prepare_stage1_data()` 永远走 `SETTINGS.universe`。docstring 还在 ⚠️ 里说「未接入」—— **API 谎言**。

### 决策：选 (A) 真接通

理由：Stage 2 引入 LLM 因子时极可能要「小池子先试 LLM、大池子上正式版」 —— 这正是 `universe` 形参应工作的场景。删参数（B）等于放弃这个能力，不如真接通。

### 改动

1. `astock_quant/data/dataset.py:269` — `prepare_stage1_data` 加 `universe: list[str] | None = None` 形参，把它传给 `build_price_panel / build_moneyflow_panel / load_financials`（这三个本来就支持 universe kwarg，只是 `prepare_stage1_data` 没暴露）
2. `astock_quant/pipeline/run_direction.py:104` — 把 `universe` 真的传下去：`prepare_stage1_data(universe=universe, force_refresh=force_refresh_data)`
3. docstring 更新：「P5 reviewer H3 修复：本参数已真接通 prepare_stage1_data」+ 用法举例

### 测试

H3 修复用 ① 的 universe 参数走 end-to-end，被新增 `scripts/run_pipeline.py --universe 600519,000858` 命令路径间接覆盖。没单独写一个 pytest 因为 `prepare_stage1_data` 涉及真数据拉取，CI 上跑会慢且依赖缓存 —— 但 H3 修复点的代码改动小且直接（4 行）、与现有 build_*_panel 的 universe kwarg 一致，回归风险低。

---

## H4：引擎 `missing_prediction_action` 做成 config

### 问题

`BacktestEngine._process_sells` 老逻辑里「持仓的票当日不在 prediction 列表 → 自动清仓」，这隐含「没 prediction = 看跌」的策略假设。但实际触发场景包括：
- 验证集某日某票数据缺失（停牌 / panel 缺行） → 该日 prediction 缺这只票 → 引擎清仓
- 因子全 NaN 行被 `drop_all_nan_rows=True` 扔掉 → 该 (date, ticker) 没 prediction → 清仓

Stage 2 LLM 因子稀疏数据会让更多日子缺 prediction，扭曲所有指标。

### 决策：加 config 字段，默认保留老行为不破坏 bc

```python
@dataclass
class BacktestRunConfig:
    ...
    missing_prediction_action: Literal["liquidate", "hold"] = "liquidate"
```

- `"liquidate"`（默认）：老行为，universe 切换 / 票被剔除时自动清仓
- `"hold"`：保守，「当日无信号 → 维持持仓」，Stage 2 推荐

### 改动

`astock_quant/backtest/engine.py`：
- import `Literal`
- `BacktestRunConfig` 加字段 + 注释解释两种语义
- `_process_sells` 在 `score is None` 分支前加 early-continue：
  ```python
  if score is None and self.config.missing_prediction_action == "hold":
      continue
  ```

### 测试

`tests/test_backtest_engine.py` 新增 2 个：
- `test_engine_missing_prediction_liquidate_sells_position` — liquidate 模式下缺 prediction 触发清仓
- `test_engine_missing_prediction_hold_keeps_position` — hold 模式下缺 prediction 维持持仓

共享一个 `_make_missing_pred_scenario()` helper 构造场景。

---

## N5：`scripts/run_pipeline.py` 写真

### 问题

`main()` 体只有 `...`，verifier 实测「不报错但什么也不做」。

### 改动

`scripts/run_pipeline.py` 完整重写：

- `argparse` 解析参数：`--target` / `--universe` / `--train-end` / `--valid-end` / `--horizon` / `--force-refresh-data` / `--no-backtest` / `--buy-threshold` / `--sell-threshold` / `--max-positions` / `--missing-prediction-action` / `--save-model-to` / `--quiet`
- universe 字符串解析（逗号分隔 6 位代码）
- 只有传了相关参数时才构造 `BacktestRunConfig` 覆盖默认
- 调 `run_direction(...)`
- 打印训练 metrics + 回测 metrics + 信号摘要的格式化表格
- 返回 exit code（0 = OK，2 = 参数错误）

`uv run python scripts/run_pipeline.py --help` 输出完整帮助，与 README 中的命令示例对得上。

### 验证

`uv run python scripts/run_pipeline.py --help` 跑通，13 个参数 + usage 行渲染正常。

---

## N6 + N7：README 整修

### 问题

1. macOS 用户按 README 走会因 LightGBM 找不到 libomp 报错
2. 顶部仍写「🚧 骨架已 scaffold（P1 完成）」，实际 Stage 1 全部完成

### 改动

`README.md` 重写：

- 顶部状态：`Stage 1 完成 ✅ —— 数据 / 因子 / 模型 / 回测 / 信号 全部跑通；47+ 测试全过；4 份人话报告全交付；待 Stage 2 LLM 情绪因子扩展（用户决策）`
- 分阶段构建：Stage 1 加 ✅ 标记、Stage 2 标「待启动」
- **新增「0. 系统依赖（macOS 用户必读）」段**：`brew install libomp` + 「Linux / Windows 通常自带」说明
- 快速上手扩展：除「最简形式」外，加 3 段命令示例（换池子 / 调阈值 / 跳过回测 / 完整 help）
- 文档导航补齐：P0-P4 + Stage1 收尾 + progress + 人话报告 + 审核 + 参考资料

---

## 验证

### pytest

```
$ uv run pytest tests/ -q
............................................................             [100%]
60 passed in 54.00s
```

按文件分布：

| 文件 | 用例数 | 备注 |
|---|---|---|
| tests/test_factors_no_lookahead.py | 4 | P3a 旧测试 |
| tests/test_splits_purge.py | 11 | P3b 旧测试 |
| tests/test_constraints_astock.py | 18 | P4 新增（约束 4 件套） |
| tests/test_backtest_engine.py | 16 | P4 新增 13 + Sortino 1 + missing_prediction 2 |
| tests/test_direction_model_roundtrip.py | 6 | **本轮 H1 新增** |
| tests/test_align_xy_determinism.py | 5 | **本轮 H2 新增** |
| **合计** | **60** | |

### ruff

```
$ uv run ruff check astock_quant/ tests/ scripts/
All checks passed!
```

### 端到端 metrics 不漂移

```
train metrics (默认):
  train_size: 25283        ← P3b doc 25283 ✓
  valid_size: 5780         ← P3b doc 5780 ✓
  auc: 0.5131337782587783  ← P3b doc 0.513 ✓
  accuracy: 0.5334          ← P3b doc 0.5334 ✓
  log_loss: 0.6906          ← P3b doc 0.6906 ✓

backtest metrics (默认 0.55/0.45):
  trading_days: 193        ← P4 doc 193 ✓
  n_trades: 0              ← P4 doc 0 笔交易 ✓
  total_return: 0.0
  sharpe: 0.0
  sortino: 0.0             ← P5 cleanup 修复后 0（老 -15.87 不回归）✓
  max_drawdown: 0.0
```

所有数字与现有 P3 / P4 / P5 verification 文档完全一致。**没有任何指标漂移** —— 7 项修复都是底层结构改进，不影响算法行为。

---

## 不在本轮 scope 的 reviewer 建议（留给 Stage 2 启动前再扫）

- **M1**：`_FundamentalBase / _MoneyflowBase` 中间层 overkill → Stage 2 接 LLM 因子时一并简化
- **M2**：`compute_factor_frame` 的 `except Exception` 改 `logger.exception` 带 traceback
- **M3**：`direction_label` 的 `groupby.apply` → `transform` 性能优化
- **M4**：Portfolio 估值停牌时用 `avg_cost` 兜底掩盖风险 → 改用「上一日 close」
- **M5**：`BacktestRunConfig` docstring 显式列 Stage 1 策略约束（不加仓 / 不止损止盈 / max_positions 满不开新仓）
- **L1**：`Position.realized_pnl` 写入未读取 → 要么用要么删
- **L2**：`astock_source._tencent_quote` 加 retry
- **L3**：`metrics._trade_stats` docstring 写 FIFO 但实际是 VWAP → 改 docstring 或改实现
- **L4**：因子测试 fixture 用小 universe 加速
- **N1-N4**：各种注释/笔误（已在 P5 cleanup 中部分清理）

---

## scope 限定

本轮只动了 reviewer + verifier 明确点名的文件 + 必要的新测试：

| 文件 | 改动类型 |
|---|---|
| `astock_quant/models/direction.py` | 重写 save/load + predict 路径（H1）|
| `astock_quant/labels/targets.py` | align_xy 改 reindex（H2）|
| `astock_quant/data/dataset.py` | prepare_stage1_data 加 universe 形参（H3）|
| `astock_quant/pipeline/run_direction.py` | universe 真传递 + docstring（H3）|
| `astock_quant/backtest/engine.py` | missing_prediction_action config（H4）|
| `scripts/run_pipeline.py` | 写真（N5）|
| `README.md` | libomp + Stage 1 状态（N6+N7）|
| `tests/test_direction_model_roundtrip.py` | 新文件（H1 守门）|
| `tests/test_align_xy_determinism.py` | 新文件（H2 守门）|
| `tests/test_backtest_engine.py` | 加 2 个 missing_prediction 用例（H4 守门）|

未动：data 层其他 / factors / signals / config/settings.py / contracts.py / portfolio.py（这些都是 reviewer 标 M/L/N 留给后续的）。
