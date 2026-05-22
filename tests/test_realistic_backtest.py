"""scripts/realistic_backtest.py 的 build_topn_predictions 单测（P26）.

build_topn_predictions 是验证脚本里唯一有「算法逻辑」的新函数 —— 把 pipeline
的逐日 predictions「稀释」成「只在调仓日出现」的离散买卖信号。它的正确性直接
决定回测换手率/成本是否真实，必须测。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd

from astock_quant.contracts import Prediction

# 用 importlib 加载 scripts/ 下的脚本（不需要 scripts 是 package）—— 同 test_build_index.py
_SCRIPT = Path(__file__).parent.parent / "scripts" / "realistic_backtest.py"
_spec = importlib.util.spec_from_file_location("realistic_backtest", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_topn_predictions = _mod.build_topn_predictions


def _mk(ticker: str, d: date, score: float) -> Prediction:
    return Prediction(
        ticker=ticker, date=d, target_type="ranking",
        value=float(score), score=float(score),
    )


def test_only_rebalance_days_produce_signals():
    """非调仓日不产出 prediction —— 引擎在非调仓日维持持仓."""
    dates = list(pd.bdate_range(date(2025, 7, 1), periods=10).date)
    preds = [
        _mk(tk, d, j) for d in dates
        for j, tk in enumerate(["A", "B", "C", "D", "E"])
    ]
    out = build_topn_predictions(preds, top_n=2, rebalance_days=5)
    out_dates = {p.date for p in out}
    # 调仓日 = dates[::5] = {dates[0], dates[5]}
    assert out_dates == {dates[0], dates[5]}


def test_topn_scores_rewritten():
    """调仓日：Top-N 的 score 改写 0.99，其余改写 0.01."""
    dates = list(pd.bdate_range(date(2025, 7, 1), periods=5).date)
    preds = [
        _mk(tk, d, j) for d in dates
        for j, tk in enumerate(["A", "B", "C", "D", "E"])
    ]
    out = build_topn_predictions(preds, top_n=2, rebalance_days=5)
    day0 = [p for p in out if p.date == dates[0]]
    assert len(day0) == 5, "调仓日应产出全部 5 只票"
    high = [p for p in day0 if p.score == 0.99]
    low = [p for p in day0 if p.score == 0.01]
    assert len(high) == 2, "Top-2 应改写成 0.99"
    assert len(low) == 3, "其余 3 只应改写成 0.01"


def test_dropped_out_ticker_gets_low_score():
    """上轮选中、本轮掉出 Top-N 的票 → score 转 0.01（引擎据此卖出换仓）."""
    dates = list(pd.bdate_range(date(2025, 7, 1), periods=6).date)
    day0 = {"A": 0.9, "B": 0.8, "C": 0.1, "D": 0.2, "E": 0.3}
    day5 = {"A": 0.1, "B": 0.2, "C": 0.9, "D": 0.8, "E": 0.3}
    preds = [_mk(tk, dates[0], s) for tk, s in day0.items()]
    preds += [_mk(tk, dates[5], s) for tk, s in day5.items()]
    # 中间日（会被非调仓日过滤掉）
    preds += [_mk(tk, d, 0.5) for d in dates[1:5] for tk in day0]

    out = build_topn_predictions(preds, top_n=2, rebalance_days=5)
    d5 = {p.ticker: p.score for p in out if p.date == dates[5]}
    assert d5["C"] == 0.99 and d5["D"] == 0.99, "本轮新 Top-2 应为 0.99"
    assert d5["A"] == 0.01 and d5["B"] == 0.01, "掉出 Top-N 的票应转 0.01（会被卖）"


def test_rebalance_days_1_keeps_all_days():
    """rebalance_days=1（每日调仓，direction 反面对照用）：每天都是调仓日."""
    dates = list(pd.bdate_range(date(2025, 7, 1), periods=4).date)
    preds = [
        _mk(tk, d, j) for d in dates
        for j, tk in enumerate(["A", "B", "C"])
    ]
    out = build_topn_predictions(preds, top_n=1, rebalance_days=1)
    assert {p.date for p in out} == set(dates), "每日调仓时所有日期都应产出"
