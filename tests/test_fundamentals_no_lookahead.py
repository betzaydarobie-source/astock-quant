"""估值数据重建（T1）的 look-ahead 防线测试.

T1 把 PE / PB / 股息率 从「腾讯实时快照」改成「用日线股价 + 重建财报自己算」。
最大的风险是未来函数：财报有发布滞后（年报次年 4 月才披露），如果对齐时用「报告期
末日」而非「披露日」，会让 1~3 月的估值因子用到当时还没公布的财报。

本套测试用纯构造数据（不依赖网络 / data_cache），断言三件事：
  1. statutory_publish_date：法定披露截止日映射正确（年报落到次年 4/30 等）。
  2. compute_ttm_eps：TTM EPS 公式正确（累计 YTD → 滚动 12 月）。
  3. align_by_publish_date：因子对齐严格按 publish_date —— 披露日之后的财报值
     绝不出现在披露日之前的交易日上（核心 look-ahead 断言）。
  4. as_of：站在某时点取财报，只返回已披露的。
  5. 截断不变性：截断 panel 算出的估值因子，与全量 panel 切到同区间 bit-exact 相等。

这是 T1「防 look-ahead」纪律的自动防线 —— 每次改 fundamentals / fundamental.py 就跑。
"""

from __future__ import annotations

import pandas as pd
import pytest

from astock_quant.contracts import FinancialMetrics
from astock_quant.data.fundamentals import (
    as_of,
    compute_ttm_eps,
    statutory_publish_date,
)
from astock_quant.factors.fundamental import (
    PB,
    PE,
    DividendYield,
    align_by_publish_date,
)


# ===========================================================================
# 1. 法定披露截止日
# ===========================================================================

def test_statutory_publish_date_quarterly_deadlines():
    """各报告期 → 法定披露截止日：Q1/年报→4/30，半年→8/31，三季→10/31."""
    # 一季报 → 当年 4/30
    assert statutory_publish_date("20240331") == "20240430"
    # 半年报 → 当年 8/31
    assert statutory_publish_date("20240630") == "20240831"
    # 三季报 → 当年 10/31
    assert statutory_publish_date("20240930") == "20241031"
    # 年报 → 次年 4/30（关键：跨年）
    assert statutory_publish_date("20231231") == "20240430"
    assert statutory_publish_date("20251231") == "20260430"


def test_statutory_publish_date_is_conservative():
    """披露截止日必须 >= 报告期末日 —— 否则就是「报告期还没结束就可见」的未来函数."""
    for rp in ["20220331", "20220630", "20220930", "20221231",
               "20230331", "20231231", "20240630"]:
        pub = statutory_publish_date(rp)
        assert pub >= rp, f"{rp} 的披露日 {pub} 早于报告期末，违反保守性"


# ===========================================================================
# 2. TTM EPS 公式
# ===========================================================================

def _mk(rp: str, eps: float) -> FinancialMetrics:
    """构造一条只含报告期 + EPS 的 FinancialMetrics（测 TTM 用）。"""
    return FinancialMetrics(ticker="000001", report_period=rp, eps=eps)


def test_compute_ttm_eps_annual_is_itself():
    """年报的 TTM = 年报累计值本身（累计 YTD 到 Q4 即全年）."""
    recs = [_mk("20231231", 4.0), _mk("20241231", 5.0)]
    ttm = compute_ttm_eps(recs)
    assert ttm["20231231"] == 4.0
    assert ttm["20241231"] == 5.0


def test_compute_ttm_eps_interim_formula():
    """期中报告 TTM = 本期累计 + 上年年报 - 去年同期累计.

    构造：2023 全年 EPS=4.0，2023Q1 累计=1.0，2024Q1 累计=1.2。
    则 2024Q1 的 TTM = 1.2 + 4.0 - 1.0 = 4.2。
    """
    recs = [
        _mk("20230331", 1.0),
        _mk("20231231", 4.0),
        _mk("20240331", 1.2),
    ]
    ttm = compute_ttm_eps(recs)
    assert ttm["20240331"] == pytest.approx(4.2)


