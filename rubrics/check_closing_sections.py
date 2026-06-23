#!/usr/bin/env python3
"""check_closing_sections.py — 检查收盘复盘报告的8个必需章节"""
import sys, re, json

REQUIRED_SECTIONS = [
    r"\*\*① 今日指数\*\*",
    r"\*\*② 外盘验证\*\*",
    r"\*\*③ 极端波动\*\*",
    r"\*\*④ 推荐方向 vs 实际收盘方向\*\*",
    r"\*\*⑤ 价格穿越\*\*",
    r"\*\*⑥ 市场温度计\*\*",
    r"\*\*⑦ 本周认知\*\*",
    r"\*\*⑧ 虚拟盘\*\*",
]

if len(sys.argv) < 2:
    print(json.dumps({"pass": False, "errors": ["usage: check_closing_sections.py <report>"], "score": 0}))
    sys.exit(1)

with open(sys.argv[1]) as f:
    text = f.read()

found = 0
missing = []
for pattern in REQUIRED_SECTIONS:
    if re.search(pattern, text):
        found += 1
    else:
        # 提取章节名
        name = re.search(r'[①②③④⑤⑥⑦⑧]\s*(.+)', pattern)
        missing.append(name.group(1) if name else pattern)

score = max(0, 10 - (8 - found) * 1.5)
passed = found >= 7

result = {
    "pass": passed,
    "score": round(score, 1),
    "found": found,
    "total": 8,
    "missing": missing,
}
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if passed else 1)
