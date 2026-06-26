# 金桥项目 AGENTS.md

本文件覆盖金桥系统（trading-agents + overseas-morning-brief）的项目级约束。
与全局 `~/openclaw/workspace/AGENTS.md` 叠加生效，项目级规则覆盖/细化全局规则。

---

## 🔬 反幻觉铁律（项目级强化版）

适用：任何涉及技术分析、个股/指数研判的输出，包括 LLM 生成、对话回复、群聊推送。

1. **数字来源绑定**：所有价格/涨跌幅/乖离率/PE/市值等数字，必须标注来源（日线缓存/Sina实时/akshare/close_snapshot/公告数据）。
2. **技术位证据链**：支撑位/阻力位必须引用 **具体日期区间+价格范围**。禁止 "XX附近是前期平台"、"XX区域是成交密集区"。
3. **结论诚实性**：从数据推导结论，禁止预设方向后编造依据。无法形成明确判断时必须说 "方向不明确"。
4. **交叉验证**：关键数字至少两个独立源验证。实时价格优先新浪交叉腾讯，静态数据优先 close_snapshot。
5. **禁止"大概/附近/平台/密集区"模糊词**：没有具体日期和价格的技术位描述等于没描述。

事故记录：
- 2026-06-26: 中国移动分析 "86附近是4月初平台" — 4月实际最低92.70

---

## ⏰ 推送时间与质量优先

- 推送时间围绕「信息完整性 > 时效性」原则
- 外盘研判 06:30，开盘前分析 08:00
- rubric retry 不与推送时间冲突——低置信度标记继续推送，修正版后补
- 所有推送脚注必须包含版本号+commit hash

---

## 📋 Rubrics 体系

项目内 rubrics 目录：`rubrics/`

| Rubric | 评估对象 | 关键项 |
|--------|----------|--------|
| trade_signals.json | 开盘前信号格式 | schema(veto), factual(high), data_timeliness(high), action_consistency(high) |
| trade_recommendation.json | 分析报告深度 | analysis_completeness(veto), logic(medium), timeliness(high), risk(medium), consistency(high) |
| closing_review.json | 收盘复盘 | completeness(high), accuracy(veto), insight(medium), morning_consistency(high) |
| overseas_morning.json | 外盘研判 | schema(veto), factual(high), coherence(high), conclusion(medium) |
| anti_hallucination.json | 分析输出反幻觉 | traceability(veto), evidence(high), honesty(high) |

所有 rubrics 通过 `rubrics/run_rubrics.py` 统一调用，支持 script + LLM 双评判。

---

## 📡 数据流

```
每日日线缓存 (data/cache/*-daily.csv)
  → update_daily_cache.py (新浪K线)
  → 收盘复盘 closing_review.py
    → close_snapshot_{date}.json (经 data_accuracy rubric)
    → 开盘前推送 run_premarket_push.sh
      → 第③保障: close_snapshot fallback
      → 前置一致性检查: check_morning_consistency.py
```

关键修复记录：
- 2026-06-26: fetch_real_open_prices() fallback 链增加 close_snapshot（修复收盘价T-2问题）

---

## 🔗 子项目

- `trading-agents/` — A股分析+推送+虚拟盘
- `overseas-morning-brief/` — 外盘研判生成
- 两个项目共享 rubrics 评估体系（外盘研判引用 trading-agents/rubrics/）

---

*金桥项目 AGENTS.md v1.0.0 | 2026-06-26 创建*
