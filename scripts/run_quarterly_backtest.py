"""scripts/run_quarterly_backtest.py — 价值选股「季度调仓回测」端到端入口（T4）.

诚实回答一个问题：**按季度持有「价值+质量综合分最高的一篮子好公司」，
扣掉真实交易成本后，能不能跑赢躺平买沪深300指数？跑赢多少？**

用法：
    uv run python scripts/run_quarterly_backtest.py
    uv run python scripts/run_quarterly_backtest.py --top-n 20 --stage stage4

产出：
    artifacts/quarterly_backtest/results_<date>.json   —— 机读：指标 + 按年超额
    artifacts/quarterly_backtest/report_<date>.md      —— 人读：策略 vs 沪深300 对照

────────────────────────────────────────────────────────────────────────
本脚本的定位
────────────────────────────────────────────────────────────────────────
它把 T4 的几个零件串起来：
  data → 综合分(T2) → 季度调仓回测(quarterly.py) → 沪深300 基准(benchmark.py) → 报告

⚠️ 与 scripts/realistic_backtest.py 的区别：那个脚本回测的是「短期涨跌模型」
（已被证明接近随机）、用「成分股等权」当基准。本脚本回测的是「价值选股」、
用**真·沪深300指数**当基准 —— 是 value-pivot 改造的核心交付物。

────────────────────────────────────────────────────────────────────────
诚实边界（也会写进产出的 report）
────────────────────────────────────────────────────────────────────────
- 回测区间约 4 年（2022-2026），单一市场环境 —— 结论是「参考」不是「铁证」。
- 选股池若是「今日沪深300成分」，有幸存者偏差（退市/被剔除的输家缺席）。
- 用收盘价成交、滑点固定、未建模冲击成本 —— 真实实盘成本更高。
这些偏差全部朝「让回测好于实盘」方向，不会互相抵消。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from astock_quant.backtest.quarterly import QuarterlyBacktestConfig, run_quarterly_backtest
from astock_quant.config.settings import get_universe
from astock_quant.data.benchmark import csi300_daily_returns
from astock_quant.data.dataset import prepare_stage1_data

logger = logging.getLogger(__name__)

OUT_DIR = Path("artifacts/quarterly_backtest")


# ===========================================================================
# T2 综合分接口 —— 价值+质量打分（factor-engineer 的 T2 产物）
# ===========================================================================

def _compute_value_score(data: dict) -> pd.Series:
    """算「价值+质量综合分」panel —— 季度调仓回测的选股依据.

    T2（factor-engineer）已交付 `astock_quant/factors/value_score.py`，本函数把
    它接进来：先算全因子矩阵（含 T1 重建的 PE/PB/股息率 + ROE/毛利率/成长因子），
    再做透明的价值/质量/成长三维横截面打分，取综合分。

    契约（run_quarterly_backtest 要求的 score_panel）：
        返回 pd.Series，MultiIndex=(date, ticker)，值为「综合吸引力分数」∈ [0,1]。
        **分数越大越好**（越「便宜 + 能赚钱」），季度调仓挑分数最高的 Top-N。
        NaN 分数的票当日不参与排名。

    防 look-ahead：综合分的所有标准化/排名都按「每日横截面」（value_score 模块
    保证），财报因子按披露日对齐（T1 fundamentals 保证）—— 全链路无未来函数。
    """
    from astock_quant.factors.registry import compute_factor_frame
    from astock_quant.factors.value_score import compute_value_scores

    # 全因子矩阵 —— 估值/盈利/成长因子都在里面（PE/PB/股息率为 T1 重建的真实历史）。
    # drop_nan_threshold=1.1 关掉「丢高 NaN 列」：价值打分要稳定的因子集，
    # 不能让某只票/某天的数据状态改变可用因子，否则打分口径会漂移。
    factor_frame = compute_factor_frame(
        price_panel=data["prices"],
        moneyflow_panel=data.get("moneyflow"),
        financials=data.get("financials"),
        drop_nan_threshold=1.1,
    )
    scores = compute_value_scores(factor_frame)
    return scores["composite_score"]


# ===========================================================================
# 报告
# ===========================================================================

def _fmt_pct(v) -> str:
    """小数 → 带符号百分数字符串；None → N/A."""
    if v is None:
        return "N/A"
    return f"{v * 100:+.2f}%"


def _fmt_num(v, nd: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{nd}f}"


def assemble_report(out: dict, meta: dict) -> str:
    """把回测结果拼成人读的 markdown 报告 —— 突出「策略 vs 沪深300」对照."""
    m = out["metrics"]
    cfg = out["config"]
    yearly = out["yearly_breakdown"]

    lines = [
        f"# 价值选股 · 季度调仓回测报告 — {meta['date']}",
        "",
        "> 一句话：这份回测检验「按季度挑价值+质量综合分最高的好公司持有」能不能",
        "> 跑赢沪深300指数。**结论是参考，不是实盘背书** —— 先看末尾「诚实声明」。",
        "",
        "---",
        "",
        "## 回测设置",
        "",
        f"- 股票池：{meta['universe_size']} 只",
        f"- 每季度持仓：综合分最高的 {cfg['top_n']} 只，等权",
        f"- 调仓频率：{cfg['rebalance_frequency']}，共调仓 {cfg['n_rebalances']} 次",
        f"- 回测区间：{m.get('start_date', 'N/A')} ~ {m.get('end_date', 'N/A')}",
        f"- 初始资金：{cfg['initial_cash']:,.0f} 元",
        f"- 交易成本：佣金 {cfg['commission_rate'] * 100:.3f}%（双边）"
        f" + 印花税 {cfg['stamp_tax_rate'] * 100:.3f}%（卖出）"
        f" + 滑点 {cfg['slippage_bps']:.0f}bp",
        "",
        "---",
        "",
        "## 策略 vs 沪深300指数",
        "",
        "| 指标 | 价值选股策略 | 沪深300指数 |",
        "|---|---:|---:|",
        f"| 累计收益 | {_fmt_pct(m.get('total_return'))} | "
        f"{_fmt_pct(m.get('benchmark_total_return'))} |",
        f"| 年化收益 | {_fmt_pct(m.get('annualized_return'))} | — |",
        f"| 最大回撤 | {_fmt_pct(m.get('max_drawdown'))} | — |",
        f"| 夏普比率 | {_fmt_num(m.get('sharpe'))} | — |",
        f"| 索提诺比率 | {_fmt_num(m.get('sortino'))} | — |",
        "",
        f"**超额收益（年化 alpha = 策略 − 指数）：{_fmt_pct(m.get('excess_return_annualized'))}**",
        f"　信息比率：{_fmt_num(m.get('information_ratio'))}　β：{_fmt_num(m.get('beta'))}",
        "",
    ]

    if m.get("benchmark_total_return") is None:
        lines += [
            "⚠️ **沪深300指数数据未能获取** —— 本次回测无法计算超额收益（alpha）。",
            "上面「指数」列与超额相关指标为 N/A。这是数据缺失的如实标注，不是 0。",
            "",
        ]

    # 按年超额分解
    lines += ["---", "", "## 按年份分解（哪年赢、哪年输）", ""]
    if yearly:
        lines.append("| 年份 | 策略收益 | 沪深300收益 | 超额 | 交易日数 |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in yearly:
            lines.append(
                f"| {row['year']} | {_fmt_pct(row['strategy_return'])} | "
                f"{_fmt_pct(row['benchmark_return'])} | {_fmt_pct(row['excess_return'])} | "
                f"{row['n_trading_days']} |"
            )
        # 诚实提示：别被「总超额」掩盖年度波动
        rows_with_excess = [r for r in yearly if r["excess_return"] is not None]
        if rows_with_excess:
            n_year = len(rows_with_excess)
            n_win = sum(1 for r in rows_with_excess if r["excess_return"] > 0)
            # A股 一年约 242 个交易日 —— 交易日数明显偏少的年份是「不完整年」（如回测
            # 末年只跑了一个季度）。诚实区分：不完整年的超额波动大、参考意义弱。
            partial = [r for r in rows_with_excess if r["n_trading_days"] < 200]
            line = f"策略在这 {n_year} 个年份里赢指数 {n_win} 年、输 {n_year - n_win} 年。"
            if partial:
                ys = "、".join(str(r["year"]) for r in partial)
                line += (
                    f"注意其中 {ys} 是**不完整年**（交易日数 "
                    f"{', '.join(str(r['n_trading_days']) for r in partial)}，"
                    f"远少于一年约 242 天），该年超额波动大、别太当真。"
                )
            lines += [
                "",
                line,
                "单一区间的总超额可能是某一两年堆出来的 —— 看年度分解才知道稳不稳。",
            ]
    else:
        lines.append("（无按年数据 —— 回测区间不足或净值曲线为空）")

    # 交易统计
    lines += [
        "", "---", "", "## 交易统计", "",
        f"- 总成交笔数：{m.get('n_trades', 0)}"
        f"（买 {m.get('n_buy_orders', 0)} / 卖 {m.get('n_sell_orders', 0)}）",
        f"- 已平仓胜率：{_fmt_pct(m.get('win_rate'))}",
        f"- 盈亏比：{_fmt_num(m.get('profit_loss_ratio'))}",
        f"- 被 A股 约束拦截的下单：{m.get('n_rejected_constraint', 0)} 笔"
        f"（涨跌停 / T+1 / 手数等）",
    ]

    # 诚实声明
    lines += ["", "---", "", "## ⚠️ 诚实声明（为什么不能直接相信上面的数字）", ""]
    for i, d in enumerate(out["disclaimers"], 1):
        lines.append(f"{i}. {d}")
    lines += [
        "",
        "**结论口径**：即使回测显示跑赢沪深300，由于以上偏差，也不足以支持实盘部署。",
        "这份回测最强能给出的是「证伪」—— 若连偏乐观的回测都跑输指数，实盘几乎必输。",
    ]

    return "\n".join(lines)


# ===========================================================================
# main
# ===========================================================================

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_quarterly_backtest",
        description="价值选股·季度调仓回测 vs 沪深300指数",
    )
    p.add_argument("--top-n", type=int, default=15,
                   help="每季度持仓只数（任务要求 15-20，默认 15）")
    p.add_argument("--stage", type=str, default="stage4",
                   help="股票池：stage1（30 只蓝筹）/ stage4（沪深300 全量，默认）")
    p.add_argument("--force-refresh-data", action="store_true",
                   help="忽略缓存全部重拉")
    p.add_argument("--quiet", action="store_true", help="只输出关键结果")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    logging.getLogger("astock_quant.data").setLevel(logging.ERROR)
    t0 = time.time()

    universe = get_universe(args.stage)
    print(f"[1/4] 准备数据（{len(universe)} 只，stage={args.stage}）...", flush=True)
    data = prepare_stage1_data(universe=universe, force_refresh=args.force_refresh_data)
    price_panel = data["prices"]
    if price_panel is None or price_panel.empty:
        print("❌ 行情 panel 为空，无法回测。", file=sys.stderr)
        return 1

    print("[2/4] 计算「价值+质量综合分」...", flush=True)
    try:
        score_panel = _compute_value_score(data)
    except NotImplementedError as e:
        print(f"\n⏸  回测暂停 —— {e}", file=sys.stderr)
        return 2

    print("[3/4] 拉沪深300 指数基准...", flush=True)
    # 基准日期范围对齐到行情 panel —— 只拉回测实际用到的区间，不多拉到「今天」。
    # （yearly_alpha_breakdown 内部也会再 clip 一次兜底，这里 scope 取数更干净。）
    panel_dates = price_panel.index.get_level_values("date")
    bt_start = panel_dates.min().strftime("%Y-%m-%d")
    bt_end = panel_dates.max().strftime("%Y-%m-%d")
    benchmark = csi300_daily_returns(
        start_date=bt_start, end_date=bt_end, force_refresh=args.force_refresh_data
    )
    if benchmark is None:
        print("⚠️  沪深300 指数拉取失败 —— 回测继续，但报告无法给出超额收益（如实标注）。",
              flush=True)

    print(f"[4/4] 跑季度调仓回测（Top-{args.top_n}）...", flush=True)
    out = run_quarterly_backtest(
        score_panel,
        price_panel,
        benchmark_returns=benchmark,
        config=QuarterlyBacktestConfig(top_n=args.top_n),
    )

    # 落盘
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    meta = {
        "date": date_str,
        "universe_size": len(universe),
        "stage": args.stage,
    }

    json_payload = {
        "meta": meta,
        "metrics": out["metrics"],
        "yearly_breakdown": out["yearly_breakdown"],
        "config": out["config"],
        "disclaimers": out["disclaimers"],
    }
    json_path = OUT_DIR / f"results_{date_str}.json"
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report = assemble_report(out, meta)
    md_path = OUT_DIR / f"report_{date_str}.md"
    md_path.write_text(report, encoding="utf-8")

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    print(f"\nJSON → {json_path}")
    print(f"报告 → {md_path}")
    print(f"总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
