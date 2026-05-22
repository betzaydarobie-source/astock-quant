# 每日预测报告 —— 用户友好改造设计稿

> explainer 出品 · 2026-05-16 · 用户实测痛点反馈后第 1 版
>
> 用户原话：「置信度 0.477 看不懂 / AUC 数字看不懂 / 不知道 600519 是哪家 / ASCII 条图像乱码 / 看完不知道今天到底买不买啥」
>
> 本设计交付 5 项改造的**完整文案 + 数据**，factor-integrator 直接照搬实现。
> 改 3 个文件：`predict/templates/daily_report.html.template` + `daily_report.md.template` + `predict/renderer.py`。

---

## 改造原则

5 条原则，贯穿所有文案：

1. **每个数字配一句翻译**：原数字保留（专业读者要看）+ 紧跟 `→ 📖 人话` 翻译（小白看翻译）
2. **每个 § 末尾必有「📖 大白话」段**：把这一节技术内容用 1-3 句大白话总结
3. **股票代码必带中文名**：`600519 贵州茅台`，绝不只显示代码
4. **顶部「今日一句话总结」**：用户哪怕只看顶部那 3 行也能知道"今天该不该动手"
5. **诚信红线不能松**：所有翻译必须诚信（不掩盖"模型没把握"），跟前 11 份报告诚信纪律一脉相承

---

## §0 顶部「今日一句话总结」（最重要）

### 设计

报告最顶部加一个新的 `<div class="today-summary">`（**显眼，第一眼看到**）。3 行结构：

```
🎯 今日一句话：模型今天看跌 {N_sell} 只 / 看涨 {N_buy} 只 / 中性 {N_hold} 只，
   平均把握 {avg_conf:.2f} —— {avg_conf_verdict}。
🥇 如果非要选 1 只：{top1_name}（{top1_code}），但模型自己说 {top1_conf:.2f} 的把握。
⚠️ 诚信结论：{honesty_one_liner}
```

### 动态字段生成逻辑（factor-integrator 实现）

| 字段 | 怎么算 | 例子 |
|---|---|---|
| `N_buy / N_sell / N_hold` | 数 ① direction 信号分布 | `0 / 30 / 0` |
| `avg_conf` | 全部预测的平均 `|score - 0.5| * 2` | `0.46` |
| `avg_conf_verdict` | 根据 avg_conf 翻译 | `< 0.3 → "几乎没把握"` / `0.3~0.5 → "把握很弱"` / `0.5~0.7 → "把握一般"` / `> 0.7 → "把握较强（仍不构成投资建议）"` |
| `top1_name / top1_code / top1_conf` | ③ ranking 模型的 Top 1（按 rank score 降序）| `隆基绿能 / 601012 / 0.92` |
| `honesty_one_liner` | 根据 ① direction 信号分布 + 平均把握 决定 | 见下表 |

#### `honesty_one_liner` 决策表

| 场景 | 文案 |
|---|---|
| 全部 hold（无 buy/sell）| 「模型对所有票都没强信号，今天什么都别做就是最佳策略。」 |
| 全部 sell（30 只全看跌）+ avg_conf < 0.3 | 「模型今天对所有票都看跌，但置信度非常低（< 0.3）—— 极可能是大盘整体震荡的体现，不要跟单。」 |
| 全 sell + avg_conf > 0.5 | 「模型对全部票都看跌且有一定把握，但**单日信号统计意义弱**，建议先看 P14 准确率追踪几周。」 |
| buy 多于 sell 且 avg_conf > 0.5 | 「模型今天偏看涨且有一定把握，但请记住 AUC=0.5131（跟猜硬币差不多），**不构成投资建议**。」 |
| 其他情况（混杂）| 「模型信号方向不一，建议**不要跟单**，先看几周准确率追踪后再判断。」 |

### 大白话样例（顶部总结的 3 个真实例子）

**例子 A（最常见 — 全 hold）**：

```
🎯 今日一句话：模型今天看跌 0 只 / 看涨 0 只 / 中性 30 只，平均把握 0.18 —— 几乎没把握。
🥇 如果非要选 1 只：贵州茅台（600519），但模型自己说 0.21 的把握。
⚠️ 诚信结论：模型对所有票都没强信号，今天什么都别做就是最佳策略。
```

