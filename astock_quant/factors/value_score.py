"""价值+质量综合打分 —— 透明、可解释的选股分数（T2 价值选股改造）.

────────────────────────────────────────────────────────────────────────
为什么是「透明打分」而不是机器学习
────────────────────────────────────────────────────────────────────────
本项目用 8 天反复诚实证明：「短期涨跌」用散户可得因子接近随机（准确率 ≈51%）。
价值选股不重蹈覆辙的关键之一，是**不在这一层套机器学习黑箱**：

  - 财报一年只更新 4 次，4 年只有 ~16 期 —— 样本量对 ML 是杯水车薪。
  - ML 在这么少的样本上必然过拟合「噪音」，回测漂亮、实盘崩盘。
  - 价值投资的逻辑本身是简单、可解释的：「便宜（低估值）+ 能赚钱（高质量）+
    在成长（营收/利润增长）的好公司，长期会被市场修复定价」。这套逻辑用透明
    打分就能表达，不需要、也不应该用黑箱。

所以本模块用**因子打分**：每个因子按「越大越好」定向 → 每日横截面排名 → 三维
（价值/质量/成长）合成 → 加权得综合分。每一步都看得见、可解释，能直接告诉用户
「这只票为什么入选」（价值分高？质量分高？）。

────────────────────────────────────────────────────────────────────────
★ 防 look-ahead —— 一切标准化/排名都按「每日横截面」★
────────────────────────────────────────────────────────────────────────
本项目踩过的坑（P3a）：`winsorize` / z-score 如果用「整列」（全样本）的分位/均值，
就把「整个时间轴（含未来）的分布」泄漏进了当前时点的因子值 —— 数据依赖型未来函数。

本模块所有「极端值截尾」「标准化」「排名」**必须按 date 分组**，只在「同一交易日的
横截面」内做：
    ✓ s.groupby(level="date").rank(pct=True)
    ✓ s.groupby(level="date").transform(winsorize_within_day)
    ❌ s.rank(pct=True)            # 全样本排名 = look-ahead
    ❌ (s - s.mean()) / s.std()    # 全样本标准化 = look-ahead

「某交易日 T 的横截面」只含 T 当天所有股票，不含任何其它日期 —— 按 date 分组算分位/
排名，天然只用 T 当天信息，与「未来」无关。这是本模块防 look-ahead 的根本。

注意区分：本模块产出的是**选股「分数」（特征 X）** —— 用「今天」可见的因子算「今天」
该买谁。它不是标签 y。标签（"未来一个季度实际涨了多少"）是 labels/targets.py 的
value_ranking_label，那个才允许看未来。两者别混。

────────────────────────────────────────────────────────────────────────
三个维度与所用因子
────────────────────────────────────────────────────────────────────────
  价值（便宜度）：pe（负向）、pb（负向）、dividend_yield（正向）
  质量（赚钱能力）：roe（正向）、net_margin（正向）、gross_margin（正向）、
                   debt 暂不单列（resv：资产负债率因子未进 default_factors，
                   有则可加；当前用 roe/net_margin/gross_margin 表达质量）
  成长（增长性）：revenue_growth_yoy（正向）、net_profit_growth_yoy（正向）

「负向」= 数值越小越好（PE 越低越便宜）—— 打分时取相反数再排名，统一成「分数越高
越好」。
"""

from __future__ import annotations

import logging

import pandas as pd

from astock_quant.config.settings import SETTINGS, ValueScoreConfig
from astock_quant.contracts import FactorFrame

logger = logging.getLogger(__name__)


# ===========================================================================
# 维度定义 —— 每个维度由哪些因子、各因子的方向
# ===========================================================================
# direction: +1 = 越大越好（高 ROE 好）；-1 = 越小越好（低 PE 好，打分时取负）。
# 因子名必须与 factors/registry.default_factors() 产出的列名一致。

_VALUE_FACTORS: dict[str, int] = {
    "pe": -1,              # 市盈率：越低越便宜
    "pb": -1,              # 市净率：越低越便宜
    "dividend_yield": +1,  # 股息率：越高现金回报越好
}

_QUALITY_FACTORS: dict[str, int] = {
    "roe": +1,           # 净资产收益率：越高赚钱能力越强
    "net_margin": +1,    # 净利率：越高盈利质量越好
    "gross_margin": +1,  # 毛利率：越高护城河/定价权越强
}

_GROWTH_FACTORS: dict[str, int] = {
    "revenue_growth_yoy": +1,     # 营收同比增速：越高成长性越好
    "net_profit_growth_yoy": +1,  # 净利同比增速：越高成长性越好
}

# 输出列名
COL_VALUE = "value_score"
COL_QUALITY = "quality_score"
COL_GROWTH = "growth_score"
COL_COMPOSITE = "composite_score"


# ===========================================================================
# 横截面工具 —— 全部按 date 分组（防 look-ahead 的根本）
# ===========================================================================

