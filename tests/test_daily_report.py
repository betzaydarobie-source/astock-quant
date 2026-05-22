"""P12 每日预测报告测试 —— daily.py + renderer.py.

覆盖：
- _resolve_date：'today' / ISO / 非法格式
- run_daily_predict：部分失败 exit 0 / 全失败 / JSON 落盘 schema / error log / targets filter
- CLI main()：exit code 路径
- renderer.render：HTML + MD 双输出 / 命门诚信声明守门
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from astock_quant.contracts import Prediction


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_pred(ticker: str = "600519", value: float = 1.0) -> Prediction:
    return Prediction(
        ticker=ticker,
        date=pd.Timestamp("2026-05-16").date(),
        target_type="direction",
        value=value,
        score=0.62,
        proba=(0.38, 0.62),
    )


def _make_ok_result(n_preds: int = 3) -> dict:
    preds = [_make_pred(f"60{i:04d}") for i in range(n_preds)]
    return {
        "predictions": preds,
        "metrics": {"mode": "predict_only", "n_predictions": n_preds},
        "predict_model_path": "artifacts/models/direction_2026-05-16.lgb",
        "factor_names": ["ma5_close", "rsi_14"],
    }


def _make_full_results(date_str: str = "2026-05-16") -> dict:
    """renderer.render 期望的完整 results dict."""
    preds = [_make_pred("600519", 1.0), _make_pred("000858", 0.0)]
    return {
        "report_date": date_str,
        "universe_size": 2,
        "generated_at": f"{date_str}T16:32:00",
        "data_cutoff": date_str,
        "total_seconds": 1.23,
        "model_version": date_str,
        "model_paths": "direction=artifacts/models/direction_2026-05-16.lgb",
        "json_path": "artifacts/daily_reports/predictions_2026-05-16.json",
        "errors": [],
        "direction": {"predictions": preds, "metrics": {"auc": 0.5131}},
        "return_": {"predictions": preds, "metrics": {"r2": -0.002}},
        "ranking": {"predictions": preds, "metrics": {"spearman_corr": 0.01}},
        "trade_signal": {
            "predictions": [_make_pred("600519", 1.0), _make_pred("000001", -1.0)],
            "buy_predictions": [_make_pred("600519", 1.0)],
            "metrics": {"macro_f1": 0.33},
        },
        "accuracy": None,
    }


# ---------------------------------------------------------------------------
# _resolve_date
# ---------------------------------------------------------------------------

class TestResolveDate:

    def test_today_resolves_to_iso(self):
        from astock_quant.predict.daily import _resolve_date
        import datetime
        result = _resolve_date("today")
        assert result == datetime.date.today().isoformat()

    def test_none_resolves_to_today(self):
        from astock_quant.predict.daily import _resolve_date
        import datetime
        result = _resolve_date(None)
        assert result == datetime.date.today().isoformat()

    def test_iso_date_passthrough(self):
        from astock_quant.predict.daily import _resolve_date
        assert _resolve_date("2026-05-15") == "2026-05-15"

    def test_invalid_date_raises(self):
        from astock_quant.predict.daily import _resolve_date
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            _resolve_date("2026/05/15")

    def test_invalid_date_string_raises(self):
        from astock_quant.predict.daily import _resolve_date
        with pytest.raises(ValueError):
            _resolve_date("not-a-date")


# ---------------------------------------------------------------------------
# run_daily_predict — 核心流程
# ---------------------------------------------------------------------------

class TestRunDailyPredict:

    def _mock_call_target_ok(self, *args, **kwargs) -> dict:
        return _make_ok_result()

    def test_partial_failure_returns_nonempty_errors_and_some_results(self, tmp_path):
        """1 个 target fail，3 个 succeed → errors 非空，其他结果存在."""
        call_count = [0]
        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            call_count[0] += 1
            if "direction" in module_name:
                raise RuntimeError("direction pipeline 爆了")
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import run_daily_predict
            results = run_daily_predict(
                date="2026-05-16",
                universe=["600519", "000858"],
                output_dir=tmp_path,
                render_report=False,
            )

        assert len(results["errors"]) >= 1
        assert any("direction" in e for e in results["errors"])
        # 其他 3 个 target 仍有 predictions
        assert results["return_"].get("predictions") or results["ranking"].get("predictions") or \
               results["trade_signal"].get("predictions")

    def test_all_failure_errors_list_has_all_targets(self, tmp_path):
        """4 个全 fail → errors 列表含 4 条。"""
        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            raise RuntimeError(f"{func_name} 失败")

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import run_daily_predict
            results = run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                render_report=False,
            )

        assert len(results["errors"]) == 4

    def test_json_payload_schema(self, tmp_path):
        """JSON 落盘后含 report_date / universe_size / errors / results keys."""
        with patch("astock_quant.predict.daily._call_target",
                   side_effect=self._mock_call_target_ok):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519", "000858"],
                output_dir=tmp_path,
                render_report=False,
            )

        json_file = tmp_path / "predictions_2026-05-16.json"
        assert json_file.exists(), "JSON 文件未落盘"
        payload = json.loads(json_file.read_text(encoding="utf-8"))
        for key in ["report_date", "universe_size", "errors", "results"]:
            assert key in payload, f"JSON payload 缺 key: {key}"
        assert payload["report_date"] == "2026-05-16"
        assert payload["universe_size"] == 2

    def test_json_predictions_are_dicts_not_objects(self, tmp_path):
        """JSON 里 predictions 是 list[dict]，不是 Pydantic 对象（JSON 可序列化）."""
        with patch("astock_quant.predict.daily._call_target",
                   side_effect=self._mock_call_target_ok):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                render_report=False,
            )

        payload = json.loads((tmp_path / "predictions_2026-05-16.json").read_text())
        for target_name, target_data in payload["results"].items():
            preds = target_data.get("predictions", [])
            for p in preds:
                assert isinstance(p, dict), \
                    f"{target_name} predictions 里有非 dict 对象: {type(p)}"

    def test_error_log_written_on_partial_failure(self, tmp_path):
        """任何 target 失败 → error_{date}.log 生成."""
        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            if "direction" in module_name:
                raise ValueError("direction 数据拉不到")
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                render_report=False,
            )

        log_file = tmp_path / "error_2026-05-16.log"
        assert log_file.exists(), "错误日志未生成"
        content = log_file.read_text(encoding="utf-8")
        assert "direction" in content

    def test_no_error_log_when_all_succeed(self, tmp_path):
        """全部成功 → 不生成 error log。"""
        with patch("astock_quant.predict.daily._call_target",
                   side_effect=self._mock_call_target_ok):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                render_report=False,
            )

        log_file = tmp_path / "error_2026-05-16.log"
        assert not log_file.exists(), "全部成功时不应生成 error log"

    def test_targets_filter_only_calls_selected(self, tmp_path):
        """targets=['direction'] 只跑 direction，其余 3 个不调。"""
        called_targets = []

        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            called_targets.append(func_name)
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                targets=["direction"],
                render_report=False,
            )

        assert called_targets == ["run_direction"], \
            f"targets filter 未生效，实际调用: {called_targets}"

    def test_no_render_skips_html_md(self, tmp_path):
        """render_report=False 不生成 HTML/MD 文件。"""
        with patch("astock_quant.predict.daily._call_target",
                   side_effect=self._mock_call_target_ok):
            from astock_quant.predict.daily import run_daily_predict
            results = run_daily_predict(
                date="2026-05-16",
                universe=["600519"],
                output_dir=tmp_path,
                render_report=False,
            )

        assert "html_path" not in results
        assert "md_path" not in results
        html_files = list(tmp_path.glob("*.html"))
        assert len(html_files) == 0, f"--no-render 下不应有 HTML: {html_files}"


# ---------------------------------------------------------------------------
# CLI main() exit codes
# ---------------------------------------------------------------------------

class TestDailyMain:

    def test_main_partial_failure_exit_0(self, tmp_path):
        """1 个 target fail, 3 成功 → exit 0。"""
        call_count = [0]
        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            call_count[0] += 1
            if "direction" in module_name:
                raise RuntimeError("direction 失败")
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import main
            exit_code = main([
                "--date", "2026-05-16",
                "--universe", "600519,000858",
                "--output-dir", str(tmp_path),
                "--no-render",
            ])
        assert exit_code == 0

    def test_main_all_failure_exit_1(self, tmp_path):
        """4 个全 fail → exit 1。"""
        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            raise RuntimeError("全部挂了")

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import main
            exit_code = main([
                "--date", "2026-05-16",
                "--output-dir", str(tmp_path),
                "--no-render",
            ])
        assert exit_code == 1

    def test_main_invalid_target_exit_2(self, tmp_path):
        """非法 target 名 → exit 2。"""
        from astock_quant.predict.daily import main
        exit_code = main([
            "--date", "2026-05-16",
            "--output-dir", str(tmp_path),
            "--targets", "nonexistent_model",
            "--no-render",
        ])
        assert exit_code == 2

    def test_main_empty_universe_exit_2(self, tmp_path):
        """--universe 解析为空 → exit 2。"""
        from astock_quant.predict.daily import main
        exit_code = main([
            "--date", "2026-05-16",
            "--output-dir", str(tmp_path),
            "--universe", ",,,",
            "--no-render",
        ])
        assert exit_code == 2

    def test_main_universe_parsed_correctly(self, tmp_path):
        """--universe '600519,000858' 正确传给 _call_target。"""
        received_universes = []

        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            received_universes.append(list(universe))
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import main
            main([
                "--date", "2026-05-16",
                "--universe", "600519,000858",
                "--output-dir", str(tmp_path),
                "--no-render",
            ])

        assert received_universes, "mock 未被调用"
        for u in received_universes:
            assert set(u) == {"600519", "000858"}, f"universe 解析错误: {u}"

    def test_main_universe_stage4_returns_300_tickers(self, tmp_path):
        """--universe stage4 真返回 300 只 universe（smoke test）。"""
        received_universes = []

        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            received_universes.append(list(universe))
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import main
            exit_code = main([
                "--date", "2026-05-16",
                "--universe", "stage4",
                "--output-dir", str(tmp_path),
                "--no-render",
            ])

        assert exit_code == 0, f"--universe stage4 exit code 非 0: {exit_code}"
        assert received_universes, "mock 未被调用"
        # 沪深 300 成分股应 >= 200（允许数据源返回略少）
        assert len(received_universes[0]) >= 200, \
            f"stage4 universe 只有 {len(received_universes[0])} 只，期望 >= 200"

    def test_main_universe_hs300_alias_same_as_stage4(self, tmp_path):
        """--universe hs300 与 stage4 等价，返回同样大小的 universe。"""
        received_stage4 = []
        received_hs300 = []

        def mock_stage4(module_name, func_name, universe, date_str, **_kw):
            received_stage4.append(list(universe))
            return _make_ok_result()

        def mock_hs300(module_name, func_name, universe, date_str, **_kw):
            received_hs300.append(list(universe))
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_stage4):
            from astock_quant.predict.daily import main
            main(["--date", "2026-05-16", "--universe", "stage4",
                  "--output-dir", str(tmp_path), "--no-render"])

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_hs300):
            main(["--date", "2026-05-16", "--universe", "hs300",
                  "--output-dir", str(tmp_path), "--no-render"])

        assert len(received_stage4[0]) == len(received_hs300[0]), \
            f"stage4({len(received_stage4[0])}) 与 hs300({len(received_hs300[0])}) universe 大小不一致"

    def test_main_universe_stage1_returns_30_tickers(self, tmp_path):
        """--universe stage1 返回 30 只 universe。"""
        received_universes = []

        def mock_call_target(module_name, func_name, universe, date_str, **_kw):
            received_universes.append(list(universe))
            return _make_ok_result()

        with patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import main
            exit_code = main([
                "--date", "2026-05-16",
                "--universe", "stage1",
                "--output-dir", str(tmp_path),
                "--no-render",
            ])

        assert exit_code == 0
        assert len(received_universes[0]) == 30, \
            f"stage1 universe 应为 30 只，实际: {len(received_universes[0])}"


# ---------------------------------------------------------------------------
# 性能 bug 回归：prepare_stage1_data 只调 1 次
# ---------------------------------------------------------------------------

class TestDailySharedPreparedData:

    def test_daily_shares_prepared_data_across_4_pipelines(self, tmp_path):
        """命门：daily predict 全跑时 prepare_stage1_data 只调 1 次（不是 4 次）.

        HS300 时 4× 重拉 = 1200 次 akshare 请求 = 14 小时。修复后共享 1 次。
        """
        prepare_call_count = [0]

        class _FakeSource:
            def get_news(self, *a, **kw):
                return []

        fake_data = {
            "prices": object(),
            "moneyflow": None,
            "financials": {},
            "source": _FakeSource(),
        }

        def mock_prepare(universe=None, force_refresh=False):
            prepare_call_count[0] += 1
            return fake_data

        def mock_call_target(module_name, func_name, universe, date_str,
                             prepared_data=None):
            assert prepared_data is not None, \
                f"pipeline {func_name} 收到 prepared_data=None，共享未生效"
            return _make_ok_result()

        with patch("astock_quant.data.dataset.prepare_stage1_data", side_effect=mock_prepare), \
             patch("astock_quant.predict.daily._call_target", side_effect=mock_call_target):
            from astock_quant.predict.daily import run_daily_predict
            run_daily_predict(
                date="2026-05-16",
                universe=["600519", "000858"],
                output_dir=tmp_path,
                render_report=False,
            )

        assert prepare_call_count[0] <= 1, \
            f"prepare_stage1_data 被调了 {prepare_call_count[0]} 次，应该 <= 1 次"


# ---------------------------------------------------------------------------
# renderer.render — HTML + MD 双输出
# ---------------------------------------------------------------------------

class TestRenderer:

    def test_render_creates_html_and_md(self, tmp_path):
        """render() 产生 HTML 和 MD 两个文件。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        html_path, md_path = render(results, tmp_path)
        assert html_path.exists(), "HTML 文件未生成"
        assert md_path.exists(), "MD 文件未生成"

    def test_render_html_contains_report_date(self, tmp_path):
        """HTML 里含报告日期。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results("2026-05-16")
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "2026-05-16" in content

    def test_render_md_contains_report_date(self, tmp_path):
        """MD 里含报告日期。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results("2026-05-16")
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "2026-05-16" in content

    def test_render_html_is_valid_html(self, tmp_path):
        """HTML 文件含 <html> 标签（最基本结构检查）。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "<html" in content.lower() or "<!doctype" in content.lower(), \
            "HTML 文件不含 <html> 标签"

    # -----------------------------------------------------------------------
    # 命门：诚信声明守门
    # -----------------------------------------------------------------------

    def test_daily_report_html_contains_honesty_disclaimer(self, tmp_path):
        """命门：HTML 报告必须含诚信声明关键词（AUC 或 诚信声明）。防止有人删掉诚信声明。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")

        has_disclaimer = (
            "诚信声明" in content
            or "AUC" in content
            or "不构成投资" in content
            or "随机" in content
        )
        if not has_disclaimer:
            pytest.fail(
                "命门失败：HTML 报告缺少诚信声明。\n"
                "诚信声明是工程红线（AUC=0.513，接近随机猜），必须出现在报告里。\n"
                "请检查 daily_report.html.template 是否还有 诚信声明 / AUC / 不构成投资 等关键词。"
            )

    def test_daily_report_md_contains_honesty_disclaimer(self, tmp_path):
        """命门：MD 报告必须含诚信声明关键词。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")

        has_disclaimer = (
            "诚信声明" in content
            or "AUC" in content
            or "不构成投资" in content
            or "随机" in content
        )
        if not has_disclaimer:
            pytest.fail(
                "命门失败：MD 报告缺少诚信声明。\n"
                "请检查 daily_report.md.template 是否还有 诚信声明 / AUC 等关键词。"
            )

    def test_disclaimer_is_not_empty_placeholder(self, tmp_path):
        """诚信声明区域不能是空占位符（如空 div 或空 blockquote）。"""
        from astock_quant.predict.renderer import render
        # 传入真实 AUC 值，验证渲染后值出现在报告里
        results = _make_full_results()
        results["direction"]["metrics"]["auc"] = 0.5131
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        # 渲染后 $direction_auc 占位符应被替换
        assert "$direction_auc" not in content, \
            "HTML 里 $direction_auc 占位符未被渲染替换"

    def test_render_empty_predictions_does_not_crash(self, tmp_path):
        """各 target 的 predictions 为空时 render 不崩溃（错误场景下的降级）。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["direction"]["predictions"] = []
        results["return_"]["predictions"] = []
        results["ranking"]["predictions"] = []
        results["trade_signal"]["predictions"] = []
        results["trade_signal"]["buy_predictions"] = []

        html_path, md_path = render(results, tmp_path)
        assert html_path.exists()
        assert md_path.exists()

    def test_render_with_errors_shows_error_summary(self, tmp_path):
        """有错误时 HTML/MD 里含错误信息。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["errors"] = ["direction: FileNotFoundError: 找不到模型"]
        html_path, md_path = render(results, tmp_path)
        html_content = html_path.read_text(encoding="utf-8")
        md_content = md_path.read_text(encoding="utf-8")
        # 某个文件里含错误信息
        assert "FileNotFoundError" in html_content or "FileNotFoundError" in md_content


# ---------------------------------------------------------------------------
# ASCII bar helper
# ---------------------------------------------------------------------------

class TestAsciiBar:

    def test_ascii_bar_dict_input(self):
        from astock_quant.predict.renderer import make_ascii_bar
        result = make_ascii_bar({"涨": 10, "跌": 5})
        assert "涨" in result
        assert "跌" in result
        assert "│" in result

    def test_ascii_bar_empty_returns_placeholder(self):
        from astock_quant.predict.renderer import make_ascii_bar
        result = make_ascii_bar({})
        assert result == "(empty)"

    def test_ascii_bar_list_input(self):
        from astock_quant.predict.renderer import make_ascii_bar
        result = make_ascii_bar([1.0, 0.5, 0.25])
        assert "0" in result and "1" in result and "2" in result


# ---------------------------------------------------------------------------
# _build_value_picks wiring unit tests
# ---------------------------------------------------------------------------

class TestBuildValuePicks:

    def _make_scores_df(self) -> "pd.DataFrame":
        import pandas as pd
        idx = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2026-05-16"), "600519"),
                (pd.Timestamp("2026-05-16"), "000858"),
                (pd.Timestamp("2026-05-16"), "601012"),
            ],
            names=["date", "ticker"],
        )
        return pd.DataFrame(
            {
                "composite_score": [0.9, 0.7, 0.5],
                "value_score":     [0.8, 0.6, 0.4],
                "quality_score":   [0.75, 0.65, 0.3],
                "growth_score":    [0.5, 0.4, 0.6],
            },
            index=idx,
        )

    def test_returns_list_of_dicts(self):
        from astock_quant.predict.daily import _build_value_picks
        picks = _build_value_picks(self._make_scores_df(), None, "2026-05-16")
        assert isinstance(picks, list)
        assert len(picks) > 0
        assert all(isinstance(p, dict) for p in picks)

    def test_sorted_by_composite_score_desc(self):
        from astock_quant.predict.daily import _build_value_picks
        picks = _build_value_picks(self._make_scores_df(), None, "2026-05-16")
        scores = [p["composite_score"] for p in picks]
        assert scores == sorted(scores, reverse=True)

    def test_each_pick_has_required_keys(self):
        from astock_quant.predict.daily import _build_value_picks
        picks = _build_value_picks(self._make_scores_df(), None, "2026-05-16")
        for p in picks:
            for key in ("ticker", "composite_score", "reason"):
                assert key in p, f"pick missing key '{key}': {p}"

    def test_reason_string_nonempty(self):
        from astock_quant.predict.daily import _build_value_picks
        picks = _build_value_picks(self._make_scores_df(), None, "2026-05-16")
        for p in picks:
            assert isinstance(p["reason"], str) and p["reason"], \
                f"reason should be non-empty string, got: {p['reason']!r}"

    def test_empty_dataframe_returns_empty_list(self):
        import pandas as pd
        from astock_quant.predict.daily import _build_value_picks
        empty = pd.DataFrame(
            columns=["composite_score", "value_score", "quality_score", "growth_score"]
        )
        picks = _build_value_picks(empty, None, "2026-05-16")
        assert picks == []

    def test_top_n_respected(self):
        import pandas as pd
        from astock_quant.predict.daily import _build_value_picks
        idx = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2026-05-16"), f"6000{i:02d}") for i in range(30)],
            names=["date", "ticker"],
        )
        df = pd.DataFrame(
            {
                "composite_score": [i / 30 for i in range(30)],
                "value_score":     [0.5] * 30,
                "quality_score":   [0.5] * 30,
                "growth_score":    [0.5] * 30,
            },
            index=idx,
        )
        picks = _build_value_picks(df, None, "2026-05-16", top_n=5)
        assert len(picks) <= 5

    def test_future_date_slices_latest_available(self):
        from astock_quant.predict.daily import _build_value_picks
        # Asking for a date after the data — should still return picks from last available
        picks = _build_value_picks(self._make_scores_df(), None, "2026-12-31")
        assert len(picks) > 0

    def test_none_scores_df_returns_empty(self):
        from astock_quant.predict.daily import _build_value_picks
        picks = _build_value_picks(None, None, "2026-05-16")
        assert picks == []

    def test_roe_shown_as_ttm_full_year_when_financials_given(self):
        """T10：传入 financials 时，§1 ROE 显示「全年口径」TTM ROE，不是单季数.

        构造一只票：最新报告期是 2026Q1，单季累计 ROE=3.5（季报口径，会误导用户）。
        提供完整 4 季 + 上年同期记录让 TTM 算得出：
          TTM ROE(2026Q1) = 3.5 + 2025全年(15.0) - 2025Q1(3.6) = 14.9（全年量级）。
        断言 pick 里的 roe ≈ 14.9，不是 3.5。
        """
        import pandas as pd
        from astock_quant.contracts import FinancialMetrics
        from astock_quant.predict.daily import _build_value_picks

        scores = pd.DataFrame(
            {
                "composite_score": [0.9],
                "value_score": [0.8],
                "quality_score": [0.75],
                "growth_score": [0.5],
            },
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-05-16"), "601838")], names=["date", "ticker"]
            ),
        )
        # 季报口径 ROE 累计 YTD：单季 3.5、上年同期 3.6、上年全年 15.0
        recs = [
            FinancialMetrics(ticker="601838", report_period="20250331",
                             publish_date="20250430", roe=3.6),
            FinancialMetrics(ticker="601838", report_period="20251231",
                             publish_date="20260430", roe=15.0),
            FinancialMetrics(ticker="601838", report_period="20260331",
                             publish_date="20260430", roe=3.5),
        ]
        picks = _build_value_picks(
            scores, None, "2026-05-16", financials={"601838": recs}
        )
        assert len(picks) == 1
        roe = picks[0]["roe"]
        assert roe is not None
        # TTM = 3.5 + 15.0 - 3.6 = 14.9（全年量级），不是单季 3.5
        assert abs(roe - 14.9) < 1e-6, f"§1 ROE 应是全年口径 TTM 14.9，实际 {roe}"

    def test_roe_ttm_respects_publish_date(self):
        """T10：TTM ROE 展示严守披露日 —— 年报披露前不用它.

        站在 2026-03-15：2025 年报（publish_date=2026-04-30）还没披露，最新可见的是
        2025Q3。所以这天的 TTM ROE 应基于 2025Q3，而不是用上 2025 年报数据。
        """
        import pandas as pd
        from astock_quant.contracts import FinancialMetrics
        from astock_quant.predict.daily import _build_value_picks

        scores = pd.DataFrame(
            {
                "composite_score": [0.9], "value_score": [0.8],
                "quality_score": [0.75], "growth_score": [0.5],
            },
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-03-15"), "601838")], names=["date", "ticker"]
            ),
        )
        # 2025 三季报（披露 2025-10-31，3-15 前已可见）+ 2025 年报（披露 2026-04-30，未来）
        recs = [
            FinancialMetrics(ticker="601838", report_period="20240930",
                             publish_date="20241031", roe=13.0),
            FinancialMetrics(ticker="601838", report_period="20241231",
                             publish_date="20250430", roe=17.0),
            FinancialMetrics(ticker="601838", report_period="20250930",
                             publish_date="20251031", roe=12.0),
            FinancialMetrics(ticker="601838", report_period="20251231",
                             publish_date="20260430", roe=15.0),  # 未来，不应被用
        ]
        picks = _build_value_picks(
            scores, None, "2026-03-15", financials={"601838": recs}
        )
        assert len(picks) == 1
        # 站在 3-15，最新可见是 2025Q3 → TTM = 12.0 + 17.0 - 13.0 = 16.0
        # 若误用 2025 年报会得 15.0（look-ahead）
        roe = picks[0]["roe"]
        assert roe is not None
        assert abs(roe - 16.0) < 1e-6, (
            f"3-15 时 TTM ROE 应基于 2025Q3（=16.0），实际 {roe} —— "
            "若为 15.0 说明误用了未披露的 2025 年报"
        )


# ---------------------------------------------------------------------------
# 价值选股推荐名单渲染
# ---------------------------------------------------------------------------

def _make_value_picks() -> list[dict]:
    return [
        {
            "ticker": "600519",
            "composite_score": 0.85,
            "pe_percentile": 12.0,
            "pb_percentile": 8.5,
            "roe": 28.3,
            "reason": "PE 历史低位，ROE 行业第一",
        },
        {
            "ticker": "000858",
            "composite_score": 0.72,
            "pe_percentile": 25.0,
            "pb_percentile": 18.0,
            "roe": 22.1,
            "reason": "估值合理，持续盈利能力强",
        },
    ]


def _make_backtest() -> dict:
    return {
        "strategy_total_return": 0.423,
        "benchmark_total_return": 0.187,
        "excess_return": 0.236,
        "sharpe_ratio": 1.24,
        "max_drawdown": -0.158,
        "n_quarters": 8,
        "period": "2022-01-01 ~ 2024-01-01",
        "caveat": "回测不代表实盘，历史收益不预测未来。",
    }


class TestValuePicksRenderer:

    def test_value_picks_html_renders_ticker(self, tmp_path):
        """有 value_picks 时 HTML 包含股票代码。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "600519" in content

    def test_value_picks_md_renders_ticker(self, tmp_path):
        """有 value_picks 时 MD 包含股票代码。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "600519" in content

    def test_value_picks_html_shows_score(self, tmp_path):
        """HTML 里显示综合分。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "0.850" in content

    def test_value_picks_none_shows_placeholder(self, tmp_path):
        """value_picks=None 时报告显示占位提示，不崩溃。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = None
        html_path, md_path = render(results, tmp_path)
        html_content = html_path.read_text(encoding="utf-8")
        md_content = md_path.read_text(encoding="utf-8")
        assert "尚未就绪" in html_content or "尚未就绪" in md_content

    def test_value_picks_missing_key_no_crash(self, tmp_path):
        """value_picks 条目缺字段时不崩溃（容错）。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = [{"ticker": "600519"}]
        html_path, md_path = render(results, tmp_path)
        assert html_path.exists()
        assert md_path.exists()

    def test_value_picks_html_contains_reason(self, tmp_path):
        """入选理由出现在 HTML 报告里。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "ROE 行业第一" in content


class TestBacktestRenderer:

    def test_backtest_html_renders_strategy_return(self, tmp_path):
        """有 backtest 时 HTML 包含策略收益数字。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["backtest"] = _make_backtest()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "42.3%" in content

    def test_backtest_md_renders_benchmark(self, tmp_path):
        """有 backtest 时 MD 包含沪深300 基准收益。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["backtest"] = _make_backtest()
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "18.7%" in content

    def test_backtest_none_shows_placeholder(self, tmp_path):
        """backtest=None 时报告显示占位提示，不崩溃。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["backtest"] = None
        html_path, md_path = render(results, tmp_path)
        html_content = html_path.read_text(encoding="utf-8")
        md_content = md_path.read_text(encoding="utf-8")
        assert "尚未就绪" in html_content or "尚未就绪" in md_content

    def test_backtest_html_contains_caveat(self, tmp_path):
        """回测免责声明出现在 HTML 里。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["backtest"] = _make_backtest()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "回测不代表实盘" in content

    def test_backtest_excess_return_in_md(self, tmp_path):
        """超额收益出现在 MD 里。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["backtest"] = _make_backtest()
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        assert "23.6%" in content


