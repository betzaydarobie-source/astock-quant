"""财务因子 —— 估值 / 盈利 / 成长 / 质量.

数据来源：`data/dataset.load_financials()` → `dict[ticker, list[FinancialMetrics]]`。
财务是季度粒度、按报告期不规则发布；因子层负责把它对齐到行情 panel 的日频。

────────────────────────────────────────────────────────────────────────
★★ T1 重建：估值因子改用「自己算的历史 PE/PB/股息率」★★
────────────────────────────────────────────────────────────────────────
原系统的 PE / PB 因子直接取 FinancialMetrics.pe / .pb —— 那是腾讯实时快照、只挂
在最新一期报告上，历史 95% 全 NaN，价值选股的「便宜度」地基塌了。

T1 把估值因子改成「用日线股价 + 季度财报自己算」：
    PE(TTM) = 当日收盘价 / 每股收益(TTM)
    PB      = 当日收盘价 / 每股净资产(BVPS)
    股息率  = 近 12 月每股现金分红 / 当日收盘价
财报历史由 `data/fundamentals.py` 重建（同花顺财报 + 东财分红），覆盖率从 5%
拉到接近 100%。

────────────────────────────────────────────────────────────────────────
★★ 防 look-ahead —— 按「财报披露日」对齐，不是「报告期末日」★★
────────────────────────────────────────────────────────────────────────
原 `align_financials_to_panel` 按 `report_period`（报告期末日）排序后 ffill ——
这有未来函数：年报报告期末是 12-31，但实际次年 4 月才披露，站在 1~3 月用「报告期末
≤ T」会用到当时还没公布的财报。

T1 修正：新增 `align_by_publish_date()` —— 按 `FinancialMetrics.publish_date`
（保守可见日，见 data/fundamentals.py）对齐。某交易日 T 只用「publish_date ≤ T」
的财报。所有 T1 估值/质量因子都走这条对齐路径。

旧的 `align_financials_to_panel`（按 report_period）保留 —— 它服务于「数据源已
保证不返回未来报告期」的旧契约（Stage 1 的 ROE / 成长因子仍用它，因为那些因子
当时已知有 Q1 轻微 forward-looking 的局限、且已在报告里披露）。新代码一律用
`align_by_publish_date`。
"""

from __future__ import annotations

import logging

import pandas as pd

from astock_quant.contracts import FinancialMetrics
from astock_quant.factors.base import BaseFactor

logger = logging.getLogger(__name__)


# ===========================================================================
# 工具：把 dict[ticker, list[FinancialMetrics]] 对齐到行情 panel 的 (date, ticker)
# ===========================================================================

def align_financials_to_panel(
    financials: dict[str, list[FinancialMetrics]],
    panel: pd.DataFrame,
    field: str,
) -> pd.Series:
    """把 FinancialMetrics 的某字段 forward-fill 对齐到 panel 的 (date, ticker).

    ⚠️ 按 `report_period`（报告期末日）对齐 —— 仅用于「数据源已保证不返回未来
    报告期」的旧契约。新代码（T1 估值/质量因子）一律改用 `align_by_publish_date`，
    后者按真实/保守披露日对齐，无 Q1 forward-looking 缺陷。

    步骤（per ticker）：转 (报告期, value) Series → 按报告期升序 → reindex 到
    panel 该 ticker 的交易日 + ffill → 拼回 panel 全索引。
    """
    panel_tickers = panel.index.get_level_values("ticker").unique()
    ticker_map = _build_ticker_map(panel_tickers, list(financials.keys()))

    parts: list[pd.Series] = []
    for panel_tk in panel_tickers:
        fin_key = ticker_map.get(panel_tk)
        if fin_key is None:
            continue
        recs = financials.get(fin_key, [])
        if not recs:
            continue
        rows = [(r.report_period, getattr(r, field)) for r in recs if getattr(r, field) is not None]
        if not rows:
            continue
        dates = pd.to_datetime([p for p, _ in rows], format="%Y%m%d", errors="coerce")
        vals = [v for _, v in rows]
        report_s = pd.Series(vals, index=dates, dtype=float).sort_index()
        report_s = report_s[~report_s.index.duplicated(keep="last")]

        tk_idx = panel.xs(panel_tk, level="ticker").index
        aligned = report_s.reindex(report_s.index.union(tk_idx)).sort_index().ffill().reindex(tk_idx)
        aligned.index = pd.MultiIndex.from_product([tk_idx, [panel_tk]], names=["date", "ticker"])
        parts.append(aligned)

    if not parts:
        return pd.Series(index=panel.index, dtype=float)

    out = pd.concat(parts).sort_index()
    return out.reindex(panel.index)


