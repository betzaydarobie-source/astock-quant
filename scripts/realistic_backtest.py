"""scripts/realistic_backtest.py — 实盘可行性回测验证（P26）.

诚实回答一个问题：**这套量化策略，扣掉真实交易成本后，到底亏不亏、
跑不跑得赢沪深300指数。**

⚠️ 本验证的定位是「证伪器」，不是「实盘背书」：
回测有幸存者偏差、验证集污染等多项无法补救的偏差，且**全部朝
「让回测好于实盘」方向**。即使回测数字好看，也不能据此上实盘。
详见报告末尾的「诚实声明清单」。

用法：
    uv run python scripts/realistic_backtest.py

产出：artifacts/realistic_backtest/results_<date>.json + report_<date>.md
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from astock_quant.backtest.engine import BacktestEngine, BacktestRunConfig
from astock_quant.config.settings import get_universe
from astock_quant.data.dataset import prepare_stage1_data
from astock_quant.pipeline.run_direction import run_direction
from astock_quant.pipeline.run_ranking import run_ranking
from astock_quant.pipeline.run_return import run_return

logger = logging.getLogger(__name__)

# --- 三档交易成本（往返）---
# 真实档：commission 万3.5 双边 + 印花税千0.5 卖出 + 滑点 15bp → 往返 ~0.42%（有意偏保守）
REALISTIC_COST = {"commission_rate": 0.00035, "stamp_tax_rate": 0.0005, "slippage_bps": 15.0}
# 乐观档：项目默认 → 往返 ~0.12%
COST_OPTIMISTIC = {"commission_rate": 0.0003, "stamp_tax_rate": 0.0005, "slippage_bps": 5.0}
# 压力档：往返 ~0.6%
COST_STRESS = {"commission_rate": 0.0005, "stamp_tax_rate": 0.0005, "slippage_bps": 25.0}

_INITIAL_CASH = 1_000_000.0
CACHE_DIR = Path("data_cache")
OUT_DIR = Path("artifacts/realistic_backtest")

# --- 策略变体网格 ---
# 诚信：全部变体结果都列出（含亏损的），不事后挑「最好的」当结论。
STRATEGY_VARIANTS = [
    {"name": "ranking·Top5·周频", "model": "ranking", "top_n": 5, "rebal": 5, "cost": REALISTIC_COST},
    {"name": "ranking·Top10·周频", "model": "ranking", "top_n": 10, "rebal": 5, "cost": REALISTIC_COST},
    {"name": "ranking·Top5·双周", "model": "ranking", "top_n": 5, "rebal": 10, "cost": REALISTIC_COST},
    {"name": "ranking·Top10·月频", "model": "ranking", "top_n": 10, "rebal": 20, "cost": REALISTIC_COST},
    {"name": "return·Top5·周频", "model": "return", "top_n": 5, "rebal": 5, "cost": REALISTIC_COST},
    {"name": "return·Top10·周频", "model": "return", "top_n": 10, "rebal": 5, "cost": REALISTIC_COST},
    {"name": "ranking·Top5·周频·乐观成本", "model": "ranking", "top_n": 5, "rebal": 5, "cost": COST_OPTIMISTIC},
    {"name": "ranking·Top5·周频·压力成本", "model": "ranking", "top_n": 5, "rebal": 5, "cost": COST_STRESS},
    {"name": "direction·Top5·每日(反面对照)", "model": "direction", "top_n": 5, "rebal": 1, "cost": REALISTIC_COST},
]


# ===========================================================================
# 沪深300 基准
# ===========================================================================

def load_hs300_benchmark(start, end) -> pd.Series | None:
    """拉沪深300指数日收益率 Series（DatetimeIndex），带本地 cache.

    返回 None 表示拉取失败 —— 验证继续，基准指标显示 N/A，不让整体挂掉。
    """
    cache_path = CACHE_DIR / "hs300_index.csv"
    df = None
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, parse_dates=["date"])
        except Exception:  # noqa: BLE001
            df = None
    if df is None:
        try:
            import akshare as ak

            raw = ak.index_zh_a_hist(
                symbol="000300", period="daily",
                start_date="20220101", end_date="20261231",
            )
            df = pd.DataFrame({
                "date": pd.to_datetime(raw["日期"]),
                "close": raw["收盘"].astype(float),
            })
            CACHE_DIR.mkdir(exist_ok=True)
            df.to_csv(cache_path, index=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("拉沪深300指数失败，基准指标将显示 N/A：%s", e)
            return None

    df = df.sort_values("date").set_index("date")
    s = df["close"].pct_change().dropna()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    s.name = "hs300_return"
    return s


# ===========================================================================
# 调仓节奏离散化（命门）
# ===========================================================================

def build_topn_predictions(predictions: list, top_n: int, rebalance_days: int) -> list:
    """把 pipeline 的逐日 predictions「稀释」成「只在调仓日出现」的离散买卖信号.

    现有回测引擎是「阈值穿越驱动」（score≥buy_threshold 买、<sell_threshold 卖），
    不是「周期调仓驱动」。不做这步离散化，换手率和成本就不真实。

    做法（不碰引擎，纯 predictions 列表变换）：
      - 取所有交易日，每 rebalance_days 个交易日设一个「调仓日」
      - 调仓日：当天 Top-N（按 score 降序）的 score 改写 0.99（≥buy_threshold 触发买入）；
        其余票 score 改写 0.01（<sell_threshold 触发卖出 —— 实现换仓）
      - 非调仓日：不产出 prediction → 引擎 missing_prediction_action="hold" 下维持持仓
    """
    by_date: dict = defaultdict(list)
    for p in predictions:
        by_date[p.date].append(p)
    all_dates = sorted(by_date.keys())
    rebal_dates = set(all_dates[::rebalance_days]) if rebalance_days > 0 else set(all_dates)

    out: list = []
    for d in all_dates:
        if d not in rebal_dates:
            continue  # 非调仓日不产出，引擎维持持仓
        day_preds = by_date[d]
        ranked = sorted(
            day_preds,
            key=lambda p: (p.score if p.score is not None else p.value),
            reverse=True,
        )
        topn_tickers = {p.ticker for p in ranked[:top_n]}
        for p in day_preds:
            if p.ticker in topn_tickers:
                out.append(p.model_copy(update={"score": 0.99, "value": 1.0}))
            else:
                out.append(p.model_copy(update={"score": 0.01, "value": 0.0}))
    return out


# ===========================================================================
# 单策略回测
# ===========================================================================

def run_one_strategy(variant: dict, predictions: list, price_panel: pd.DataFrame,
                     benchmark: pd.Series | None) -> dict:
    """跑一个策略变体，返回指标 dict."""
    topn_preds = build_topn_predictions(predictions, variant["top_n"], variant["rebal"])
    cfg = BacktestRunConfig(
        initial_cash=_INITIAL_CASH,
        max_positions=variant["top_n"],
        **variant["cost"],
    )
    engine = BacktestEngine(price_panel=price_panel, config=cfg, benchmark_returns=benchmark)
    result = engine.run(topn_preds)
    m = result.metrics

    # 累计交易成本
    total_cost = 0.0
    trades = result.trades
    if trades is not None and len(trades) > 0:
        for col in ("commission", "stamp_tax", "slippage_cost"):
            if col in trades.columns:
                total_cost += float(trades[col].sum())

    return {
        "variant": variant["name"],
        "model": variant["model"],
        "top_n": variant["top_n"],
        "rebal_days": variant["rebal"],
        "total_return": m.get("total_return"),
        "annualized_return": m.get("annualized_return"),
        "sharpe": m.get("sharpe"),
        "max_drawdown": m.get("max_drawdown"),
        "win_rate": m.get("win_rate"),
        "n_trades": m.get("n_trades"),
        "benchmark_total_return": m.get("benchmark_total_return"),
        "excess_return_annualized": m.get("excess_return_annualized"),
        "information_ratio": m.get("information_ratio"),
        "total_cost": round(total_cost, 2),
        "total_cost_pct": round(total_cost / _INITIAL_CASH, 4),
    }


def run_benchmark_buy_and_hold(benchmark: pd.Series | None) -> dict:
    """沪深300 买入持有基准."""
    if benchmark is None or len(benchmark) == 0:
        return {"variant": "沪深300 买入持有", "total_return": None}
    total_return = float((1 + benchmark).prod() - 1)
    n_days = len(benchmark)
    annualized = float((1 + total_return) ** (252 / max(n_days, 1)) - 1)
    equity = (1 + benchmark).cumprod()
    drawdown = float((equity / equity.cummax() - 1).min())
    return {
        "variant": "沪深300 买入持有（基准）",
        "model": "benchmark",
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4),
        "max_drawdown": round(drawdown, 4),
        "n_trades": 0,
    }


# ===========================================================================
# 报告
# ===========================================================================

_HONESTY_DISCLAIMER = """\
## ⚠️ 诚实声明清单（必读 —— 为什么不能简单相信上面的数字）

