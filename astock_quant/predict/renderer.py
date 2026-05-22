"""每日价值选股报告渲染器.

报告结构（HTML / MD 一致）：今日速览 → 诚信声明 → §1 价值选股推荐名单
→ §2 策略回测 → §3 历史准确率 → §4 运行元数据。
旧的 4 个短期涨跌预测模型（direction/return/ranking/trade_signal）已不在报告里
单独成章；direction / ranking 的结果仅用于「今日速览」三行速读文字。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from astock_quant.predict.ticker_names import get_ticker_name, get_ticker_short_name

_TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# ASCII bar helper (kept for backward compat / non-signal uses)
# ---------------------------------------------------------------------------

def make_ascii_bar(values: dict[str, float] | list[float], max_width: int = 40) -> str:
    """Return a plain-text bar chart using █░ characters."""
    if isinstance(values, list):
        items: list[tuple[str, float]] = [(str(i), v) for i, v in enumerate(values)]
    else:
        items = list(values.items())

    if not items:
        return "(empty)"

    max_val = max(abs(v) for _, v in items) or 1.0
    max_lbl = max(len(k) for k, _ in items)

    lines = []
    for label, val in items:
        bar_len = int(abs(val) / max_val * max_width)
        bar = "█" * bar_len + "░" * (max_width - bar_len)
        lines.append(f"{label:<{max_lbl}} │{bar}│ {val:+.4f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Metric translation helpers
# ---------------------------------------------------------------------------

def _translate_metric(metric_name: str, value: Any) -> str:
    """Dynamically translate a metric value to plain-language verdict."""
    if not isinstance(value, (int, float)):
        return "数据不足"
    v = float(value)

    if metric_name == "auc":
        # P25：① 改为「明日强势评分」（预测明日涨 >3%）。这是不平衡任务，
        # AUC 天然偏高 —— 高 AUC ≠ 能盈利，措辞须诚实不夸大。
        if v < 0.5:
            return "📖 比抛硬币还差，模型在帮倒忙"
        if v < 0.55:
            return "📖 跟猜硬币差不多（基线 0.5）"
        if v < 0.65:
            return "📖 有一点区分力，但很弱，仅供参考"
        if v < 0.80:
            return (
                "📖 有真实区分力（能挑出较可能走强的票），但预测的是少见的大涨、"
                "AUC 偏乐观，不代表能稳定盈利"
            )
        return "📖 异常高，请检查是否数据泄漏（look-ahead）"

    if metric_name == "r2":
        if v < 0:
            return "📖 比「直接猜均值」还差一点点"
        if v < 0.01:
            return "📖 接近「直接猜均值」水平"
        if v < 0.03:
            return "📖 略好于「直接猜均值」，但解释力很弱"
        if v < 0.10:
            return "📖 有一定预测力，业界算合格"
        return "📖 解释力强，请检查是否数据泄漏（look-ahead）"

    if metric_name in ("rank_ic", "spearman_corr"):
        abs_v = abs(v)
        if abs_v < 0.02:
            return "📖 给 30 只票排名跟抓阄差不多"
        if abs_v < 0.05:
            return "📖 排名能力很弱，统计意义不显著"
        if abs_v < 0.10:
            return "📖 排名有一定信号，量化业界算合格"
        return "📖 排名信号较强，请检查是否数据泄漏"

    if metric_name in ("accuracy", "macro_f1", "signal_f1"):
        if v < 0.33:
            return "📖 比「买/卖/不动」三选一瞎猜还差"
        if v < 0.38:
            return "📖 跟从「买/卖/不动」三选一瞎猜一样"
        if v < 0.45:
            return "📖 略好于三选一瞎猜"
        return "📖 真有点信号，但仍需多次验证"

    return ""


# ---------------------------------------------------------------------------
# Today summary (§0)
# ---------------------------------------------------------------------------

def _render_today_summary(results: dict[str, Any]) -> dict[str, str]:
    """Generate the 3-line 今日速览 for the value-stock report.

    三行均围绕「价值选股」：line1 本期综合分第一的票，line2 名单规模，
    line3 诚信结论（回测跑赢≠实盘赚钱）。数据取自 results 的 value_picks /
    backtest —— 不再读旧的 4 个短期涨跌预测模型。

    Returns dict with keys: summary_line_1, summary_line_2, summary_line_3.
    """
    value_picks = results.get("value_picks") or []
    backtest = results.get("backtest") or {}

    # line1：本期综合分最高的价值股
    if value_picks:
        top = value_picks[0]
        top_code = str(top.get("ticker", "?"))
        top_name = get_ticker_name(top_code)
        top_score = top.get("composite_score", top.get("score"))
        try:
            score_str = f"（综合分 {float(top_score):.3f}）" if top_score is not None else ""
        except (TypeError, ValueError):
            score_str = ""
        line1 = (
            f"🎯 今日一句话：本期综合分最高的是 {top_name}（{top_code}）{score_str}"
            f"—— 综合分看的是「又便宜又能赚钱」，不是预测明天涨跌。"
        )
    else:
        line1 = "🎯 今日一句话：本期暂无价值选股名单（数据未就绪）。"

    # line2：名单规模 + 持有方式
    if value_picks:
        line2 = (
            f"🥇 本期推荐名单共 {len(value_picks)} 只，按综合分降序排列；"
            f"策略设计是每季度调一次仓、长期持有。"
        )
    else:
        line2 = "🥇 本期推荐名单暂不可用。"

    # line3：诚信结论 —— 口径与首页 / docs 说明一致
    excess = backtest.get("excess_return")
    try:
        excess_str = f"（回测年化超额约 {float(excess) * 100:.1f}%，但样本小、偏乐观）" if excess is not None else ""
    except (TypeError, ValueError):
        excess_str = ""
    line3 = (
        f"⚠️ 诚信结论：这套价值选股策略在历史回测里有跑赢沪深300的迹象"
        f"{excess_str}，但「回测跑赢」不等于「实盘能赚钱」。"
        f"全部仅供学习研究，不构成投资建议。"
    )
    return {"summary_line_1": line1, "summary_line_2": line2, "summary_line_3": line3}


# ---------------------------------------------------------------------------
# Signal distribution renderer
# ---------------------------------------------------------------------------

def _render_signal_distribution(predictions: list, style: str = "md") -> str:
    """Render signal distribution. style='md' → ASCII; style='html' → CSS bars."""
    n_buy = sum(1 for p in predictions if getattr(p, "value", None) == 1.0)
    n_sell = sum(1 for p in predictions if getattr(p, "value", None) == 0.0)
    n_hold = len(predictions) - n_buy - n_sell
    total = len(predictions) or 1

    if style == "html":
        buy_pct = n_buy / total * 100
        sell_pct = n_sell / total * 100
        hold_pct = n_hold / total * 100
        return (
            '<div class="signal-distribution">'
            f'<div class="signal-bar buy" style="width:{buy_pct:.0f}%;min-width:60px"><span>↑ 看涨 {n_buy} 只</span></div>'
            f'<div class="signal-bar sell" style="width:{sell_pct:.0f}%;min-width:60px"><span>↓ 看跌 {n_sell} 只</span></div>'
            f'<div class="signal-bar hold" style="width:{hold_pct:.0f}%;min-width:60px"><span>→ 中性 {n_hold} 只</span></div>'
            "</div>"
        )

    # ASCII mode for markdown
    scores = [abs(getattr(p, "score", 0.5) - 0.5) * 2 for p in predictions]
    n_no = sum(1 for s in scores if s < 0.3)
    n_weak = sum(1 for s in scores if 0.3 <= s < 0.5)
    n_mid = sum(1 for s in scores if 0.5 <= s < 0.7)
    n_strong = sum(1 for s in scores if s >= 0.7)

    def bar(n: int) -> str:
        return "█" * max(1, int(n / total * 15)) if n else ""

    lines = [
        "当前涨跌分布：",
        f"  ↑ 看涨 {n_buy} 只",
        f"  ↓ 看跌 {n_sell} 只",
        f"  → 中性 {n_hold} 只",
        "",
        f"模型把握度分布（共 {total} 只）：",
        f"  没把握（<0.3）      {bar(n_no):<15} {n_no} 只 ({n_no/total*100:.0f}%)",
        f"  把握很弱（0.3~0.5） {bar(n_weak):<15} {n_weak} 只 ({n_weak/total*100:.0f}%)",
        f"  把握一般（0.5~0.7） {bar(n_mid):<15} {n_mid} 只 ({n_mid/total*100:.0f}%)",
        f"  把握较强（>0.7）    {bar(n_strong):<15} {n_strong} 只 ({n_strong/total*100:.0f}%)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plain language section summaries
# ---------------------------------------------------------------------------

def _render_plain_language(section: str, section_data: dict[str, Any]) -> str:
    """Generate 📖 plain-language summary for a section."""
    preds = section_data.get("predictions", [])
    metrics = section_data.get("metrics", {})

    if section == "direction":
        # P25：① 改为「明日强势评分」—— 解读 score 排名，不再涨/跌二分类
        if not preds:
            return "📖 明日强势评分无数据。"
        ranked = sorted(preds, key=lambda p: getattr(p, "score", 0.0), reverse=True)
        top = ranked[0]
        top_name = get_ticker_name(getattr(top, "ticker", ""))
        top_score = getattr(top, "score", 0.0)
        auc = metrics.get("auc", 0.0)
        n_strong = sum(1 for p in preds if getattr(p, "score", 0.0) >= 0.5)
        return (
            f"📖 模型今天评分最高的是 {top_name}（{top_score:.2f}），"
            f"全场 {n_strong} 只评分 ≥ 0.5。评分 = 模型估计的「明日涨 >3%」概率，"
            f"AUC={auc:.2f}（有区分力但偏弱，且预测的是较少见的大涨事件）。"
            f"高分 ≠ 稳赚，不构成投资建议。"
        )

    if section == "return":
        if not preds:
            return "📖 收益率预测无数据。"
        vals = [abs(getattr(p, "value", 0.0)) for p in preds]
        max_abs = max(vals) if vals else 0.0
        r2 = metrics.get("r2", 0.0)
        if max_abs < 0.005:
            return (
                "📖 模型预测所有票涨跌幅都在 ±0.5% 之内，绝对值接近 0 —— 等于**偷懒猜均值**。"
                f"这就是 R²={r2:.4f} 的体现：模型没本事预测幅度。"
            )
        if max_abs < 0.02:
            return (
                f"📖 模型预测涨跌幅最大 {max_abs:.2%}，但 IC 接近 0 意味着**幅度方向都不准**。看个热闹就行。"
            )
        sorted_by_val = sorted(preds, key=lambda p: getattr(p, "value", 0), reverse=True)
        top_p = sorted_by_val[0]
        bot_p = sorted_by_val[-1]
        top_name = get_ticker_name(getattr(top_p, "ticker", ""))
        bot_name = get_ticker_name(getattr(bot_p, "ticker", ""))
        top_val = getattr(top_p, "value", 0.0)
        bot_val = getattr(bot_p, "value", 0.0)
        return (
            f"📖 模型预测最大涨幅 {top_val:+.2%}（{top_name}）、最大跌幅 {bot_val:+.2%}（{bot_name}）。"
            f"但模型 R²={r2:.4f}，**幅度准确性不可信**。"
        )

    if section == "ranking":
        ic = abs(metrics.get("spearman_corr", 0.0) or 0.0)
        sorted_preds = sorted(preds, key=lambda p: getattr(p, "score", 0), reverse=True)
        top5_shorts = " / ".join(
            get_ticker_short_name(getattr(p, "ticker", "")) for p in sorted_preds[:5]
        ) or "（无数据）"
        if ic < 0.02:
            return f"📖 前 5 名（{top5_shorts}）按 ranking score 排，但 rank-IC≈{ic:.3f} **相当于抓阄**，不要当真。"
        if ic < 0.05:
            return f"📖 前 5 名（{top5_shorts}）有弱相关性（rank-IC≈{ic:.3f}），统计意义不显著。"
        return f"📖 前 5 名（{top5_shorts}）相关性较强（rank-IC≈{ic:.3f}），但仍需多次验证后才能信。"

    if section == "trade_signal":
        n_tp = sum(1 for p in preds if getattr(p, "value", None) == 1.0)
        n_sl = sum(1 for p in preds if getattr(p, "value", None) == -1.0)
        n_hold = sum(1 for p in preds if getattr(p, "value", None) == 0.0)
        # P25b：用真实 macro-F1，不再写死「33%」假数字（三选一瞎猜基线才是 0.33）
        macro_f1 = metrics.get("macro_f1", 0.0)
        buy_preds = section_data.get("buy_predictions", [])
        if n_tp == 0 and n_sl == 0:
            return (
                f"📖 今天 buy 信号 0 / sell 信号 0 / hold {n_hold}，"
                f"**模型不让你买任何东西**也不让卖任何东西——这是 Stage 4 设计的保守默认行为。"
            )
        if n_tp > 0:
            top_buy_names = "、".join(
                get_ticker_name(getattr(p, "ticker", "")) for p in buy_preds[:3]
            )
            top_conf = max((getattr(p, "score", 0.0) for p in buy_preds), default=0.0)
            return (
                f"📖 今天 buy 信号 {n_tp}：{top_buy_names}，置信度 {top_conf:.2f}。"
                f"模型 macro-F1≈{macro_f1:.2f}（三选一瞎猜基线≈0.33），信号偏弱，不构成投资建议。"
            )
        if n_sl > 0:
            return (
                f"📖 今天 sell 信号 {n_sl}，hold {n_hold}。"
                f"模型 macro-F1≈{macro_f1:.2f}（三选一瞎猜基线≈0.33），信号偏弱，不构成投资建议。"
            )

    return ""


# ---------------------------------------------------------------------------
# Value picks section — quarterly recommended buy list
# ---------------------------------------------------------------------------

def _fmt_value_picks_html(value_picks: list[dict[str, Any]] | None) -> str:
    """Render quarterly value stock picks as HTML table."""
    if not value_picks:
        return (
            "<div class='card'><p class='meta'>价值选股数据尚未就绪"
            "（factor-engineer / strategy-engineer 完成后自动填充）。</p></div>"
        )
    def _fmt_val(pct_val: Any, raw_val: Any, is_pct: bool) -> str:
        """Show percentile (e.g. '12%') if available, else raw value, else '-'."""
        import math
        v = pct_val
        if v is None or (isinstance(v, float) and math.isnan(v)):
            v = raw_val
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "-"
        try:
            return f"{float(v):.0f}%" if is_pct else f"{float(v):.1f}"
        except (TypeError, ValueError):
            return "-"

    rows = ""
    for i, pick in enumerate(value_picks[:20], 1):
        ticker = pick.get("ticker", "")
        name = get_ticker_name(ticker)
        score = pick.get("composite_score", pick.get("score", 0.0))
        pe_pct = pick.get("pe_percentile", None)
        pb_pct = pick.get("pb_percentile", None)
        pe_raw = pick.get("pe", None)
        pb_raw = pick.get("pb", None)
        roe = pick.get("roe", None)
        reason = pick.get("reason", "")

        import math
        pe_str = _fmt_val(pe_pct, pe_raw, is_pct=(pe_pct is not None and not (isinstance(pe_pct, float) and math.isnan(pe_pct))))
        pb_str = _fmt_val(pb_pct, pb_raw, is_pct=(pb_pct is not None and not (isinstance(pb_pct, float) and math.isnan(pb_pct))))
        roe_str = f"{float(roe):.1f}%" if roe is not None and not (isinstance(roe, float) and math.isnan(roe)) else "-"
        score_str = f"{score:.3f}" if isinstance(score, float) else str(score)

        rows += (
            f"<tr>"
            f"<td>#{i}</td>"
            f"<td>{ticker} <small>{name}</small></td>"
            f"<td><strong>{score_str}</strong></td>"
            f"<td>{pe_str}</td>"
            f"<td>{pb_str}</td>"
            f"<td>{roe_str}</td>"
            f"<td style='color:#555;font-size:0.88em'>{reason}</td>"
            f"</tr>\n"
        )
    return f"""<div class="card">