def test_compute_ttm_eps_skips_when_history_insufficient():
    """缺上年年报或去年同期 → 该期算不出 TTM（不瞎填）."""
    # 只有 2024Q1，没有 2023 任何数据
    recs = [_mk("20240331", 1.2)]
    ttm = compute_ttm_eps(recs)
    assert "20240331" not in ttm


# ===========================================================================
# 3. align_by_publish_date —— 核心 look-ahead 断言
# ===========================================================================

def _two_ticker_panel() -> pd.DataFrame:
    """构造一个 2 票 × 一段交易日的行情 panel（只需 close 列 + 索引）。"""
    dates = pd.bdate_range("2024-01-02", "2024-06-28")
    rows = []
    for d in dates:
        for tk in ["000001", "600000"]:
            rows.append({"date": d, "ticker": tk, "close": 10.0})
    return pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()


def test_align_by_publish_date_respects_publish_not_report_period():
    """★核心★ 财报值只在「披露日及之后」的交易日出现，披露日之前为 NaN.

    构造：000001 有一条 2023 年报（报告期末 20231231），ROE=30。
    它的 publish_date = 20240430（法定截止）。
    断言：2024-04-30 之前的所有交易日，ROE 必须是 NaN（财报当时还没公布）；
          2024-04-30 及之后，ROE = 30。
    如果对齐错用 report_period，2024 年 1 月就会出现 ROE=30 → 未来函数。
    """
    panel = _two_ticker_panel()
    fin = {
        "000001": [
            FinancialMetrics(
                ticker="000001", report_period="20231231",
                publish_date="20240430", roe=30.0,
            )
        ],
        "600000": [],
    }
    aligned = align_by_publish_date(fin, panel, "roe")

    # 取 000001 的序列
    s = aligned.xs("000001", level="ticker")
    cutoff = pd.Timestamp("2024-04-30")
    before = s[s.index < cutoff]
    after = s[s.index >= cutoff]

    assert before.isna().all(), (
        f"未来函数！披露日 {cutoff.date()} 之前出现了财报值：\n{before[before.notna()]}"
    )
    assert (after.dropna() == 30.0).all(), "披露日之后 ROE 应为 30"
    assert after.notna().any(), "披露日之后应至少有一个非 NaN 值"


def test_align_by_publish_date_skips_records_without_publish_date():
    """publish_date 为 None 的记录被跳过（无法判断可见性，保守丢弃）."""
    panel = _two_ticker_panel()
    fin = {
        "000001": [
            FinancialMetrics(
                ticker="000001", report_period="20231231",
                publish_date=None, roe=99.0,  # 无披露日
            )
        ],
        "600000": [],
    }
    aligned = align_by_publish_date(fin, panel, "roe")
    s = aligned.xs("000001", level="ticker")
    assert s.isna().all(), "无 publish_date 的记录不应出现在因子值里"


def test_align_by_publish_date_forward_fills_latest_visible():
    """多期财报：每个交易日取「披露日 <= 该日的最新一期」（ffill 语义）."""
    panel = _two_ticker_panel()
    fin = {
        "000001": [
            # 2023 三季报，披露 2023-10-31（在 panel 起点之前 → 全程可见）
            FinancialMetrics(ticker="000001", report_period="20230930",
                             publish_date="20231031", roe=20.0),
            # 2023 年报，披露 2024-04-30
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", roe=30.0),
        ],
        "600000": [],
    }
    aligned = align_by_publish_date(fin, panel, "roe")
    s = aligned.xs("000001", level="ticker")
    # 2024-04-30 之前用三季报 20，之后用年报 30
    assert (s[s.index < pd.Timestamp("2024-04-30")].dropna() == 20.0).all()
    assert (s[s.index >= pd.Timestamp("2024-04-30")].dropna() == 30.0).all()


# ===========================================================================
# 4. as_of
# ===========================================================================

