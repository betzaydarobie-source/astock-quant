# 人话报告 11 —— Stage 4 每日预测工具：从「代码能跑」到「日常能用」

> 给你（项目主人）看的「通俗讲解版」。延续报告 01-10 的风格和标准。
> 对应技术文档：`dev-docs/Stage4-启动设计.md` + 4 份审核 PASS（P12 / P13 / P14 / P15）。那 5 份你可以不看，看这篇就够。
> explainer 出品 · 2026-05-16

---

## 一、欢迎进 Stage 4

报告 10 末尾讲过：「Stage 3 收尾后，由你决定 Stage 4 方向」。你选了——**把训好的模型变成每天能用的工具**（对应 lead 给的 A/B/C/D/E 候选里的"实用化打磨"路线，比 A 实盘保守、比 C 教学 demo 实用）。

**Stage 4 = 把"代码能跑"变成"日常能用"**。

```
Stage 1/2/3：造车（数据 / 因子 / 模型 / 回测 / 信号 / LLM / 4 类目标）
Stage 4：    装钥匙 + 仪表盘 + 自动停车  ← 本期
              ↓
            你能开了
```

打个最直观的类比：

> **造完一辆车之后，再给装上钥匙 + 仪表盘 + 自动停车，让你能开**。
>
> 前 3 个 Stage 把车造好了——发动机能转、变速箱能换挡、刹车能停。但你**还不能开**——没钥匙、没仪表盘、没人帮你停车。
>
> Stage 4 做的就是这三件事：
> - **钥匙**（P12 每日预测报告）：一行命令打着火，看模型今天怎么说
> - **仪表盘**（P14 准确率追踪）：天天看模型预测对了多少、跟瞎猜比差多远
> - **自动停车**（P13 launchd 自动跑）：到点了自动跑，你不用记得
> - **顺便扩个车队**（P15 沪深 300）：从只能开 30 辆车，扩到 300 辆

---

## 二、流水线最后一次画图（完整版）

老规矩，把那张走了 10 份报告的流水线，最后一次画完整：

```
                         Stage 1 + 2 + 3（造车）
   ┌──────────────────────────────────────────────────────┐
   │  data → factors → labels → 4 个 model → backtest → signals │
   └──────────────────────────────────────────────────────┘
                                │
                                ▼
                      ┌─ predict/daily.py ─┐
                      │  调 4 个 pipeline    │   ← P12 钥匙
                      │  整合 → 渲染 HTML/MD │
                      └─────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
       artifacts/reports/    JSON 落盘     osascript 通知 + 开浏览器
       YYYY-MM-DD.html      (P14 用)      (P13 自动触发后)
                                │
                                ▼
                      ┌─ predict/accuracy.py ─┐
                      │  读 N 天前的 JSON     │   ← P14 仪表盘
                      │  对照真实涨跌算命中率   │
                      └────────────────────────┘
                                │
                                ▼
                    📊 看模型是 lucky 还是真信号
```

---

## 三、五件事讲清楚

### 3.1 P12 —— 每日预测报告（钥匙）

#### 干啥的

一行命令出预测报告——4 个模型（涨跌方向 / 收益率 / 排名 / 买卖信号）今天对每只股票怎么说，整合到一个 HTML 报告里。

**类比**：

> **私人金融秘书每天给你出一份股市晨报**。
>
> 早上你打开报告——里面写「今天我觉得茅台 P(涨)=0.62（看涨）、五粮液 P(涨)=0.41（看跌）」「预期收益 ≥ +2% 的有这几只」「Top 5 排名是 …… 」「买入点信号 …… 」。
>
> 一目了然，不用你自己去翻代码、跑 pipeline。

#### 3 件关键设计

**设计 1：predict_only 模式（不重训，只推理）**

> **类比**：你不需要每天早上去汽车工厂**重新造一辆车**——你直接**钥匙打火**就行。

