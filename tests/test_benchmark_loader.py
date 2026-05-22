"""T4 沪深300 指数基准 —— data/benchmark.py 单元测试.

聚焦本模块自己的逻辑（解析 / 缓存 / 收益率换算 / 失败兜底），**不打真实网络** ——
网络源（东财 / mootdx）用 monkeypatch 替身，保证 CI / 任何机器都能稳定跑。

诚信纪律：重点测「两个源都失败时返回 None，绝不伪造基准」这条红线。
"""

from __future__ import annotations

import pandas as pd
import pytest

from astock_quant.data import benchmark as bm


# ===========================================================================
# helpers
# ===========================================================================

def _fake_index_df(start: str = "2022-01-04", periods: int = 30) -> pd.DataFrame:
    """合成一份「指数日线」DataFrame[date, close]，模拟数据源返回."""
    dates = pd.bdate_range(start, periods=periods)
    closes = [4900.0 + i * 5 for i in range(periods)]
    return pd.DataFrame({"date": dates, "close": closes})


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """把缓存目录指到 tmp_path，避免污染真实 data_cache/ + 测试间互相干扰."""
    monkeypatch.setattr(bm, "_cache_path", lambda: tmp_path / "000300-index.csv")


# ===========================================================================
# 主路径：东财源可用
# ===========================================================================

def test_load_uses_eastmoney_when_available(monkeypatch):
    """东财源返回数据时，load_csi300_index 直接用它."""
    fake = _fake_index_df()
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: fake.copy())
    # mootdx 不应被调用 —— 设成抛错，若被调到测试就挂
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: (_ for _ in ()).throw(AssertionError("不该调 mootdx")))

    df = bm.load_csi300_index("2022-01-01", "2022-03-01")
    assert df is not None
    assert list(df.columns) == ["date", "close"]
    assert len(df) > 0
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


# ===========================================================================
# 兜底路径：东财失败 → mootdx
# ===========================================================================

def test_load_falls_back_to_mootdx(monkeypatch):
    """东财源失败（返回 None）时，自动切到 mootdx 兜底源."""
    fake = _fake_index_df()
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: None)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: fake.copy())

    df = bm.load_csi300_index("2022-01-01", "2022-03-01")
    assert df is not None
    assert len(df) > 0


# ===========================================================================
# 命门（诚信）：网络全失败 + 无缓存 → 返回 None，绝不伪造
# ===========================================================================

def test_load_returns_none_when_no_network_and_no_cache(monkeypatch):
    """命门：东财 + mootdx 都失败、且磁盘无任何缓存时，必须返回 None.

    绝不能编造一条基准曲线。回测层拿到 None 时如实标注「基准缺失」。
    （tmp_path 缓存目录是空的 —— 由 _isolate_cache fixture 保证。）
    """
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: None)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    df = bm.load_csi300_index("2022-01-01", "2022-03-01")
    assert df is None, "网络全失败且无缓存时必须返回 None，不能伪造基准数据"


# ===========================================================================
# 命门（verifier 离线验证）：网络全失败但有过期缓存 → 回退用缓存
# ===========================================================================

def test_load_falls_back_to_stale_cache_when_network_fails(monkeypatch, tmp_path):
    """命门：网络两源都失败，但磁盘有（过期）缓存时，回退用缓存而非返回 None.

    场景：verifier 在无网络环境跑回测验证。昨天拉的真·沪深300 指数缓存仍是
    真实数据（指数历史日线不会变），用它兜底完全合规。这条保证 verifier 的
    离线验证不会因「缓存过期 + 没网」而拿不到基准。
    """
    import os
    import time

    # 先写一份缓存文件，再把 mtime 改成 2 天前 → 模拟「过期缓存」
    cache_file = tmp_path / "000300-index.csv"
    _fake_index_df(periods=30).to_csv(cache_file, index=False, encoding="utf-8")
    two_days_ago = time.time() - 2 * 86400
    os.utime(cache_file, (two_days_ago, two_days_ago))
    assert not bm._is_fresh(cache_file), "测试前置：缓存应已过期"

    # 网络两源都失败
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: None)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    df = bm.load_csi300_index("2022-01-01", "2022-12-31")
    assert df is not None, "网络失败但有过期缓存时，应回退用缓存，不能返回 None"
    assert len(df) > 0
    assert list(df.columns) == ["date", "close"]