def align_by_publish_date(
    financials: dict[str, list[FinancialMetrics]],
    panel: pd.DataFrame,
    field: str,
) -> pd.Series:
    """把 FinancialMetrics 的某字段对齐到 panel —— ★按披露日，防 look-ahead★.

    与 `align_financials_to_panel` 的唯一区别：用 `publish_date`（财报实际/保守
    可见日）排序后 ffill，而不是 `report_period`（报告期末日）。

    这样某交易日 T 的财务因子值只反映「截至 T 已披露」的财报 —— 年报在次年 4 月底
    才披露，1~3 月不会误用它。这是 T1 所有估值/质量因子的对齐方式。

    实现细节：
      - 同一交易日可能对应多条「已披露」财报，ffill 自然取「披露日 ≤ T 的最新一期」。
      - publish_date 为 None 的记录跳过（无法判断可见性，保守丢弃）。
      - 同一 publish_date 有多条（如年报+一季报常同日披露）→ 取报告期更新的那条。
    """
    panel_tickers = panel.index.get_level_values("ticker").unique()
    ticker_map = _build_ticker_map(panel_tickers, list(financials.keys()))

    parts: list[pd.Series] = []
    for panel_tk in panel_tickers:
        fin_key = ticker_map.get(panel_tk)
        if fin_key is None:
            continue
        recs = financials.get(fin_key, [])
        if not recs:
            continue
        # (publish_date, report_period, value) —— 剔 None 字段 / 无披露日的记录
        rows = []
        for r in recs:
            v = getattr(r, field, None)
            if v is None or r.publish_date is None:
                continue
            rows.append((r.publish_date, r.report_period, v))
        if not rows:
            continue
        # 按 (披露日, 报告期) 升序 —— 同披露日下报告期新的排后面，ffill 时胜出
        rows.sort(key=lambda x: (x[0], x[1]))
        pub_dates = pd.to_datetime([p for p, _, _ in rows], format="%Y%m%d", errors="coerce")
        vals = [v for _, _, v in rows]
        pub_s = pd.Series(vals, index=pub_dates, dtype=float)
        # 同一披露日多条 → 保留最后（已按报告期排序，最后即报告期最新）
        pub_s = pub_s[~pub_s.index.duplicated(keep="last")].sort_index()

        tk_idx = panel.xs(panel_tk, level="ticker").index
        aligned = pub_s.reindex(pub_s.index.union(tk_idx)).sort_index().ffill().reindex(tk_idx)
        aligned.index = pd.MultiIndex.from_product([tk_idx, [panel_tk]], names=["date", "ticker"])
        parts.append(aligned)

    if not parts:
        return pd.Series(index=panel.index, dtype=float)

    out = pd.concat(parts).sort_index()
    return out.reindex(panel.index)


def _build_ticker_map(panel_tickers, fin_keys: list[str]) -> dict:
    """把 panel 的 ticker（可能是 int / str）映射到 financials 的 str key.

    P2 的 CSV 缓存把 '000858' 读成 int 858（丢了前导零），这里宽松匹配：
    int 858 ↔ str '000858' / '858' / '0000858'。
    """
    out: dict = {}
    for pt in panel_tickers:
        pt_str = str(pt)
        for fk in fin_keys:
            if fk.lstrip("0") == pt_str.lstrip("0"):
                out[pt] = fk
                break
    return out


def _close_series(panel: pd.DataFrame) -> pd.Series:
    """从行情 panel 取收盘价 Series（MultiIndex=(date, ticker)）。

    估值因子（PE/PB/股息率）= 股价 / 某财务量 —— 都要当日收盘价做分子。
    """
    return panel["close"]


# ===========================================================================
# 因子基类辅助：财务因子统一从 kwargs 取 financials 字典
# ===========================================================================

class _FundamentalBase(BaseFactor):
    """财务因子共用：从 kwargs['financials'] 取 dict，调对齐函数.

    子类只需覆盖 name 和 _compute_inner（拿到对齐后的 Series 再做计算）。
    """

    def compute(self, panel: pd.DataFrame, **kwargs) -> pd.Series:
        financials = kwargs.get("financials") or {}
        if not financials:
            return pd.Series(index=panel.index, dtype=float, name=self.name)
        return self._compute_inner(panel, financials)

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        raise NotImplementedError


# ===========================================================================
# 1. 估值因子（PE / PB / 股息率）—— T1 重建：自己算历史
# ===========================================================================