之前 4 个 pipeline（run_direction / run_return / run_ranking / run_trade_signal）每次跑都"训练 + 回测"一体——每天用太慢（5-10 分钟训练）。P12 给每个 pipeline 加了个 **`predict_only=True`** 开关——只**加载已训练的模型 + 推理今天的 30 只票**，**30 秒搞定**。

**命门测试**：4 个 pipeline 各有一个测试叫 `test_run_*_predict_only_does_not_call_fit`——用一种叫 **fit_spy** 的技术，把 `model.fit()` 包装一下让它一旦被调用就抛 AssertionError。如果未来谁不小心改坏了 predict_only 开关，让它偷偷调了 fit——测试当场报警。

**设计 2：诚信声明放在最显眼位置（不可省略）**

> **类比**：药盒上的"副作用警告"必须写在显眼位置——不能塞到包装盒底部小字里。

> **名词解释·诚信声明**：报告每一份都强制写「⚠️ 本报告所有预测基于历史数据统计模型。AUC = 0.513（随机猜 = 0.5）/ R² = -0.002（不如猜均值）/ 以上信号不构成投资建议，仅供学习研究使用」。

P12 给这事配了**3 个命门测试**：
- `test_daily_report_html_contains_honesty_disclaimer` —— HTML 报告必须含诚信声明
- `test_daily_report_md_contains_honesty_disclaimer` —— Markdown 报告同款
- `test_disclaimer_is_not_empty_placeholder` —— 不能是空壳子

未来谁手滑把诚信声明删了或留个空模板——**CI 立刻红，bug 别想偷偷溜进生产**。这是从代码层面强制守住"不能假装有 alpha"。

**设计 3：4 个 pipeline 完全隔离（任一失败不影响其他）**

P12 用 `try/except` 把 4 个 pipeline 隔离起来。其中一个挂了（比如某个模型文件没找到、某个数据拉取超时），其他 3 个继续跑。**失败的在报告里标 ⚠️**，不会一锅全废。

#### 用户怎么用

```bash
# 跑今天
uv run python -m astock_quant.predict.daily

# 跑指定日期
uv run python -m astock_quant.predict.daily --date 2026-05-15

# 只跑部分模型
uv run python -m astock_quant.predict.daily --targets direction return
```

跑完 30 秒左右出 `artifacts/reports/YYYY-MM-DD.html`，浏览器打开就能看。

---

### 3.2 P13 —— launchd 自动跑（自动停车）

#### 干啥的

macOS 自带的"任务计划"功能（launchd），每天 16:30 自动触发 P12 跑一遍，跑完弹通知 + 自动开浏览器。

**类比**：

> **晨报订阅了，每天上午 9 点自动到信箱**。
>
> 你不用每天记得去手动跑——电脑帮你跑。A股 16:00 收盘，留 30 分钟数据更新，16:30 触发；跑完 30 秒，**通知弹出"今日预测报告已生成"+ 浏览器自动打开**。你晚饭后回家随手扫一眼就行。

#### 关键设计

**用 macOS 内置的 launchd + osascript，不引入任何第三方依赖**：
- `~/Library/LaunchAgents/com.astock.daily.plist` —— 调度配置（16:30 触发）
- `scripts/daily_predict_wrapper.sh` —— shell 包装脚本
- **失败时弹通知**（含日志文件名）：「⚠️ 预测脚本失败，查看日志：wrapper_error_2026-05-16.log」
- **成功时弹通知 + 自动开浏览器**：「✅ 今日预测报告已生成，点击查看」

> **名词解释·launchd**：macOS 自带的任务调度服务（类似 Linux 的 cron）。它跟你电脑一起开机/运行，你睡觉时它能帮你按时跑脚本。

**核心安全设计**：
- `KeepAlive=false`（失败了等明天，不无限重启）
- `RunAtLoad=false`（开机不自动跑，避免一开机刷一堆通知）
- `wrapper.sh` 永远 `exit 0`（失败也不阻塞 launchd 下次调度）

#### 用户怎么用

