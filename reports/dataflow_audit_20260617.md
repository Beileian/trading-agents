# 金桥系统数据流依赖审计报告

> 版本: v2.5.0 | 日期: 2026-06-17 | 状态: 首次全景审计

---

## 一、数据流全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                       数 据 源 层                               │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│ 新浪行情      │ 腾讯行情      │ IMA知识库     │ overseas-brief     │
│ hq.sinajs.cn │ qt.gtimg.cn  │ (内部API)    │ morning_brief      │
└──┬───────────┴──┬───────────┴──┬───────────┴──┬─────────────────┘
   │              │              │              │
   ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       处 理 层                                   │
│                                                                 │
│  ① update_daily_cache ──→ data/cache/*.csv                      │
│  ② trading_analysis ──→ trading_analysis_{date}.md              │
│  ③ extract_ima_opinions ──→ opinions_{date}.md                  │
│  ④ extract_signal ──→ overseas_signal_{date}.md                 │
│  ⑤ generate_trade_signals ──→ trade_signals_{date}.md           │
│  ⑥ closing_review ──→ closing_review_{date}.md                  │
│  ⑦ paper_trading ──→ paper_state.json                          │
│  ⑧ price_watch ──→ 钉钉直推                                    │
│  ⑨ style_rotation_signals ──→ 风格轮动报告                      │
│                                                                 │
└──┬──────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│                       推 送 层                                   │
│  send_to_dingtalk.py ──→ 钉钉机器人API ──→ 谈股论金奔富群        │
│  (price_watch / closing_review / premarket_push 均调用)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、六条主链路逐条走查

### 链路A: 外盘研判
```
overseas-morning-brief (cron 08:00)
  → morning_brief_{date}.md
    → extract_signal.py (金桥侧 08:05)
      → overseas_signal_{date}.md (金桥 reports/)
        → generate_trade_signals.py (08:55 读取)
          → trade_signals_{date}.md
            → 推送
```
**状态**: ✅ 已解耦（v2.5.0）。extract_signal 在金桥仓库独立运行。
**风险**: morning_brief 未生成时降级到新浪实时美股指数，降级信号精度低于AI研判。

### 链路B: IMA观点
```
IMA知识库 (extract_ima_opinions.py, 跑在 run_premarket_push.sh 内)
  → opinions_{date}.md
    → summarize_ima_opinions.py (DeepSeek摘要)
      → generate_trade_signals.py (08:55 读取)
        → trade_signals_{date}.md
```
**状态**: ⚠️ 同一环节用了两个Python脚本（extract + summarize），而且有重复的DeepSeek API调用。
**问题**: extract_ima_opinions.py 和 summarize_ima_opinions.py 功能边界模糊——前者提取+时间衰减，后者再摘要一次。是否应该合并？

### 链路C: 技术分析
```
update_daily_cache.py (新浪K线, 08:55前执行)
  → data/cache/*.csv
    → run_premarket_push.sh 先跑 verify_cache_sync.py 校验
      → trading_analysis_latest.py (DeepSeek LLM)
        → generate_trade_signals.py
```
**状态**: ⚠️ 有两个 trading_analysis 脚本 (latest + 20260604)，功能重叠。`trading_analysis_20260604.py` 是带日期后缀的旧版。
**冗余**: 缓存校验 `verify_cache_sync.py` 是独立的一步，应该合并进 `update_daily_cache.py` 的末尾自动执行。

### 链路D: 实时行情（多路复用）
```
新浪/腾讯行情
  ├── price_watch.py (每5分钟) → 支撑/阻力穿越 → 钉钉直推
  ├── generate_trade_signals.py (08:55) → 开盘价覆盖 → 推送
  └── closing_review.py (15:30) → 收盘价 → close_snapshot → paper_trading
```
**状态**: ⚠️ 三个脚本独立拉取同一数据源，没有共享缓存。每次各拉一次完整的 `hq.sinajs.cn` 请求。

### 链路E: 收盘复盘
```
closing_review.py (15:30)
  ├── 新浪/腾讯拉收盘价
  ├── load_thresholds(trade_signals) → 穿越检测
  ├── load_overseas_direction(overseas_signal) → 方向验证
  ├── close_snapshot.json → paper_trading.py 复用
  ├── paper_state.json (更新浮盈)
  └── closing_review_{date}.md → 推送
```
**状态**: ✅ v2.6.0 加固。快照传递消除了 paper_trading 的独立新浪请求。
**历史债**: paper_trading.py 的 `_fetch_close_price` 三个 fallback 层（快照→缓存→新浪），后两层已在 closing_review 中覆盖但逻辑仍保留在 paper_trading 中未清理。

### 链路F: 推送
```
send_to_dingtalk.py (钉钉机器人API)
  被调用方:
  - price_watch.py (直接用 requests 调API, 不经过 send_to_dingtalk!)
  - run_closing_push.sh (通过 send_to_dingtalk.py)
  - run_premarket_push.sh (通过 send_to_dingtalk.py)
  - run_overseas_push.sh (通过 send_to_dingtalk.py)
```
**状态**: ⚠️ 不一致。price_watch.py 自己写了钉钉API调用（包含 appKey/secret/CID 硬编码），而其他三个 shell 走 send_to_dingtalk.py。同样一套密钥散落在两个地方。

---

## 三、识别的问题

### 🔴 P0 — 严重

**P0-1: 钉钉推送密钥硬编码三份**
- price_watch.py 内联了 appKey/secret/CID
- send_to_dingtalk.py 内联了 appKey/secret/CID
- 两处完全相同的魔法字符串，修改一处遗漏另一处
- **建议**: 统一为 `.env` 文件读取，所有脚本从同一个地方取

**P0-2: run_premarket_push.sh 和 run_closing_push.sh 仍引用 overseas 项目的 send_to_dingtalk.py**
```bash
# run_closing_push.sh 第22行:
PUSH_SCRIPT="$SCRIPT_DIR/send_to_dingtalk.py"
# 这个是金桥本地的，正确 ✓
# 但 auto_analysis.sh 里还有:
OVERSEAS_DIR="/root/.openclaw/workspace/projects/overseas-morning-brief"
# 引用 overseas 的 extract_signal.py ❌
```
**实际验证**: run_closing_push.sh 和 run_premarket_push.sh 已指向金桥本地 `send_to_dingtalk.py` ✅。但 `auto_analysis.sh` 仍引用 overseas 路径。需要确认 auto_analysis.sh 是否还被任何 cron 调用。

**P0-3: trading_analysis 存在两个版本**
- `trading_analysis_latest.py` — 活跃使用
- `trading_analysis_20260604.py` — 疑似废弃，但仍在 scripts/ 目录
- run_premarket_push.sh 明确调用的是 `trading_analysis_latest.py`，旧版是孤儿脚本

### 🟡 P1 — 中等

**P1-1: 同一新浪行情被5个脚本独立请求**
每次执行时间是错开的（08:55, 09:30-15:00每5分钟, 15:30），所以不是并发冗余。但错误处理和降级逻辑在每个脚本中重复实现——6个脚本各有自己的 try/except 和 fallback。

**建议**: 提取共享的行情拉取工具模块 `market_data.py`，统一：
- `fetch_realtime_price(code)` → 新浪主源 + 腾讯交叉 + 时间戳校验
- 降级/重试/超时策略在一处配置

**P1-2: extract_ima_opinions + summarize_ima_opinions 两段式处理**
- extract 先提取+去重+时间衰减
- summarize 再调 DeepSeek 摘要
- 两次独立执行，但 DeepSeek API key 在两个脚本中分别读取

**建议**: 合并为一个脚本 `ima_pipeline.py`，一步完成提取→衰减→摘要

**P1-3: paper_trading.py 的三层 fallback 中后两层实际已废弃**
```python
_fetch_close_price:
  ① 快照JSON → ② 日线缓存 → ③ 新浪实时行情
```
第②③层在 closing_review 已成功后永远不会被调用，但代码和错误处理逻辑仍保留，增加了维护成本。

### 🟢 P2 — 低风险

**P2-1: style_rotation_signals.py 独立存在，不参与任何 cron**
它是独立脚本，拉取腾讯PE数据做风格轮动分析，但产出没有被任何其他脚本消费。
**建议**: 要么接入 generate_trade_signals（市场温度计环节），要么归档。

**P2-2: verify_cache_sync.py 是独立检查步骤**
在 run_premarket_push.sh 中作为独立一步执行，其功能（缓存过期检测）应该在 update_daily_cache.py 内部自动完成。

**P2-3: auto_analysis.sh 可能的孤儿**
引用了 overseas 项目路径的 extract_signal.py（旧路径），而新版 extract_signal.py 已在金桥仓库。需要确认此脚本是否有 cron 引用。

**P2-4: 认知闭环滚动指标从未填充**
`cognition_state.json` 中 `rolling_metrics: {}` 始终为空。closing_review.py 有认知状态记录但只写了 `last_trade_date`，方向准确率/触发命中率从未统计。

---

## 四、优化路线图

| 优先级 | 项目 | 改动范围 | 预期效果 |
|--------|------|---------|---------|
| **P0** | 密钥统一环境变量 | send_to_dingtalk.py + price_watch.py | 消除硬编码，一处修改全局生效 |
| **P0** | 清理孤儿脚本 | 删除 trading_analysis_20260604.py + 确认 auto_analysis.sh 状态 | 减少混淆 |
| **P1** | 提取行情工具模块 | 新建 scripts/market_data.py | 消除6处重复的拉取/校验/降级逻辑 |
| **P1** | 合并 IMA pipeline | extract + summarize → ima_pipeline.py | 减少一次 DeepSeek API 调用 |
| **P1** | 清理 paper_trading 废弃 fallback | 删除缓存/新浪层 | 减少代码复杂度 |
| **P2** | style_rotation 接入推送 | 接入市场温度计 | 利用已有分析产出 |
| **P2** | 缓存校验内化 | verify → update_daily_cache 尾部 | 减少 shell script 步骤数 |
| **P2** | 启用滚动指标统计 | closing_review 加方向准确率追踪 | 可量化策略质量 |

---

## 五、依赖关系图（有向）

```
cron:scheduler
├── cron:08:05 → run_overseas_push.sh
│   └── extract_signal.py ← morning_brief (overseas项目输出)
│       └── overseas_signal.md
├── cron:08:55 → run_premarket_push.sh
│   ├── update_daily_cache.py ← 新浪K线
│   ├── verify_cache_sync.py
│   ├── extract_ima_opinions.py ← IMA知识库
│   ├── summarize_ima_opinions.py ← DeepSeek
│   ├── trading_analysis_latest.py ← DeepSeek + 缓存 + IMA + 外盘
│   │   └── trading_analysis.md
│   ├── generate_trade_signals.py ← trading_analysis + overseas_signal + 实时行情
│   │   └── trade_signals.md
│   └── send_to_dingtalk.py → 钉钉群
├── cron:09:30~15:00(每5分钟) → price_watch.py ← 新浪实时行情 → 钉钉群(直推)
└── cron:15:30 → run_closing_push.sh
    ├── closing_review.py ← 新浪/腾讯 + trade_signals + overseas_signal
    │   ├── close_snapshot.json
    │   └── closing_review.md
    ├── paper_trading.py close ← close_snapshot.json
    └── send_to_dingtalk.py → 钉钉群
```

---

*审计完成时间: 2026-06-17 22:15 BJT*
*下次审计建议: v2.6.0 发布前，确认 P0-P1 各项已关闭*