**例子 B（用户当前真实场景 — 全 sell + 低置信度）**：

```
🎯 今日一句话：模型今天看跌 30 只 / 看涨 0 只 / 中性 0 只，平均把握 0.46 —— 把握很弱。
🥇 如果非要选 1 只：隆基绿能（601012），但模型自己说 0.49 的把握。
⚠️ 诚信结论：模型今天对所有票都看跌，但置信度非常低（<0.5）—— 极可能是大盘整体震荡的体现，不要跟单。
```

**例子 C（少见 — 偏看涨）**：

```
🎯 今日一句话：模型今天看跌 5 只 / 看涨 8 只 / 中性 17 只，平均把握 0.34 —— 把握很弱。
🥇 如果非要选 1 只：宁德时代（300750），但模型自己说 0.62 的把握。
⚠️ 诚信结论：模型信号方向不一，建议不要跟单，先看几周准确率追踪后再判断。
```

### HTML 渲染（factor-integrator 照搬）

```html
<div class="today-summary">
  <h2>🎯 今日一句话总结</h2>
  <p class="summary-line">${summary_line_1}</p>
  <p class="summary-line">${summary_line_2}</p>
  <p class="summary-line honesty">${summary_line_3}</p>
</div>
```

CSS（黄色警示框风格，跟诚信声明区分但同样显眼）：

```css
.today-summary {
  background: #fff7e6;  /* 暖黄色 */
  border-left: 4px solid #fa8c16;
  padding: 16px 20px;
  margin: 0 0 24px 0;
}
.today-summary .summary-line { margin: 4px 0; font-size: 16px; }
.today-summary .honesty { color: #d4380d; font-weight: bold; }
```

---

## §1 诚信声明数字加翻译

### 当前文案

```
本报告所有预测基于历史数据统计模型。
- ① direction AUC = 0.5131（随机猜 AUC = 0.5）
- ② return R² = -0.0019（不如猜均值）
- ③ ranking rank-IC ≈ 0（横截面排序无能力）
- ④ trade_signal macro accuracy ≈ baseline
```

### 改造后文案（每个数字 + 翻译）

```
⚠️ 诚信声明 —— 请在查看预测前仔细阅读

本报告所有预测基于历史数据统计模型，模型本身没有 alpha（不会真正赚钱）：

- ① 涨跌方向 AUC = 0.5131  →  📖 跟猜硬币差不多（基线 0.5 = 抛硬币）
- ② 收益率预测 R² = -0.0019  →  📖 比"直接猜涨幅=0"还差一点点
- ③ 横截面排名 rank-IC ≈ 0  →  📖 给 30 只票排名跟抓阄差不多
- ④ 买卖信号 macro accuracy ≈ 33%  →  📖 跟从"买/卖/不动"三选一瞎猜一样

以上信号**不构成投资建议**，仅供学习研究使用。过去表现不代表未来收益。
A股 短期方向接近随机过程，没做任何特征工程深挖前不应该有 alpha。
```

### 数字 → 翻译动态评级（factor-integrator 实现）

不要硬编码当前数字。renderer 读取 metrics 后**根据数值动态生成翻译**，让未来 AUC 真的提升时翻译也跟着改：

#### AUC（① direction）

| 区间 | 翻译 |
|---|---|
| `< 0.5` | `📖 比抛硬币还差，模型在帮倒忙` |
| `0.50 ~ 0.52` | `📖 跟猜硬币差不多（基线 0.5 = 抛硬币）` |
| `0.52 ~ 0.55` | `📖 略好于随机猜，但不够稳定` |
| `0.55 ~ 0.58` | `📖 接近学术界蓝筹股预测理论上限` |
| `0.58 ~ 0.65` | `📖 真有点信号，但仍需多次验证` |
| `> 0.65` | `📖 异常高，请检查是否数据泄漏（look-ahead）` |

#### R²（② return）

| 区间 | 翻译 |
|---|---|
| `< 0` | `📖 比"直接猜均值"还差一点点` |
| `0 ~ 0.01` | `📖 接近"直接猜均值"水平` |
| `0.01 ~ 0.03` | `📖 略好于"直接猜均值"，但解释力很弱` |
| `0.03 ~ 0.10` | `📖 有一定预测力，业界算合格` |
| `> 0.10` | `📖 解释力强，请检查是否数据泄漏（look-ahead）` |

