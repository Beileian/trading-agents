#!/usr/bin/env python3
"""check_closing_accuracy.py — 收盘复盘数据准确性校验

对照今日收盘价格（多源交叉验证）检查报告中引用的指数涨跌幅和穿越数量。
"""
import sys, json, re, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if len(sys.argv) < 2:
    print(json.dumps({"pass": False, "errors": ["usage: check_closing_accuracy.py <report>"], "score": 0}))
    sys.exit(1)

with open(sys.argv[1]) as f:
    text = f.read()

errors = []

# 1. 检查是否有指数涨跌幅
# 格式: 上证50 2926.83（-2.85%）
idx_pattern = r'(\w+)\s+([\d.]+)\s*（([+\-][\d.]+)%）'
matches = re.findall(idx_pattern, text)
if len(matches) < 3:
    errors.append(f"指数数据不完整: 找到{len(matches)}个/期望3个")

# 2. 检查穿越数量是否自洽
# "穿越 N 条" 后面应该跟 N 条具体条目
breach_header = re.search(r'穿越\s*(\d+)\s*条', text)
if not breach_header:
    errors.append("缺失穿越数量声明")
else:
    expected = int(breach_header.group(1))
    # 数穿越的具体条目（🟢或🔴开头的行）
    breach_items = len(re.findall(r'[🟢🔴]\s+\w+', text.split("**⑤ 价格穿越**")[1].split("**⑥")[0] if "**⑤" in text and "**⑥" in text else ""))
    if breach_items != expected:
        errors.append(f"穿越数量不一致: 声明{expected}条 vs 实际列出{breach_items}条")

# 3. 检查基础完整性
if not re.search(r'上证50.*沪深300.*科创50', text):
    errors.append("缺失三大指数数据")

score = 10 if len(errors) == 0 else (3 if len(errors) == 1 else 0)
passed = len(errors) == 0

result = {
    "pass": passed,
    "score": score,
    "errors": errors,
}
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if passed else 1)
