"""标签生成 —— 把「未来 N 日表现」打成 4 类预测目标的训练 label.

4 类目标共用同一套 data → factors 上游，只在这里（labels）和 models 这步按 target_type 分派。

- direction   ① 涨跌方向（Stage 1 做透）：未来 N 日累计收益 > 阈值 → 1，否则 0（二分类）
- return      ② 收益率/价格（扩展点 stub）：未来 N 日收益率 / 价格（回归 target）
- ranking     ③ 选股排序（扩展点 stub）：横截面上未来 N 日收益的排序 / 分位
- trade_signal ④ 买卖信号（扩展点 stub）：基于未来路径的买卖点标注

────────────────────────────────────────────────────────────────────────
look-ahead 注意（重要）
────────────────────────────────────────────────────────────────────────
标签的本质就是「未来 N 日的真实表现」—— **训练阶段允许**用历史已发生的「未来」作为答案
（这正是监督学习要的 y）。但有两个红线：

1. 训练 → 验证集的时序边界：训练样本的「未来 N 日窗口」不能伸进验证集的「过去」。
   这是 models/splits.py 的 purge gap 守住，不在本模块的职责内。
2. **推理（for_training=False）**：当前 T 之后的真实价格还没发生，T 之后 N-1 天的样本
   不能算 label。本模块的 `direction_label(..., for_training=False)` 把这部分 label 强制
   置 NaN，让下游清晰看到「这些样本不可训练 / 不可评估」。

具体到 direction 的实现：用 `close.groupby('ticker').pct_change(horizon).shift(-horizon)`
拿到「以 T 为起点、未来 horizon 日的累计收益率」。**这个 shift(-horizon) 是本模块唯一允许
出现的负数 shift** —— 因为它的输出语义就是「T 时刻的标签 = T+horizon 时刻的相对涨幅」。
因子层绝不允许出现此类 forward-looking 操作（详见 factors/base.py 的纪律说明）。

参数（horizon / threshold）来自 config.settings.LabelConfig。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as _date

import numpy as np
import pandas as pd

from astock_quant.config.settings import SETTINGS
from astock_quant.contracts import Label, TargetType


# ===========================================================================
# ① direction —— Stage 1 做透
# ===========================================================================

def direction_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    threshold: float | None = None,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """① 涨跌方向二分类标签 —— Stage 1 做透.

    标签定义（与 dev-docs/P1-架构设计.md 3.5 节一致）：
        未来 `horizon` 个交易日的累计收益率 > `threshold` → 1（涨），否则 0（跌/平）。
        累计收益率 = close[T + horizon] / close[T] - 1。

    参数：
        price_panel:    行情 panel，MultiIndex=(date, ticker)，含 close_col 列。
                        若 panel 已经过 cache.truncate_by_date 截断（curr_date=T），
                        最后 horizon 个交易日的标签自然不可知 —— 见 for_training。
        horizon:        预测未来 N 个交易日，默认走 SETTINGS.label.horizon（5）。
        threshold:      二分类阈值，默认走 SETTINGS.label.direction_threshold（0.0，
                        即单纯涨跌）。设成 0.02 之类可以过滤小幅震荡的噪音样本。
        for_training:   - True（默认，训练用）：尾部 horizon 行 label 为 NaN（因为算不
                          出真实未来），但 panel 中段允许「看未来」—— 这是监督学习的
                          自然语义。
                        - False（推理用）：当前 T 之后的真实价格未知，label 一律 NaN。
                          实际效果与 for_training=True 一致（pct_change(N).shift(-N)
                          的尾部 N 行本来就是 NaN），但语义上明示「这些样本不能算 y」。
        close_col:      收盘价列名，默认 "close"。

    返回：
        pd.Series，MultiIndex=(date, ticker)，name="direction_label"。
        值域：{0.0, 1.0, NaN}。NaN 在 (a) 数据起始处无法回溯 (b) 尾部 horizon 行
        未来未知 两种情况下出现。

    NaN 的下游处理：训练前 dropna 把 NaN 样本扔掉；如果 label NaN 但 X 有值，是
    无意义样本。
    """
    horizon = horizon or SETTINGS.label.horizon
    threshold = threshold if threshold is not None else SETTINGS.label.direction_threshold

    if price_panel is None or price_panel.empty:
        return pd.Series(dtype=float, name="direction_label")
    if close_col not in price_panel.columns:
        raise ValueError(f"price_panel 缺少 close 列 '{close_col}'，可用列: {list(price_panel.columns)}")

    # 累计收益率：close[T+N]/close[T]-1。groupby ticker 防止跨股票串数据。
    # 实现：每只票 pct_change(N) 算的是「相对 N 期前」的收益率 = close[T]/close[T-N]-1，
    # 我们要的是「相对 N 期后」 = close[T+N]/close[T]-1，所以再 shift(-N) 把 T+N 时刻
    # 的值搬到 T 行。整条链路只在本模块出现「负数 shift」，符合标签层的语义。
    #
    # Stage 2 prep M3 修复：把 groupby.apply 换成 groupby.transform —— apply 走 Python 循环、
    # transform 走 Cython 路径，30 票 × 4 年 panel 上后者快 5-10x。两者在「输出形状与输入一致」
    # 的场景下功能等价，pct_change + shift 链对 transform 透明（实测 bit-exact 一致）。
    # 顺带消掉 pandas 2.x 对 apply 的 FutureWarning（"DataFrameGroupBy.apply operated on the
    # grouping columns"）。
    future_ret = (
        price_panel[close_col]
        .groupby(level="ticker", group_keys=False)
        .transform(lambda s: s.pct_change(horizon).shift(-horizon))
    )

    label = (future_ret > threshold).astype(float)
    # future_ret 为 NaN（起始处、尾部 horizon 行）→ label 也为 NaN，不强行算成 0
    label = label.where(future_ret.notna(), other=np.nan)
    label.name = "direction_label"

    # for_training=False 是推理语义；尾部 NaN 本来就已经存在（shift(-N) 的自然结果），
    # 这里不再额外裁掉中段——中段的「看未来」是训练阶段的合法用法。该红线由 splits 守住。
    return label


# ===========================================================================
# Label 契约转换 —— 让上游模型 / 测试可以拿到 list[Label]
# ===========================================================================

def series_to_labels(
    label_series: pd.Series,
    target_type: TargetType = "direction",
) -> list[Label]:
    """把 MultiIndex=(date, ticker) 的 label Series 转成 list[Label] 契约对象.

    跳过 NaN 样本 —— 它们不构成有效标签。
    """
    if label_series is None or label_series.empty:
        return []
    out: list[Label] = []
    for (dt, ticker), val in label_series.items():
        if pd.isna(val):
            continue
        out.append(
            Label(
                ticker=str(ticker),
                date=_normalize_date(dt),
                target_type=target_type,
                value=float(val),
            )
        )
    return out


def _normalize_date(dt) -> _date:
    """pandas Timestamp / np.datetime64 / str → datetime.date."""
    if isinstance(dt, _date) and not isinstance(dt, pd.Timestamp):
        return dt
    return pd.Timestamp(dt).date()


# ===========================================================================
# X / y 对齐 —— FactorFrame.data 与 label series 按 (date, ticker) 内连接
# ===========================================================================

def align_xy(
    factor_data: pd.DataFrame,
    label_series: pd.Series,
    *,
    factor_names: Iterable[str] | None = None,
    drop_label_nan: bool = True,
    drop_all_nan_rows: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """把因子矩阵和标签按 (date, ticker) 内连接成训练用的 X / y.

    参数：
        factor_data:        FactorFrame.data，MultiIndex=(date, ticker)，columns=因子名
        label_series:       direction_label() 等的输出
        factor_names:       要保留的因子列子集（默认全部）
        drop_label_nan:     去掉 label 为 NaN 的行（默认 True；训练 / 评估都该去）
        drop_all_nan_rows:  去掉所有特征列都为 NaN 的行（默认 True；这种行 LightGBM
                            也只会学到空，浪费）；单列 NaN 由 LightGBM 原生处理

    返回：(X DataFrame, y Series)，索引一致。
    """
    if factor_data is None or factor_data.empty:
        empty_X = pd.DataFrame(index=label_series.index)
        return empty_X, label_series.iloc[0:0]
    if factor_names is not None:
        cols = [c for c in factor_names if c in factor_data.columns]
        factor_data = factor_data[cols]

    # 顺序确定性（P5 reviewer H2 → 收尾 polish 修复）：
    # 老实现 `factor_data.index.intersection(label_series.index)` 在 MultiIndex 上
    # 不保证保留原顺序 —— pandas 版本切换会让 (date, ticker) 行序漂移，破坏下游
    # time_series_split / predict 的索引对齐。改用 `reindex` 严格按 factor_data
    # 的索引顺序拉齐 label，无 label 的行 reindex 会填 NaN，由后面 drop_label_nan 收掉。
    # 因子 panel 已 sort_index，所以最终 (X, y) 索引顺序 = factor_data 的 (date, ticker) 升序，
    # 可复现、与训练时一致。
    X = factor_data
    y = label_series.reindex(factor_data.index)

    if drop_label_nan:
        mask = y.notna()
        X, y = X.loc[mask], y.loc[mask]
    if drop_all_nan_rows and not X.empty:
        mask = ~X.isna().all(axis=1)
        X, y = X.loc[mask], y.loc[mask]
    return X, y


# ===========================================================================
# ②③④ 扩展点 stub —— 类骨架 + docstring，不写实现
# ===========================================================================

def return_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """② 未来 horizon 日累计收益率 —— 回归 target（P9 实装）.

    标签定义（与 direction_label 共用底层 shift 链，不二值化）：
        累计收益率 = close[T + horizon] / close[T] - 1。
        每个 (date, ticker) 对应一个 float —— 直接喂 LightGBM regressor。

    参数：
        price_panel:    行情 panel，MultiIndex=(date, ticker)，含 close_col 列。
        horizon:        预测未来 N 个交易日，默认走 SETTINGS.label.horizon（5）。
        for_training:   - True（默认，训练用）：尾部 horizon 行 label 为 NaN（算不出真实未来），
                          中段允许「看未来」—— 监督学习的自然语义。
                        - False（推理用）：当前 T 之后的真实价格未知，label 一律 NaN。
                          实际效果与 for_training=True 一致（shift(-N) 尾部 N 行自然 NaN），
                          但语义上明示「这些样本不能算 y」。
        close_col:      收盘价列名，默认 "close"。

    返回：
        pd.Series，MultiIndex=(date, ticker)，name="return_label"。
        值域：float ∪ {NaN}。NaN 在 (a) 数据起始处无法回溯 (b) 尾部 horizon 行未来
        未知 两种情况下出现。

    与 direction_label 的关系（命门不变量）：
        对同一份 panel + 同一 horizon，`return_label > 0` 必然等价于
        `direction_label(threshold=0) == 1`（不考虑 NaN）。
        测试 `test_return_label_consistent_with_direction` 守住这条数学恒等式。

    look-ahead 防线（与 direction 一致，三道闸全部对齐）：
        1. 数据层：上游传入的 panel 若经 `cache.truncate_by_date(curr_date=T)` 截断，
           未来 horizon 天的 close 不可见 → 尾部自然 NaN
        2. 切分层：`models/splits.py` purge gap 守住「训练样本的 N 日未来不能伸进验证集」
        3. 训练前：`align_xy(drop_label_nan=True)` 把 NaN 样本扔掉
    """
    horizon = horizon or SETTINGS.label.horizon

    if price_panel is None or price_panel.empty:
        return pd.Series(dtype=float, name="return_label")
    if close_col not in price_panel.columns:
        raise ValueError(
            f"price_panel 缺少 close 列 '{close_col}'，可用列: {list(price_panel.columns)}"
        )

    # 累计收益率：close[T+N]/close[T]-1。groupby ticker 防止跨股票串数据。
    # transform 路径（与 direction_label M3 修复同款，bit-exact 等价于 apply 但快 5-10x）。
    # 这个 shift(-horizon) 是 labels 层允许的唯一负数 shift —— 因为输出语义就是
    # 「T 时刻的标签 = T+horizon 时刻的相对涨幅」。
    future_ret = (
        price_panel[close_col]
        .groupby(level="ticker", group_keys=False)
        .transform(lambda s: s.pct_change(horizon).shift(-horizon))
    )

    future_ret.name = "return_label"

    # for_training=False 是推理语义；尾部 NaN 本来就已经存在（shift(-N) 的自然结果），
    # 这里不再额外裁掉中段——中段的「看未来」是训练阶段的合法用法。该红线由 splits 守住。
    return future_ret


def ranking_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """③ 横截面分位排序标签 —— P10 实装.

    标签定义（两步）：
        step 1：每个 (date, ticker) 的未来 horizon 日累计收益率 = close[T+h]/close[T]-1
                **复用 return_label 的 shift 链**，不重复实现，确保数学一致。
        step 2：**按日期 groupby 做横截面 rank**（pct=True，归一化到 [0, 1]）
                同一日横截面分数最高的票 → 1.0；最低 → 0.0；中位 → 0.5。
                rank 内部对 NaN 自动跳过（NaN 维持 NaN）。

    返回 pd.Series，MultiIndex=(date, ticker)，name="ranking_label"，值域 [0.0, 1.0] ∪ {NaN}。

    ────────────────────────────────────────────────────────────────────────
    CRITICAL look-ahead 命门（Stage 3 设计 §5.1）—— 横截面 rank 高风险区
    ────────────────────────────────────────────────────────────────────────
    本函数的 **step 2 必须用 `groupby(level="date").rank(pct=True)`**，**绝不**允许：

        ❌ future_ret.rank(pct=True)             # 全样本 rank，等于用整个时间轴的涨跌幅分布做分位
        ❌ future_ret.groupby(level="ticker")...  # 按 ticker rank，跨日比较没有横截面意义

    全样本 rank 会让 T 时刻的标签隐含「整个历史 + 未来分布」的信息 —— 这正是 P3a winsorize
    bug 的同款 look-ahead 模式（用未来数据的统计量算当前分位）。

    winsorize / 标准化同款纪律（如未来要加 z-score 标签）：
        ✓ per_date_zscore = groupby(level="date").transform(lambda x: (x - x.mean()) / x.std())
        ❌ zscore = (future_ret - future_ret.mean()) / future_ret.std()

    本函数当前不做 winsorize / 标准化，只做横截面 pct rank。但未来扩展时，作者必须看完
    这段警告再加代码。

    ────────────────────────────────────────────────────────────────────────
    三道防 look-ahead（与 direction_label / return_label 对齐）
    ────────────────────────────────────────────────────────────────────────
    1. 数据层：上游 panel 经 `cache.truncate_by_date` 截断时，未来 close 不可见 → 尾部自然 NaN
    2. 切分层：`models/splits.py` purge gap 守住「训练样本的 N 日未来不能伸进验证集」。
       注意：splits 按日期切，同一日所有 ticker 要么全在 train、要么全在 valid（不会跨集，
       group-aware 默认安全；§5.4 命门测试守门由 factor-integrator 在 P10 启动前补）。
    3. 训练前：`align_xy(drop_label_nan=True)` 把 NaN 样本扔掉。

    参数：
        price_panel:    行情 panel，MultiIndex=(date, ticker)，含 close_col 列。
        horizon:        预测未来 N 个交易日，默认走 SETTINGS.label.horizon（5）。
        for_training:   与 return_label 同款语义。本函数当前两种模式行为一致（尾部 NaN
                        来自 shift 的自然结果）。
        close_col:      收盘价列名，默认 "close"。

    返回：
        pd.Series，MultiIndex=(date, ticker)，name="ranking_label"。
        值域：[0.0, 1.0] ∪ {NaN}。NaN 在：
            (a) 数据起始处 pct_change 无法回溯
            (b) 尾部 horizon 行未来未知
            (c) 当日横截面只有 1 只票（rank 退化）—— 实际场景不会出现，但 rank 内部安全处理
        三种情况下出现。

    与 return_label 的关系（命门不变量）：
        对同一份 panel + 同一 horizon，**同一日内**，return_label 越大 → ranking_label 越大。
        测试 `test_ranking_label_consistent_with_return_label` 守住这条单调关系（按日期分组）。
    """
    horizon = horizon or SETTINGS.label.horizon

    if price_panel is None or price_panel.empty:
        return pd.Series(dtype=float, name="ranking_label")
    if close_col not in price_panel.columns:
        raise ValueError(
            f"price_panel 缺少 close 列 '{close_col}'，可用列: {list(price_panel.columns)}"
        )

    # step 1：复用 return_label 的 shift 链 —— 确保数学定义一致
    # （如果 return_label 改了 shift 实现，ranking_label 自动跟上）
    future_ret = return_label(
        price_panel,
        horizon=horizon,
        for_training=for_training,
        close_col=close_col,
    )

    # step 2：横截面 rank —— **必须** groupby(level="date")（命门）
    # pct=True：归一化到 [0, 1]，便于跨日比较模型表现（不同日 universe 数量可能不同）
    # rank 默认对 NaN 保持 NaN（na_option="keep"），与 future_ret 的 NaN 位置对齐
    ranks = future_ret.groupby(level="date", group_keys=False).rank(pct=True)
    ranks.name = "ranking_label"
    return ranks


def value_ranking_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """③-价值 季度级横截面相对收益排名标签 —— T3 价值选股改造.

    与 `ranking_label` 的关系（关键）：
        本函数就是 `ranking_label`，**唯一区别是默认 horizon = 一个季度（~60 交易日）**
        而不是短窗的 5 日。底层完全复用 `ranking_label` → `return_label` 的 shift +
        groupby(date).rank 链路，**不重写任何计算逻辑**，因此：
          - 横截面 rank 命门（必须 groupby(date)，绝不全样本 rank）自动继承
          - 三道防 look-ahead（数据截断 / splits purge gap / align_xy dropna）自动继承
          - return_label ↔ ranking_label 的单调不变量自动继承

    为什么价值标签必须是季度尺度（诚信披露，不是拍脑袋）：
        本项目用 8 天反复诚实证明：「未来 5 日涨跌」用散户可得的因子预测准确率 ≈ 51%，
        接近随机。原因是 5 日尺度被噪音主导，价值因子（低 PE/PB、高 ROE、高股息）的
        信号被淹没。价值因子的逻辑是「便宜 + 能赚钱的好公司，长期会被市场修复定价」——
        这个修复过程以季度/年为单位发生。所以预测目标必须放到一个季度（~60 交易日，
        A股 每年约 242 交易日 ÷ 4）才有真实的横截面区分力。

    标签定义（两步，与 ranking_label 完全一致，只是 horizon 变大）：
        step 1：每个 (date, ticker) 的未来 horizon 日累计收益率 = close[T+h]/close[T]-1
        step 2：按 **date** groupby 做横截面 pct rank ∈ [0, 1]
                同一日未来一季度涨得最多的票 → 1.0；最差 → 0.0；中位 → 0.5。

    参数：
        price_panel:    行情 panel，MultiIndex=(date, ticker)，含 close_col 列。
        horizon:        预测未来 N 个交易日。**默认走 SETTINGS.label.value_horizon（60）**
                        —— 这是本函数与 ranking_label 的唯一差异点。
        for_training:   与 ranking_label 同款语义（当前两种模式行为一致，尾部 NaN 来自
                        shift 的自然结果）。
        close_col:      收盘价列名，默认 "close"。

    返回：
        pd.Series，MultiIndex=(date, ticker)，name="value_ranking_label"。
        值域：[0.0, 1.0] ∪ {NaN}。NaN 在 (a) 数据起始处 pct_change 无法回溯
        (b) 尾部 horizon 行未来未知 两种情况下出现。

    ────────────────────────────────────────────────────────────────────────
    horizon 从 5 变 60 → purge gap 必须同步放大（look-ahead 第二道防线）
    ────────────────────────────────────────────────────────────────────────
    本标签的语义是「T 日标签 = T+60 日的相对涨幅」，所以训练集 date=T 的样本，其 y
    实际「看了」T+60 日的真实价格。切训练/验证集时，`models/splits.py` 的 purge gap
    **必须 >= 60**，否则训练集尾部样本的 60 日未来窗口会跨切线泄漏进验证集。

    配套配置：`SETTINGS.split.value_purge_gap`（默认 60）就是为此准备的。调用
    `time_series_split` 时务必传 `purge_gap_days=SETTINGS.split.value_purge_gap`
    且 `label_horizon=SETTINGS.label.value_horizon`，让命门校验 `gap >= horizon` 生效。
    短窗任务用的 `SETTINGS.split.purge_gap`（10）对价值标签是不够的，绝不能混用。
    """
    horizon = horizon or SETTINGS.label.value_horizon

    # 完全委托给 ranking_label —— 不重写任何计算，确保横截面 rank 命门 + 三道防线一致。
    # 唯一动作：把默认 horizon 换成季度尺度，再把输出 Series 改名以便下游区分。
    ranks = ranking_label(
        price_panel,
        horizon=horizon,
        for_training=for_training,
        close_col=close_col,
    )
    ranks.name = "value_ranking_label"
    return ranks


def trade_signal_label(
    price_panel: pd.DataFrame,
    *,
    horizon: int | None = None,
    tp_pct: float = 0.05,
    sl_pct: float = -0.03,
    for_training: bool = True,
    close_col: str = "close",
) -> pd.Series:
    """④ 买卖信号三元标注 —— P11 实装.

    标签定义（基于未来价格路径的 TP/SL 先触达逻辑）：
        对每个 (ticker, T)，往后看 horizon 个交易日（T+1 ~ T+horizon）的收盘价路径：
            - 路径中存在某日 close ≥ entry × (1 + tp_pct)，且**先于** SL 触达 → +1（TP 命中）
            - 路径中存在某日 close ≤ entry × (1 + sl_pct)，且**先于** TP 触达 → -1（SL 命中）
            - horizon 内 TP 和 SL 都没触达 → 0（HOLD）

        entry = close[T]（基于 T 收盘价开仓的假设）。

    参数：
        price_panel:    行情 panel，MultiIndex=(date, ticker)，含 close_col 列。
        horizon:        往后看 N 个交易日，默认走 SETTINGS.label.horizon（5）。
        tp_pct:         止盈幅度（正数），默认 +5%。触发条件 close ≥ entry × (1 + tp_pct)。
        sl_pct:         止损幅度（负数），默认 -3%。触发条件 close ≤ entry × (1 + sl_pct)。
                        必须 sl_pct < tp_pct，否则抛 ValueError（标签语义无意义）。
        for_training:   与 return_label 同款语义。当前两种模式行为一致（尾部 NaN 来自
                        前向 path 不完整的自然结果）。
        close_col:      收盘价列名，默认 "close"。

    返回：
        pd.Series，MultiIndex=(date, ticker)，name="trade_signal_label"。
        值域：{-1.0, 0.0, 1.0} ∪ {NaN}。NaN 在：
            (a) 数据起始处 path 无法完整回溯（本函数语义是「看未来」，所以起始处只要 path
                有 horizon 日就有效；NaN 主要发生在 b）
            (b) 尾部 horizon 行无法看完整未来路径

    ────────────────────────────────────────────────────────────────────────
    决策来源（Stage 3 启动设计 §5.3 + lead 拍板）—— 收盘价触发
    ────────────────────────────────────────────────────────────────────────
    本函数**只看收盘价**判断 TP/SL 触发，**不用 OHLC 盘中价格（如 high / low）**。理由：
        - 简化语义：A股 T+1 下「今日买、次日才能卖」，盘中触发还要考虑 high/low 顺序、
          一字板等复杂情形，回测层 stop-loss 触发引擎也用同款约束
        - 与回测引擎一致：`backtest/engine.py` 主循环按 close mark-to-market，TP/SL
          触发应同口径
        - 保守估计：盘中触发会高估 TP/SL 命中率（盘中高低点不一定能成交），收盘价口径
          是真实可达的下界

    未来若要支持 OHLC 盘中触发，应改 docstring 显式标注 `decision_rule="ohlc_path"`
    并新增参数，**不要默默改这条函数的行为**。

    ────────────────────────────────────────────────────────────────────────
    三道防 look-ahead（与 direction/return/ranking 对齐）
    ────────────────────────────────────────────────────────────────────────
    1. 数据层：上游 panel 经 `cache.truncate_by_date` 截断时，未来 close 不可见 → 尾部
       horizon 行 path 不完整 → NaN
    2. 切分层：`models/splits.py` purge gap 守住「训练样本的 N 日未来不能伸进验证集」
    3. 训练前：`align_xy(drop_label_nan=True)` 把 NaN 样本扔掉

    实现：用 `groupby(level="ticker").apply` 遍历每只票算 path（path 逻辑无法用 transform
    链式表达 —— 必须看未来多日的逐日「谁先触达」，不是单点函数）。

    与 return_label 的关系：
        trade_signal_label 是 return_label 的「路径 vs 终点」差异：
        - return_label：只看 close[T+horizon] / close[T] - 1（终点收益率）
        - trade_signal_label：看 close[T+1..T+horizon] 路径中谁先触达 TP/SL
        所以 return 大不代表 trade_signal=1（可能先跌穿 SL 再涨穿 TP）。
        测试 `test_trade_signal_label_consistent_with_return` 可守住 partial 关系。
    """
    horizon = horizon or SETTINGS.label.horizon

    if sl_pct >= tp_pct:
        raise ValueError(
            f"sl_pct ({sl_pct}) 必须 < tp_pct ({tp_pct})，否则 TP/SL 区间语义无意义"
        )

    if price_panel is None or price_panel.empty:
        return pd.Series(dtype=float, name="trade_signal_label")
    if close_col not in price_panel.columns:
        raise ValueError(
            f"price_panel 缺少 close 列 '{close_col}'，可用列: {list(price_panel.columns)}"
        )

    def _label_one_ticker(s: pd.Series) -> pd.Series:
        """对单只票的 close Series 算 trade_signal label 序列.

        对每个时刻 T，看接下来 horizon 个交易日（T+1 ~ T+horizon）的 close：
        逐日扫描，遇到先触发的就标 TP/SL，都没触发则标 HOLD。
        """
        vals = s.values
        n = len(vals)
        out = np.full(n, np.nan, dtype=float)
        for t in range(n - horizon):
            entry = vals[t]
            if pd.isna(entry) or entry <= 0:
                continue
            tp_price = entry * (1.0 + tp_pct)
            sl_price = entry * (1.0 + sl_pct)
            label = 0.0  # 默认 HOLD（horizon 内都没触发）
            # 逐日扫 path，先触达者赢
            for i in range(1, horizon + 1):
                p = vals[t + i]
                if pd.isna(p):
                    continue
                if p >= tp_price:
                    label = 1.0
                    break
                if p <= sl_price:
                    label = -1.0
                    break
            out[t] = label
        return pd.Series(out, index=s.index)

    label = (
        price_panel[close_col]
        .groupby(level="ticker", group_keys=False)
        .apply(_label_one_ticker)
    )
    label.name = "trade_signal_label"
    return label


__all__ = [
    "direction_label",
    "series_to_labels",
    "align_xy",
    "return_label",
    "ranking_label",
    "value_ranking_label",
    "trade_signal_label",
]