class TestNewReportStructure:

    def test_html_has_value_section_before_experimental(self, tmp_path):
        """HTML 中价值选股 §1 出现在实验性预测 §5 之前。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        idx_value = content.find("§1")
        idx_exp = content.find("§5")
        assert idx_value != -1, "HTML 缺少 §1"
        assert idx_exp != -1, "HTML 缺少 §5"
        assert idx_value < idx_exp, "价值选股 §1 应在实验性预测 §5 之前"

    def test_md_has_value_section_before_experimental(self, tmp_path):
        """MD 中价值选股 §1 出现在实验性预测 §5 之前。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        results["value_picks"] = _make_value_picks()
        _, md_path = render(results, tmp_path)
        content = md_path.read_text(encoding="utf-8")
        idx_value = content.find("§1")
        idx_exp = content.find("§5")
        assert idx_value != -1, "MD 缺少 §1"
        assert idx_exp != -1, "MD 缺少 §5"
        assert idx_value < idx_exp, "价值选股 §1 应在实验性预测 §5 之前"

    def test_experimental_section_labeled_in_html(self, tmp_path):
        """短期预测区域有「实验性」标注。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        html_path, _ = render(results, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        assert "实验性" in content or "接近随机" in content

    def test_disclaimer_still_present_with_new_structure(self, tmp_path):
        """命门：诚信声明在新报告结构里仍存在（不构成投资建议）。"""
        from astock_quant.predict.renderer import render
        results = _make_full_results()
        html_path, md_path = render(results, tmp_path)
        html_content = html_path.read_text(encoding="utf-8")
        md_content = md_path.read_text(encoding="utf-8")
        for content, name in [(html_content, "HTML"), (md_content, "MD")]:
            has_disclaimer = (
                "诚信声明" in content
                or "不构成投资" in content
                or "AUC" in content
            )
            assert has_disclaimer, f"命门失败：{name} 新结构里缺少诚信声明"


# ---------------------------------------------------------------------------
# 集成测试：_try_build_value_picks 用 compute_factor_frame 产出非空 picks
# ---------------------------------------------------------------------------

class TestTryBuildValuePicksIntegration:
    """Guard that _try_build_value_picks produces real picks via compute_factor_frame.

    Uses real factor/value_score modules but injects minimal synthetic data so the
    test does not require network access or on-disk artifacts.
    """

    def _make_price_panel(self) -> pd.DataFrame:
        """Build a MultiIndex=(date, ticker) price panel matching compute_factor_frame's expectation."""
        import numpy as np
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        tickers = [f"60{i:04d}" for i in range(10)]
        rng = np.random.default_rng(42)
        frames = []
        for t in tickers:
            base = 10 + rng.random() * 90
            prices = base + rng.random(len(dates)).cumsum() * 0.1
            df = pd.DataFrame({
                "close": prices,
                "open": prices * 0.99,
                "high": prices * 1.01,
                "low": prices * 0.98,
                "volume": rng.integers(1_000_000, 5_000_000, len(dates)).astype(float),
                "pe": 8 + rng.random(len(dates)) * 20,
                "pb": 0.5 + rng.random(len(dates)) * 3,
                "dividend_yield": rng.random(len(dates)) * 0.05,
            }, index=dates)
            df.index.name = "date"
            df["ticker"] = t
            frames.append(df)
        panel = pd.concat(frames).reset_index().set_index(["date", "ticker"]).sort_index()
        return panel

    def _make_financials(self, tickers: list) -> dict:
        import numpy as np
        from astock_quant.contracts import FinancialMetrics
        rng = np.random.default_rng(42)
        fin = {}
        fin_dates = pd.date_range("2025-01-01", periods=4, freq="QE")
        for t in tickers:
            records = []
            for d in fin_dates:
                records.append(FinancialMetrics(
                    ticker=t,
                    report_period=d.strftime("%Y%m%d"),
                    publish_date=d.strftime("%Y%m%d"),
                    roe=0.05 + rng.random() * 0.25,
                    gross_margin=0.1 + rng.random() * 0.5,
                    net_margin=0.05 + rng.random() * 0.3,
                ))
            fin[t] = records
        return fin

    def test_try_build_value_picks_returns_nonempty_list(self):
        """§1 核心命门：_try_build_value_picks 必须返回非空 list，不得返回 None。"""
        from astock_quant.predict.daily import _try_build_value_picks

        price_panel = self._make_price_panel()
        tickers = price_panel.index.get_level_values("ticker").unique().tolist()
        financials = self._make_financials(tickers)
        prepared_data = {"prices": price_panel, "financials": financials, "moneyflow": None}

        picks = _try_build_value_picks(
            universe=tickers,
            date_str="2026-02-28",
            prepared_data=prepared_data,
        )

        assert picks is not None, "§1 命门：_try_build_value_picks 返回 None，报告会显示占位"
        assert len(picks) > 0, "§1 命门：value_picks 列表为空，报告会显示空名单"
        assert all("ticker" in p and "composite_score" in p for p in picks)

    def test_try_build_value_picks_sorted_desc(self):
        """picks 按 composite_score 降序排列。"""
        from astock_quant.predict.daily import _try_build_value_picks

        price_panel = self._make_price_panel()
        tickers = price_panel.index.get_level_values("ticker").unique().tolist()
        financials = self._make_financials(tickers)
        prepared_data = {"prices": price_panel, "financials": financials, "moneyflow": None}

        picks = _try_build_value_picks(
            universe=tickers, date_str="2026-02-28", prepared_data=prepared_data
        )
        if picks and len(picks) > 1:
            scores = [p["composite_score"] for p in picks]
            assert scores == sorted(scores, reverse=True)