```bash
# 一次性安装（详见 scripts/README.md）
# 1. 复制模板
cp scripts/com.astock.daily.plist.template /tmp/com.astock.daily.plist
# 2. 替换路径
sed -i '' 's|{project_root}|/Users/yourname/claude code/量化|g' /tmp/com.astock.daily.plist
# 3. 注册 launchd
launchctl load /tmp/com.astock.daily.plist

# 装完不用管，每天 16:30 自动跑
```

---

### 3.3 P14 —— 准确率追踪（仪表盘）

#### 干啥的

每天 P12 跑完会把预测落盘到 JSON。**P14 隔一段时间（horizon=5 天后）回头对照真实涨跌**，算每个模型命中率——看模型是 **lucky** 还是 **真有信号**。

**类比**：

> **健康手环记录步数 + 每月对比是否真减肥**。
>
> 你戴手环每天记录走了 10000 步。光看步数没用——**得过一段时间称体重对比**：「这一个月走了 30 万步，体重真的减了 2 公斤吗？还是没减但今天恰好不重？」
>
> 模型也一样——预测出来「茅台明天会涨」当时没法验证。**得等 5 天后，看茅台实际涨没涨**，才能给那次预测打个对错。每天积累，**过几周再看一次过去 30 天的命中率**。

#### 4 类命中率

| 模型 | 命中怎么算 | baseline |
|---|---|---|
| ① direction | 预测涨 + 真实涨 → 命中 | 50%（瞎猜）|
| ② return | 预测涨/跌方向跟真实一致 → 命中（不要求幅度精确）+ MAE 看预测误差 | 50% 方向一致 |
| ③ ranking | Top N 里真实涨幅排前 N/2 的比例（precision@N/2）+ Spearman 相关 | 50% precision |
| ④ trade_signal | 预测 TP/SL/HOLD 与实际触发方向一致 | 33.3%（3 选 1 瞎猜）|

#### 命中率结果会"诚信结论"——bp 量化跟 baseline 偏差

> **名词解释·bp（basis point，基点）**：万分之一。`(hit_rate - 0.5) * 10000` 就是相对随机基线偏差多少 bp。比如命中率 52% → +200 bp（高于 baseline 2 个百分点）。

P14 输出会直接写明：

```
① direction：命中率 52%，相对 50% baseline +200 bp
② return：方向一致率 51%，相对 50% baseline +100 bp
③ ranking：Spearman = 0.04（接近 0，无排序能力）
④ trade_signal：命中率 34%，相对 33% baseline +100 bp

整体判断：与 Stage 1 ① direction AUC=0.5131 / P9 ② return R²=-0.002 一脉相承，
模型仍是诚信弱基线，没有 alpha。学习/研究项目的预期结果，不要当真盘。
```

跟之前 5 次诚信弱基线**完全一脉相承**——不装、不夸大、用 bp 量化偏差。

#### 用户怎么用

```bash
# 看过去 30 天命中率
uv run python -m astock_quant.predict.accuracy --days 30

# 只看某个模型
uv run python -m astock_quant.predict.accuracy --days 30 --target direction

# 只看某只股票
uv run python -m astock_quant.predict.accuracy --days 30 --ticker 600519
```

**注意**：P14 需要积累数据。第一次跑可能显示「数据积累中（已有 0 天）」，等你跑了 10-30 天后才有意义。

---

### 3.4 P15 —— 股票池扩到沪深 300（扩车队）

#### 干啥的

之前 Stage 1-3 一直用的是 30 只大盘蓝筹（茅台、五粮液、招行……）。**P15 把可选股票池扩到沪深 300**——从只能开 30 辆车扩到 300 辆。

**类比**：

> **从只看茅台五粮液 → 看遍 A 股流量前 300**。
>
> 之前只盯 30 只——这 30 只可能哪天都没什么动静，你的模型只能在这 30 只里挑。
>
> 扩到 300 只——选股范围大 10 倍，模型能挑的"好票"理论上更多，**至少不会因为蓝筹整体没行情就空仓**。

#### 关键设计

**1. STAGE1_UNIVERSE 一字不动（向后兼容）**

