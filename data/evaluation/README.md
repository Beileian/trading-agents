# 金桥认知闭环评估数据集 v1.0

## 数据来源

- `cognition_daily/*.json` — 每日收盘复盘结构化记录
- `cognition_state.json` — 指标滚动累计
- `overseas-morning-brief/reports/*signal*.md` — 外盘晨间研判
- `trading_memory.md` — LLM投资决策全文

## 数据结构（cognition_dataset.jsonl）

每行一条 JSON，字段：

| 字段 | 含义 | 来源 |
|------|------|------|
| `date` | 交易日 | 自动 |
| `input.overseas_signal` | 盘前外盘研判方向（偏多/偏空/中性） | overseas_signal |
| `input.temperature` | 市场温度五指标信号 | cognition_daily |
| `input.trade_recommendations` | 交易推荐摘要 | closing_review |
| `output.*_chg` | 三大指数实际涨跌幅 | 新浪实时 |
| `output.direction_match` | 外盘研判方向是否吻合 | closing_review |
| `output.extreme_stocks` | 极端波动标的（+/-3%） | closing_review |
| `output.sell_wrong_names` | 卖出建议但收涨的标的 | closing_review |
| `output.breach_names` | 价格穿越触发标的 | closing_review |
| `cognitive_tag` | 当日市场风格标签 | closing_review |

## 评估维度（供LLM分析使用）

1. **外盘研判准确率**：direction_match = "吻合" 的天数占比
2. **温度计信号准确率**：各指标信号与实际后市的对应关系
3. **卖出建议准确率**：卖出标的实际是否收跌
4. **极端波动预测**：是否被提前捕捉到
5. **认知标签一致性**：tag 是否真正描述了当日特征

## 渐进计划

- Step 1（当前）：手动构建评估数据集，积累 2-3 周数据
- Step 2：LLM 每日收盘后分析偏差但不自动执行
- Step 3（30+交易日后）：夜间自动实验循环