#### rank-IC（③ ranking）

| 区间 | 翻译 |
|---|---|
| `abs(IC) < 0.02` | `📖 给 30 只票排名跟抓阄差不多` |
| `0.02 ~ 0.05` | `📖 排名能力很弱，统计意义不显著` |
| `0.05 ~ 0.10` | `📖 排名有一定信号，量化业界算合格` |
| `> 0.10` | `📖 排名信号较强，请检查是否数据泄漏` |

#### Accuracy（④ trade_signal，3 分类 baseline=33%）

| 区间 | 翻译 |
|---|---|
| `< 0.33` | `📖 比"买/卖/不动"三选一瞎猜还差` |
| `0.33 ~ 0.38` | `📖 跟从"买/卖/不动"三选一瞎猜一样` |
| `0.38 ~ 0.45` | `📖 略好于三选一瞎猜` |
| `> 0.45` | `📖 真有点信号，但仍需多次验证` |

### Markdown 渲染

```markdown
## ⚠️ 诚信声明（请在查看预测前仔细阅读）

本报告所有预测基于历史数据统计模型，模型本身没有 alpha：

| 模型 | 指标 | 数值 | 📖 翻译 |
|---|---|---:|---|
| ① 涨跌方向 | AUC | 0.5131 | 跟猜硬币差不多（基线 0.5 = 抛硬币）|
| ② 收益率预测 | R² | -0.0019 | 比"直接猜涨幅=0"还差一点点 |
| ③ 横截面排名 | rank-IC | ≈ 0 | 给 30 只票排名跟抓阄差不多 |
| ④ 买卖信号 | macro acc | ≈ 33% | 跟"买/卖/不动"三选一瞎猜一样 |

**以上信号不构成投资建议**，仅供学习研究。
```

---

## §2 股票代码 → 加中文名

### 设计

所有显示 ticker 的地方都改成 `{code} {name}` 格式：

- 表格：`600519 贵州茅台`（不是只 `600519`）
- 顶部「今日一句话」：`贵州茅台（600519）`（中文优先，括号给代码）
- 大白话段：`前 5 名（隆基 / 东财 / 海康 / 五粮液 / 宁德）` 用中文简称

### 放哪：硬编码到 `predict/ticker_names.py`（新建）

理由：
- 30 只蓝筹的中文名稳定（不会今天叫"茅台"明天改名）
- 沪深 300 扩展时**支持 fallback**：name 表里没有 → 调 akshare `ak.stock_info_a_code_name()` 拉，第一次拉完后写到缓存 JSON（跟 P15 沪深 300 cache 同款 1 天 TTL）

#### 30 只蓝筹中文名 mapping（直接照搬到 ticker_names.py）

```python
# astock_quant/predict/ticker_names.py
"""
ticker → 中文名映射。
- STAGE1_NAMES：30 只蓝筹硬编码，覆盖 Stage 1 universe
- get_ticker_name(code)：先查 STAGE1_NAMES，再查缓存 JSON，最后兜底调 akshare
"""

STAGE1_NAMES: dict[str, str] = {
    # 食品饮料 3
    "600519": "贵州茅台",
    "000858": "五粮液",
    "600887": "伊利股份",

    # 银行 3
    "601398": "工商银行",
    "600036": "招商银行",
    "000001": "平安银行",

    # 非银金融 3
    "601318": "中国平安",
    "600030": "中信证券",
    "300059": "东方财富",

    # 新能源 3
    "300750": "宁德时代",
    "002594": "比亚迪",
    "601012": "隆基绿能",

    # 医药生物 3
    "600276": "恒瑞医药",
    "300760": "迈瑞医疗",
    "603259": "药明康德",

    # 家电 3
    "000333": "美的集团",
    "000651": "格力电器",
    "600690": "海尔智家",

    # 科技/电子 4
    "002415": "海康威视",
    "002475": "立讯精密",
    "000725": "京东方A",
    "600703": "三安光电",

    # 资源/周期 3
    "601899": "紫金矿业",
    "600028": "中国石化",
    "601088": "中国神华",

    # 基建/地产/交运 2
    "601668": "中国建筑",
    "600009": "上海机场",

    # 汽车/机械 2
    "601633": "长城汽车",
    "600031": "三一重工",

    # 消费/零售 1
    "603288": "海天味业",
}
```