P15 没改 Stage 1 那 30 只——而是**新增一种 stage**：

```python
get_universe("stage1")  # 还是 30 只蓝筹（默认）
get_universe("stage4")  # 沪深 300
```

**所有老代码不传参 = 默认 stage1，行为一字不变。** 这是延续 Stage 2/3 的"加法纪律"——加新的不破坏老的。

**2. akshare 实测 25 秒（远低于 architect-2 估的 5-10 分钟）**

> **名词解释·akshare**：免费的金融数据 Python 库，提供 A股 行情 / 沪深 300 成分股 / 财务等数据。报告 02 讲过 P2 数据层用的就是它。

architect-2 在 Stage 4 设计文档里估算"首次 prewarm 300 只数据约 5-10 分钟"。data-engineer-2 实测**只用了 25 秒**——比估算快 10-20 倍。

> **类比**：你预算装修要 1 个月，结果一周搞定——惊喜。

原因：
- data_cache/ 已经有 stage1 的 30 只历史数据，prewarm 跳过这些
- akshare 东财源响应快

**3. 1 天 TTL 缓存（不每次都拉 akshare）**

沪深 300 成分股每季度才调整，但每次都 fetch 浪费时间。所以 P15 加了 1 天 TTL 缓存：第一次拉 akshare，存到 `data_cache/hs300_universe.json`，**之后 24 小时内直接读缓存**。过期了再 fetch。

**4. 3 次重试 + 错误差异化处理**

每只票拉数据**重试 3 次**（应对偶发网络抖动）。3 次都失败：
- **prices 失败** → 加入 missing list（核心数据必须有）
- **moneyflow / financials 失败** → 打印警告但继续（辅助数据，pipeline 能降级运行）

#### 用户怎么用

```bash
# 首次拉沪深 300 数据（25 秒，带进度条）
uv run python -m astock_quant.scripts.prewarm_hs300

# 之后用 stage4 跑预测
uv run python -m astock_quant.predict.daily --universe stage4
```

---

### 3.5 5 件事串起来看

| 阶段 | 角色 | 输出 |
|---|---|---|
| P12 | 钥匙 | 一行命令出 HTML/MD 报告 |
| P13 | 自动停车 | 16:30 自动跑 + 通知 + 开浏览器 |
| P14 | 仪表盘 | 跑几周后看模型 lucky vs 真信号 |
| P15 | 扩车队 | 股票池 30 → 300 |

**整体的意义**：项目从「代码 demo」升级到**真正可日常用的工具**。你不需要懂代码、不需要每天记得手动跑——电脑帮你跑，你看报告就行。

---

## 四、诚信声明强度——从代码层面强制守住

这一节单独讲，因为是 Stage 4 最特别的工程纪律。

### 4.1 为什么要这么严

跟 Stage 1-3 一样，**模型仍是诚信弱基线**：
- ① AUC = 0.5131（略高于随机）
- ② R² = -0.002（不如猜均值）
- ③ rank-IC ≈ 0
- ④ macro accuracy ≈ baseline

**Stage 4 让用户每天看报告，但模型实际没 alpha**——稍不留神，用户会把报告当"投资建议"误用。

### 4.2 三道防线守住

```
第一道：HTML/MD 模板放最显眼位置
       ─→ §1 直接是诚信声明，不是附录不是页脚
              ↓
第二道：renderer 测试守门
       ─→ test_daily_report_*_contains_honesty_disclaimer
              ↓
第三道：测试断言非空占位
       ─→ test_disclaimer_is_not_empty_placeholder
```

**任何人改坏模板/删掉声明/换成空壳子——CI 立刻红，根本进不了主分支**。

类比：

> **就像药盒上的"副作用警告"**——法律规定必须印在显眼位置、不能小字、不能省。我们这个项目从代码层面强制做这件事。

### 4.3 P14 命中率报告也带诚信结论

P14 命中率输出**强制带"诚信结论"段**，直接写「与 Stage 1 ① direction AUC=0.5131 / P9 ② return R²=-0.002 一脉相承，模型仍是诚信弱基线，没有 alpha」+ 用 bp 量化偏差。

