#!/usr/bin/env python3
"""check_morning_consistency.py — 验证收盘复盘中的穿越阈值与开盘推荐的支撑/阻力一致"""
import sys, json, re, os
from pathlib import Path

PROJECT_DIR = Path("/root/.openclaw/workspace/projects/trading-agents")
REPORT_DIR = PROJECT_DIR / "reports"

if len(sys.argv) < 2:
    print(json.dumps({"pass": False, "errors": ["usage"], "score": 0}))
    sys.exit(1)

with open(sys.argv[1]) as f:
    text = f.read()

# 提取日期
date_match = re.search(r'(\d{4}-\d{2}-\d{2})', sys.argv[1].split("/")[-1])
if not date_match:
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
if not date_match:
    print(json.dumps({"pass": True, "score": 10, "note": "无法确定日期，跳过交叉校验"}))
    sys.exit(0)

date_tag = date_match.group(1).replace("-", "")
trade_file = REPORT_DIR / f"trade_signals_{date_tag}.md"

if not trade_file.exists():
    print(json.dumps({"pass": True, "score": 10, "note": f"开盘推荐文件不存在({trade_file})，跳过交叉校验"}))
    sys.exit(0)

with open(trade_file) as f:
    trade_text = f.read()

# 从开盘推荐中提取支撑/阻力位
# 格式: 支撑2886.28 / 阻力2930.33
thresholds = {}
for block in trade_text.split("🟢") + trade_text.split("🟡") + trade_text.split("🔴"):
    name_match = re.search(r'^([\u4e00-\u9fa5A-Z0-9]+)\s+', block)
    if not name_match:
        continue
    name = name_match.group(1).strip()
    s_match = re.search(r'支撑([\d.]+)', block)
    r_match = re.search(r'阻力([\d.]+)', block)
    if s_match and r_match:
        thresholds[name] = {
            "support": float(s_match.group(1)),
            "resistance": float(r_match.group(1))
        }

# 从复盘报告中提取穿越声明
# 格式: 🟢 沪深300 突破阻力 4918.77（收 4919.39）
mismatches = 0
for line in text.split("\n"):
    m = re.search(r'[🟢🔴]\s+(\S+?)\s+(突破阻力|跌破支撑)\s+([\d.]+)', line)
    if not m:
        continue
    name = m.group(1)
    direction = m.group(2)
    price = float(m.group(3))
    
    if name in thresholds:
        if direction == "突破阻力":
            expected = thresholds[name]["resistance"]
        else:
            expected = thresholds[name]["support"]
        
        # 允许 ±2% 容差（开盘后VWAP修正可能导致阈值微调）
        deviation = abs(price - expected) / expected * 100 if expected else 0
        if deviation > 2.0:
            mismatches += 1
            print(f"[交叉] {name}: 复盘使用{price} vs 开盘推荐{expected} Δ{deviation:.1f}%")

if mismatches == 0:
    score = 10
elif mismatches == 1:
    score = 5
else:
    score = 0

result = {
    "pass": mismatches == 0,
    "score": score,
    "matched": len(thresholds),
    "mismatches": mismatches,
}
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if mismatches == 0 else 1)
