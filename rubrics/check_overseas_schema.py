#!/usr/bin/env python3
"""check_overseas_schema.py — 外盘晨间研判格式完整性检查

用法: python3 rubrics/check_overseas_schema.py <brief_file>
exit code: 0=通过, 1=不通过
"""

import sys, re

REQUIRED_SECTIONS = [
    (r"【外盘晨间研判\s*[—\-]\s*\d{4}-\d{2}-\d{2}】", "标题"),
    (r"^\d+\.\s+", "至少1条编号信号"),
    (r"📊\s*综合研判", "综合研判"),
    (r"⚠️\s*风险提示", "风险提示"),
]

def main():
    report_file = sys.argv[1]
    with open(report_file) as f:
        text = f.read()
    
    errors = []
    for pattern, label in REQUIRED_SECTIONS:
        if not re.search(pattern, text, re.MULTILINE):
            errors.append(f"缺少章节: {label}")
    
    if errors:
        print(f"FAIL: {', '.join(errors)}")
        sys.exit(1)
    
    # 信号数量检查（至少2条）
    signals = re.findall(r"^\d+\.\s+", text, re.MULTILINE)
    if len(signals) < 2:
        print(f"FAIL: 信号数量不足({len(signals)}条，需要>=2)")
        sys.exit(1)
    
    print(f"PASS: {len(signals)}条信号, 4个必需章节完整")
    sys.exit(0)

if __name__ == "__main__":
    main()