<p class="meta">综合分 = 便宜度（PE/PB）+ 质量（ROE 等）加权，PE/PB 越低越便宜，ROE 越高越赚钱。</p>
<table>
<thead><tr><th>排名</th><th>股票</th><th>综合分</th><th>PE</th><th>PB</th><th>ROE</th><th>入选理由</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div>"""


def _fmt_value_picks_md(value_picks: list[dict[str, Any]] | None) -> str:
    """Render quarterly value stock picks as Markdown table."""
    if not value_picks:
        return "价值选股数据尚未就绪（factor-engineer / strategy-engineer 完成后自动填充）。"
    lines = [
        "综合分 = 便宜度（PE/PB）+ 质量（ROE 等）加权，PE/PB 越低越便宜，ROE 越高越赚钱。",
        "",
        "| 排名 | 股票 | 综合分 | PE | PB | ROE | 入选理由 |",
        "|-----|------|--------|------------|------------|-----|---------|",
    ]
    for i, pick in enumerate(value_picks[:20], 1):
        import math
        ticker = pick.get("ticker", "")
        name = get_ticker_name(ticker)
        score = pick.get("composite_score", pick.get("score", 0.0))
        pe_pct = pick.get("pe_percentile", None)
        pb_pct = pick.get("pb_percentile", None)
        pe_raw = pick.get("pe", None)
        pb_raw = pick.get("pb", None)
        roe = pick.get("roe", None)
        reason = pick.get("reason", "")

        def _nan_none(v: Any) -> Any:
            return None if (isinstance(v, float) and math.isnan(v)) else v

        pe_pct = _nan_none(pe_pct)
        pb_pct = _nan_none(pb_pct)
        pe_raw = _nan_none(pe_raw)
        pb_raw = _nan_none(pb_raw)
        roe = _nan_none(roe)

        pe_v = pe_pct if pe_pct is not None else pe_raw
        pb_v = pb_pct if pb_pct is not None else pb_raw
        pe_str = (f"{float(pe_v):.0f}%" if pe_pct is not None else f"{float(pe_v):.1f}") if pe_v is not None else "-"
        pb_str = (f"{float(pb_v):.0f}%" if pb_pct is not None else f"{float(pb_v):.2f}") if pb_v is not None else "-"
        roe_str = f"{float(roe):.1f}%" if roe is not None else "-"
        score_str = f"{score:.3f}" if isinstance(score, float) else str(score)

        lines.append(f"| #{i} | {ticker} {name} | {score_str} | {pe_str} | {pb_str} | {roe_str} | {reason} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backtest performance section — strategy vs HS300
# ---------------------------------------------------------------------------

# 第二行回测 caveat —— 固定补充，与首页 / docs 说明的诚实口径保持一致。
# artifact 的 disclaimers[0] 只覆盖「单一市场环境」一点，这里把「样本小 / 幸存者偏差 /
# 超额靠 2024 单年 / IR 偏低 / 回测跑赢≠实盘赚钱」也明说，避免报告口径比网站单薄。
_BACKTEST_CAVEAT_2 = (
    "这段回测还偏乐观：样本小、选股池有幸存者偏差、超额收益主要靠 2024 年单独一年、"
    "信息比率 IR 偏低（说明这点超额并不稳）。「回测跑赢」不等于「未来实盘能赚钱」。"
)


def _fmt_backtest_html(backtest: dict[str, Any] | None) -> str:
    """Render backtest performance vs HS300 as HTML."""
    if not backtest:
        return (
            "<div class='card'><p class='meta'>回测数据尚未就绪"
            "（T4 strategy-engineer 完成后自动填充）。</p></div>"
        )
    strategy_return = backtest.get("strategy_total_return", None)
    benchmark_return = backtest.get("benchmark_total_return", None)
    excess_return = backtest.get("excess_return", None)
    sharpe = backtest.get("sharpe_ratio", None)
    max_dd = backtest.get("max_drawdown", None)
    n_quarters = backtest.get("n_quarters", None)
    period = backtest.get("period", "")
    caveat = backtest.get("caveat", "回测不代表实盘，历史收益不预测未来。")

    def _pct(v: Any) -> str:
        if v is None:
            return "-"
        try:
            return f"{float(v)*100:.1f}%"
        except (TypeError, ValueError):
            return str(v)

    def _f2(v: Any) -> str:
        if v is None:
            return "-"
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v)

    excess_color = "#27ae60" if (excess_return or 0) > 0 else "#c0392b"
    rows = f"""