#### 简称 mapping（用于大白话段，避免句子太长）

> 用「短名」让大白话更顺溜。表格里用全名「贵州茅台」，大白话段里用「茅台」。

```python
STAGE1_SHORT_NAMES: dict[str, str] = {
    "600519": "茅台",
    "000858": "五粮液",
    "600887": "伊利",
    "601398": "工行",
    "600036": "招行",
    "000001": "平安",  # 平安银行
    "601318": "中国平安",  # 中国平安（保险，跟 000001 区分）
    "600030": "中信证",
    "300059": "东财",
    "300750": "宁德",
    "002594": "比亚迪",
    "601012": "隆基",
    "600276": "恒瑞",
    "300760": "迈瑞",
    "603259": "药明",
    "000333": "美的",
    "000651": "格力",
    "600690": "海尔",
    "002415": "海康",
    "002475": "立讯",
    "000725": "京东方",
    "600703": "三安",
    "601899": "紫金",
    "600028": "中石化",
    "601088": "神华",
    "601668": "中建",
    "600009": "上海机场",
    "601633": "长城",
    "600031": "三一",
    "603288": "海天",
}
```

#### 沪深 300 扩展 fallback

```python
def get_ticker_name(code: str) -> str:
    """ticker → 中文名，3 道 fallback."""
    # 1. 硬编码 30 只蓝筹（最快）
    if code in STAGE1_NAMES:
        return STAGE1_NAMES[code]

    # 2. 缓存 JSON（沪深 300 第一次拉过）
    cache_path = SETTINGS.data_cache_dir / "ticker_names.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if code in cached:
                return cached[code]
        except Exception:
            pass

    # 3. akshare fallback（1 次性慢）
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        name_map = dict(zip(df["code"].astype(str).str.zfill(6), df["name"]))
        # 写回 cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(name_map, ensure_ascii=False), encoding="utf-8")
        return name_map.get(code, code)  # 真的拉不到 → 兜底返回 code 本身
    except Exception:
        return code  # akshare 都挂 → 返回 code，不抛异常
```

---

## §3 每个 § 末尾加「📖 大白话」段

### 设计

每个 §（除诚信声明外）末尾加一个 `<div class="plain-language">` 段，用 1-3 句大白话总结这一节。

renderer 实现：每个 § 渲染完后，**根据本节数据动态生成大白话**（不要硬编码文案，让未来数据变了文案也跟着改）。

### §2 涨跌方向（① direction）大白话生成逻辑

读取本节 `n_buy / n_sell / n_hold / avg_score`，按下表生成：

| 场景 | 文案模板 |
|---|---|
| 全 hold | `📖 模型对 {N} 只股票全部「拿着不动」，预测分都在 0.45-0.55 中间犹豫不决（基线 0.5）。建议今天**什么都别买**。` |
| 全 sell + avg_score < 0.5 | `📖 模型今天对 {N} 只全部看跌，但预测分（avg {avg:.2f}）都低于但接近 0.5，说明**模型自己也没把握**。**不要跟单卖出**——很可能是大盘当天整体震荡的副作用。` |
| 偏 buy（buy > sell）| `📖 模型今天看涨 {n_buy} 只 / 看跌 {n_sell} 只，平均把握 {avg:.2f}。请记住 AUC=0.5131 跟猜硬币差不多，**这些看涨信号大概率是噪音**，不构成投资建议。` |
| 偏 sell | `📖 模型今天看跌 {n_sell} 只 / 看涨 {n_buy} 只，平均把握 {avg:.2f}。同款提醒：不构成投资建议。` |

### §3 收益率预测（② return）大白话生成逻辑

读取本节 `mean_pred_return / n_pos / n_neg / max_abs_pred`：

| 场景 | 文案模板 |
|---|---|
| `max_abs_pred < 0.005`（全预测 ±0.5% 内）| `📖 模型预测所有票涨跌幅都在 ±0.5% 之内，绝对值接近 0 —— 等于**偷懒猜均值**。这就是 R²<0 的体现：模型没本事预测幅度。` |
| `max_abs_pred 0.005~0.02` | `📖 模型预测涨跌幅最大 {max:.2%}，但 IC 接近 0 意味着**幅度方向都不准**。看个热闹就行。` |
| `max_abs_pred > 0.02` | `📖 模型预测最大涨幅 {max_pos:.2%}（{top_name}）、最大跌幅 {max_neg:.2%}（{bot_name}）。但模型 R²={r2:.4f}，**幅度准确性不可信**。` |

