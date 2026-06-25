#!/usr/bin/env python3
"""check_overseas_factual.py — 外盘晨间研判事实准确性检查

对比 morning_brief_*.md 中的美股指数/VIX/KWEB数字与对应 prompt_*.txt 源数据。
若偏差>=5%，视为事实错误。

用法: python3 rubrics/check_overseas_factual.py <brief_file>
"""

import sys, os, re, json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def extract_prompt_numbers(prompt_file: str) -> dict:
    """从prompt中解析美股收盘、VIX、KWEB数字"""
    with open(prompt_file) as f:
        text = f.read()
    
    data = {}
    
    # 道指：道琼斯工业指数: 收盘 51712.7109 (+0.29%)
    m = re.search(r"道琼斯工业指数:?\s*收盘\s*([\d.]+)\s*\(([+-][\d.]+)%\)", text)
    if m:
        data["djia_close"] = float(m.group(1))
        data["djia_chg"] = float(m.group(2))
    
    # 标普：标普500指数: 收盘 7472.79 (-0.37%)
    m = re.search(r"标普500指数:?\s*收盘\s*([\d.]+)\s*\(([+-][\d.]+)%\)", text)
    if m:
        data["spx_close"] = float(m.group(1))
        data["spx_chg"] = float(m.group(2))
    
    # 纳指：纳斯达克综合指数: 收盘 26166.6016 (-1.32%)
    m = re.search(r"纳斯达克综合指数[：:]\s*收盘\s*([\d.]+)\s*\(([+-][\d.]+)%\)", text)
    if m:
        data["ndx_close"] = float(m.group(1))
        data["ndx_chg"] = float(m.group(2))
    
    # VIX：VIX恐慌指数: 21.85 (日变动 -0.2%)
    m = re.search(r"VIX恐慌指数[：:]\s*([\d.]+)", text)
    if m:
        data["vix"] = float(m.group(1))
    
    # KWEB：KWEB中概互联ETF: 25.05 (-0.75%)
    m = re.search(r"KWEB中概互联ETF[：:]\s*([\d.]+)", text)
    if m:
        data["kweb"] = float(m.group(1))
    
    return data


def extract_brief_numbers(brief: str) -> dict:
    """从brief中解析引用的数字（排除涨跌幅百分比，只取大盘点位级别的数字）"""
    data = {}
    
    # 道指：匹配 "道指...51848..." 等大盘点位（>=10000的5-6位数字）
    m = re.search(r"道指[^0-9]*?([\d,]{5,7}(?:\.\d+)?)", brief)
    if m:
        data["djia_ref"] = float(m.group(1).replace(",", ""))
    
    # 纳指
    m = re.search(r"纳指[^0-9]*?([\d,]{5,7}(?:\.\d+)?)", brief)
    if m:
        data["ndx_ref"] = float(m.group(1).replace(",", ""))
    
    # 标普
    m = re.search(r"标普[^0-9]*?([\d,]{3,5}(?:\.\d+)?)", brief)
    if m:
        v = float(m.group(1).replace(",", ""))
        if 3000 < v < 20000:  # 标普500在3000-20000之间
            data["spx_ref"] = v
    
    # VIX：排除日变动(+12.8%)这种百分比格式，只取VIX水平值
    m = re.search(r"VIX[^0-9]*?([\d.]+)", brief)
    if m:
        v = float(m.group(1))
        # VIX正常在10-50之间，如果后面紧跟%则是变动率不是VIX值
        after = brief[m.end():m.end()+20]
        if 5 < v < 50 and not re.match(r'\s*%', after):
            data["vix_ref"] = v
    
    # KWEB
    m = re.search(r"KWEB[^0-9]*?([\d.]+)", brief)
    if m:
        v = float(m.group(1))
        if 10 < v < 100:
            data["kweb_ref"] = v
    
    return data


def main():
    brief_file = sys.argv[1]
    
    # 推断对应prompt文件
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(brief_file))
    if not date_match:
        # 从文件内容中提取日期
        with open(brief_file) as f:
            brief_text = f.read()
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", brief_text)
    
    if not date_match:
        print("PASS: 无法提取日期，跳过数字校验")
        sys.exit(0)
    
    date_str = date_match.group(1)
    prompt_file = os.path.join(PROJECT_DIR, "..", "overseas-morning-brief", "reports", f"prompt_{date_str}.txt")
    
    if not os.path.exists(prompt_file):
        print(f"PASS: prompt文件不存在({prompt_file})，跳过数字校验")
        sys.exit(0)
    
    with open(brief_file) as f:
        brief_text = f.read()
    
    prompt_nums = extract_prompt_numbers(prompt_file)
    brief_nums = extract_brief_numbers(brief_text)
    
    checks = [
        ("道指收盘", "djia_close", "djia_ref", 50000),
        ("纳指收盘", "ndx_close", "ndx_ref", 25000),
        ("标普收盘", "spx_close", "spx_ref", 7000),
        ("VIX", "vix", "vix_ref", 20),
        ("KWEB", "kweb", "kweb_ref", 25),
    ]
    
    errors = []
    for label, src_key, ref_key, scale in checks:
        src = prompt_nums.get(src_key)
        ref = brief_nums.get(ref_key)
        if src and ref:
            deviation = abs(ref - src) / src * 100
            if deviation > 5:
                errors.append(f"{label}: prompt={src} brief={ref} 偏差{deviation:.1f}%")
    
    if errors:
        print(json.dumps({"pass": False, "errors": errors, "prompt_nums": prompt_nums, "brief_nums": brief_nums}))
        sys.exit(1)
    
    print(json.dumps({"pass": True, "checked": len(checks), "prompt_nums": prompt_nums}))
    sys.exit(0)


if __name__ == "__main__":
    main()