<tr><td>策略累计收益</td><td><strong>{_pct(strategy_return)}</strong></td></tr>
<tr><td>沪深300 同期</td><td>{_pct(benchmark_return)}</td></tr>
<tr><td>超额收益</td><td style="color:{excess_color};font-weight:bold">{_pct(excess_return)}</td></tr>
<tr><td>Sharpe 比率</td><td>{_f2(sharpe)}</td></tr>
<tr><td>最大回撤</td><td>{_pct(max_dd)}</td></tr>
<tr><td>回测期间</td><td>{period}</td></tr>
<tr><td>换仓次数</td><td>{n_quarters if n_quarters is not None else "-"} 季度</td></tr>
"""
    return f"""<div class="card">
<table>
<thead><tr><th>指标</th><th>值</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="meta" style="margin-top:8px;color:#d4380d">⚠️ {caveat}</p>
<p class="meta" style="margin-top:6px;color:#d4380d">⚠️ {_BACKTEST_CAVEAT_2}</p>
</div>"""


def _fmt_backtest_md(backtest: dict[str, Any] | None) -> str:
    """Render backtest performance vs HS300 as Markdown."""
    if not backtest:
        return "回测数据尚未就绪（T4 strategy-engineer 完成后自动填充）。"

    strategy_return = backtest.get("strategy_total_return", None)
    benchmark_return = backtest.get("benchmark_total_return", None)
    excess_return = backtest.get("excess_return", None)
    sharpe = backtest.get("sharpe_ratio", None)
    max_dd = backtest.get("max_drawdown", None)
    n_quarters = backtest.get("n_quarters", None)
    period = backtest.get("period", "")
    caveat = backtest.get("caveat", "回测不代表实盘，历史收益不预测未来。")

    def _pct(v: Any) -> str:
        if v is None:
            return "-"
        try:
            return f"{float(v)*100:.1f}%"
        except (TypeError, ValueError):
            return str(v)

    def _f2(v: Any) -> str:
        if v is None:
            return "-"
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v)

    lines = [
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 策略累计收益 | **{_pct(strategy_return)}** |",
        f"| 沪深300 同期 | {_pct(benchmark_return)} |",
        f"| 超额收益 | {_pct(excess_return)} |",
        f"| Sharpe 比率 | {_f2(sharpe)} |",
        f"| 最大回撤 | {_pct(max_dd)} |",
        f"| 回测期间 | {period} |",
        f"| 换仓次数 | {n_quarters if n_quarters is not None else '-'} 季度 |",
        "",
        f"> ⚠️ {caveat}",
        "",
        f"> ⚠️ {_BACKTEST_CAVEAT_2}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Accuracy section
# ---------------------------------------------------------------------------

def _fmt_accuracy_html(accuracy: dict[str, Any] | None) -> str:
    if not accuracy:
        return "<p class='meta'>暂无历史准确率数据（P14 落盘后可用）</p>"
    rows = ""
    for model, metrics in accuracy.items():
        for k, v in metrics.items():
            rows += f"<tr><td>{model}</td><td>{k}</td><td>{v}</td></tr>\n"
    return f"""<table>