def _winsorize_cross_section(
    s: pd.Series,
    lower: float,
    upper: float,
) -> pd.Series:
    """按「每日横截面」分位截尾 —— 把每天里的极端值压到当天分位边界.

    ★ 必须按 date 分组 ★：分位 lo/hi 只能来自「同一交易日的横截面」，不能用整列
    （全样本）分位 —— 后者是 P3a 踩过的数据依赖型 look-ahead。

    某日横截面全 NaN / 只有 1 个值时 quantile 退化，原样返回该日（clip 无害）。
    """
    def _clip_one_day(x: pd.Series) -> pd.Series:
        valid = x.dropna()
        if len(valid) < 2:
            return x  # 单点无法定义分位，原样返回
        lo = valid.quantile(lower)
        hi = valid.quantile(upper)
        if pd.isna(lo) or pd.isna(hi) or lo > hi:
            return x
        return x.clip(lower=lo, upper=hi)

    return s.groupby(level="date", group_keys=False).transform(_clip_one_day)


def _cross_section_rank(
    s: pd.Series,
    min_count: int,
) -> pd.Series:
    """按「每日横截面」转分位排名 ∈ [0, 1] —— 当天最好的票 → 1.0，最差 → 0.0.

    ★ 必须按 date 分组 ★：排名只在「同一交易日的横截面」内做。全样本 rank 会把
    整个时间轴（含未来）的分布泄漏进当前分数。

    min_count：当日有效（非 NaN）票数少于此值 → 该日横截面排名无统计意义，
    整天置 NaN（避免「3 只票里排第 1」这种噪音分数）。

    rank 用 pct=True 归一到 [0,1]，对 NaN 保持 NaN（na_option="keep" 默认）。
    """
    def _rank_one_day(x: pd.Series) -> pd.Series:
        if x.notna().sum() < min_count:
            return pd.Series(float("nan"), index=x.index)
        return x.rank(pct=True)

    return s.groupby(level="date", group_keys=False).transform(_rank_one_day)


def _score_one_factor(
    raw: pd.Series,
    direction: int,
    cfg: ValueScoreConfig,
) -> pd.Series:
    """单因子 → 横截面分数 ∈ [0,1]（越大越好）.

    三步：① 按方向定向（负向因子取相反数，统一成「大=好」）② 每日横截面截尾去极值
    ③ 每日横截面分位排名。全程按 date 分组，无 look-ahead。
    """
    oriented = raw if direction > 0 else -raw
    winsorized = _winsorize_cross_section(oriented, cfg.winsor_lower, cfg.winsor_upper)
    return _cross_section_rank(winsorized, cfg.min_cross_section)


def _dimension_score(
    factor_data: pd.DataFrame,
    factor_dirs: dict[str, int],
    cfg: ValueScoreConfig,
    dim_name: str,
) -> pd.Series:
    """一个维度（价值/质量/成长）的分数 = 该维度各因子横截面分数的「等权平均」.

    某因子列在 factor_data 里缺失 → 跳过（打 warning），用剩下的因子算维度分。
    某 (date, ticker) 只有部分因子有值 → mean(skipna=True) 用有的那些算（部分缺失
    不致整行 NaN）。某 (date, ticker) 该维度因子全 NaN → 维度分 NaN。

    返回 pd.Series，MultiIndex=(date, ticker)，值域 [0,1]，name=dim_name。
    """
    factor_scores: list[pd.Series] = []
    for fname, direction in factor_dirs.items():
        if fname not in factor_data.columns:
            logger.warning(
                "value_score: 维度 %s 缺少因子列 '%s'，跳过该因子", dim_name, fname
            )
            continue
        factor_scores.append(_score_one_factor(factor_data[fname], direction, cfg))

    if not factor_scores:
        logger.warning("value_score: 维度 %s 一个因子都没有，维度分全 NaN", dim_name)
        return pd.Series(float("nan"), index=factor_data.index, name=dim_name)

    # 等权平均：把各因子分数拼成 DataFrame 后按行 mean（skipna，部分缺失不毁整行）
    dim_df = pd.concat(factor_scores, axis=1)
    score = dim_df.mean(axis=1, skipna=True)
    score.name = dim_name
    return score


# ===========================================================================
# 主入口 —— 综合打分
# ===========================================================================

