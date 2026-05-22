"""每日价值选股报告 —— Stage 4.

入口：
    uv run python -m astock_quant.predict.daily --date today
    uv run python -m astock_quant.predict.daily --universe stage4
    uv run python -m astock_quant.predict.daily --help

设计：
- 拉一次 stage1 数据（prepare_stage1_data）→ 算因子 → compute_value_scores
  → 组装当季「价值选股推荐名单」（§1）。
- 读最新季度回测 artifact（results_*.json）→ §2 策略回测。
- 组装 renderer 期望的 schema → 喂 `predict.renderer.render()` 出 HTML + Markdown。
- 当日运行记录（value_picks + 元数据 + errors）落 `<output_dir>/predictions_{date}.json`。
- 错误日志落 `<output_dir>/error_{date}.log`。

价值选股每季度调一次仓、长期持有；报告每日刷新只为展示最新名单与回测，
不做短期涨跌预测。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from astock_quant.config.settings import SETTINGS

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("artifacts/daily_reports")


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------


def _resolve_date(date_arg: str | None) -> str:
    """解析 --date 参数 → ISO 日期字符串.

    "today" 或 None → 今天日期；否则按 YYYY-MM-DD 解析（校验格式）。
    """
    if date_arg is None or date_arg.lower() == "today":
        return _dt.date.today().isoformat()
    try:
        return _dt.date.fromisoformat(date_arg).isoformat()
    except ValueError as e:
        raise ValueError(f"--date 必须是 'today' 或 YYYY-MM-DD 格式，实际：{date_arg}") from e


def _build_value_picks(
    scores_df: "Any",
    factor_frame: "Any | None",
    date_str: str,
    top_n: int = 20,
    financials: dict | None = None,
) -> list[dict[str, Any]]:
    """Convert compute_value_scores() output → renderer value_picks list.

    Args:
        scores_df:    DataFrame with MultiIndex=(date, ticker), columns include
                      composite_score, value_score, quality_score, growth_score.
        factor_frame: Optional FactorFrame or DataFrame with raw factor values
                      (pe, pb, roe) for display; None means those columns show '-'.
        date_str:     The report date (YYYY-MM-DD) to slice the latest scores.
        top_n:        Maximum picks to include (default 20).
        financials:   Optional {ticker: list[FinancialMetrics]} —— 用来把 §1 的 ROE
                      展示值换成「全年口径」TTM ROE（T10）。None 时退回因子矩阵里的
                      最新报告期 ROE（最新是季报时会偏小，仅作降级）。

    Returns:
        list of dicts, each with keys: ticker, composite_score, value_score,
        quality_score, growth_score, pe, pb, roe, reason.
    """
    try:
        import pandas as _pd
        from astock_quant.factors.value_score import (
            COL_COMPOSITE, COL_VALUE, COL_QUALITY, COL_GROWTH,
        )
    except Exception:  # noqa: BLE001
        return []

    if scores_df is None or scores_df.empty:
        return []

    # T10：预计算每只票的「全年口径」TTM ROE —— §1 ROE 列展示用。
    # 严守披露日安全：latest_ttm_roe_as_of 内部用 publish_date 截断到 date_str。
    ttm_roe_by_ticker: dict[str, float] = {}
    if financials:
        try:
            from astock_quant.data.fundamentals import latest_ttm_roe_as_of
            for tk, recs in financials.items():
                if not recs:
                    continue
                v = latest_ttm_roe_as_of(recs, date_str)
                if v is not None:
                    ttm_roe_by_ticker[str(tk)] = v
        except Exception:  # noqa: BLE001 —— TTM ROE 算不出不应让整个 §1 崩
            ttm_roe_by_ticker = {}

    # Slice to the most recent date at or before report date
    try:
        target_date = _pd.Timestamp(date_str)
        if isinstance(scores_df.index, _pd.MultiIndex):
            dates = scores_df.index.get_level_values(0)
            available = dates[dates <= target_date]
            if available.empty:
                return []
            latest = available.max()
            day_df = scores_df.xs(latest, level=0)
        else:
            day_df = scores_df
    except Exception:  # noqa: BLE001
        day_df = scores_df

    if day_df.empty or COL_COMPOSITE not in day_df.columns:
        return []

    top = day_df.nlargest(top_n, COL_COMPOSITE)

    # Optionally join raw factor values for display
    raw: "Any | None" = None
    if factor_frame is not None:
        try:
            ff = factor_frame
            if hasattr(ff, "data"):
                ff = ff.data
            if isinstance(ff.index, _pd.MultiIndex):
                dates_ff = ff.index.get_level_values(0)
                avail_ff = dates_ff[dates_ff <= target_date]
                if not avail_ff.empty:
                    raw = ff.xs(avail_ff.max(), level=0)
            else:
                raw = ff
        except Exception:  # noqa: BLE001
            raw = None

    picks = []
    for ticker, row in top.iterrows():
        composite = float(row.get(COL_COMPOSITE, 0.0))
        val_s = float(row.get(COL_VALUE, 0.0))
        qual_s = float(row.get(COL_QUALITY, 0.0))
        growth_s = float(row.get(COL_GROWTH, 0.0))

        pe = pb = roe = None
        if raw is not None and ticker in raw.index:
            import math as _math
            raw_row = raw.loc[ticker]
            def _get_float(row: Any, key: str) -> float | None:
                v = row.get(key) if hasattr(row, "get") else None
                try:
                    f = float(v) if v is not None else None
                    return None if (f is not None and _math.isnan(f)) else f
                except (TypeError, ValueError):
                    return None
            pe = _get_float(raw_row, "pe")
            pb = _get_float(raw_row, "pb")
            roe = _get_float(raw_row, "roe")

        # T10：§1 ROE 列改为「全年口径」。raw 里的 roe 因子是「最新报告期」ROE ——
        # 最新报告是季报时它是单季累计数（如成都银行 2026Q1 roe=3.5），用户误读为
        # 全年。这里用 TTM ROE（滚动 12 月，全年量级，银行 ~12-18%）覆盖展示值。
        # 严守披露日安全：latest_ttm_roe_as_of 只取「截至 date_str 已披露」的财报。
        # 仅改展示，不动 value_score 打分用的 roe 因子（避免影响回测）。
        ttm_roe = ttm_roe_by_ticker.get(str(ticker))
        if ttm_roe is not None:
            roe = ttm_roe

        # Auto-generate entry reason from sub-scores.
        # Scores are cross-sectional ranks (0=bottom, 1=top) within today's universe,
        # NOT absolute valuation levels — use relative wording only.
        parts = []
        if val_s >= 0.7:
            parts.append("当前池子里估值相对便宜")
        elif val_s >= 0.5:
            parts.append("估值相对偏低")
        if qual_s >= 0.7:
            parts.append("盈利质量相对较强")
        elif qual_s >= 0.5:
            parts.append("盈利质量尚可")
        if growth_s >= 0.7:
            parts.append("成长性相对较好")
        if roe is not None and roe >= 15:
            parts.append(f"ROE {roe:.1f}%")
        reason = "、".join(parts) if parts else "综合分在当前池子里领先"

        picks.append({
            "ticker": str(ticker),
            "composite_score": composite,
            "value_score": val_s,
            "quality_score": qual_s,
            "growth_score": growth_s,
            "pe": pe,
            "pb": pb,
            "roe": roe,
            "reason": reason,
        })

    return picks


# ---------------------------------------------------------------------------
# Value picks wiring — graceful fallback if T2 module not yet available
# ---------------------------------------------------------------------------


def _try_build_value_picks(
    universe: list[str],
    date_str: str,
    prepared_data: dict | None,
) -> list[dict[str, Any]] | None:
    """Try to build value picks from compute_value_scores; return None on any failure.

    This function is intentionally defensive — T2 (value_score) may not be
    installed yet, or the factor data may be incomplete. None causes the
    report to show a placeholder instead of crashing.
    """
    try:
        from astock_quant.factors.registry import compute_factor_frame
        from astock_quant.factors.value_score import compute_value_scores
        from astock_quant.data.dataset import prepare_stage1_data

        factor_data = prepared_data or prepare_stage1_data(universe=universe)
        if factor_data is None or factor_data.get("prices") is None:
            return None

        factor_frame = compute_factor_frame(
            price_panel=factor_data["prices"],
            moneyflow_panel=factor_data.get("moneyflow"),
            financials=factor_data.get("financials"),
            drop_nan_threshold=1.1,
        )
        scores_df = compute_value_scores(factor_frame)
        if scores_df is None or (hasattr(scores_df, "empty") and scores_df.empty):
            return None

        return _build_value_picks(
            scores_df, factor_frame, date_str, top_n=20,
            financials=factor_data.get("financials"),
        )

    except Exception as e:  # noqa: BLE001
        logger.info("value_picks 构建跳过（T2 尚未就绪或数据不足）：%s", e)
        return None


# ---------------------------------------------------------------------------
# Backtest artifact loader — maps T4 JSON fields to renderer schema
# ---------------------------------------------------------------------------


def _load_backtest_for_report(date_str: str, output_dir: Path) -> dict[str, Any] | None:
    """Load the latest quarterly backtest artifact and map to renderer schema.

    Looks for artifacts/quarterly_backtest/results_{date}.json under output_dir's
    parent tree.  Returns None if file not found or parse fails (graceful degradation).
    """
    try:
        import json as _json

        # Walk up from output_dir to find artifacts/quarterly_backtest/
        search_roots = [output_dir, output_dir.parent, output_dir.parent.parent]
        artifact_path: Path | None = None
        for root in search_roots:
            candidate = root / "artifacts" / "quarterly_backtest" / f"results_{date_str}.json"
            if candidate.exists():
                artifact_path = candidate
                break
            # Also accept the most recent results_*.json in the directory
            bt_dir = root / "artifacts" / "quarterly_backtest"
            if bt_dir.is_dir():
                matches = sorted(bt_dir.glob("results_*.json"), reverse=True)
                if matches:
                    artifact_path = matches[0]
                    break

        if artifact_path is None:
            return None

        data = _json.loads(artifact_path.read_text(encoding="utf-8"))
        m = data.get("metrics", {})
        cfg = data.get("config", {})
        disclaimers = data.get("disclaimers", [])

        start = m.get("start_date", "")
        end = m.get("end_date", "")
        period = f"{start[:7]} ~ {end[:7]}" if start and end else ""

        caveat = disclaimers[0] if disclaimers else "回测不代表实盘，历史收益不预测未来。"

        return {
            "strategy_total_return": m.get("total_return"),
            "benchmark_total_return": m.get("benchmark_total_return"),
            "excess_return": m.get("excess_return_annualized"),
            "sharpe_ratio": m.get("sharpe"),
            "max_drawdown": m.get("max_drawdown"),
            "n_quarters": cfg.get("n_rebalances"),
            "period": period,
            "caveat": caveat,
        }
    except Exception as e:  # noqa: BLE001
        logger.info("回测 artifact 加载跳过：%s", e)
        return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_daily_predict(
    *,
    date: str | None = None,
    universe: list[str] | None = None,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    render_report: bool = True,
) -> dict[str, Any]:
    """跑每日价值选股报告：拉数据 → 算价值名单 → 读回测 → 渲染报告.

    参数：
        date:           报告日期（YYYY-MM-DD）或 "today" / None。None 走今天。
        universe:       股票池。None 走 SETTINGS.universe。
        output_dir:     报告输出目录。默认 `artifacts/daily_reports`。
        render_report:  是否调 renderer 出 HTML / Markdown。默认 True。

    返回：dict，含 value_picks / backtest / 报告元数据 / 错误 / 报告路径。
    数据不足时不抛异常，调用者可看 `errors` 字段判断。
    """
    t_total = time.time()
    date_str = _resolve_date(date)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    universe = universe or SETTINGS.universe

    logger.info("daily report: date=%s, universe=%d ticker(s)", date_str, len(universe))

    errors: list[str] = []

    # 一次性 prepare 数据（价值选股因子计算用）。失败则 prepared_data=None，
    # _try_build_value_picks 内部会再尝试拉一次（保留兜底）。
    prepared_data: dict | None = None
    try:
        from astock_quant.data.dataset import prepare_stage1_data
        t_prep = time.time()
        prepared_data = prepare_stage1_data(universe=universe)
        logger.info("prepare_stage1_data: OK (%.2fs)", time.time() - t_prep)
    except Exception as e:  # noqa: BLE001
        logger.warning("prepare_stage1_data 失败，value_picks 将再尝试拉一次：%s", e)

    # 组装 renderer 期望的 schema
    results: dict[str, Any] = {
        "report_date": date_str,
        "universe_size": len(universe),
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "data_cutoff": date_str,
        "total_seconds": round(time.time() - t_total, 2),
        "model_version": date_str,  # 简化：模型版本 = 报告日期
        "errors": errors,
        "accuracy": None,  # P14 待实装
        # 价值选股：尝试从 compute_value_scores 拉取；失败时降级 None（报告显示占位提示）
        "value_picks": _try_build_value_picks(
            universe=universe, date_str=date_str, prepared_data=prepared_data
        ),
        # 回测结果：从 T4 artifact 读取；None 时报告显示占位提示
        "backtest": _load_backtest_for_report(date_str, output_dir),
    }
    # total_seconds 在 value_picks / backtest 算完后再刷新一次，反映真实耗时
    results["total_seconds"] = round(time.time() - t_total, 2)

    # JSON 落盘 —— 仅作当日运行记录（value_picks + 元数据 + errors）。
    # 旧版本曾序列化 4 个短期预测 pipeline 供 P14 准确率追踪用；项目转价值选股后
    # 那 4 个 pipeline 已不再跑，JSON 瘦身为单纯的运行记录。
    json_path = output_dir / f"predictions_{date_str}.json"
    results["json_path"] = str(json_path)

    # 渲染报告（先渲染，再落 JSON / error log —— 让 error log 也能记录 renderer 失败）
    if render_report:
        try:
            from astock_quant.predict.renderer import render
            html_path, md_path = render(results, output_dir)
            results["html_path"] = str(html_path)
            results["md_path"] = str(md_path)
            logger.info("report → %s + %s", html_path, md_path)
        except Exception as e:  # noqa: BLE001
            msg = f"renderer: {type(e).__name__}: {e}"
            errors.append(msg)
            logger.exception("renderer FAILED")
            results["errors"] = errors

    json_payload = {
        "report_date": date_str,
        "universe_size": len(universe),
        "generated_at": results["generated_at"],
        "total_seconds": results["total_seconds"],
        "errors": errors,
        "value_picks": results["value_picks"],
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2)
    logger.info("运行记录 JSON → %s", json_path)

    # 错误日志（有错误时落盘）
    if errors:
        err_path = output_dir / f"error_{date_str}.log"
        with err_path.open("w", encoding="utf-8") as f:
            f.write(f"# daily report errors — {date_str}\n")
            f.write(f"# generated_at: {results['generated_at']}\n\n")
            for e in errors:
                f.write(f"- {e}\n")
        logger.warning("error log → %s", err_path)

    return results


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="astock_quant.predict.daily",
        description=(
            "A股 每日价值选股报告生成器 —— 算当季「价值选股推荐名单」+ 读季度回测，"
            "出 HTML + Markdown + JSON 运行记录。\n\n"
            "价值选股每季度调一次仓、长期持有；报告每日刷新只为展示最新名单与回测，"
            "不做短期涨跌预测。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--date",
        type=str,
        default="today",
        help="报告日期，'today' 或 YYYY-MM-DD（默认 today）。",
    )
    p.add_argument(
        "--universe",
        type=str,
        default=None,
        help=(
            "股票池：'stage1'（默认 30 蓝筹）/ 'stage4' 或 'hs300'（沪深 300）/ "
            "逗号分隔 ticker（如 '600519,000858'）。默认走 SETTINGS.universe（30 只蓝筹）"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"报告输出目录（默认 {DEFAULT_OUTPUT_DIR}）",
    )
    p.add_argument(
        "--no-render",
        action="store_true",
        help="跳过 HTML/Markdown 渲染，只落 JSON（debug 用）",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="只输出 WARNING 及以上",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    universe = None
    if args.universe:
        arg = args.universe.strip().lower()
        if arg in ("stage1", "stage4", "hs300", "hs300_universe"):
            from astock_quant.config.settings import get_universe
            stage_name = "stage1" if arg == "stage1" else "stage4"
            try:
                universe = get_universe(stage=stage_name)
                print(f"[INFO] --universe {arg} → {stage_name} ({len(universe)} 只)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[ERROR] 加载 {stage_name} universe 失败: {e}", file=sys.stderr)
                return 2
        else:
            universe = [t.strip() for t in args.universe.split(",") if t.strip()]
            if not universe:
                print("[ERROR] --universe 解析为空，请检查逗号分隔的代码或使用 stage1/stage4/hs300", file=sys.stderr)
                return 2

    try:
        results = run_daily_predict(
            date=args.date,
            universe=universe,
            output_dir=args.output_dir,
            render_report=not args.no_render,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] daily report crashed: {type(e).__name__}: {e}",
              file=sys.stderr)
        traceback.print_exc()
        return 1

    # 有错误（目前仅 renderer 失败会进 errors）→ exit 1
    if results.get("errors"):
        print(f"[FAIL] daily report 有 {len(results['errors'])} 个错误：", file=sys.stderr)
        for e in results["errors"]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