跟之前 6 次诚信弱基线（① / Stage 2 LLM / ② / ③ / ④ / 命中率追踪）**完全一脉相承**。

---

## 五、团队事件：第 4 次队员重组 —— data-engineer 装死 → data-engineer-2

### 5.1 怎么发生的

P15 阶段，lead 派给 data-engineer 任务（拉沪深 300 数据 + prewarm 脚本）。data-engineer **15 分钟零响应**——既不出文件、也不报错、也不汇报。

按现在的 **zero-tolerance 装死纪律**（报告 09 讲过），lead 立刻 shutdown 旧 data-engineer + spawn `data-engineer-2` 替补。

新人接手后 **真做完了**：4 个文件（settings.py 升级 + dataset.py 加 stage 参数 + scripts/ 包 + prewarm_hs300.py）+ 烟测 25 秒拉完，远超 architect-2 估算。

### 5.2 项目至今 5 次队员重组累计

老员工 9 个里，**4 个被换了**（factor-engineer 拆 3 / architect / auditor / data-engineer）：

| 时间 | 谁 | 原因 | 处理 |
|---|---|---|---|
| Stage 2 中段 | factor-engineer | 8 次装死症 | 拆 3 专职新人（traditional / llm-factor / factor-integrator）|
| Stage 3 启动 | architect | 50 分钟装死 | shutdown → architect-2 |
| P10 中段 | auditor | 25 分钟无响应（事后查证消息延迟）| shutdown → auditor-2 |
| **P15 阶段** | **data-engineer** | **15 分钟零响应** | **shutdown → data-engineer-2** |
| 加 progress-reporter | — | 主动监控代替被动等 | 新加 |

**累计 5 次队员重组 = 老员工 9 人 → 现在 6 个换了（4 个 shutdown + 1 个拆 3）+ 2 个新加**。

### 5.3 关键观察：6 个新人 0 装死

| 新人 | 表现 |
|---|---|
| traditional-factor-engineer | P10/P11/P12/P14/P15 测试 + 复审 - 0 装死 |
| llm-factor-engineer | Stage 2 M1 防线 + 顺手清容错测试 - 0 装死 |
| factor-integrator | P10/P11/P12/P13 接线 + 中途修 n_quantiles - 0 装死 |
| architect-2 | Stage 3 设计 v1.1 "罕见纪律深度" + Stage 4 设计 - 0 装死 |
| auditor-2 | P11/P12/P13/P14/P15 复审全 PASS - 0 装死 |
| data-engineer-2 | P15 ✅ 4 文件 + 烟测超预期 - 0 装死 |

**老员工 4 个都被换了，新员工 0 装死**。这套 zero-tolerance + progress-reporter 主动监控 + 新人 prompt 钉死纪律的模式**彻底成熟**。

类比：

> **公司治理**：老员工抓不住的 KPI，换一批严格执行 KPI 的新员工——业绩稳了。这是项目层面的"组织进化"。

---

## 六、三人协作模式持续：第 3 次（P12）+ 第 4 次（P14）

### 6.1 模式回顾

报告 09 / 10 讲过 P10 + P11 是项目第一次 + 第二次**三人协作**。Stage 4 又用了 2 次（P12 + P14）——这个模式现在彻底固化下来。

**核心纪律**："考试出题人 ≠ 监考 ≠ 阅卷"——**作者审查强制分离**。

### 6.2 P12 三人分工

| 角色 | 谁 | 干啥 |
|---|---|---|
| 核心算法 | model-engineer | 4 个 pipeline 的 predict_only 模式接口 + daily.py 主入口 + CLI |
| 渲染 + 接线 | factor-integrator | renderer.py + HTML/MD 模板 + JSON 落盘 |
| 测试 + 复审 | traditional-factor-engineer | 42 个测试（含 4 个 predict_only 命门 + 3 个诚信声明命门）|

### 6.3 P14 三人分工

