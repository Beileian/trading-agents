#!/usr/bin/env python3
"""
金桥量化交易推荐系统 v2.5.0

版本历史:
  v2.5.0 (2026-06-17): 数据准确性基础设施加固 — 三重校验体系 + 外盘自包含 + 经验沉淀
    - 收盘复盘 v2.6.0: 新浪时间戳校验 + 腾讯多源交叉 + 数据源溯源摘要
    - 开盘推送 v2.4.0: 实时开盘价拉取（腾讯主源+新浪交叉+昨收fallback）
    - 日线缓存 v1.1: 新浪失败时收盘快照兜底
    - IMA观点: 线性衰减→等比衰减 (0.85^days)
    - 价格穿越: 钉钉机器人API直推 + 去重跨日隔离
    - 阈值解析: 段落式格式兼容
    - 外盘信号: 自包含化（金桥仓库独立处理+降级信号）
    - 经验沉淀: LESSONS.md (6条结构化教训)
  v2.3.0 (2026-06-12): Schema校验 + 自动重试 + 指数前置查询
  v2.2.0: 外盘+IMA三认知回路
  v2.1.0: 乖离率体系重构
  v2.0.0: 金桥项目独立化 (Beileian/trading-agents)
  v1.x: 初版迭代

组件版本:
  generate_trade_signals.py  v2.4.0  开盘推送（实时开盘价+Schema校验+自动重试）
  closing_review.py          v2.6.0  收盘复盘（三重校验+数据源溯源）
  price_watch.py             v2.1.0  盘中价格穿越（钉钉直推+跨日去重）
  update_daily_cache.py      v1.1    日线缓存（新浪+快照兜底）
  extract_ima_opinions.py    v1.1    IMA观点提取（等比衰减）
  paper_trading.py           v1.1    虚拟盘（快照收盘价）
  extract_signal.py          v2.0    外盘信号提取（金桥自包含+降级）
  send_to_dingtalk.py        v1.0    钉钉推送工具

架构:
  数据采集层: 新浪/腾讯实时行情 + IMA知识库 + 外盘研判
  缓存层: 新浪日K线(主) + 收盘快照兜底
  处理层: 技术分析(乖离率+支撑阻力) + LLM研判
  输出层: 钉钉机器人API直推 + Markdown报告留存

Cron 调度 (BJT):
  08:05  外盘信号提取推送
  08:55  开盘前交易推荐
  09:30-15:00 盘中价格穿越预警 (每5分钟)
  15:30  收盘复盘

项目仓库: https://github.com/Beileian/trading-agents
"""