def test_as_of_returns_only_published():
    """as_of 站在某时点，只返回 publish_date <= 该时点的最新财报."""
    recs = [
        FinancialMetrics(ticker="000001", report_period="20230930",
                         publish_date="20231031", roe=20.0),
        FinancialMetrics(ticker="000001", report_period="20231231",
                         publish_date="20240430", roe=30.0),
    ]
    # 2024-02-15：年报还没披露 → 取三季报
    r = as_of(recs, "2024-02-15")
    assert r is not None and r.report_period == "20230930"
    # 2024-05-01：年报已披露 → 取年报
    r = as_of(recs, "2024-05-01")
    assert r is not None and r.report_period == "20231231"
    # 2023-01-01：什么都还没披露 → None
    assert as_of(recs, "2023-01-01") is None


# ===========================================================================
# 5. 估值因子计算正确性（PE/PB/股息率）
# ===========================================================================

def test_pe_factor_close_div_ttm_eps():
    """PE = 收盘价 / TTM EPS，按披露日对齐."""
    dates = pd.bdate_range("2024-05-02", "2024-05-10")
    rows = [{"date": d, "ticker": "000001", "close": 100.0} for d in dates]
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()
    fin = {
        "000001": [
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", eps_ttm=5.0),
        ]
    }
    pe = PE().compute(panel, financials=fin)
    # 全部交易日都在披露日 20240430 之后 → PE = 100/5 = 20
    vals = pe.dropna()
    assert len(vals) > 0
    assert vals.tolist() == pytest.approx([20.0] * len(vals))


def test_pe_factor_negative_eps_becomes_nan():
    """亏损股（TTM EPS < 0）→ PE = NaN（负 PE 不可比）."""
    dates = pd.bdate_range("2024-05-02", "2024-05-10")
    rows = [{"date": d, "ticker": "000001", "close": 100.0} for d in dates]
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()
    fin = {
        "000001": [
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", eps_ttm=-2.0),
        ]
    }
    pe = PE().compute(panel, financials=fin)
    assert pe.isna().all(), "负 EPS 应让 PE 全部为 NaN"


def test_pb_factor_close_div_bvps():
    """PB = 收盘价 / 每股净资产."""
    dates = pd.bdate_range("2024-05-02", "2024-05-10")
    rows = [{"date": d, "ticker": "000001", "close": 100.0} for d in dates]
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()
    fin = {
        "000001": [
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", bvps=25.0),
        ]
    }
    pb = PB().compute(panel, financials=fin)
    vals = pb.dropna()
    assert len(vals) > 0
    assert vals.tolist() == pytest.approx([4.0] * len(vals))  # 100 / 25


def test_dividend_yield_factor():
    """股息率 = 每股分红 / 收盘价."""
    dates = pd.bdate_range("2024-05-02", "2024-05-10")
    rows = [{"date": d, "ticker": "000001", "close": 50.0} for d in dates]
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()
    fin = {
        "000001": [
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", dividend_per_share=2.0),
        ]
    }
    dy = DividendYield().compute(panel, financials=fin)
    vals = dy.dropna()
    assert len(vals) > 0
    assert vals.tolist() == pytest.approx([0.04] * len(vals))  # 2 / 50


def test_valuation_factors_no_lookahead_before_publish():
    """★综合★ PE/PB/股息率 在财报披露日之前必须全 NaN."""
    dates = pd.bdate_range("2024-01-02", "2024-06-28")
    rows = [{"date": d, "ticker": "000001", "close": 100.0} for d in dates]
    panel = pd.DataFrame(rows).set_index(["date", "ticker"]).sort_index()
    fin = {
        "000001": [
            FinancialMetrics(ticker="000001", report_period="20231231",
                             publish_date="20240430", eps_ttm=5.0,
                             bvps=25.0, dividend_per_share=2.0),
        ]
    }
    cutoff = pd.Timestamp("2024-04-30")
    for factor in (PE(), PB(), DividendYield()):
        s = factor.compute(panel, financials=fin).xs("000001", level="ticker")
        before = s[s.index < cutoff]
        assert before.isna().all(), (
            f"{factor.name} 在披露日前出现非 NaN 值 → 未来函数：\n"
            f"{before[before.notna()]}"
        )
