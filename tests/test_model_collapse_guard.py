"""退化模型保护测试 —— 守住 sanity check 标记 degenerate flag.

场景：LightGBM early stopping 选了 best_iteration=1（只有 1 棵树），
说明特征几乎无信号（常见原因：akshare 因子 100% NaN）。

设计取舍：
- 早期版本 sanity check raise RuntimeError 拒绝保存退化模型。
- 实测发现这让 daily.py 完全跑不出任何报告（找不到模型文件），
  比"带警告的报告"还糟。
- 改为：模型仍 save，但 fit 时设 `_degenerate = True` 并 logger.warning。
  渲染层（renderer.py）通过 conf_std < 0.02 显示「教学示例」说明给用户
  （P24：用户决定保留 direction 作「短期方向不可预测」的反面教材）。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from astock_quant.models.direction import DirectionModel


def _make_xy(n: int = 200, n_features: int = 5, seed: int = 42) -> tuple[pd.DataFrame, pd.Series]:
    """构造带 MultiIndex=(date, ticker) 的合成训练数据."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.standard_normal((n, n_features)), columns=[f"f{i}" for i in range(n_features)])
    # 纯随机标签 —— 特征和标签无关，模拟 NaN 因子退化场景
    y = pd.Series(rng.integers(0, 2, size=n).astype(int))

    dates = pd.date_range("2025-01-01", periods=n // 5).repeat(5)
    tickers = ["A", "B", "C", "D", "E"] * (n // 5)
    idx = pd.MultiIndex.from_arrays([dates, tickers], names=["date", "ticker"])
    X.index = idx
    y.index = idx
    return X, y


def test_degenerate_model_flag_set_and_warning_logged(caplog):
    """1 棵树的退化模型：fit 不 raise，但 _degenerate=True 且 log warning.

    用 n_estimators=1 强制 LightGBM 只训 1 棵树，触发 sanity 检测。
    """
    X, y = _make_xy()
    model = DirectionModel(n_estimators=1)
    with caplog.at_level(logging.WARNING):
        model.fit(X, y)  # 不再 raise

    assert model._degenerate is True
    assert model._booster is not None
    assert model._booster.num_trees() == 1
    # log warning 应包含 "退化"
    assert any("退化" in r.message for r in caplog.records)


def test_healthy_model_no_degenerate_flag():
    """正常模型（足够多的树）：fit 后 _degenerate=False.

    使用可分离的合成数据 + n_estimators=50，确保训出 >= 5 棵树。
    """
    rng = np.random.default_rng(0)
    n = 300
    X = pd.DataFrame(rng.standard_normal((n, 5)), columns=[f"f{i}" for i in range(5)])
    # 可分离标签：第 0 列 > 0 时标 1
    y = pd.Series((X.iloc[:, 0] > 0).astype(int))
    dates = pd.date_range("2025-01-01", periods=n // 5).repeat(5)
    tickers = ["A", "B", "C", "D", "E"] * (n // 5)
    idx = pd.MultiIndex.from_arrays([dates, tickers], names=["date", "ticker"])
    X.index = idx
    y.index = idx

    model = DirectionModel(n_estimators=50)
    model.fit(X, y)

    assert model._degenerate is False
    assert model._booster is not None
    assert model._booster.num_trees() >= 5


def test_degenerate_threshold_boundary():
    """边界条件：恰好 4 棵树（< 5）→ degenerate=True，>= 5 棵则 False."""
    X, y = _make_xy()
    model = DirectionModel(n_estimators=4)
    model.fit(X, y)
    assert model._degenerate is True
    assert model._booster.num_trees() == 4
