#!/usr/bin/env python3
"""
金桥量化交易推荐系统 v3.3.0

版本历史:
  v3.3.0 (2026-07-09): P0 稳定性 — 并发技术分析 + 断点续跑
    - trading_analysis_concurrent.py v1.0.0: ThreadPoolExecutor 并发分析（MAX_WORKERS=5）
      - 10只标的从串行6-10分钟压缩到~1分钟
      - 每个标的独立 DeepSeek API 调用，45s 超时 + 2次重试
      - fallback 机制：单标的失败不影响整体报告
    - run_premarket_analysis.sh v3.1.0:
      - 断点续跑：analysis/opinions/trade_signals 已存在则跳过
      - 重试时使用并发版脚本替代旧串行版
      - 变量作用域清理（移除重复定义）
  v3.2.1 (2026-06-27): P0 逆向+冲突信号共振 — 外盘与A股趋势背离时上调置信度
    - generate_trade_signals.py: build_synthesis_paragraph() Rule 0 逆向+冲突检测
    - closing_review.py: update_cognition_state() 新增 a_share_actual_direction 字段
    - 逻辑: 温度与A股趋势一致 + 外盘反向 → 跟随外盘方向，标注高置信
    - 背景: 本周认知复盘发现06-26命中恰好是逆向+冲突同日出现
  v3.2.0 (2026-06-26): rubrics v3.2 反幻觉体系 + 东方财富API走VPS代理
    - anti_hallucination.json: 数字溯源(veto) + 技术位证据链(high) + 叙述诚实性(high)
    - 中国移动事故写入，驱动反幻觉rubric创建
  v3.1.0 (2026-06-26): 前置一致性检查 + 推送脚注注入git commit hash
  v3.0.0 (2026-06-25): rubrics v3.0 归一化评分标准(0-10分) + 收盘复盘全章节覆盖
  v2.6.5 (2026-06-25): closing_review.py 三修复 + 本周认知升级
  v2.6.0 (2026-06-24): 收盘复盘 v2.6 三重校验 + 仓位联动 + 版本标注
  v2.5.5 (2026-06-25): 价格硬保护 — LLM幻觉偏差>1%强制校准
  v2.5.3 (2026-06-25): 价格硬保护 — LLM幻觉偏差>1%强制校准
  v2.5.2 (2026-06-25): 中证机器人→机器人ETF名称修正
  v2.5.1 (2026-06-24): Rubrics 评审体系升级 — 双套标准 + TimesFM时效性门禁
    - rubrics v3.2.0: 分析报告5维度(analysis_completeness + LLM×3 + data_timeliness)
    - rubrics v1.0.0: 信号格式4维度(schema + factual + timeliness + action_consistency)
    - 新增 check_data_timeliness.py: TimesFM校准数据14天时效性检查(high级)
    - 新增 check_analysis_completeness.py: 分析报告格式完整性检查(veto)
    - run_rubrics.py: LLM评判加3次重试(2s/4s退避) + --rubric参数支持
    - risk_specificity prompt对齐实际输出: 接受混合风险(技术面+基本面)
    - 信号表恢复仓位字段: 支撑/阻力行上方显示仓位百分比
    - trading_analysis prompt风险边界: 技术指标形态优先→基本面补充
    - IMA空内容兜底: 观点为空时跳过外部参考section
    - run_premarket_push.sh: 步骤3.5分Rubric A+B双通道评估
    - 收盘复盘 v2.6.0: 新浪时间戳校验 + 腾讯多源交叉 + 数据源溯源摘要
    - 开盘推送 v2.4.0: 实时开盘价拉取（腾讯主源+新浪交叉+昨收fallback）
    - 日线缓存 v1.1: 新浪失败时收盘快照兜底
    - IMA观点: 线性衰减→等比衰减 (0.85^days)
    - 价格穿越: 钉钉机器人API直推 + 去重跨日隔离
    - 阈值解析: 段落式格式兼容
    - 外盘信号: 自包含化（金桥仓库独立处理+降级信号）
    - 经验沉淀: LESSONS.md (6条结构化教训)
  v2.5.3 (2026-06-25): 价格硬保护 — LLM幻觉偏差>1%强制校准
  v2.5.2 (2026-06-25): 中证机器人→机器人ETF名称修正
  v2.5.1 (2026-06-24): Rubrics 评审体系升级 — 双套标准 + TimesFM时效性门禁
    - rubrics v3.2.0: 分析报告5维度(analysis_completeness + LLM×3 + data_timeliness)
    - rubrics v1.0.0: 信号格式4维度(schema + factual + timeliness + action_consistency)
    - 新增 check_data_timeliness.py: TimesFM校准数据14天时效性检查(high级)
    - 新增 check_analysis_completeness.py: 分析报告格式完整性检查(veto)
    - run_rubrics.py: LLM评判加3次重试(2s/4s退避) + --rubric参数支持
    - risk_specificity prompt对齐实际输出: 接受混合风险(技术面+基本面)
    - 信号表恢复仓位字段: 支撑/阻力行上方显示仓位百分比
    - trading_analysis prompt风险边界: 技术指标形态优先→基本面补充
    - IMA空内容兜底: 观点为空时跳过外部参考section
    - run_premarket_push.sh: 步骤3.5分Rubric A+B双通道评估
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
  generate_trade_signals.py  v2.5.0  开盘推送（Rubrics规则门控+Schema校验+自动重试+仓位显示+逆向冲突检测）
  closing_review.py          v3.2.1  收盘复盘（三重校验+仓位联动+版本标注+A股方向记录）
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