class PE(_FundamentalBase):
    """PE（市盈率 TTM）= 当日收盘价 / 每股收益(TTM).

    T1 重建：原 PE 直接取腾讯快照（历史 95% NaN），现改为用日线股价 + 重建的
    财报 TTM EPS 自己算，覆盖全历史。

    口径：
      - 分母用 `eps_ttm`（滚动 12 月每股收益，由 fundamentals.compute_ttm_eps 算）。
      - 按 `publish_date` 对齐 —— 防 look-ahead（见 align_by_publish_date）。
      - 负 PE（亏损股，EPS_TTM < 0）→ NaN：负 PE 数值不可比，作为特征会污染。
    """

    @property
    def name(self) -> str:
        return "pe"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        eps_ttm = align_by_publish_date(financials, panel, "eps_ttm")
        close = _close_series(panel)
        pe = self._safe_div(close, eps_ttm)
        pe = self._replace_inf(pe)
        pe = pe.where(pe > 0)  # 负 PE（亏损股）→ NaN
        pe.name = self.name
        return pe


class PB(_FundamentalBase):
    """PB（市净率）= 当日收盘价 / 每股净资产(BVPS).

    T1 重建：同 PE，原取腾讯快照、现自己算。

    口径：
      - 分母用 `bvps`（每股净资产，同花顺财报直接给）。
      - 按 `publish_date` 对齐 —— 防 look-ahead。
      - 负 PB（资不抵债，极罕见）→ NaN。
    """

    @property
    def name(self) -> str:
        return "pb"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        bvps = align_by_publish_date(financials, panel, "bvps")
        close = _close_series(panel)
        pb = self._safe_div(close, bvps)
        pb = self._replace_inf(pb)
        pb = pb.where(pb > 0)
        pb.name = self.name
        return pb


class DividendYield(_FundamentalBase):
    """股息率 = 近 12 个月每股现金分红 / 当日收盘价.

    T1 新增因子。价值选股关注「现金回报」—— 高股息往往是「便宜且稳健」的信号。

    口径：
      - 分子用 `dividend_per_share`（近 12 月每股现金分红，元，税前；由
        fundamentals 从东财分红明细取，按报告期挂到对应财报上）。
      - 按 `publish_date` 对齐 —— 分红方案随定期报告披露，用财报披露日近似分红
        信息可见日（保守：实际分红预案公告通常与年报同期或稍晚）。
      - 分红记录稀疏（只有分红的报告期有值）—— ffill 会把上一次分红一直延续到下次，
        这正是「近 12 月股息」的意图（一年分一次的公司，分红后 12 个月内股息率有效）。
      - 无分红记录的票 → 全程 NaN（不分红 = 无股息率，不强行填 0，让下游 mask）。
    """

    @property
    def name(self) -> str:
        return "dividend_yield"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        dps = align_by_publish_date(financials, panel, "dividend_per_share")
        close = _close_series(panel)
        dy = self._safe_div(dps, close)
        dy = self._replace_inf(dy)
        # 股息率为负无意义（分红不会是负数）；为 0 视为「这期方案派 0」也保留为 0
        dy = dy.where(dy >= 0)
        dy.name = self.name
        return dy


# ===========================================================================
# 2. 盈利因子（ROE / 净利率）—— T1：改用披露日对齐
# ===========================================================================

class ROE(_FundamentalBase):
    """ROE（净资产收益率，%）—— 直接取 FinancialMetrics.roe.

    T1：对齐方式从 report_period 改为 publish_date（防 look-ahead）。
    同花顺把单位写成百分数（如 10.57 表示 10.57%），保持此口径。
    """

    @property
    def name(self) -> str:
        return "roe"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        s = align_by_publish_date(financials, panel, "roe")
        s.name = self.name
        return s


class NetMargin(_FundamentalBase):
    """净利率（销售净利率，%）—— 直接取 FinancialMetrics.net_margin.

    T1：原 NetMargin 用 net_profit / revenue 现算；现直接取同花顺给的「销售净利率」
    （口径更标准，且省一层「两字段都要非 None」的脆弱依赖）。按 publish_date 对齐。
    """

    @property
    def name(self) -> str:
        return "net_margin"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        s = align_by_publish_date(financials, panel, "net_margin")
        s.name = self.name
        return s


class GrossMargin(_FundamentalBase):
    """毛利率（销售毛利率，%）—— T1 新增质量因子.

    毛利率反映产品/护城河的定价权 —— 价值选股里「便宜的好公司」的「好」的一面。
    直接取同花顺「销售毛利率」，按 publish_date 对齐。
    """

    @property
    def name(self) -> str:
        return "gross_margin"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        s = align_by_publish_date(financials, panel, "gross_margin")
        s.name = self.name
        return s


