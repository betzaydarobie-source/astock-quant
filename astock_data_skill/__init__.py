"""astock_data_skill —— SKILL.md 28 端点的 Python 包装.

源: ~/.claude/skills/a-stock-data/SKILL.md V3.1 (2026-05-19 实测).

7 个 layer 子模块:
- quotes:        Layer 1 行情层 (mootdx + 腾讯 + 百度 K 线)
- research:      Layer 2 研报层 (东财研报 + 同花顺 EPS + iwencai)
- signals:       Layer 3 信号层 (同花顺热点 / 北向 / 概念 / 资金流分钟 / 龙虎榜 / 解禁 / 行业)
- flows:         Layer 4 资金面/筹码层 (融资融券 / 大宗 / 股东户数 / 分红 / 资金流 120 日)
- news:          Layer 5 新闻层 (东财个股新闻 / 财联社 / 东财全球资讯)
- fundamentals:  Layer 6 基础数据层 (mootdx finance/F10 / 东财个股信息 / 新浪三表)
- announcements: Layer 7 公告层 (巨潮 / mootdx F10 最新提示)

使用方式:

    from astock_data_skill import tencent_quote, eastmoney_stock_info, ths_eps_forecast
    q = tencent_quote(["600519"])
    info = eastmoney_stock_info("600519")

诚信声明: 所有端点实测于 2026-05-19, 但底层 API 可能在未来变更. 失败一律
优雅降级 (返回空 dict/list), 不抛崩 —— 上层调用方必须做 None/空值兜底.
"""

from __future__ import annotations

# Layer 1 行情
from astock_data_skill.quotes import (
    baidu_kline_with_ma,
    mootdx_klines,
    mootdx_quotes,
    mootdx_transactions,
    tencent_quote,
)

# Layer 2 研报
from astock_data_skill.research import (
    dedup_articles,
    download_research_pdf,
    eastmoney_reports,
    iwencai_query,
    iwencai_search,
    ths_eps_forecast,
)

# Layer 3 信号
from astock_data_skill.signals import (
    baidu_concept_blocks,
    daily_dragon_tiger,
    dragon_tiger_board,
    eastmoney_fund_flow_minute,
    hsgt_realtime,
    industry_comparison,
    load_northbound_history,
    lockup_expiry,
    save_northbound_snapshot,
    ths_hot_reason,
)

# Layer 4 资金面
from astock_data_skill.flows import (
    block_trade,
    dividend_history,
    holder_num_change,
    margin_trading,
    stock_fund_flow_120d,
)

# Layer 5 新闻
from astock_data_skill.news import (
    cls_telegraph,
    eastmoney_global_news,
    eastmoney_stock_news,
)

# Layer 6 基础数据
from astock_data_skill.fundamentals import (
    F10_CATEGORIES,
    eastmoney_stock_info,
    mootdx_f10,
    mootdx_finance,
    sina_financial_report,
)

# Layer 7 公告
from astock_data_skill.announcements import (
    cninfo_announcements,
    mootdx_latest_announcement,
)

# Helpers
from astock_data_skill._common import (
    eastmoney_datacenter,
    get_prefix,
    get_secid,
    normalize_ticker,
)


__all__ = [
    # Layer 1
    "baidu_kline_with_ma",
    "mootdx_klines",
    "mootdx_quotes",
    "mootdx_transactions",
    "tencent_quote",
    # Layer 2
    "dedup_articles",
    "download_research_pdf",
    "eastmoney_reports",
    "iwencai_query",
    "iwencai_search",
    "ths_eps_forecast",
    # Layer 3
    "baidu_concept_blocks",
    "daily_dragon_tiger",
    "dragon_tiger_board",
    "eastmoney_fund_flow_minute",
    "hsgt_realtime",
    "industry_comparison",
    "load_northbound_history",
    "lockup_expiry",
    "save_northbound_snapshot",
    "ths_hot_reason",
    # Layer 4
    "block_trade",
    "dividend_history",
    "holder_num_change",
    "margin_trading",
    "stock_fund_flow_120d",
    # Layer 5
    "cls_telegraph",
    "eastmoney_global_news",
    "eastmoney_stock_news",
    # Layer 6
    "F10_CATEGORIES",
    "eastmoney_stock_info",
    "mootdx_f10",
    "mootdx_finance",
    "sina_financial_report",
    # Layer 7
    "cninfo_announcements",
    "mootdx_latest_announcement",
    # Helpers
    "eastmoney_datacenter",
    "get_prefix",
    "get_secid",
    "normalize_ticker",
]