<thead><tr><th>模型</th><th>指标</th><th>值</th></tr></thead>
<tbody>{rows}</tbody>
</table>"""


def _fmt_accuracy_md(accuracy: dict[str, Any] | None) -> str:
    if not accuracy:
        return "暂无历史准确率数据（P14 落盘后可用）"
    lines = ["| 模型 | 指标 | 值 |", "|-----|------|-----|"]
    for model, metrics in accuracy.items():
        for k, v in metrics.items():
            lines.append(f"| {model} | {k} | {v} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(results: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Render the value-stock daily report to HTML and Markdown.

    报告结构（HTML 与 MD 一致）：今日速览 → 诚信声明 → §1 价值选股推荐名单
    → §2 策略回测 → §3 历史准确率 → §4 运行元数据。

    Args:
        results: dict returned by daily.py with keys:
            report_date, universe_size, generated_at, data_cutoff,
            total_seconds, json_path, errors,
            direction / ranking (optional): 仅用来生成「今日速览」三行速读文字；
                这 4 个短期模型本身已不在报告里单独展示。
            accuracy (optional): 历史准确率表数据，None 时显示占位。
            value_picks (optional): list of dicts with keys ticker, composite_score,
                pe_percentile, pb_percentile, roe, reason
            backtest (optional): dict with strategy_total_return, benchmark_total_return,
                excess_return, sharpe_ratio, max_drawdown, n_quarters, period, caveat
        output_dir: directory to write reports into (created if missing)

    Returns:
        (html_path, md_path)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_date = results.get("report_date", datetime.now().strftime("%Y-%m-%d"))

    accuracy = results.get("accuracy", None)
    value_picks = results.get("value_picks", None)
    backtest = results.get("backtest", None)

    errors = results.get("errors", [])
    errors_summary = "无" if not errors else "; ".join(str(e) for e in errors)

    # 今日速览三行 —— 仍读 direction / ranking 数据生成速读文字
    today = _render_today_summary(results)

    subs: dict[str, str] = {
        "report_date": report_date,
        "universe_size": str(results.get("universe_size", "?")),
        "generated_at": str(results.get("generated_at", datetime.now().isoformat(timespec="seconds"))),
        "data_cutoff": str(results.get("data_cutoff", "?")),
        "total_seconds": str(results.get("total_seconds", "?")),
        "json_path": str(results.get("json_path", "?")),
        "errors_summary": errors_summary,
        "summary_line_1": today["summary_line_1"],
        "summary_line_2": today["summary_line_2"],
        "summary_line_3": today["summary_line_3"],
    }

    # HTML
    html_tpl = (_TEMPLATE_DIR / "daily_report.html.template").read_text(encoding="utf-8")
    html_subs = dict(subs)
    html_subs.update({
        "value_picks_section": _fmt_value_picks_html(value_picks),
        "backtest_section": _fmt_backtest_html(backtest),
        "accuracy_section": _fmt_accuracy_html(accuracy),
    })
    html_content = Template(html_tpl).safe_substitute(html_subs)
    html_path = output_dir / f"daily_report_{report_date}.html"
    html_path.write_text(html_content, encoding="utf-8")

    # Markdown
    md_tpl = (_TEMPLATE_DIR / "daily_report.md.template").read_text(encoding="utf-8")
    md_subs = dict(subs)
    md_subs.update({
        "value_picks_section": _fmt_value_picks_md(value_picks),
        "backtest_section": _fmt_backtest_md(backtest),
        "accuracy_section": _fmt_accuracy_md(accuracy),
    })
    md_content = Template(md_tpl).safe_substitute(md_subs)
    md_path = output_dir / f"daily_report_{report_date}.md"
    md_path.write_text(md_content, encoding="utf-8")

    return html_path, md_path
