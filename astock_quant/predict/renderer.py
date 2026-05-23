"""每日价值选股报告渲染器.

报告结构（HTML / MD 一致）：今日速览 → 诚信声明 → §1 价值选股推荐名单
→ §2 策略回测 → §3 历史准确率 → §4 运行元数据。
今日速览三行均围绕价值选股名单与回测；不含短期涨跌预测内容。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from astock_quant.predict.ticker_names import get_ticker_name

_TEMPLATE_DIR = Path(__file__).parent / "templates"


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
            import math as _math
            score_str = (
                f"（综合分 {float(top_score):.3f}）"
                if top_score is not None and not _math.isnan(float(top_score))
                else ""
            )
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
# Value picks section — quarterly recommended buy list
# ---------------------------------------------------------------------------

def _pick_reason_text(pick: dict[str, Any]) -> tuple[str, bool]:
    """选 reason 列展示内容: 优先 llm_rationale (AI 生成), fallback 旧 reason.

    Returns (text, is_llm). is_llm=True 时模板加 🤖 标识 + 禁用 HTML 转义.
    """
    rationale = pick.get("llm_rationale")
    if rationale and isinstance(rationale, str) and rationale.strip():
        return rationale.strip(), True
    return pick.get("reason", ""), False


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
        reason_text, is_llm = _pick_reason_text(pick)

        import math
        pe_str = _fmt_val(pe_pct, pe_raw, is_pct=(pe_pct is not None and not (isinstance(pe_pct, float) and math.isnan(pe_pct))))
        pb_str = _fmt_val(pb_pct, pb_raw, is_pct=(pb_pct is not None and not (isinstance(pb_pct, float) and math.isnan(pb_pct))))
        roe_str = f"{float(roe):.1f}%" if roe is not None and not (isinstance(roe, float) and math.isnan(roe)) else "-"
        score_str = f"{score:.3f}" if isinstance(score, float) else str(score)

        if is_llm:
            # AI 生成: 显式 🤖 标 + 保留 markdown 换行 (用 <br> 替换), 限高滚动
            from html import escape as _esc
            safe_reason = _esc(reason_text).replace("\n", "<br>")
            reason_html = (
                f"<details style='font-size:0.88em;color:#333'>"
                f"<summary style='cursor:pointer;color:#1a6e38;font-weight:bold'>"
                f"🤖 AI 解读 (点击展开)</summary>"
                f"<div style='margin-top:6px;max-height:240px;overflow-y:auto;"
                f"padding:8px;background:#f8fcf8;border-left:3px solid #27ae60;"
                f"border-radius:4px'>{safe_reason}</div>"
                f"</details>"
            )
        else:
            reason_html = f"<span style='color:#555;font-size:0.88em'>{reason_text}</span>"

        rows += (
            f"<tr>"
            f"<td>#{i}</td>"
            f"<td>{ticker} <small>{name}</small></td>"
            f"<td><strong>{score_str}</strong></td>"
            f"<td>{pe_str}</td>"
            f"<td>{pb_str}</td>"
            f"<td>{roe_str}</td>"
            f"<td>{reason_html}</td>"
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

        reason_text, is_llm = _pick_reason_text(pick)
        # MD 表格 cell 不能有换行 / | / 多行 markdown → 一律 squash 成单行
        reason_md = reason_text.replace("|", "\\|").replace("\n", " ").strip()
        if is_llm:
            reason_md = f"🤖 {reason_md}"
        # 限长避免 MD 表格炸开
        if len(reason_md) > 200:
            reason_md = reason_md[:197] + "..."

        lines.append(f"| #{i} | {ticker} {name} | {score_str} | {pe_str} | {pb_str} | {roe_str} | {reason_md} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM market summary section (今日市场速览)
# ---------------------------------------------------------------------------


def _fmt_market_summary_html(summary: str | None) -> str:
    """渲染今日市场速览 (LLM 生成). None 时返回空 (模板隐藏 section)."""
    if not summary or not isinstance(summary, str) or not summary.strip():
        return ""
    from html import escape as _esc
    safe = _esc(summary).replace("\n", "<br>")
    return f"""<div class="card" style="background:#f0f9ff;border-left:4px solid #1890ff">
<h3 style="margin-top:0;color:#1890ff">🤖 今日市场速览 (AI 综述)</h3>
<div style="font-size:0.95em;line-height:1.7;color:#333">{safe}</div>
</div>"""


def _fmt_market_summary_md(summary: str | None) -> str:
    """MD 版今日市场速览. None 时返回空字符串."""
    if not summary or not isinstance(summary, str) or not summary.strip():
        return ""
    return f"### 🤖 今日市场速览 (AI 综述)\n\n{summary.strip()}\n"


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
    llm_market_summary = results.get("llm_market_summary", None)

    errors = results.get("errors", [])
    errors_summary = "无" if not errors else "; ".join(str(e) for e in errors)

    # 今日速览三行 —— 取 value_picks / backtest 数据生成速读文字
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
        "llm_market_summary_section": _fmt_market_summary_html(llm_market_summary),
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
        "llm_market_summary_section": _fmt_market_summary_md(llm_market_summary),
    })
    md_content = Template(md_tpl).safe_substitute(md_subs)
    md_path = output_dir / f"daily_report_{report_date}.md"
    md_path.write_text(md_content, encoding="utf-8")

    return html_path, md_path