def compute_value_scores(
    factors: FactorFrame | pd.DataFrame,
    config: ValueScoreConfig | None = None,
) -> pd.DataFrame:
    """计算价值+质量综合打分 —— T2 主入口.

    参数：
        factors:  FactorFrame（registry.compute_factor_frame 的产出）或其底层
                  DataFrame —— MultiIndex=(date, ticker)，columns 含估值/盈利/成长因子。
        config:   ValueScoreConfig；None 用 SETTINGS.value_score（默认权重 4:4:2）。

    返回：
        DataFrame，MultiIndex=(date, ticker)，4 列：
          - value_score      价值维度分 ∈ [0,1]（低 PE/PB + 高股息，越高越便宜）
          - quality_score    质量维度分 ∈ [0,1]（高 ROE/净利率/毛利率，越高越赚钱）
          - growth_score     成长维度分 ∈ [0,1]（高营收/净利增速，越高成长性越好）
          - composite_score  综合分 ∈ [0,1]（三维加权 + 按权重和归一化）

        三个分项分留着 —— 报告层用它解释「这只票为什么入选」（价值高？质量高？）。

    综合分公式：
        composite = (value×w_v + quality×w_q + growth×w_g) / (w_v + w_q + w_g)

        某 (date,ticker) 若某维度分是 NaN（该维度因子当天全缺）—— 用「可用维度」按其
        权重重新归一化算综合分，不让一个维度缺失就整票综合分 NaN。三维全 NaN 才 NaN。

    防 look-ahead：所有截尾/排名按 date 横截面（见模块 docstring）；本函数不引入任何
    跨日操作 —— 综合分只是「同一 (date,ticker) 行内」三个维度分的加权，天然无未来函数。
    """
    cfg = config or SETTINGS.value_score

    # 接受 FactorFrame 或裸 DataFrame
    factor_data = factors.data if isinstance(factors, FactorFrame) else factors
    if factor_data is None or factor_data.empty:
        logger.error("compute_value_scores: 输入因子为空，返回空 DataFrame")
        return pd.DataFrame(
            columns=[COL_VALUE, COL_QUALITY, COL_GROWTH, COL_COMPOSITE]
        )

    # 三维分项分
    value_s = _dimension_score(factor_data, _VALUE_FACTORS, cfg, COL_VALUE)
    quality_s = _dimension_score(factor_data, _QUALITY_FACTORS, cfg, COL_QUALITY)
    growth_s = _dimension_score(factor_data, _GROWTH_FACTORS, cfg, COL_GROWTH)

    # 综合分 —— 加权 + 按「可用维度」的权重和归一化
    composite_s = _weighted_composite(
        value_s, quality_s, growth_s,
        cfg.value_weight, cfg.quality_weight, cfg.growth_weight,
    )
    composite_s.name = COL_COMPOSITE

    out = pd.concat([value_s, quality_s, growth_s, composite_s], axis=1)
    out.columns = [COL_VALUE, COL_QUALITY, COL_GROWTH, COL_COMPOSITE]
    return out.sort_index()


def _weighted_composite(
    value_s: pd.Series,
    quality_s: pd.Series,
    growth_s: pd.Series,
    w_value: float,
    w_quality: float,
    w_growth: float,
) -> pd.Series:
    """三维分项分 → 综合分，按「该行可用维度」的权重和归一化.

    逐 (date,ticker) 行：把非 NaN 的维度分按其权重加权求和，再除以「这些维度权重之和」。
    例：某票成长分 NaN（成长因子当天全缺）→ 综合分 = (value×0.4 + quality×0.4) / 0.8。
    三维全 NaN → 综合分 NaN。

    这样「一个维度数据缺失」不会让整票综合分作废 —— 对 A股 财报数据不齐的现实更鲁棒。
    """
    dims = pd.concat([value_s, quality_s, growth_s], axis=1)
    weights = pd.Series(
        [w_value, w_quality, w_growth],
        index=dims.columns,
        dtype=float,
    )
    # 加权和（NaN 维度不计入）：dims × weights 后按行 sum(skipna)
    weighted = dims.mul(weights, axis=1)
    weighted_sum = weighted.sum(axis=1, skipna=True, min_count=1)
    # 每行「可用维度」的权重之和 —— mask 掉 NaN 维度后对 weights 求和
    valid_mask = dims.notna()
    weight_sum = valid_mask.mul(weights, axis=1).sum(axis=1)
    # 归一化；weight_sum 为 0（该行三维全 NaN）→ 综合分 NaN
    composite = weighted_sum.div(weight_sum.where(weight_sum > 0))
    return composite


def attach_value_scores(
    factors: FactorFrame,
    config: ValueScoreConfig | None = None,
) -> FactorFrame:
    """把 4 个打分列拼进 FactorFrame —— 便于下游统一从一个 FactorFrame 取特征.

    返回一个新的 FactorFrame（不改入参）：.data 多出 value/quality/growth/composite
    四列，.factor_names 相应追加。

    用途：让综合分能像普通因子一样进 X 矩阵 / 进报告。注意综合分本身是「因子的派生
    打分」，若同时把原始因子和综合分都喂模型会有共线性 —— 价值选股主线用 composite
    直接选股、不喂 ML（见模块 docstring），此函数主要服务报告层与回测排名。
    """
    scores = compute_value_scores(factors, config)
    merged = factors.data.join(scores, how="left")
    new_names = list(factors.factor_names) + [
        COL_VALUE, COL_QUALITY, COL_GROWTH, COL_COMPOSITE
    ]
    return FactorFrame(data=merged, factor_names=new_names)


__all__ = [
    "compute_value_scores",
    "attach_value_scores",
    "COL_VALUE",
    "COL_QUALITY",
    "COL_GROWTH",
    "COL_COMPOSITE",
]
