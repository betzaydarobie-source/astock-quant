"""批量重建财报历史缓存 —— 把 universe 全历史财报一次性拉下来落盘.

T1 产物之一。运行后 data_cache/ 下每只票多一份 {code}-fundamentals.csv，
之后回测 / 因子计算直接读缓存，可复现、不再每次打网络。

用法：
    python -m astock_quant.scripts.build_fundamentals_cache              # stage1 30 只
    python -m astock_quant.scripts.build_fundamentals_cache --stage4     # 沪深 300 全量
    python -m astock_quant.scripts.build_fundamentals_cache --force      # 强制重拉

每只票拉取 ~1-2s（同花顺财报 + 东财分红两个网络请求）。失败的票会打印出来，
不中断整体（Protocol 风格降级）。
"""

from __future__ import annotations

import argparse
import logging
import time

from astock_quant.config.settings import get_universe
from astock_quant.data import fundamentals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="批量重建财报历史缓存")
    parser.add_argument("--stage4", action="store_true", help="用沪深 300 全量股票池")
    parser.add_argument("--force", action="store_true", help="强制重拉（忽略已有缓存）")
    args = parser.parse_args()

    stage = "stage4" if args.stage4 else "stage1"
    universe = get_universe(stage)
    logger.info("重建财报缓存：stage=%s，universe=%d 只", stage, len(universe))

    ok, empty, total_periods = 0, 0, 0
    failed: list[str] = []
    t_start = time.time()

    for i, ticker in enumerate(universe, 1):
        try:
            records = fundamentals.load_financial_history(ticker, force_refresh=args.force)
            if records:
                ok += 1
                total_periods += len(records)
                logger.info("[%d/%d] %s: %d 期财报已缓存", i, len(universe), ticker, len(records))
            else:
                empty += 1
                failed.append(ticker)
                logger.warning("[%d/%d] %s: 无财报数据", i, len(universe), ticker)
        except Exception as e:  # noqa: BLE001
            empty += 1
            failed.append(ticker)
            logger.error("[%d/%d] %s: 异常 %s", i, len(universe), ticker, e)

    dt = time.time() - t_start
    logger.info("=" * 60)
    logger.info("完成：%d/%d 只成功，共 %d 期财报记录，耗时 %.1fs",
                ok, len(universe), total_periods, dt)
    if failed:
        logger.warning("失败 %d 只：%s", len(failed), failed)


if __name__ == "__main__":
    main()
