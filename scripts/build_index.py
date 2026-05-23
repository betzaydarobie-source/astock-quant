#!/usr/bin/env python3
"""生成 docs/index.html 价值选股报告归档首页。无参数，从 PROJECT_ROOT 运行。

2026-05-22 起本项目方法从「涨跌预测」改为「价值选股」。首页因此重写：
- 不再展示已废弃的「方向 AUC / 收益 R²」指标
- 改为展示价值策略回测的累计收益 vs 沪深300（取自 quarterly_backtest artifact）
- 归档表简化为「日期 + 查看」—— 每份报告的价值名单只存在于渲染后的 HTML，
  predictions_<date>.json 里没有可逐日对比的价值指标
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_REPORTS = PROJECT_ROOT / "docs" / "reports"
BACKTEST_DIR = PROJECT_ROOT / "artifacts" / "quarterly_backtest"
INDEX_HTML = PROJECT_ROOT / "docs" / "index.html"

DATE_RE = re.compile(r"daily_report_(\d{4}-\d{2}-\d{2})\.html$")

# 方法切换日：此日（含）起为「价值选股」，更早的报告是旧「涨跌预测」方法
METHOD_SWITCH_DATE = "2026-05-22"


def load_backtest() -> dict:
    """读取最新一份季度回测 artifact —— 给首页展示策略累计表现。

    回测是「整段历史的策略表现」，不是「某一天的预测」，所以全站共用一份，
    取目录里最新的 results_*.json。文件缺失 / 解析失败 → 返回 {}（首页降级不显示）。
    """
    if not BACKTEST_DIR.is_dir():
        return {}
    matches = sorted(BACKTEST_DIR.glob("results_*.json"), reverse=True)
    if not matches:
        return {}
    try:
        with open(matches[0], encoding="utf-8") as f:
            data = json.load(f)
        m = data.get("metrics", {})
        return {
            "total_return": m.get("total_return"),
            "benchmark_total_return": m.get("benchmark_total_return"),
            "excess_annualized": m.get("excess_return_annualized"),
            "information_ratio": m.get("information_ratio"),
            "max_drawdown": m.get("max_drawdown"),
            "start_date": m.get("start_date", ""),
            "end_date": m.get("end_date", ""),
        }
    except Exception:
        return {}


def fmt_pct(val) -> str:
    """小数 → 百分比字符串，带正负号。None → 'N/A'。"""
    if val is None:
        return "N/A"
    try:
        return f"{float(val) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def fmt_num(val, fmt=".2f") -> str:
    if val is None:
        return "N/A"
    try:
        return format(float(val), fmt)
    except (TypeError, ValueError):
        return "N/A"


def build_row(report_date: str, is_latest: bool) -> str:
    """归档表一行：日期 + 方法标签 + 查看按钮。

    价值选股报告的逐日数值（推荐名单）只存在于渲染后的 HTML，
    predictions JSON 里没有可比的价值指标，所以表格只放日期 + 入口。
    """
    link = f"reports/daily_report_{report_date}.html"
    row_class = "latest-row" if is_latest else "hist-row"
    if report_date >= METHOD_SWITCH_DATE:
        method_tag = '<span class="tag tag-value">价值选股</span>'
    else:
        method_tag = '<span class="tag tag-old">旧·涨跌预测</span>'
    return (
        f'<tr class="{row_class}">'
        f'<td><a href="{link}">{report_date}</a></td>'
        f"<td>{method_tag}</td>"
        f'<td><a href="{link}" class="btn">查看 →</a></td>'
        f"</tr>\n"
    )


def build_index() -> None:
    DOCS_REPORTS.mkdir(parents=True, exist_ok=True)

    report_files = sorted(
        [f for f in DOCS_REPORTS.glob("daily_report_*.html") if DATE_RE.search(f.name)],
        key=lambda f: f.name,
        reverse=True,
    )

    today_str = date.today().isoformat()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if report_files:
        latest_date = DATE_RE.search(report_files[0].name).group(1)
        latest_exists = True
    else:
        latest_date = today_str
        latest_exists = False

    bt = load_backtest()
    bt_total = fmt_pct(bt.get("total_return"))
    bt_bench = fmt_pct(bt.get("benchmark_total_return"))
    bt_excess = fmt_pct(bt.get("excess_annualized"))
    bt_ir = fmt_num(bt.get("information_ratio"))
    bt_period = ""
    if bt.get("start_date") and bt.get("end_date"):
        bt_period = f"{bt['start_date'][:7]} ~ {bt['end_date'][:7]}"

    if latest_exists:
        latest_status = (
            f'<a href="reports/daily_report_{latest_date}.html" '
            f'class="btn-primary">点击查看本期推荐名单 →</a>'
        )
        latest_badge = '<span class="badge ok">已生成</span>'
    else:
        latest_status = '<span class="badge warn">暂无报告</span>'
        latest_badge = '<span class="badge warn">暂无</span>'

    # 回测小卡片 —— 有数据才显示；口径必须诚实，不夸大
    if bt and bt.get("total_return") is not None:
        backtest_block = f"""
    <div class="backtest-strip">
      <div class="bt-title">策略历史回测（{bt_period}）</div>
      <div class="bt-metrics">
        <div class="bt-item"><div class="bt-val up">{bt_total}</div><div class="bt-lbl">策略累计收益</div></div>
        <div class="bt-item"><div class="bt-val down">{bt_bench}</div><div class="bt-lbl">沪深300 同期</div></div>
        <div class="bt-item"><div class="bt-val up">{bt_excess}</div><div class="bt-lbl">年化超额</div></div>
        <div class="bt-item"><div class="bt-val">{bt_ir}</div><div class="bt-lbl">信息比率 IR</div></div>
      </div>
      <p class="bt-caveat">⚠️ 这是一段约 4 年、偏乐观的历史回测结果（样本小、超额主要靠 2024 年单年、IR 偏低）。
      「回测跑赢」不等于「未来实盘能赚钱」，仅供学习研究参考。</p>
    </div>"""
    else:
        backtest_block = ""

    rows_html = ""
    for i, f in enumerate(report_files[:30]):
        d = DATE_RE.search(f.name).group(1)
        rows_html += build_row(d, i == 0)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股 价值选股报告 · 归档</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;
    background: #f5f5f5;
    color: #333;
    min-height: 100vh;
  }}
  .header {{
    background: linear-gradient(135deg, #14532d 0%, #166534 100%);
    color: #fff;
    padding: 2rem 1rem;
    text-align: center;
  }}
  .header h1 {{ font-size: 1.6rem; font-weight: 700; letter-spacing: 0.05em; }}
  .header .subtitle {{ margin-top: 0.4rem; font-size: 0.9rem; opacity: 0.8; }}
  .container {{ max-width: 860px; margin: 0 auto; padding: 1.5rem 1rem; }}
  .card {{
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
    padding: 1.5rem;
    margin-bottom: 1.5rem;
  }}
  .card-title {{ font-size: 1rem; font-weight: 600; color: #555; margin-bottom: 1rem; }}
  .intro {{
    background: #e6f4ea; border-left: 3px solid #16a34a;
    padding: 1rem 1.2rem; border-radius: 0 8px 8px 0;
    font-size: 0.9rem; color: #14532d; line-height: 1.7;
  }}
  .intro strong {{ color: #14532d; }}
  .latest-info {{ display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
  .latest-date {{ font-size: 2rem; font-weight: 700; color: #14532d; }}
  .badge {{
    display: inline-block; padding: 0.25rem 0.6rem; border-radius: 4px;
    font-size: 0.8rem; font-weight: 600;
  }}
  .badge.ok {{ background: #d4edda; color: #155724; }}
  .badge.warn {{ background: #fff3cd; color: #856404; }}
  .btn-primary {{
    display: inline-block; background: #16a34a; color: #fff;
    padding: 0.5rem 1.2rem; border-radius: 6px; text-decoration: none;
    font-size: 0.9rem; font-weight: 500; margin-top: 1rem;
  }}
  .btn-primary:hover {{ background: #15803d; }}
  .btn {{
    color: #166534; text-decoration: none; font-size: 0.85rem; font-weight: 500;
  }}
  .btn:hover {{ text-decoration: underline; }}
  /* 回测小条 */
  .backtest-strip {{
    margin-top: 1rem; padding: 1rem 1.2rem;
    background: #f6f8ff; border-radius: 8px; border-left: 3px solid #2c7be5;
  }}
  .bt-title {{ font-size: 0.85rem; color: #555; font-weight: 600; margin-bottom: 0.7rem; }}
  .bt-metrics {{ display: flex; gap: 1.2rem; flex-wrap: wrap; }}
  .bt-item {{ min-width: 90px; }}
  .bt-val {{ font-size: 1.3rem; font-weight: 700; color: #333; }}
  .bt-val.up {{ color: #16a34a; }}
  .bt-val.down {{ color: #c0392b; }}
  .bt-lbl {{ font-size: 0.72rem; color: #888; margin-top: 0.2rem; }}
  .bt-caveat {{
    margin-top: 0.7rem; font-size: 0.76rem; color: #b45309; line-height: 1.6;
  }}
  /* 标签 */
  .tag {{
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px;
    font-size: 0.72rem; font-weight: 600;
  }}
  .tag-value {{ background: #d4edda; color: #155724; }}
  .tag-old {{ background: #e2e3e5; color: #6c757d; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th {{ background: #f0f0f0; text-align: left; padding: 0.6rem 0.8rem; font-size: 0.8rem; color: #666; }}
  td {{ padding: 0.55rem 0.8rem; border-bottom: 1px solid #f0f0f0; }}
  tr.latest-row td {{ background: #f0fdf4; font-weight: 600; }}
  tr:hover td {{ background: #fafafa; }}
  .footer {{
    text-align: center; padding: 1.5rem 1rem 2rem;
    font-size: 0.75rem; color: #999; line-height: 1.6;
  }}
  .disclaimer {{
    background: #fff8e1; border-left: 3px solid #f9a825;
    padding: 0.8rem 1rem; border-radius: 0 6px 6px 0;
    font-size: 0.8rem; color: #5d4037; line-height: 1.6;
  }}
  /* AI 分析入口卡 */
  .ai-entry-card {{
    display: flex; align-items: center; gap: 1rem;
    background: linear-gradient(135deg, #f0fdf4 0%, #ecfeff 100%);
    border: 1.5px solid #16a34a;
    border-radius: 10px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 1.5rem;
    text-decoration: none; color: inherit;
    box-shadow: 0 2px 8px rgba(22,163,74,.10);
    transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
  }}
  .ai-entry-card:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(22,163,74,.18);
    border-color: #15803d;
  }}
  .ai-entry-icon {{
    font-size: 2rem; line-height: 1; flex-shrink: 0;
  }}
  .ai-entry-body {{ flex: 1; min-width: 0; }}
  .ai-entry-title {{
    font-size: 1.05rem; font-weight: 700; color: #14532d;
    margin-bottom: 0.3rem;
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
  }}
  .ai-entry-new {{
    display: inline-block;
    background: #16a34a; color: #fff;
    font-size: 0.65rem; font-weight: 700;
    padding: 0.1rem 0.4rem; border-radius: 3px;
    letter-spacing: 0.05em;
  }}
  .ai-entry-desc {{
    font-size: 0.83rem; color: #555; line-height: 1.55;
  }}
  .ai-entry-disclaimer {{
    color: #b45309; font-size: 0.76rem;
  }}
  .ai-entry-arrow {{
    font-size: 1.4rem; color: #16a34a; flex-shrink: 0;
    font-weight: 600;
  }}
  @media (max-width: 600px) {{
    .latest-date {{ font-size: 1.4rem; }}
    table {{ font-size: 0.8rem; }}
    th, td {{ padding: 0.45rem 0.5rem; }}
    .bt-val {{ font-size: 1.1rem; }}
    .ai-entry-card {{ padding: 0.9rem 1rem; gap: 0.7rem; }}
    .ai-entry-icon {{ font-size: 1.6rem; }}
    .ai-entry-title {{ font-size: 0.98rem; }}
    .ai-entry-desc {{ font-size: 0.78rem; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>A股 价值选股报告 · 归档</h1>
  <div class="subtitle">量化学习研究项目 · 更新于 {generated_at}</div>
</div>
<div class="container">

  <div class="card">
    <div class="intro">
      这个项目用电脑程序，从沪深300里挑出<strong>又便宜（市盈率、市净率低）又能赚钱（净资产收益率高）</strong>的公司，
      模拟「每季度调一次仓、长期持有」，看能不能跑赢大盘（沪深300指数）。
      <br>本项目<strong>2026-05-22 起方法已升级</strong>：从早期的「猜明天涨还是跌」（被验证基本等于抛硬币）
      改成了现在的「价值选股」。更早的报告是旧方法，留作历史记录。
    </div>
  </div>

  <a href="ai-analysis/" class="ai-entry-card">
    <div class="ai-entry-icon">🤖</div>
    <div class="ai-entry-body">
      <div class="ai-entry-title">AI 个股分析 <span class="ai-entry-new">NEW</span></div>
      <div class="ai-entry-desc">
        输入股票代码或中文名 → AI 调 28 个数据端点 + DeepSeek 实时分析<br>
        <span class="ai-entry-disclaimer">⚠️ 仅供学习研究，不构成任何投资建议</span>
      </div>
    </div>
    <div class="ai-entry-arrow">→</div>
  </a>

  <div class="card">
    <div class="card-title">最新一期报告（{latest_date}）</div>
    <div class="latest-info">
      <div class="latest-date">{latest_date}</div>
      {latest_badge}
    </div>
    <div>{latest_status}</div>{backtest_block}
  </div>

  <div class="card">
    <div class="card-title">历史归档（最近 30 期）</div>
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>日期</th>
          <th>方法</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
{rows_html}      </tbody>
    </table>
    </div>
  </div>

  <div class="disclaimer">
    ⚠️ 本站为量化<strong>学习 / 研究</strong>项目，所有内容仅供学习参考，<strong>不构成任何投资建议</strong>。
    回测「跑赢沪深300」是一段偏乐观的历史结果，不代表未来实盘能赚钱。
    市场有风险，投资须谨慎，请勿据此进行实盘操作。
  </div>

</div>
<div class="footer">
  A股量化价值选股学习项目 · 数据来源：AkShare &nbsp;|&nbsp; 非投资建议
</div>
</body>
</html>
"""
    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"[build_index] 已写入 {INDEX_HTML}（{len(report_files)} 条归档）")


if __name__ == "__main__":
    try:
        build_index()
    except Exception as e:
        print(f"[build_index] 错误：{e}", file=sys.stderr)
        sys.exit(1)
