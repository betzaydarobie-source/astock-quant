"""沪深300 指数基准数据 —— T4 季度调仓回测的对照系.

价值选股策略要诚实回答的问题不是「赚了多少」，而是「比躺平买沪深300指数多赚多少」。
多赚的部分（超额收益 / alpha）才是「选股」这件事的真实价值。所以回测必须有一条
**真·沪深300指数**收益曲线做对照。

────────────────────────────────────────────────────────────────────────
为什么是「真指数」而不是「成分股等权合成」
────────────────────────────────────────────────────────────────────────
项目早期脚本（scripts/realistic_backtest.py）用「今日沪深300成分股等权持有」
合成基准，因为当时 akshare 的指数接口反爬坏了。但成分股等权 ≠ 沪深300指数本身
（指数是市值加权），而且「今日成分」自带幸存者偏差。

本模块直接拉**真·沪深300指数**（代码 000300）的日线收盘价。数据源优先级：
  1. 东方财富 push2his HTTP 端点（secid=1.000300）—— 实测稳定，无反爬
  2. mootdx 的 index() 接口（TCP 通达信）—— 兜底

两个源都拉不到时返回 None —— **绝不用任何东西伪造一条基准曲线**。回测层拿到
None 时如实标注「基准缺失，无法计算超额收益」。这是项目诚信红线。

────────────────────────────────────────────────────────────────────────
look-ahead 说明
────────────────────────────────────────────────────────────────────────
指数收盘价是「当日盘后即公开」的公共信息，不存在 look-ahead 风险 —— 回测在
交易日 T 用 index_close[T] 做对照与策略在 T 用 stock_close[T] mark-to-market
完全同口径。基准曲线只用于「事后对照」，不进入任何选股 / 下单决策，因此也不
经过 truncate_by_date（它不是某只票的「特征」）。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from astock_quant.config.settings import SETTINGS

logger = logging.getLogger(__name__)

# 沪深300 指数代码（A股 指数代码，非个股）
CSI300_CODE = "000300"

# 缓存文件 —— 放在 data_cache/ 下，与个股行情缓存同级（已 gitignore）
_CACHE_FILENAME = f"{CSI300_CODE}-index.csv"

# 东财 push2his K 线端点。secid 前缀 1 = 上交所（沪深300 指数挂在上交所）。
# klt=101 日线，fqt=0 不复权（指数无除权，复权与否一致）。
_EM_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid=1.{code}"
    "&fields1=f1,f2,f3,f4,f5"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    "&klt=101&fqt=0&beg={beg}&end={end}"
)


# ===========================================================================
# 缓存
# ===========================================================================

def _cache_path() -> Path:
    d = SETTINGS.data_cache_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / _CACHE_FILENAME


def _is_fresh(path: Path) -> bool:
    """缓存是否「今天更新过」—— 与 data/cache.py 同款日频有效性判断."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return mtime.date() == datetime.now().date()


def _read_cache_in_range(
    path: Path, start_date: str, end_date: str
) -> pd.DataFrame | None:
    """读缓存 CSV 并按 [start_date, end_date] 过滤 —— 不管缓存新旧.

    被「今日缓存命中」和「过期缓存兜底」两条路径共用。缓存文件不存在 / 损坏 /
    过滤后为空都返回 None（让调用方决定下一步）。
    """
    if not path.exists():
        return None
    try:
        cached = pd.read_csv(path, encoding="utf-8")
        cached["date"] = pd.to_datetime(cached["date"])
        start_ts, end_ts = pd.to_datetime(start_date), pd.to_datetime(end_date)
        mask = (cached["date"] >= start_ts) & (cached["date"] <= end_ts)
        return cached[mask].sort_values("date").reset_index(drop=True)
    except Exception as e:  # noqa: BLE001 —— 缓存损坏当作未命中
        logger.warning("沪深300 指数缓存读取失败: %s", e)
        return None


# ===========================================================================
# 数据源 1：东方财富 push2his HTTP
# ===========================================================================

def _fetch_from_eastmoney(start_date: str, end_date: str) -> pd.DataFrame | None:
    """从东财 push2his 拉沪深300 指数日线.

    返回 DataFrame[date, close]（升序），失败返回 None。
    东财 klines 每行是逗号分隔字符串：date,open,close,high,low,volume,amount,amplitude。
    我们只取 date 和 close。
    """
    beg = pd.to_datetime(start_date).strftime("%Y%m%d")
    end = pd.to_datetime(end_date).strftime("%Y%m%d")
    url = _EM_KLINE_URL.format(code=CSI300_CODE, beg=beg, end=end)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
        payload = json.loads(raw)
        klines = (payload.get("data") or {}).get("klines") or []
        if not klines:
            logger.warning("东财 push2his 返回空 klines（沪深300）")
            return None
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            # parts[0]=date, parts[2]=close（parts[1]=open）
            rows.append({"date": parts[0], "close": float(parts[2])})
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:  # noqa: BLE001 —— 数据源失败不抛，让上层走兜底
        logger.warning("东财 push2his 拉沪深300 指数失败: %s", e)
        return None


# ===========================================================================
# 数据源 2：mootdx index（兜底）
# ===========================================================================

