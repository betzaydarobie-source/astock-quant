"""scripts/grid_direction_label.py — DirectionModel 标签网格实验（P24 第 4 步）.

direction 模型退化的根因之二：标签 `threshold=0.0` 太钝 ——「未来 5 日累计收益
> 0 即为涨」，收益在 0 附近的海量噪音样本被强行二分类，信号被淹没。
（根因之一「财务因子全 NaN」已由 P24 换同花顺数据源修复。）

本脚本网格搜 `threshold × horizon`，找让模型「不退化」的标签配置。

用法:
    uv run python scripts/grid_direction_label.py

诚信：打印全部组合（含退化的），完整结果落 artifacts/grid_direction_results.json，
绝不挑结果。全退化时显式提示需 Plan B。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from astock_quant.config.settings import get_universe
from astock_quant.pipeline.run_direction import run_direction

# 「健康」判据 —— 与 renderer.py 退化警告阈值 + direction.py 的 _degenerate 对齐
MIN_TREES = 5         # < 5 棵树 = direction.py 判定的退化
MIN_CONF_STD = 0.02   # < 0.02 = renderer.py 触发「模型严重退化警告」
MIN_AUC = 0.50        # < 0.5 等于反指标

THRESHOLDS = [0.0, 0.01, 0.02, 0.03, 0.05]
HORIZONS = [1, 5, 10, 20]


def evaluate_one(threshold: float, horizon: int) -> dict:
    """跑一个 (threshold, horizon) 组合，返回评估指标 + verdict."""
    purge = max(10, horizon)  # purge_gap 必须 >= horizon，否则 time_series_split 报错
    t0 = time.time()
    r = run_direction(
        universe=get_universe("stage1"),  # 30 只蓝筹 —— 网格阶段求快；定配置后用 300 只正式重训
        horizon=horizon,
        threshold=threshold,
        purge_gap_days=purge,
        run_backtest=False,
        save_model_to=None,
        verbose=False,
    )
    m = r["metrics"]
    num_trees = r["model"]._booster.num_trees()
    conf_std = float(r["score_frame"]["score"].std())
    auc = float(m["auc"])
    healthy = num_trees >= MIN_TREES and conf_std >= MIN_CONF_STD and auc >= MIN_AUC
    return {
        "threshold": threshold,
        "horizon": horizon,
        "auc": round(auc, 4),
        "accuracy": round(float(m["accuracy"]), 4),
        "num_trees": num_trees,
        "conf_std": round(conf_std, 4),
        "base_rate_train": round(float(m["base_rate_train"]), 4),
        "base_rate_valid": round(float(m["base_rate_valid"]), 4),
        "n_features": int(m["n_features"]),
        "elapsed_s": round(time.time() - t0, 1),
        "verdict": "HEALTHY" if healthy else "DEGENERATE",
    }


def _print_table(rows: list[dict]) -> None:
    order = {"HEALTHY": 0, "DEGENERATE": 1, "ERROR": 2}
    rows = sorted(rows, key=lambda r: (order.get(r["verdict"], 9), -r.get("auc", 0)))
    print(f"\n{'thr':>5} {'h':>3} {'auc':>8} {'trees':>6} {'conf_std':>9} "
          f"{'base_v':>8} {'verdict':>11}")
    print("-" * 60)
    for r in rows:
        if r["verdict"] == "ERROR":
            print(f"{r['threshold']:>5} {r['horizon']:>3}   ERROR  {r.get('error', '')[:36]}")
            continue
        print(f"{r['threshold']:>5} {r['horizon']:>3} {r['auc']:>8.4f} "
              f"{r['num_trees']:>6} {r['conf_std']:>9.4f} "
              f"{r['base_rate_valid']:>8.4f} {r['verdict']:>11}")
    healthy = [r for r in rows if r["verdict"] == "HEALTHY"]
    print()
    if healthy:
        best = healthy[0]
        print(f"[推荐] threshold={best['threshold']} horizon={best['horizon']} "
              f"→ AUC={best['auc']}, trees={best['num_trees']}, conf_std={best['conf_std']}")
        print("[下一步] 用此配置在 300 只 HS300 上正式重训 + 固化默认值")
    else:
        print("[结论] 全部配置退化 —— 调标签无法单独救活 direction。")
        print("[Plan B] 换数据源补回资金流因子 / 路径型标签 / 暂停 direction。")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    rows: list[dict] = []
    total = len(THRESHOLDS) * len(HORIZONS)
    i = 0
    for thr in THRESHOLDS:
        for h in HORIZONS:
            i += 1
            print(f"[{i}/{total}] threshold={thr} horizon={h} ...", flush=True)
            try:
                rows.append(evaluate_one(thr, h))
            except Exception as e:  # noqa: BLE001 —— 单组合失败不拖垮整张表
                rows.append({
                    "threshold": thr, "horizon": h,
                    "verdict": "ERROR", "error": repr(e)[:200],
                })

    out_path = Path("artifacts/grid_direction_results.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print_table(rows)
    print(f"\n完整结果（含全部组合）→ {out_path}")


if __name__ == "__main__":
    main()