### §4 横截面排名（③ ranking）大白话生成逻辑

读取本节 Top 5 的 ticker + score + 模型 rank-IC：

| 场景 | 文案模板 |
|---|---|
| `rank-IC ≈ 0`（abs < 0.02）| `📖 前 5 名（{top1_short} / {top2_short} / {top3_short} / {top4_short} / {top5_short}）按 ranking score 排，但 rank-IC≈0 **相当于抓阄**，不要当真。` |
| `rank-IC 0.02~0.05` | `📖 前 5 名（...）有弱相关性（rank-IC≈{ic:.3f}），统计意义不显著。` |
| `rank-IC > 0.05` | `📖 前 5 名（...）相关性较强（rank-IC≈{ic:.3f}），但仍需多次验证后才能信。` |

### §5 买卖信号（④ trade_signal）大白话生成逻辑

读取本节 `n_buy / n_sell / n_hold`：

| 场景 | 文案模板 |
|---|---|
| 全 hold | `📖 今天 buy 信号 0 / sell 信号 0 / hold {N}，**模型不让你买任何东西**也不让卖任何东西——这是 Stage 4 设计的保守默认行为。` |
| 有 buy 信号 | `📖 今天 buy 信号 {n_buy}：{top_buy_names}，置信度 {top_buy_conf:.2f}。但模型 macro accuracy≈33% 跟瞎猜一样，**不构成投资建议**。` |
| 有 sell 信号 | `📖 今天 sell 信号 {n_sell}：{top_sell_names}。同款提醒：模型 macro acc≈33%，不构成投资建议。` |

### 渲染样式（HTML）

```html
<div class="plain-language">
  <span class="emoji">📖</span>
  <span class="text">${plain_lang_text}</span>
</div>
```

```css
.plain-language {
  background: #f6ffed;  /* 淡绿，区分于诚信声明的红/橙 */
  border-left: 3px solid #52c41a;
  padding: 12px 16px;
  margin: 16px 0 24px 0;
  font-size: 15px;
  line-height: 1.6;
}
.plain-language .emoji { font-size: 18px; margin-right: 6px; }
```

---

## §4 ASCII 条图改友好

### 当前问题

`█░░░░░░░░░░░░░░░░░░░` 这种横条图，用户反馈像乱码。

### 改造方案 A（推荐 — 简单直接）：用 emoji 箭头 + 数字

```
当前涨跌分布：
  ↑ 看涨 0 只
  ↓ 看跌 30 只
  → 中性 0 只

模型把握度分布（共 30 只）：
  没把握（<0.3）  ███████████████  15 只 (50%)
  把握很弱（0.3~0.5）  ██████  6 只 (20%)
  把握一般（0.5~0.7）  █████  5 只 (17%)
  把握较强（>0.7）  ████  4 只 (13%)
```

注意：保留 ASCII 条但配数字 + 百分比 + 评级文字 = 用户能秒懂。

### 改造方案 B（HTML 增强）：彩色横条 + 数字

HTML 模板里加 CSS 横条：

```html
<div class="signal-distribution">
  <div class="signal-bar buy" style="width: 0%;"><span>↑ 看涨 0 只</span></div>
  <div class="signal-bar sell" style="width: 100%;"><span>↓ 看跌 30 只</span></div>
  <div class="signal-bar hold" style="width: 0%;"><span>→ 中性 0 只</span></div>
</div>
```

```css
.signal-bar { display: block; padding: 6px 12px; margin: 4px 0; min-width: 100px; color: white; font-weight: bold; }
.signal-bar.buy { background: #52c41a; }   /* 绿涨 */
.signal-bar.sell { background: #f5222d; }  /* 红跌（A股 习惯）*/
.signal-bar.hold { background: #bfbfbf; }  /* 灰持有 */
```

### 推荐 factor-integrator 选哪个

- **MD 模板**：用方案 A（ASCII + 数字 + 评级），跨平台兼容
- **HTML 模板**：用方案 B（CSS 彩色条），更直观

---

## §5 整体改造后报告结构（最终样子）

