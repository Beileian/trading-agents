#!/usr/bin/env python3
"""
金桥量化交易推荐系统 v3.5.1

版本历史:
  v3.5.1 (2026-08-11): P0 修复 — 反幻觉校验 set -e 静默终止缺陷（8/11 盘前断供根因）
    - 问题陈述: 金桥盘前 8/11 断供——check_anti_hallucination 退出码1(有违规) + set -e 命令替换
      → 分析脚本在步骤1.5 静默死亡(无任何报错输出)，trade_signals 未生成，08:00 推送 ALERT 退出，
      watchdog 报 premarket_20260811.md 产出缺失
    - 修复: run_premarket_analysis.sh 步骤1.5 AH_RESULT 赋值加 || true 豁免——违规与否由 JSON
      violation_count 承载，退出码无额外信息；真实错误(JSON解析失败)→echo 99 走重试，不静默
    - 降级路径恢复: 违规→重新生成(最多2轮)→仍违规则标注低质量继续推送（作者原设计，set -e 使不可达）
    - 验证: bash -n 语法通过；mock check 退出1 → 重试3轮可达+「标注低质量继续」分支可达，脚本不中断

    - P0-A build_prompt 重构: 「具体风险」15字限制放开至60字+强制触发条件格式（"若跌破X则Y"）；
      注入反幻觉铁律（支撑/阻力必须具体价格、趋势判断必须指标数值、同一标的方向不得矛盾、禁模糊词）
    - P0-B 生成后校验闭环: run_premarket_analysis.sh 步骤1.5 接入 check_anti_hallucination veto——
      违规自动重新生成（最多2轮），仍违规则标注低质量继续推送（不阻断，防断供）
    - 配套: 修复3个评估器 bug（字段漂移/位置参数/跨标的误报），rubrics 恢复真实评估
    - 验证: 双模型A/B试点 8/7 数据 → Flash/M3 均 pass score 9.3（修复前 reject）；
      新 prompt 报告 check_anti_hallucination 违规数 2→0
  v3.4.2 (2026-08-06): 盘前/复盘全面评审 P1 三项修复（P0 v3.4.1 同批次评审延续）
    - P1-1 IMA 空文件链路: ima_pipeline node 绝对路径解析（环境变量→shutil.which→常见nvm位置），
      修复 cron 精简 PATH 下裸 "node" FileNotFoundError → 08-03~06 连续 4 天空文件根因
    - P1-2 复盘净值渲染时序: 虚拟盘快照更新改为按 code 复用 paper_trading._fetch_close_price
      （ETF 走腾讯实时行情），替代按 name 精确匹配 close_snapshot——原逻辑漏掉 ETF 持仓
      （"科创50(科创50ETF华夏)" 匹配不上快照 key "科创50"），报告净值与 paper_trading close 后不一致（1.0570 vs 1.0577）
    - P1-3 复盘标题质量标记: run_closing_push.sh 捕获 Rubrics JSON（verdict/score），
      REJECT/LOW_CONFIDENCE 时在推送标题前注入 "⚠️ 低质量/低置信度 · Rubrics X" 标记，
      与盘前推送 RUBRIC_TAG 对称（原实现只 echo 日志，复盘标题永无标记）

  v3.4.1 (2026-08-06): 盘前/复盘系统全面评审 P0 修复（评审来源：托董全面评审+蓝红蓝，红队16条攻击16/16成立）
    - P0-1 veto 泛化: run_rubrics.py 聚合器改为动态读取全部 fail_action=reject_and_retry 项，
      替代硬编码 schema_validity/action_consistency（原缺陷: closing_review.json 的
      data_accuracy=veto 被当普通权重 0.25 加权，6.1分 pass 无标记直接推送）
    - P0-2 方向解析重写: load_morning_synthesis 改为前缀显式声明优先，
      修复「多维度偏多（…TimesFM多数偏空…）」被括号内描述词反噬误判偏空（08-03 假✅根因）
    - P0-3 外盘注入竞态修复: run_premarket_analysis.sh 外盘文件就绪轮询等待 120s
      （morning_brief 06:36 落盘 vs 盘前 06:35 启动竞态，08-06 外盘信号漏注入）
    - P0-4 复盘外盘正则修复: 兼容「方向：偏多」「置信度：中」带冒号格式
      （原正则不匹配 → 08-05/08-06 复盘连续误报“外盘信号未生成”）
    - P0-5 Rubric C 评分管道: 解析真实 score（原 grep pass 写死 10.0，掩盖内部 7.3）
    - P0-6 状态文件写入修复: bash 布尔注入 true/false→Python NameError，错误被 2>/dev/null 吞掉；
      改为参数传递 + os.path.exists + 错误可见
    - P0-7 缠论回测伪统计修复: backtest_proxy success 条件在严格交替笔序列中结构性永假
      （up 后必是 down → 延续率恒 0%），改为突破后 6 笔内创新高判定
    - 配套: check_closing_accuracy.py 无穿越日误报修复（报告写“无”时视为 0 条）

  v3.4.0 (2026-08-05): 标的检测链路评审落地 P0-P2（评审来源：托董标的检测提醒全面评审）
    - P0-1 冻结识别强化: price_fetcher 冻结状态跨运行持久化(price_freeze_state.json)，
      连续3轮价格不变→warning、5轮→stale；昨收匹配增强怀疑；
      源整体冻结占比≥80%→自动降级30分钟；踢源逻辑改为优先保留变化源
    - P0-2 穿越判定源一致性门: price_watch 主备源偏差>1.5%时穿越预警降级为状态提示(待确认)，
      杜绝临界误报（实证: 沪深300 eastmoney 4675.38 vs 阻力4675.25 的0.13临界触发）
    - P1-3 预警命中率闭环: price_watch 每条预警写 JSONL(触发源/价格/时间戳/偏差)，
      closing_review 收盘比对 → intraday_alert_hit_rate 指标 + 复盘摘要反馈
    - P1-4 噪音控制: 异动信号(935/开盘/尾盘)午后(≥14点)起转状态提示合并推送，
      去重改为 slot 维度(early/mid/afternoon/tail) + 命名空间隔离(穿越vs降级)
    - P2-5 event_chain 接入调度(08:10 周一至五 --auto，数据源修复为 morning_brief)；
      send_to_dingtalk 密钥强校验(硬编码→.env，缺失即报错)
    - P2-6 SINA_MAP 与 WATCHLIST 对齐: price_watch 补机器人ETF(异动检测覆盖)

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
