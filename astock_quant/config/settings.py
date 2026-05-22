"""项目配置 —— 股票池、缓存路径、回测参数、因子窗口、切分参数.

集中一处，所有模块从这里读配置，不在代码里散落魔法数字。
P2 阶段先把「数据层跑起来」需要的配置落实：UNIVERSE + 日期范围 + 缓存路径。
回测 / 因子 / 切分 / 标签相关配置先给合理默认值，后续阶段（P3/P4）按需调整。

用法：
    from astock_quant.config.settings import SETTINGS
    SETTINGS.universe          # -> list[str] 起步股票池
    SETTINGS.data_cache_dir    # -> Path 缓存目录
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径 —— 以本文件位置推导项目根，避免依赖运行时 cwd
# ---------------------------------------------------------------------------
# settings.py 在 astock_quant/config/ 下，parents[2] 即项目根（量化/）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Stage 1 起步股票池
# ---------------------------------------------------------------------------
# 说明：这是 Stage 1 的「起步 universe」，不是最终股票池。
# 选取原则：
#   - 流动性好的大盘蓝筹（成交活跃、停牌少、数据完整），降低数据缺失带来的噪音
#   - 跨行业分散（白酒/银行/保险/新能源/医药/家电/科技/券商/基建…），
#     让横截面因子（排序/选股）以后有意义
#   - 沪深两市都覆盖（6 开头沪市 + 0/3 开头深市），验证 market 前缀逻辑两边都通
# 共 30 只。后续要扩到沪深300 全量 / 中证500 时，直接替换这个 list 即可，
# 数据层（dataset 的 universe 循环）对池子大小无感。
STAGE1_UNIVERSE: list[str] = [
    # —— 食品饮料 ——
    "600519",  # 贵州茅台
    "000858",  # 五粮液
    "600887",  # 伊利股份
    # —— 银行 ——
    "601398",  # 工商银行
    "600036",  # 招商银行
    "000001",  # 平安银行
    # —— 非银金融 ——
    "601318",  # 中国平安
    "600030",  # 中信证券
    "300059",  # 东方财富
    # —— 新能源 / 电力设备 ——
    "300750",  # 宁德时代
    "002594",  # 比亚迪
    "601012",  # 隆基绿能
    # —— 医药生物 ——
    "600276",  # 恒瑞医药
    "300760",  # 迈瑞医疗
    "603259",  # 药明康德
    # —— 家电 ——
    "000333",  # 美的集团
    "000651",  # 格力电器
    "600690",  # 海尔智家
    # —— 科技 / 电子 / 通信 ——
    "002415",  # 海康威视
    "002475",  # 立讯精密
    "000725",  # 京东方A
    "600703",  # 三安光电
    # —— 资源 / 周期 ——
    "601899",  # 紫金矿业
    "600028",  # 中国石化
    "601088",  # 中国神华
    # —— 基建 / 地产 / 交运 ——
    "601668",  # 中国建筑
    "600009",  # 上海机场
    # —— 汽车 / 机械 ——
    "601633",  # 长城汽车
    "600031",  # 三一重工
    # —— 消费 / 零售 ——
    "603288",  # 海天味业
]


# ---------------------------------------------------------------------------
# 沪深 300 成分股 lazy loader（带 1 天 cache）
# ---------------------------------------------------------------------------

_HS300_CACHE_FILE = Path(__file__).resolve().parents[2] / "data_cache" / "hs300_universe.json"
_HS300_CACHE_TTL_SECONDS = 86400  # 1 天


def get_hs300_universe() -> list[str]:
    """从 akshare 拉沪深 300 成分股，结果 cache 到 data_cache/hs300_universe.json（1 天有效）。

    返回 list[str]，每个元素是 6 位纯数字代码（不带市场前缀），与 STAGE1_UNIVERSE 格式一致。
    """
    if _HS300_CACHE_FILE.exists():
        try:
            cached = json.loads(_HS300_CACHE_FILE.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if age < _HS300_CACHE_TTL_SECONDS:
                return cached["universe"]
        except Exception:
            pass  # cache 损坏则重拉

    import akshare as ak  # lazy import，不影响未使用此函数的模块加载速度
    logger.info("从 akshare 拉沪深 300 成分股...")
    df = ak.index_stock_cons_csindex(symbol="000300")
    codes = df["成分券代码"].astype(str).str.zfill(6).tolist()

    _HS300_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "universe": codes,
    }
    _HS300_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("沪深 300 成分股拉取完成，共 %d 只，已写入 %s", len(codes), _HS300_CACHE_FILE)
    return codes


def get_universe(stage: str = "stage1") -> list[str]:
    """按 stage 名返回对应股票池。

    stage="stage1" → STAGE1_UNIVERSE（30 只蓝筹，向后兼容）
    stage="stage4" → get_hs300_universe()（沪深 300 全量，lazy 拉取）
    """
    if stage == "stage4":
        return get_hs300_universe()
    return list(STAGE1_UNIVERSE)


# ---------------------------------------------------------------------------
# 配置 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BacktestConfig:
    """回测参数 —— P4 回测层用。P2 阶段只是占位，给合理默认值。"""

    start_date: str = "2022-01-01"  # 回测起始（落在历史区间内，留出因子预热期）
    end_date: str = "2026-05-01"
    initial_capital: float = 1_000_000.0  # 初始资金（元）
    commission_rate: float = 0.0003  # 佣金费率（双边，万 3）
    stamp_tax_rate: float = 0.0005  # 印花税（卖出单边，千 0.5）
    benchmark: str = "000300"  # 基准：沪深300 指数


@dataclass(frozen=True)
class FactorWindows:
    """因子计算窗口 —— P3 因子层用。P2 阶段占位。"""

    momentum: tuple[int, ...] = (5, 20, 60)  # 动量回看窗口（交易日）
    volatility: int = 20  # 波动率窗口
    ma: tuple[int, ...] = (5, 10, 20, 60)  # 均线窗口
    turnover: int = 20  # 换手率均值窗口


@dataclass(frozen=True)
class SplitConfig:
    """训练/验证切分参数 —— P3 models/splits.py 用。P2 阶段占位.

    purge_gap：训练集和验证集之间挖掉的交易日数，必须 >= 标签的未来窗口 N，
    否则标签的未来信息会泄漏进训练集（look-ahead 第二道防线）。
    """

    train_end: str = "2025-06-30"  # 训练集截止日
    valid_end: str = "2026-05-01"  # 验证集截止日
    purge_gap: int = 10  # purge 间隔（交易日），需 >= LABEL.horizon（①②③④ 短窗标签用）

    # —— 价值选股专属（T3/T4 价值改造）——
    # value_ranking_label 的 horizon 是 60（一个季度），purge_gap 必须相应放大到 >= 60，
    # 否则训练集尾部样本的「未来 60 日相对收益」会跨切线泄漏进验证集（look-ahead）。
    # 单独留一个字段，不动短窗任务的 purge_gap=10，互不干扰。
    value_purge_gap: int = 60  # 价值标签 purge 间隔（交易日），需 >= LABEL.value_horizon


@dataclass(frozen=True)
class LabelConfig:
    """标签参数 —— P3 labels/targets.py 用.

    horizon：②收益 ③排名 ④买卖信号 的预测窗口（未来 N 个交易日）。
    direction_horizon：① 专属预测窗口。P25 网格实验（20 组配置）证明「未来 5 日
        涨跌」用散户因子不可预测（模型必退化）；改为预测「明日是否明显走强」，
        模型才有真实区分力（300 只实测 79 棵树 / AUC 0.74）。
    direction_threshold：① 的收益阈值 —— 未来 direction_horizon 日收益 > 阈值记为 1。
        P25：0.0（单纯涨跌，实测退化）→ 0.03（明日涨幅 >3% = 明显走强）。
    value_horizon：价值选股专属预测窗口（T3 价值改造）。短期涨跌（5 日尺度）已被
        反复证明接近随机；价值因子（便宜 + 赚钱的好公司）只在季度/年尺度才显现
        超额收益。一个季度约 60 个交易日（A股 每年约 242 个交易日 ÷ 4 ≈ 60），
        所以价值标签预测「未来一个季度的横截面相对收益排名」。
    """

    horizon: int = 5  # ②③④ 预测未来 N 个交易日
    direction_horizon: int = 1  # ① 专属：预测明日（P25 改，原与 horizon 共用 5）
    direction_threshold: float = 0.03  # ① 阈值：明日涨幅 >3% 记为「走强」（P25 改，原 0.0）
    value_horizon: int = 60  # 价值选股专属：预测未来一个季度（~60 交易日）的相对收益


@dataclass(frozen=True)
class ValueScoreConfig:
    """价值+质量综合打分参数 —— T2 价值选股改造，factors/value_score.py 用.

    综合分 = 价值分 × value_weight + 质量分 × quality_weight + 成长分 × growth_weight。
    三个维度各由若干因子按「每日横截面」标准化后等权合成（详见 value_score.py）。

    权重默认「价值与质量并重、成长辅助」—— 价值投资的核心是「便宜（价值）的好公司
    （质量）」，成长是加分项但单独追高成长容易买贵，故权重低于前两者。三者之和不强制
    为 1（value_score 内部会按权重和归一化），但默认给和为 1 的直观值。

    方向约定（关键）：每个因子在 value_score 里都会被转成「越大越好」再做横截面排名 ——
    低 PE/PB/负债是好事（取负向），高 ROE/股息/毛利/成长是好事（取正向）。
    """

    # —— 三维权重 ——
    value_weight: float = 0.4    # 价值维度（低 PE / 低 PB / 高股息）
    quality_weight: float = 0.4  # 质量维度（高 ROE / 高净利率 / 高毛利率 / 低负债）
    growth_weight: float = 0.2   # 成长维度（营收同比 / 净利同比增速）

    # —— 横截面极端值处理 ——
    # 标准化前对每日横截面做分位截尾（防个别极端 PE/增速主导排名）。
    # 必须是「按日横截面」分位，不是全样本分位（项目踩过 winsorize 全样本 look-ahead 坑）。
    winsor_lower: float = 0.05  # 每日横截面下 5% 分位截尾
    winsor_upper: float = 0.95  # 每日横截面上 5% 分位截尾

    # —— 单日有效横截面最小股票数 ——
    # 某日横截面有效票数少于此值时，该日横截面排名无统计意义 → 该日该维度分置 NaN。
    min_cross_section: int = 5


@dataclass(frozen=True)
class Settings:
    """全局配置聚合 —— 各模块统一从 SETTINGS 单例读取。"""

    # —— 股票池 ——
    universe: list[str] = field(default_factory=lambda: list(STAGE1_UNIVERSE))

    # —— 历史数据区间 ——
    # 起止覆盖近 ~4.4 年（够 ML 训练 + 时序切分 + 因子预热）。
    # 注意：mootdx bars 单次最多取 ~800 根日线（约 3.2 年），astock_source
    # 需要分段翻页才能覆盖到 history_start —— 详见 astock_source 的 get_prices 实现。
    history_start: str = "2022-01-01"
    history_end: str = "2026-05-15"  # 当前日期（数据拉取上界）

    # —— 路径 ——
    project_root: Path = _PROJECT_ROOT
    data_cache_dir: Path = _PROJECT_ROOT / "data_cache"  # CSV 缓存，已 gitignore
    artifacts_dir: Path = _PROJECT_ROOT / "artifacts"  # 模型/回测产物，已 gitignore

    # —— 子配置 ——
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    factor_windows: FactorWindows = field(default_factory=FactorWindows)
    split: SplitConfig = field(default_factory=SplitConfig)
    label: LabelConfig = field(default_factory=LabelConfig)
    value_score: ValueScoreConfig = field(default_factory=ValueScoreConfig)


# 全局单例 —— 所有模块 `from astock_quant.config.settings import SETTINGS`
SETTINGS = Settings()