def _fetch_from_mootdx(start_date: str, end_date: str) -> pd.DataFrame | None:
    """从 mootdx 拉沪深300 指数日线（兜底源）.

    mootdx 的 index() 接口走 TCP 通达信，单次最多 ~800 根；这里翻 3 页足够覆盖
    任意 Stage 区间（~3 年回测 < 800 根，单页即够，多翻是保险）。
    返回 DataFrame[date, close]（升序，按区间过滤），失败返回 None。
    """
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    try:
        from mootdx.quotes import Quotes

        client = Quotes.factory(market="std")
        frames: list[pd.DataFrame] = []
        for page in range(3):
            df = client.index(symbol=CSI300_CODE, category=4, offset=800, start=page * 800)
            if df is None or df.empty:
                break
            frames.append(df)
            if pd.to_datetime(df.index.min()) <= start_ts:
                break
        if not frames:
            logger.warning("mootdx index 返回空（沪深300）")
            return None
        raw = pd.concat(frames)
        raw = raw[~raw.index.duplicated(keep="first")].sort_index()
        out = pd.DataFrame(
            {"date": pd.to_datetime(raw.index).normalize(), "close": raw["close"].astype(float).values}
        )
        mask = (out["date"] >= start_ts) & (out["date"] <= end_ts)
        return out[mask].sort_values("date").reset_index(drop=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("mootdx 拉沪深300 指数失败: %s", e)
        return None


# ===========================================================================
# 对外主接口
# ===========================================================================

def load_csi300_index(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    force_refresh: bool = False,
) -> pd.DataFrame | None:
    """加载沪深300 指数日线收盘价 —— 回测基准的数据入口.

    取数优先级：今日缓存 → 东财 HTTP → mootdx 兜底 → 过期缓存兜底。
    全部失败（无网络 + 无任何缓存文件）时才返回 None。

    为什么有「过期缓存兜底」（这一条对 verifier 离线验证很关键）：
        data 层的缓存「今日有效」规则会让昨天写的缓存当天后失效 → 默认要重拉网络。
        但 verifier 可能在没有网络的环境跑回测验证。这时「昨天拉的真·沪深300 指数」
        仍是**真实数据**（不是伪造），用它兜底完全合规 —— 指数历史日线不会变。
        所以网络两源都失败时，回退到磁盘上已有的缓存文件（哪怕过期），而不是直接
        返回 None。只有「连一份缓存都没有」才返回 None。

    参数：
        start_date:    起始日，默认 SETTINGS.history_start。
        end_date:      结束日，默认 SETTINGS.history_end。
        force_refresh: True 则忽略缓存强制重拉（但网络失败时仍会回退过期缓存）。

    返回：
        DataFrame[date(datetime64), close(float)]，按 date 升序，已按区间过滤。
        **数据完全拿不到时返回 None** —— 调用方必须处理 None，绝不能伪造基准。
    """
    start_date = start_date or SETTINGS.history_start
    end_date = end_date or SETTINGS.history_end
    path = _cache_path()

    # —— 1. 今日缓存命中 → 直接用
    if not force_refresh and _is_fresh(path):
        sub = _read_cache_in_range(path, start_date, end_date)
        if sub is not None and not sub.empty:
            return sub

    # —— 2. 东财 HTTP（主源）
    df = _fetch_from_eastmoney(start_date, end_date)

    # —— 3. mootdx（兜底）
    if df is None or df.empty:
        logger.info("东财源不可用，改用 mootdx 兜底拉沪深300 指数...")
        df = _fetch_from_mootdx(start_date, end_date)

    # —— 4. 两个网络源都失败 → 回退到磁盘上的过期缓存（真实数据，非伪造）
    if df is None or df.empty:
        stale = _read_cache_in_range(path, start_date, end_date)
        if stale is not None and not stale.empty:
            logger.warning(
                "沪深300 指数两个网络源都失败 —— 回退使用过期缓存 %s（真实历史数据，"
                "指数日线不会变，仅可能缺最近几天）。", path,
            )
            return stale
        # —— 连缓存都没有 → 返回 None（绝不伪造）
        logger.error(
            "沪深300 指数数据两个网络源都拉不到、且无任何缓存 —— 返回 None，"
            "回测将无法计算超额收益。"
        )
        return None

    # —— 5. 落盘缓存（数据源可能返回比请求区间略宽的数据，缓存写「源给的全部」，
    #        下次按区间切；返回值则严格按 [start_date, end_date] 过滤）
    try:
        df.to_csv(path, index=False, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 —— 缓存写失败不影响本次返回
        logger.warning("沪深300 指数缓存写入失败: %s", e)

    # —— 6. 按请求区间过滤后返回（与缓存命中路径同口径，保证调用方拿到的永远是
    #        [start_date, end_date] 内的数据，不受数据源多给 / 少给影响）
    start_ts, end_ts = pd.to_datetime(start_date), pd.to_datetime(end_date)
    mask = (df["date"] >= start_ts) & (df["date"] <= end_ts)
    return df[mask].sort_values("date").reset_index(drop=True)


def csi300_daily_returns(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    force_refresh: bool = False,
) -> pd.Series | None:
    """沪深300 指数日收益率 Series —— 直接喂给 backtest/metrics 的 benchmark_returns.

    在 load_csi300_index 的收盘价上做 pct_change，丢掉首日 NaN。

    返回：
        pd.Series，index=DatetimeIndex，name="csi300_return"。
        指数数据拿不到时返回 None。
    """
    idx = load_csi300_index(start_date, end_date, force_refresh=force_refresh)
    if idx is None or idx.empty:
        return None
    s = idx.set_index("date")["close"].sort_index()
    returns = s.pct_change().dropna()
    returns.name = "csi300_return"
    return returns


__all__ = ["load_csi300_index", "csi300_daily_returns", "CSI300_CODE"]
