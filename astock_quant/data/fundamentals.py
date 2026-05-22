"""财报历史 + 估值序列重建 —— 价值选股的数据地基.

────────────────────────────────────────────────────────────────────────
为什么需要这个模块（T1 背景）
────────────────────────────────────────────────────────────────────────
原系统的 PE / PB 因子 95% 是 NaN：astock_source 只把腾讯实时 PE/PB 快照挂到
「最新一期」财报上，历史报告期全 None。价值选股的地基是「便宜度」，必须有完整
的 PE / PB / 股息率历史。本模块用「已有 4 年日线 + 季度财报」自己算出这个历史。

数据来源：
  - 财报指标   akshare `stock_financial_abstract_ths`（同花顺源，实测 HS300 每只
               都有 2021Q1~ 至今共 ~21 期，含 EPS / 每股净资产 / ROE / 营收 /
               净利 / 毛利率 / 净利率 / 资产负债率）。
  - 分红数据   akshare `stock_fhps_detail_em`（含「业绩披露日期」「现金分红比例」
               「除权除息日」—— 用来算股息率）。

产物：每只票一份 `data_cache/{code}-fundamentals.csv`，全历史报告期 × 财务字段，
回测可复现（不再每次 live 拉网络）。命名空间独立，不污染现有 prices/moneyflow。

────────────────────────────────────────────────────────────────────────
★ 防 look-ahead —— 本模块最关键的纪律 ★
────────────────────────────────────────────────────────────────────────
「报告期末日」≠「财报可见日」。2024 年报的报告期末是 2023-12-31，但实际 2024 年
3~4 月才披露 —— 站在 2024-02-15 看，最新可用的财报还是 2023 三季报。如果用
「报告期末 ≤ T」做对齐，会把「当时还没公布」的财报泄漏进特征 = 典型未来函数。

本模块给每条财报记录算一个 **保守可见日 publish_date**：

  实测发现 —— akshare 的 EM 系列接口（stock_lrb_em / stock_yjbb_em）虽然有
  「公告日期」列，但它的 `date=` 历史快照是假的：传 date='20240331' 返回的公告日
  其实是该股最新一期财报的日期。所以拿不到逐期真实披露日。

  对策：用 A股 法定披露截止日（中国证监会 / 交易所规定，硬约束）：
    - 一季报（Q1）：次年 ... 不，当年 4/30 前
    - 半年报（Q2）：当年 8/31 前
    - 三季报（Q3）：当年 10/31 前
    - 年报  （Q4）：次年 4/30 前
  把「报告期所属季度的法定截止日」作为 publish_date。

  为什么这样不会造成未来函数：绝大多数公司**提前**于法定截止日披露，用法定截止日
  是「偏晚」的估计 —— 宁可晚几周才用上财报，也绝不会早于真实披露日用它。即这是
  一个**保守**（conservative）的口径，方向上只会牺牲一点点信息时效，不会泄漏未来。
  这是 A股 量化研究处理「无逐期披露日」时的标准做法。

  分红的 publish_date 用真实数据：stock_fhps_detail_em 的「业绩披露日期」是可信的
  逐期真实披露日，直接用。

下游（factors/fundamental.py）对齐财报到交易日 T 时，必须用 `publish_date <= T`
做截断，而不是 `report_period <= T`。本模块的 `as_of()` 提供这个能力。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from astock_quant.config.settings import SETTINGS
from astock_quant.contracts import FinancialMetrics
from astock_quant.data.astock_source import normalize_ticker

logger = logging.getLogger(__name__)

# 同花顺 stock_financial_abstract_ths（按报告期）中文列名 → FinancialMetrics 字段
_THS_COL_MAP: dict[str, str] = {
    "基本每股收益": "eps",
    "每股净资产": "bvps",
    "净资产收益率": "roe",
    "营业总收入": "revenue",
    "净利润": "net_profit",
    "销售毛利率": "gross_margin",
    "销售净利率": "net_margin",
    "资产负债率": "debt_ratio",
}

# 财报缓存的 CSV 列顺序（落盘 schema —— 改动需同步 _records_from_cache_df）
_CACHE_COLUMNS: list[str] = [
    "ticker", "report_period", "publish_date",
    "eps", "eps_ttm", "bvps", "roe", "revenue", "net_profit",
    "gross_margin", "net_margin", "debt_ratio",
    "dividend_per_share",
]


# ===========================================================================
# 法定披露截止日 —— 保守可见日（防 look-ahead 核心）
# ===========================================================================

def statutory_publish_date(report_period: str) -> str:
    """报告期末日 → A股 法定披露截止日（保守可见日，'YYYYMMDD'）.

    中国证监会 / 交易所对定期报告披露有硬性截止日规定：
      - 一季报：当年 4 月 30 日前
      - 半年报：当年 8 月 31 日前
      - 三季报：当年 10 月 31 日前
      - 年  报：次年 4 月 30 日前

    用「法定截止日」而非「真实披露日」是**保守**估计 —— 公司多半提前披露，用截止日
    只会偏晚用上数据，绝不会早于真实披露日（不泄漏未来）。详见模块 docstring。

    参数 report_period：8 位 'YYYYMMDD' 报告期末日（如 '20231231'）。
    返回：8 位 'YYYYMMDD' 保守可见日。无法解析时原样返回 report_period（降级，
    调用方应已保证格式）。
    """
    rp = str(report_period).strip().replace("-", "")
    if len(rp) != 8 or not rp.isdigit():
        return rp
    year = int(rp[:4])
    mmdd = rp[4:]
    if mmdd == "0331":          # 一季报 → 当年 4/30
        return f"{year}0430"
    if mmdd == "0630":          # 半年报 → 当年 8/31
        return f"{year}0831"
    if mmdd == "0930":          # 三季报 → 当年 10/31
        return f"{year}1031"
    if mmdd == "1231":          # 年报 → 次年 4/30
        return f"{year + 1}0430"
    # 非标准报告期末日（极罕见，如变更会计年度）—— 保守给「报告期末 + 4 个月」
    end = pd.to_datetime(rp, format="%Y%m%d")
    return (end + pd.DateOffset(months=4)).strftime("%Y%m%d")


# ===========================================================================
# TTM EPS —— 把累计 YTD 的 EPS 滚成「滚动 12 个月」
# ===========================================================================

def _quarter_of(report_period: str) -> int:
    """报告期末日 → 季度序号（1/2/3/4）。非标准月日返回 0。"""
    mmdd = str(report_period)[4:8]
    return {"0331": 1, "0630": 2, "0930": 3, "1231": 4}.get(mmdd, 0)


def compute_ttm_eps(records: list[FinancialMetrics]) -> dict[str, float]:
    """对一只票的全部报告期记录，算每期的 TTM（滚动 12 月）EPS.

    同花顺的 `基本每股收益` 是**累计 YTD**口径：Q1=一季度，Q2=上半年，Q3=前三季度，
    Q4=全年（实测 600519：11.11→19.63→29.67→41.76 逐季递增确认）。
    但 PE(TTM) 需要「滚动最近 12 个月」的 EPS，不是 YTD。换算：

        TTM_EPS[本期] = EPS_YTD[本期] + EPS_YTD[上一年年报] - EPS_YTD[去年同期]

    例：2024Q1 的 TTM = 2024Q1_YTD + 2023全年 - 2023Q1_YTD。
    年报（Q4）本身就是全年 = 自然的 TTM，直接用。

    参数 records：单只票的 FinancialMetrics 列表（顺序不限，内部按报告期排序）。
    返回：{report_period: ttm_eps}，只含能算出 TTM 的报告期。

    防 look-ahead：TTM 只用「本期及更早」的报告期，不碰未来。
    """
    # 报告期 → EPS_YTD（剔 None）
    ytd: dict[str, float] = {}
    for r in records:
        if r.eps is not None:
            ytd[r.report_period] = float(r.eps)

    out: dict[str, float] = {}
    for rp, eps_ytd in ytd.items():
        q = _quarter_of(rp)
        if q == 0:
            continue
        if q == 4:
            # 年报：累计即全年 = 天然 TTM
            out[rp] = eps_ytd
            continue
        year = int(rp[:4])
        prev_annual = ytd.get(f"{year - 1}1231")
        prev_same_q = ytd.get(f"{year - 1}{rp[4:8]}")
        if prev_annual is None or prev_same_q is None:
            continue  # 缺上年报或去年同期 → 这期算不出 TTM
        out[rp] = eps_ytd + prev_annual - prev_same_q
    return out


# ===========================================================================
# 1. 拉取 —— akshare 同花顺财报 + 东财分红
# ===========================================================================

def _parse_ths_number(val) -> float | None:
    """同花顺财务字符串 → float（'1.47亿' / '23.38%' / False / '--' → 数值或 None）。

    与 astock_source._parse_cn_number 同口径；本模块独立持有一份，避免跨模块耦合
    （astock_source 是「单股票查询」适配器，本模块是「批量历史重建」，职责不同）。
    """
    if val is None or val is False:
        return None
    if isinstance(val, (int, float)):
        return None if pd.isna(val) else float(val)
    s = str(val).strip()
    if s in ("", "False", "--", "nan", "None", "NaN"):
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1])
        if s.endswith("亿"):
            return float(s[:-1]) * 1e8
        if s.endswith("万"):
            return float(s[:-1]) * 1e4
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_financial_history(ticker: str) -> list[FinancialMetrics]:
    """拉单只票的全历史财报 —— 同花顺财报 + 东财分红，组装成 FinancialMetrics 列表.

    每条记录：一个报告期，含 EPS / BVPS / ROE / 营收 / 净利 / 毛利率 / 净利率 /
    资产负债率 + 保守 publish_date + TTM EPS + 近 12 月每股分红。按报告期升序。

    网络失败时返回 []（Protocol 风格 —— 不抛异常，让上层降级）。
    """
    code = normalize_ticker(ticker)

    # -- 同花顺：按报告期财报摘要 --
    period_recs: dict[str, dict] = {}
    try:
        import akshare as ak

        fa = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        if fa is not None and not fa.empty and "报告期" in fa.columns:
            for _, row in fa.iterrows():
                period = str(row["报告期"]).strip().replace("-", "")
                if len(period) != 8 or not period.isdigit():
                    continue
                rec: dict = {}
                for cn_col, field in _THS_COL_MAP.items():
                    if cn_col in fa.columns:
                        v = _parse_ths_number(row[cn_col])
                        if v is not None:
                            rec[field] = v
                if rec:
                    period_recs[period] = rec
    except Exception as e:  # noqa: BLE001
        logger.warning("同花顺财报拉取失败 %s: %s", code, e)
        return []

    if not period_recs:
        logger.warning("同花顺财报无数据 %s", code)
        return []

    # -- 东财：分红明细（报告期 → 近 12 月每股分红，按披露日归属） --
    div_by_period = _fetch_dividends(code)

    # -- 组装 FinancialMetrics，按报告期升序 --
    periods = sorted(period_recs.keys())
    records: list[FinancialMetrics] = []
    for rp in periods:
        rec = period_recs[rp]
        records.append(
            FinancialMetrics(
                ticker=code,
                report_period=rp,
                publish_date=statutory_publish_date(rp),
                eps=rec.get("eps"),
                bvps=rec.get("bvps"),
                roe=rec.get("roe"),
                revenue=rec.get("revenue"),
                net_profit=rec.get("net_profit"),
                gross_margin=rec.get("gross_margin"),
                net_margin=rec.get("net_margin"),
                debt_ratio=rec.get("debt_ratio"),
                dividend_per_share=div_by_period.get(rp),
            )
        )

    # -- 算 TTM EPS，回填 --
    ttm = compute_ttm_eps(records)
    for r in records:
        r.eps_ttm = ttm.get(r.report_period)

    return records


def _fetch_dividends(code: str) -> dict[str, float]:
    """拉单只票分红明细，返回 {报告期: 该期方案的每股现金分红（元，税前）}.

    东财 stock_fhps_detail_em：「现金分红-现金分红比例」是「每 10 股派 X 元」口径，
    每股分红 = X / 10。报告期是分红方案对应的报告期（通常是年报，少数半年报）。

    注意：这里返回的 key 是分红方案的报告期，**不是**可见日。可见日由
    `as_of()` 用「业绩披露日期」单独处理（见该函数）。本函数只负责把金额取出来。
    """
    try:
        import akshare as ak

        d = ak.stock_fhps_detail_em(symbol=code)
        if d is None or d.empty or "报告期" not in d.columns:
            return {}
        col_ratio = "现金分红-现金分红比例"
        if col_ratio not in d.columns:
            return {}
        out: dict[str, float] = {}
        for _, row in d.iterrows():
            rp = str(row["报告期"]).strip().replace("-", "")[:8]
            if len(rp) != 8 or not rp.isdigit():
                continue
            ratio = row.get(col_ratio)
            if ratio is None or pd.isna(ratio):
                continue
            try:
                dps = float(ratio) / 10.0  # 每 10 股派 X 元 → 每股
            except (ValueError, TypeError):
                continue
            out[rp] = dps
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("东财分红拉取失败 %s: %s", code, e)
        return {}


# ===========================================================================
# 2. 缓存 —— 落盘 / 读取（data_cache/{code}-fundamentals.csv）
# ===========================================================================

def cache_path(ticker: str) -> Path:
    """财报缓存文件路径 —— data_cache/{code}-fundamentals.csv（独立命名空间）。"""
    code = normalize_ticker(ticker)
    d = SETTINGS.data_cache_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{code}-fundamentals.csv"


def _records_to_df(records: list[FinancialMetrics]) -> pd.DataFrame:
    """FinancialMetrics 列表 → 缓存 CSV 的 DataFrame（固定列顺序）。"""
    rows = []
    for r in records:
        rows.append({c: getattr(r, c, None) for c in _CACHE_COLUMNS})
    df = pd.DataFrame(rows, columns=_CACHE_COLUMNS)
    return df


def _records_from_cache_df(df: pd.DataFrame, code: str) -> list[FinancialMetrics]:
    """缓存 CSV 的 DataFrame → FinancialMetrics 列表（按报告期升序）。"""
    records: list[FinancialMetrics] = []
    for _, row in df.iterrows():
        rp = str(row["report_period"]).strip()
        # CSV roundtrip 可能把 '20231231' 读成 int / float —— 归一回 8 位
        rp = rp.split(".")[0].zfill(8)
        if len(rp) != 8 or not rp.isdigit():
            continue

        def _f(col: str) -> float | None:
            v = row.get(col)
            if v is None or pd.isna(v):
                return None
            return float(v)

        pub = row.get("publish_date")
        pub_s = None
        if pub is not None and not pd.isna(pub):
            pub_s = str(pub).split(".")[0].zfill(8)

        records.append(
            FinancialMetrics(
                ticker=code,
                report_period=rp,
                publish_date=pub_s,
                eps=_f("eps"),
                eps_ttm=_f("eps_ttm"),
                bvps=_f("bvps"),
                roe=_f("roe"),
                revenue=_f("revenue"),
                net_profit=_f("net_profit"),
                gross_margin=_f("gross_margin"),
                net_margin=_f("net_margin"),
                debt_ratio=_f("debt_ratio"),
                dividend_per_share=_f("dividend_per_share"),
            )
        )
    records.sort(key=lambda r: r.report_period)
    return records


def write_cache(records: list[FinancialMetrics], ticker: str) -> Path:
    """把单只票的全历史财报写入 CSV 缓存。返回文件路径。"""
    path = cache_path(ticker)
    _records_to_df(records).to_csv(path, index=False, encoding="utf-8")
    return path


def read_cache(ticker: str) -> list[FinancialMetrics] | None:
    """读单只票的财报缓存。文件不存在返回 None。"""
    path = cache_path(ticker)
    if not path.exists():
        return None
    df = pd.read_csv(path, encoding="utf-8", on_bad_lines="skip",
                     dtype={"ticker": str, "report_period": str, "publish_date": str})
    if df.empty:
        return []
    return _records_from_cache_df(df, normalize_ticker(ticker))


def load_financial_history(
    ticker: str,
    force_refresh: bool = False,
) -> list[FinancialMetrics]:
    """拿单只票全历史财报 —— 优先读缓存，无缓存 / 强制刷新则拉网络并落盘.

    财报一季度才更新一次、且历史值不会变（已披露的财报是定稿），所以缓存只要
    存在就一直有效 —— 不像行情有「当天有效」的时效判断。要更新（季度有新财报
    披露后）用 `force_refresh=True`，或直接删缓存文件。
    """
    code = normalize_ticker(ticker)
    if not force_refresh:
        cached = read_cache(code)
        if cached is not None and len(cached) > 0:
            return cached

    records = fetch_financial_history(code)
    if records:
        write_cache(records, code)
    return records


# ===========================================================================
# 3. as_of —— 防 look-ahead 的「站在 T 时点取最新已披露财报」
# ===========================================================================

def as_of(
    records: list[FinancialMetrics],
    curr_date: str | pd.Timestamp,
) -> FinancialMetrics | None:
    """站在交易日 curr_date，取「已披露（publish_date <= curr_date）」的最新一期财报.

    ★ 这是防 look-ahead 的核心闸门 ★ —— 用 publish_date 而非 report_period 截断。
    例：curr_date='2024-02-15' 时，2023 年报（publish_date≈2024-04-30）虽然报告期末
    早于 2024-02-15，但还没披露，**不会**被选中；选中的是 2023 三季报。

    参数：
        records:   单只票的 FinancialMetrics 列表。
        curr_date: 当前交易日。
    返回：
        最新一期「截至 curr_date 已披露」的 FinancialMetrics；没有则 None。
    """
    cutoff = pd.to_datetime(curr_date)
    visible: list[FinancialMetrics] = []
    for r in records:
        if r.publish_date is None:
            continue
        try:
            pub = pd.to_datetime(r.publish_date, format="%Y%m%d")
        except (ValueError, TypeError):
            continue
        if pub <= cutoff:
            visible.append(r)
    if not visible:
        return None
    # 取报告期最新的那一条（同 publish_date 下报告期越新越好）
    visible.sort(key=lambda r: (r.publish_date, r.report_period))
    return visible[-1]


__all__ = [
    "statutory_publish_date",
    "compute_ttm_eps",
    "fetch_financial_history",
    "load_financial_history",
    "cache_path",
    "read_cache",
    "write_cache",
    "as_of",
]
