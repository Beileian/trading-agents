#!/usr/bin/env python3
"""check_analysis_completeness.py — 检查分析报告格式完整性

检查 trading_analysis_*.md 中每只标的是否包含:
  1. 技术指标概览表（含 MA5/MA20/MA60/RSI）
  2. DeepSeek 交易决策七维度表
  3. 简要理由非空

用法:
  python3 rubrics/check_analysis_completeness.py <report_file>
输出: JSON {pass, score, errors}
exit code: 0=通过, 1=不通过
"""

import sys, os, re, json

SYMBOLS = [
    "000016.SH", "000300.SH", "000688.SH",
    "601288.SH", "601988.SH", "600036.SH",
    "600795.SH", "000066.SZ", "600562.SH",
    "562500.SH",
]

REQUIRED_FIELDS = ["最新价", "MA5", "MA20", "MA60", "RSI"]
DECISION_FIELDS = ["趋势判断", "支撑位", "阻力位", "交易建议", "建议仓位", "行业背景", "具体风险", "简要理由"]

with open(sys.argv[1]) as f:
    text = f.read()

errors = []
matched = 0

for sym in SYMBOLS:
    start = text.find(f"### {sym}")
    if start < 0:
        errors.append(f"{sym}: 未找到标的小节")
        continue

    # 提取该标的的 section
    section = text[start:]
    for other in SYMBOLS:
        if other != sym:
            pos = section.find(f"### {other}", 10)
            if pos > 0:
                section = section[:pos]
                break

    # 检查技术指标表
    missing_fields = []
    for field in REQUIRED_FIELDS:
        if field not in section:
            missing_fields.append(field)
    if missing_fields:
        errors.append(f"{sym}: 技术指标表缺字段 {missing_fields}")

    # 检查决策表
    missing_decisions = []
    for field in DECISION_FIELDS:
        if field not in section:
            missing_decisions.append(field)
    if missing_decisions:
        errors.append(f"{sym}: 决策表缺字段 {missing_decisions}")

    # 简要理由非空
    m = re.search(r'\*\*简要理由\*\*\s*\|\s*(.+?)(?:\n\n|---|\n\*\*)', section, re.DOTALL)
    if not m or not m.group(1).strip() or m.group(1).strip() == '（无）':
        errors.append(f"{sym}: 简要理由为空")

    matched += 1

if not errors:
    result = {"pass": True, "score": 10, "matched": matched}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
else:
    score = max(0, 10 - len(errors))
    result = {"pass": False, "score": score, "matched": matched, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)