| 角色 | 谁 | 干啥 |
|---|---|---|
| 核心算法 | model-engineer | accuracy.py 530 行（4 类 evaluator + GroundTruthCache + horizon 截断 + 诚信结论）|
| 数据 + 接线 | data-engineer-2（新）| 配合 GroundTruthCache 与数据层对接 |
| 测试 + 复审 | traditional-factor-engineer | 34 个测试（4 类 evaluator 各覆盖正/负/边界 + 缓存 + 缺失友好 + CLI）|

### 6.4 命门测试持续

每个 P 都有 traditional 写的命门测试：
- **P12**：predict_only 不调 fit（fit_spy 4 个）+ 诚信声明守门（3 个）
- **P13**：plist 必需 key 完整（9 个）+ wrapper.sh 语法 + exit 0
- **P14**：4 类 evaluator 算法正确性 + horizon 截断无 look-ahead
- **P15**：STAGE1_UNIVERSE 一字不动（向后兼容）+ HS300 cache TTL

**作者写代码、测试由第三方独立写 + 命门测试盯死关键不变量**——P10/P11/P12/P14 4 次都落地。

---

## 七、auditor-2 第 16-19 次实战 + 4 连 PASS

回顾你设的自动质量门（auditor），到 Stage 4 全收为止已经第 **19 次** 跑：

| 轮次 | 阶段 | 结果 |
|---|---|---|
| 第 1 轮 | P3a winsorize | FAIL → 抓 bug |
| 第 2-15 轮 | P3-P11 全部阶段 | 全 PASS |
| **第 16 轮** | **P12 每日预测报告** | **Conditional PASS**（7 项审核全过，4 个 ruff F401 测试文件未用 import，可一键 `--fix`）|
| **第 17 轮** | **P13 launchd 自动跑** | **PASS**（6 项审核全过 + 16 测试 + bash -n 通过）|
| **第 18 轮** | **P14 准确率追踪** | **PASS**（6 项审核全过 + 34 测试，4 类 evaluator 算法核对完整）|
| **第 19 轮** | **P15 扩沪深 300** | **PASS**（7 项审核全过 + 22 测试，akshare 25s 远超估算）|

**4 连 PASS**。auditor-2 用同款 7 项审核框架（数字真实 / 诚实性 / scope / 命门测试 / 安全 / 无新 high/critical / pytest+ruff）一路盯下来。

**整个 Stage 4 过程，你又是 0 操心**——4 次三人协作 + 4 次 auditor-2 复审，全部自动跑通。

---

## 八、用户使用流程（手把手）

### 8.1 首次跑前（一次性）

```bash
# 1. 拉沪深 300 数据（可选，如果你想用 stage4 而不是默认 30 只）
uv run python -m astock_quant.scripts.prewarm_hs300  # ~25 秒

# 2. 训练 4 个模型（如果之前没训过）
uv run python -m astock_quant.pipeline.run_direction     # 训练 ① 涨跌方向
uv run python -m astock_quant.pipeline.run_return        # 训练 ② 收益率
uv run python -m astock_quant.pipeline.run_ranking       # 训练 ③ 横截面排名
uv run python -m astock_quant.pipeline.run_trade_signal  # 训练 ④ 买卖信号
```

### 8.2 之后每天

```bash
# 出预测报告（30 秒）
uv run python -m astock_quant.predict.daily
```

或者**装 launchd 自动跑**（详见 `量化/scripts/README.md`），16:30 电脑自动跑、通知弹出、浏览器自动开。

### 8.3 看历史准确率

```bash
# 过去 30 天命中率
uv run python -m astock_quant.predict.accuracy --days 30
```

### 8.4 一句话

> **打开钥匙 → 看仪表盘 → 享受自动停车**。

---

## 九、一页纸总结