本回测**系统性地偏乐观**，下列偏差**全部朝「让回测好于实盘」方向**，不会互相抵消：

1. **验证集已被污染** —— 模型在换数据源、调标签（P24/P25）过程中反复参考过
   验证集指标。它不是干净的样本外测试集，上面的收益含「挑出来的运气」。
2. **幸存者偏差** —— 用「今天的」沪深300成分股回测历史。这 300 只是「涨上来 +
   被指数纳入」的赢家，退市/被剔除的输家完全缺席。回测区间越长，高估越大。
3. **财务 look-ahead** —— 财务因子按「报告期末日」而非「实际发布日」截断，
   每年 Q1 的财务因子含轻微未来信息。
4. **样本太小** —— 单次时序切分、验证集仅约 10 个月、交易笔数少。所有指标
   没有置信区间，不可外推到别的市场环境。
5. **成交假设乐观** —— 用当日收盘价成交、滑点固定、未建模冲击成本，
   涨跌停/停牌只做近似。真实实盘是 T+1 开盘下单 + 滑点 + 买不满。
6. **信号本身弱** —— 4 个模型 AUC 0.5-0.74 / IC≈0.02 / macro-F1≈0.41，
   弱信号在真正的样本外通常进一步衰减。

**结论口径**：即使某个变体回测显示盈利，由于以上偏差，也**不足以支持实盘部署**。
这次验证能给出的最强结论是「证伪」——如果连这个偏乐观的回测都亏 / 跑输沪深300，
那实盘几乎必亏。
"""


def _fmt(v, pct: bool = False, nd: int = 4) -> str:
    if v is None:
        return "N/A"
    if pct:
        return f"{v * 100:+.2f}%"
    return f"{v:.{nd}f}"


def assemble_report(results: list[dict], benchmark_row: dict, meta: dict) -> str:
    """拼装 markdown 报告."""
    lines = [
        f"# 实盘可行性回测验证报告 — {meta['date']}",
        "",
        "> 一句话定位：这是一次**偏乐观**的回测。它能可靠地「证伪」（证明不行），",
        "> 但**不能**「证明能实盘」。读数字前务必先看末尾「诚实声明清单」。",
        "",
        "---",
        "",
        "## 验证设置",
        "",
        f"- 股票池：{meta['universe_size']} 只（沪深300成分）",
        f"- 回测区间：{meta['bt_start']} ~ {meta['bt_end']}（约 {meta['n_months']} 个月，单次切分）",
        f"- 真实成本档：佣金 {REALISTIC_COST['commission_rate']*100:.3f}%（双边）"
        f" + 印花税 {REALISTIC_COST['stamp_tax_rate']*100:.3f}%（卖出）"
        f" + 滑点 {REALISTIC_COST['slippage_bps']:.0f}bp → 往返约 0.42%",
        f"- 初始资金：{_INITIAL_CASH:,.0f} 元",
        "",
        "---",
        "",
        "## 结果对照表",
        "",
        "| 策略变体 | 总收益 | 年化 | 夏普 | 最大回撤 | 沪深300同期 | 年化超额 | 信息比率 | 交易数 | 累计成本占比 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    # 基准行置顶
    b = benchmark_row
    lines.append(
        f"| **{b['variant']}** | {_fmt(b.get('total_return'), pct=True)} | "
        f"{_fmt(b.get('annualized_return'), pct=True)} | — | "
        f"{_fmt(b.get('max_drawdown'), pct=True)} | — | — | — | {b.get('n_trades', 0)} | — |"
    )
    for r in results:
        lines.append(
            f"| {r['variant']} | {_fmt(r['total_return'], pct=True)} | "
            f"{_fmt(r['annualized_return'], pct=True)} | {_fmt(r['sharpe'], nd=2)} | "
            f"{_fmt(r['max_drawdown'], pct=True)} | {_fmt(r['benchmark_total_return'], pct=True)} | "
            f"{_fmt(r['excess_return_annualized'], pct=True)} | {_fmt(r['information_ratio'], nd=2)} | "
            f"{r['n_trades']} | {_fmt(r['total_cost_pct'], pct=True)} |"
        )

    # 判定
    lines += ["", "---", "", "## 判定", ""]
    realistic = [r for r in results if "乐观成本" not in r["variant"]
                 and "压力成本" not in r["variant"] and "反面对照" not in r["variant"]]
    passed = [
        r for r in realistic
        if r.get("total_return") is not None and r.get("benchmark_total_return") is not None
        and r["total_return"] > r["benchmark_total_return"]
        and (r.get("excess_return_annualized") or -1) > 0
        and (r.get("information_ratio") or -1) > 0.5
    ]
    lines.append(
        f"判定标准（真实成本下，三项全过才算「该变体可考虑」）："
        f"总收益 > 沪深300同期 + 年化超额 > 0 + 信息比率 > 0.5。"
    )
    if passed:
        lines.append("")
        lines.append(f"**{len(passed)}/{len(realistic)} 个真实成本变体通过**："
                     + "、".join(r["variant"] for r in passed))
        lines.append("")
        lines.append("⚠️ 即便如此 —— 见下方诚实声明，回测偏乐观，**通过 ≠ 能实盘**。")
    else:
        lines.append("")
        lines.append(f"**0/{len(realistic)} 个真实成本变体通过。**")
        lines.append("")
        lines.append("**诚实结论：这套量化目前不具备实盘价值。** 连一个偏乐观的回测都跑不过沪深300，"
                     "实盘（成本更高、偏差更多）几乎必亏。建议：继续作为学习项目，不要碰真钱。")

    lines += ["", "---", "", _HONESTY_DISCLAIMER]
    return "\n".join(lines)


# ===========================================================================
# main
# ===========================================================================

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("astock_quant.data").setLevel(logging.ERROR)
    t0 = time.time()

    universe = get_universe("stage4")
    print(f"[1/4] 准备数据 + 拿 price_panel（{len(universe)} 只）...", flush=True)
    data = prepare_stage1_data(universe=universe)
    price_panel = data["prices"]

    print("[2/4] 跑 3 个 pipeline 拿验证集 predictions ...", flush=True)
    preds_by_model: dict = {}
    for name, fn in [("ranking", run_ranking), ("return", run_return), ("direction", run_direction)]:
        r = fn(universe=universe, run_backtest=False, save_model_to=None, verbose=False)
        preds_by_model[name] = r["predictions"]
        print(f"    {name}: {len(r['predictions'])} 条 predictions", flush=True)

    # 回测区间 = 所有 predictions 的日期并集
    all_dates = sorted({p.date for preds in preds_by_model.values() for p in preds})
    bt_start, bt_end = all_dates[0], all_dates[-1]
    # price_panel 是 (date, ticker) MultiIndex —— 用位置 0 取 date level（不依赖 level 名）
    date_level = price_panel.index.get_level_values(0)
    mask = (date_level >= pd.Timestamp(bt_start)) & (date_level <= pd.Timestamp(bt_end))
    price_panel_bt = price_panel[mask]

    print(f"[3/4] 拉沪深300基准（{bt_start} ~ {bt_end}）...", flush=True)
    benchmark = load_hs300_benchmark(bt_start, bt_end)

    print(f"[4/4] 跑 {len(STRATEGY_VARIANTS)} 个策略变体 ...", flush=True)
    results: list[dict] = []
    for i, v in enumerate(STRATEGY_VARIANTS, 1):
        preds = preds_by_model[v["model"]]
        try:
            res = run_one_strategy(v, preds, price_panel_bt, benchmark)
        except Exception as e:  # noqa: BLE001
            res = {"variant": v["name"], "model": v["model"], "error": repr(e)[:200]}
        results.append(res)
        print(f"    [{i}/{len(STRATEGY_VARIANTS)}] {v['name']}: "
              f"总收益={_fmt(res.get('total_return'), pct=True)}", flush=True)

    benchmark_row = run_benchmark_buy_and_hold(benchmark)

    # 落盘
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    n_months = round((pd.Timestamp(bt_end) - pd.Timestamp(bt_start)).days / 30, 1)
    meta = {
        "date": date_str, "universe_size": len(universe),
        "bt_start": str(bt_start), "bt_end": str(bt_end), "n_months": n_months,
    }

    json_path = OUT_DIR / f"results_{date_str}.json"
    json_path.write_text(json.dumps(
        {"meta": meta, "benchmark": benchmark_row, "variants": results},
        ensure_ascii=False, indent=2), encoding="utf-8")

    report = assemble_report(results, benchmark_row, meta)
    md_path = OUT_DIR / f"report_{date_str}.md"
    md_path.write_text(report, encoding="utf-8")

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    print(f"\nJSON → {json_path}")
    print(f"报告 → {md_path}")
    print(f"总耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
