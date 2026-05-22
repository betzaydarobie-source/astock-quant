"""astock_quant —— A股 量化预测系统（学习/研究用）.

混合架构：传统量化 ML 为核心预测引擎（LightGBM/LSTM），训练于「量价因子 + LLM 因子」，
产出 4 类预测目标（涨跌方向 / 收益率 / 选股排序 / 买卖信号）。LLM 仅负责把新闻/研报/
公告转成情绪/事件因子，作为一路因子喂给模型，不直接出预测。

分层（数据流方向）：
    config  →  data  →  factors  →  labels  →  models  →  backtest / signals
                                                    ↘ pipeline 串成端到端

详见 ../dev-docs/P1-架构设计.md。

—— 包级 .env 自动加载 ——
任何 `import astock_quant` 的入口（pipeline / tests / LLM 模块）都会自动从项目根
读取 `.env`，把 DEEPSEEK_API_KEY / LLM_PROVIDER / ENABLE_LLM_FACTOR 等注入 os.environ。
设计选择：
- `override=False`：CI / shell 里已 export 的 env var 优先于 .env（生产覆盖本地）
- try/except 兜底：python-dotenv 没装也不挂（向后兼容老环境）
- 静默失败：.env 不存在不报错（很多场景不需要，比如纯量价 stage 1 跑回测）
"""

from __future__ import annotations

try:
    from dotenv import find_dotenv, load_dotenv

    # find_dotenv() 从当前 cwd 往上找 .env，找到就 load 到 os.environ
    # 找不到返回空字符串，load_dotenv("") 安全 no-op
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    # python-dotenv 未安装 —— 老环境兼容，env var 仍可通过 shell export 注入
    pass

__version__ = "0.1.0"