- **Stage 4 干了啥**：把"代码能跑"变成"日常能用"。**类比：造完车之后装钥匙 + 仪表盘 + 自动停车，让你能开**。4 子任务（P12-P15）全 PASS。
- **P12 每日预测报告（钥匙）**：一行命令出 HTML/MD 报告，4 类模型整合一目了然（30 秒搞定）。3 个关键设计：predict_only 模式（不重训只推理 / 4 个 fit_spy 命门测试守门）+ **诚信声明强制守门**（3 个测试守模板里必须有警告）+ 4 个 pipeline 隔离（任一失败不拖累其他）。**类比：私人金融秘书每天给你出股市晨报**。
- **P13 launchd 自动跑（自动停车）**：macOS 16:30 自动触发 P12，跑完弹通知 + 浏览器自动开。**类比：晨报订阅了每天上午 9 点自动到信箱**。失败也 exit 0 不阻塞下次调度。
- **P14 准确率追踪（仪表盘）**：每天 P12 落盘 JSON，过 N 天后回头对照真实涨跌算命中率。**类比：健康手环记录步数 + 每月对比是否真减肥**。4 类命中率（direction / return / ranking / trade_signal）+ **bp 量化** baseline 偏差 + 诚信结论自动写「与 Stage 1 ① AUC=0.5131 / ② R²=-0.002 一脉相承，模型仍是诚信弱基线」。
- **P15 扩沪深 300（扩车队）**：股票池从 30 只蓝筹扩到 300，覆盖更广。**类比：从只看茅台五粮液 → 看遍 A 股流量前 300**。STAGE1_UNIVERSE 一字不动（默认 stage1 行为字节级一致）+ HS300 1 天 TTL 缓存 + 3 次重试 + 错误差异化处理（prices 必须有，moneyflow/financials 可降级）。akshare 实测 25 秒（远低于 architect-2 估的 5-10 分钟）。
- **诚信声明从代码层面强制守住**（最重要工程纪律）：3 道防线（模板放最显眼 §1 + renderer 测试守门 + 测试断言非空）。**类比：药盒副作用警告必须印在显眼位置不能小字省略**。P14 命中率报告也带 bp 量化诚信结论——跟之前 6 次诚信弱基线一脉相承。
- **第 4 次队员重组**：data-engineer 15 分钟零响应 → data-engineer-2 接手。**项目至今 5 次队员重组累计**：factor-engineer 拆 3 / architect → architect-2 / auditor → auditor-2 / data-engineer → data-engineer-2 / 加 progress-reporter。**6 个新人 0 装死**——zero-tolerance + 新人 prompt 钉死纪律 + progress-reporter 主动监控**彻底成熟**。**类比：公司治理——老员工抓不住的 KPI，换一批严格执行的新员工，业绩稳了**。
- **三人协作模式持续**：P12 + P14 是项目第 3、第 4 次三人协作（继 P10/P11 之后）。**作者审查强制分离**模式彻底固化下来——model-engineer 写核心算法、factor-integrator 写接线 / 渲染、traditional-factor-engineer 写测试 + 复审。命门测试覆盖每个 P 的关键不变量。
- **auditor-2 第 16-19 次实战 4 连 PASS**：P12 Conditional PASS（4 ruff F401 测试文件可一键 fix）+ P13/P14/P15 全 PASS。**整个 Stage 4 你又是 0 操心**。
- **测试 212 → 326**（+114 新）：12（predict_only）+ 30（daily report）+ 16（launchd）+ 34（accuracy）+ 22（HS300）。
- **下一步可选**：
  - **跑几周看 P14 准确率追踪** —— 真假信号判断（最推荐，零代价就能知道模型在真实数据上表现如何）
  - **找真 alpha** —— 特征工程深挖 / 模型升级（不确定回报）
  - **实盘准备** —— 不推荐，先看几周准确率再说

---

走到这里，**Stage 4 全部交付**——4 类预测目标 + 每日预测工具 + 自动跑 + 准确率追踪 + 沪深 300 扩容。**项目从「能跑」升级到「日常能用」**。报告 01 当年说"造一台『学习用』的预测机"——今天你真的能每天用了。下一份就是 Stage 4 收尾说明，然后 lead 给你做最终汇报。