```
📅 A股 每日预测报告 — 2026-05-16
══════════════════════════════════

🎯 今日一句话总结  ← 新增！第一眼看到
  🎯 今日一句话：模型今天看跌 30 只 / 看涨 0 只 / 中性 0 只，平均把握 0.46 —— 把握很弱。
  🥇 如果非要选 1 只：隆基绿能（601012），但模型自己说 0.49 的把握。
  ⚠️ 诚信结论：模型今天对所有票都看跌，但置信度非常低（<0.5）—— 极可能是大盘整体震荡的体现，不要跟单。

⚠️ §1 诚信声明  ← 数字旁加翻译
  - ① 涨跌方向 AUC = 0.5131  →  📖 跟猜硬币差不多
  - ② 收益率预测 R² = -0.0019  →  📖 比"直接猜涨幅=0"还差一点点
  - ③ 横截面排名 rank-IC ≈ 0  →  📖 给 30 只票排名跟抓阄差不多
  - ④ 买卖信号 macro accuracy ≈ 33%  →  📖 跟"买/卖/不动"三选一瞎猜一样
  以上信号不构成投资建议……

§2 ① 涨跌方向（DirectionModel）
  当前涨跌分布（彩色横条 / ASCII 文字）：
    ↑ 看涨 0 只
    ↓ 看跌 30 只
    → 中性 0 只
  Top 5 看跌（按 P(涨) 升序）：
    600519 贵州茅台   P(涨)=0.41  ← 加中文名
    000858 五粮液     P(涨)=0.43
    300750 宁德时代   P(涨)=0.45
    ...
  📖 大白话：模型今天对 30 只全部看跌，但预测分（avg 0.46）都低于但接近 0.5，说明模型自己也没把握。
    不要跟单卖出 —— 很可能是大盘当天整体震荡的副作用。

§3 ② 收益率预测（ReturnRegressor）
  全部预测：
    600519 贵州茅台   预期 -0.003%
    000858 五粮液     预期 -0.001%
    ...
  📖 大白话：模型预测所有票涨跌幅都在 ±0.5% 之内，绝对值接近 0 —— 等于偷懒猜均值。

§4 ③ 横截面排名（RankingModel）
  Top 5 持仓候选（按 ranking score 降序）：
    601012 隆基绿能   score=0.92  ← 加中文名
    300059 东方财富   score=0.88
    002415 海康威视   score=0.85
    000858 五粮液     score=0.82
    300750 宁德时代   score=0.79
  📖 大白话：前 5 名（隆基 / 东财 / 海康 / 五粮液 / 宁德）按 ranking score 排，但 rank-IC≈0 相当于抓阄，不要当真。

§5 ④ 买卖信号（TradeSignalModel）
  buy 信号：（空）
  sell 信号：（空）
  hold 信号：30 只全部
  📖 大白话：今天 buy 信号 0 / sell 信号 0 / hold 30，模型不让你买任何东西也不让卖任何东西 —— 这是 Stage 4 设计的保守默认行为。

§6 历史准确率（P14）
  数据积累中（已有 0 天，需 ≥ horizon=5 天后才能开始评估）

§7 运行元数据
  生成时间 / 耗时 / 数据更新时间 / 模型文件路径
```

---

## 实施清单（factor-integrator 照搬）

### 改 3 个文件 + 新增 1 个

| 文件 | 改动 |
|---|---|
| **新建** `astock_quant/predict/ticker_names.py` | 30 只蓝筹 STAGE1_NAMES + STAGE1_SHORT_NAMES + `get_ticker_name()` 3 道 fallback + `get_ticker_short_name()` |
| `astock_quant/predict/renderer.py` | 加 5 个新 helper：`_render_today_summary()` / `_translate_auc()` / `_translate_r2()` / `_translate_rank_ic()` / `_translate_accuracy()` / `_render_plain_language(section, data)` / `_render_signal_distribution()` |
| `astock_quant/predict/templates/daily_report.html.template` | 加 `<div class="today-summary">` 在顶部 + 每个 § 末尾加 `<div class="plain-language">` + 诚信声明数字旁加翻译 + 彩色横条 CSS + 表格 ticker 改 `{code} {name}` |
| `astock_quant/predict/templates/daily_report.md.template` | 同款，MD 用方案 A（ASCII + 数字 + 评级）+ ticker 改 `{code} {name}` |