def test_daily_returns_none_when_index_unavailable(monkeypatch):
    """指数数据拿不到时，csi300_daily_returns 也返回 None（不伪造收益率）."""
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: None)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    ret = bm.csi300_daily_returns("2022-01-01", "2022-03-01")
    assert ret is None


# ===========================================================================
# 缓存：今日缓存命中直接复用
# ===========================================================================

def test_cache_hit_skips_network(monkeypatch, tmp_path):
    """缓存是今天写的 → 第二次加载直接读缓存，不打网络."""
    fake = _fake_index_df()
    call_count = {"n": 0}

    def _counting_em(s, e):
        call_count["n"] += 1
        return fake.copy()

    monkeypatch.setattr(bm, "_fetch_from_eastmoney", _counting_em)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    # 第一次：打网络 + 写缓存
    df1 = bm.load_csi300_index("2022-01-01", "2022-03-01")
    assert df1 is not None
    assert call_count["n"] == 1

    # 第二次：缓存今天刚写的 → 不再打网络
    df2 = bm.load_csi300_index("2022-01-01", "2022-03-01")
    assert df2 is not None
    assert call_count["n"] == 1, "缓存命中时不应再次调用网络源"


def test_force_refresh_bypasses_cache(monkeypatch):
    """force_refresh=True 时忽略缓存，强制重拉."""
    fake = _fake_index_df()
    call_count = {"n": 0}

    def _counting_em(s, e):
        call_count["n"] += 1
        return fake.copy()

    monkeypatch.setattr(bm, "_fetch_from_eastmoney", _counting_em)
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    bm.load_csi300_index("2022-01-01", "2022-03-01")
    bm.load_csi300_index("2022-01-01", "2022-03-01", force_refresh=True)
    assert call_count["n"] == 2, "force_refresh 应绕过缓存重新拉"


# ===========================================================================
# 收益率换算
# ===========================================================================

def test_daily_returns_is_pct_change(monkeypatch):
    """csi300_daily_returns = 收盘价的 pct_change（丢首日 NaN）."""
    fake = _fake_index_df(periods=10)
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: fake.copy())
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    ret = bm.csi300_daily_returns("2022-01-01", "2022-12-31")
    assert ret is not None
    # 收益率条数 = 收盘价条数 - 1（丢首日）
    assert len(ret) == len(fake) - 1
    assert ret.name == "csi300_return"
    # 手算第一个收益率验证
    expected_first = fake["close"].iloc[1] / fake["close"].iloc[0] - 1
    assert ret.iloc[0] == pytest.approx(expected_first)


def test_daily_returns_index_is_datetime(monkeypatch):
    """收益率 Series 的 index 是 DatetimeIndex（可直接喂 backtest metrics）."""
    fake = _fake_index_df(periods=10)
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: fake.copy())
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    ret = bm.csi300_daily_returns("2022-01-01", "2022-12-31")
    assert isinstance(ret.index, pd.DatetimeIndex)


# ===========================================================================
# 区间过滤
# ===========================================================================

def test_load_filters_by_date_range(monkeypatch):
    """返回的数据按 [start_date, end_date] 过滤."""
    fake = _fake_index_df(start="2022-01-04", periods=60)
    monkeypatch.setattr(bm, "_fetch_from_eastmoney", lambda s, e: fake.copy())
    monkeypatch.setattr(bm, "_fetch_from_mootdx", lambda s, e: None)

    # 只要前半段
    cutoff = fake["date"].iloc[20]
    df = bm.load_csi300_index("2022-01-01", cutoff.strftime("%Y-%m-%d"))
    assert df is not None
    assert df["date"].max() <= cutoff