class EPS(_FundamentalBase):
    """每股收益（TTM 口径）—— T1：改用 eps_ttm（滚动 12 月）.

    原 EPS 取累计 YTD 的 eps（Q1/Q2/Q3/Q4 不可比）；T1 改取 eps_ttm（滚动 12 月，
    跨季可比）。按 publish_date 对齐。
    """

    @property
    def name(self) -> str:
        return "eps"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        s = align_by_publish_date(financials, panel, "eps_ttm")
        s.name = self.name
        return s


# ===========================================================================
# 3. 成长因子 —— 同比增长率（T1：改用披露日对齐）
# ===========================================================================

class RevenueGrowthYoY(_FundamentalBase):
    """营收同比增速 = revenue[T] / revenue[T-4 期] - 1（4 个季度 ≈ 一年）.

    T1：对齐改用 publish_date。先在「报告期序列」上做 pct_change(4)，再按披露日对齐
    panel —— 比先对齐后 pct_change 更准（避免日频 ffill 干扰季度差分）。
    """

    @property
    def name(self) -> str:
        return "revenue_growth_yoy"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        return _yoy_growth_factor(panel, financials, "revenue", self.name, self)


class NetProfitGrowthYoY(_FundamentalBase):
    """净利润同比增速 = net_profit[T] / net_profit[T-4] - 1.

    T1：对齐改用 publish_date。净利润可能为负，pct_change 在负→正切换时符号会误导 ——
    保持原始 pct_change，由下游模型/打分自己处理这种非线性。
    """

    @property
    def name(self) -> str:
        return "net_profit_growth_yoy"

    def _compute_inner(self, panel: pd.DataFrame, financials: dict) -> pd.Series:
        return _yoy_growth_factor(panel, financials, "net_profit", self.name, self)


def _yoy_growth_factor(
    panel: pd.DataFrame,
    financials: dict,
    field: str,
    name: str,
    base: BaseFactor,
) -> pd.Series:
    """通用「同比增速」实现：对每只票按报告期 pct_change(4) 后按披露日对齐到 panel.

    T1：对齐改用 publish_date —— 季度差分仍在报告期序列上做（保证 T 与 T-4 是真正
    相邻 4 个季度），算完后用每期对应的 publish_date 对齐到交易日。
    """
    panel_tickers = panel.index.get_level_values("ticker").unique()
    ticker_map = _build_ticker_map(panel_tickers, list(financials.keys()))

    parts: list[pd.Series] = []
    for panel_tk in panel_tickers:
        fin_key = ticker_map.get(panel_tk)
        if fin_key is None:
            continue
        recs = financials.get(fin_key, [])
        # (报告期, publish_date, value) —— 同比增速要在报告期序列上算
        rows = []
        for r in recs:
            v = getattr(r, field, None)
            if v is None or r.publish_date is None:
                continue
            rows.append((r.report_period, r.publish_date, v))
        if len(rows) < 5:  # 至少 5 期才能算 yoy
            continue
        rows.sort(key=lambda x: x[0])  # 按报告期升序

        pub_dates = [pdv for _, pdv, _ in rows]
        vals = [v for _, _, v in rows]

        report_s = pd.Series(vals, dtype=float)
        yoy = report_s.pct_change(4)  # 位置 pct_change：T 期 vs T-4 期

        # 把每期的 yoy 挂到该期的 publish_date 上
        pub_ts = pd.to_datetime(pub_dates, format="%Y%m%d", errors="coerce")
        yoy_by_pub = pd.Series(yoy.values, index=pub_ts)
        yoy_by_pub = yoy_by_pub[~yoy_by_pub.index.duplicated(keep="last")].sort_index()
        yoy_by_pub = yoy_by_pub.dropna()
        if yoy_by_pub.empty:
            continue

        tk_idx = panel.xs(panel_tk, level="ticker").index
        aligned = yoy_by_pub.reindex(yoy_by_pub.index.union(tk_idx)).sort_index().ffill().reindex(tk_idx)
        aligned.index = pd.MultiIndex.from_product([tk_idx, [panel_tk]], names=["date", "ticker"])
        parts.append(aligned)

    if not parts:
        return pd.Series(index=panel.index, dtype=float, name=name)

    out = pd.concat(parts).sort_index().reindex(panel.index)
    out = base._replace_inf(out)
    out.name = name
    return out


__all__ = [
    "PE",
    "PB",
    "DividendYield",
    "ROE",
    "NetMargin",
    "GrossMargin",
    "EPS",
    "RevenueGrowthYoY",
    "NetProfitGrowthYoY",
    "align_financials_to_panel",
    "align_by_publish_date",
]