### 5 个新 helper 函数签名（factor-integrator 实现）

```python
# renderer.py

def _render_today_summary(predictions: dict, metrics: dict) -> dict:
    """生成今日总结 3 行，返回 {summary_line_1, summary_line_2, summary_line_3}."""

def _translate_metric(metric_name: str, value: float) -> str:
    """根据 metric 名 + 数值动态生成 📖 翻译。
    metric_name in {"auc", "r2", "rank_ic", "accuracy"}.
    """

def _render_plain_language(section: str, section_data: dict) -> str:
    """根据 section 名 + 数据动态生成大白话段。
    section in {"direction", "return", "ranking", "trade_signal"}.
    """

def _render_signal_distribution(predictions: list, style: str) -> str:
    """渲染信号分布 ASCII（style="md"）或 HTML 彩色横条（style="html"）."""
```

### 命门测试建议（traditional 写）

| 测试 | 守的不变量 |
|---|---|
| `test_today_summary_handles_all_hold` | 全 hold 场景生成「今天什么都别做」诚信结论 |
| `test_today_summary_handles_all_sell_low_conf` | 全 sell + 低置信度生成「不要跟单」结论 |
| `test_translate_auc_dynamic` | AUC=0.5131 → 「跟猜硬币差不多」；AUC=0.6 → 「真有点信号」 |
| `test_translate_r2_negative_gives_correct_verdict` | R²<0 → 「比猜均值还差」 |
| `test_get_ticker_name_hardcoded_30` | 30 只蓝筹全部查得到中文名 |
| `test_get_ticker_name_fallback_to_code` | akshare 都挂时返回 code 本身不抛异常 |
| `test_plain_language_section_generates_for_all_4_targets` | 4 个 section 都能生成大白话 |
| **`test_html_template_renders_today_summary_at_top`** | **HTML 报告里今日总结必须在诚信声明之前（最显眼位置）** |

---

## 跟之前 11 份人话报告纪律一脉相承

### 诚信红线持续

5 项改造里每一项都**不能掩盖**模型没 alpha 的事实：
- 顶部总结里直接写「但模型自己说 0.49 的把握」「不要跟单」
- 数字翻译里直接写「跟猜硬币差不多」「相当于抓阄」
- 大白话段里直接写「不构成投资建议」「不要当真」

跟报告 03 / 04 / 05 / 06 / 07 / 08 / 09 / 10 / 11 + Stage 1/2/3/4 收尾说明的诚信纪律完全一致。

### 类比文化持续

- 「跟猜硬币差不多」← 报告 04 用过
- 「相当于抓阄」← 类比报告 09 横截面 ranking 抓阄类比
- 「等于偷懒猜均值」← 报告 08 R² 类比

### 用户友好但不肉麻

避免「亲爱的用户」「希望您喜欢」这种没用的词。直接给信息 + 大白话 + 诚信红线。

---

## 一页纸总结

- **痛点**：用户看不懂技术术语（置信度数字 / AUC 数字 / 股票代码 / ASCII 条图），看完不知道"今天到底买不买什么"
- **5 项改造**：
  1. 顶部加「🎯 今日一句话总结」3 行（看跌/看涨数量 + 平均把握 + Top 1 推荐 + 诚信结论），用户只看顶部也能知道"该不该动手"
  2. 诚信声明每个数字旁加「📖 翻译」（动态评级，让未来 AUC 真提升时翻译跟着改）
  3. 股票代码全部加中文名（30 只蓝筹硬编码 + 沪深 300 fallback akshare + 缓存）
  4. 每个 § 末尾加「📖 大白话」段（根据本节数据动态生成，不硬编码）
  5. ASCII 条图改友好（MD 用 ASCII+数字+评级 / HTML 用彩色横条）
- **实施**：新建 `ticker_names.py`（30 只硬编码 + 3 道 fallback）+ 改 renderer.py 加 5 个 helper + 改 HTML/MD 两个模板
- **诚信红线持续**：所有翻译/总结/大白话段都不掩盖"模型没 alpha"，跟前 11 份报告 + 4 份收尾说明一脉相承
- **命门测试建议**：8 个守门测试（今日总结场景覆盖 / metric 翻译动态 / ticker 名 fallback / 大白话 4 section / HTML 模板顺序）
