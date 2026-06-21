#!/usr/bin/env python3
"""
validate_schema.py — rubrics item #1: schema_validity
对 trade_signals 输出进行 JSON Schema 结构校验。

用法:
  python3 rubrics/validate_schema.py <report_file>

输入: trade_signals_YYYYMMDD.md (generate_trade_signals.py 的输出)
输出: JSON → stdout { "pass": bool, "errors": [...] }
exit code: 0=通过, 1=不通过
"""

import sys, os, re, json

# ── 从 .md 报告中提取标的记录 ──
# generate_trade_signals.py 的输出格式:
#   🟢/🔴/🟡 name  price  乖离X   direction
#     支撑X / 阻力X
#     触发: ...
#     风险：...
#     催化：...


def parse_report(text: str) -> list[dict]:
    """从报告文本中解析每条标的记录"""
    records = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 匹配标的行: emoji + name + price [+ 乖离] + advice
        m = re.match(r'^[🔴🟢🟡]\s*(.+?)\s{2,}([¥\d][\d.,]+)', line)
        if m:
            rec = {
                'name': m.group(1).strip(),
                'price': m.group(2).strip(),
                'advice': '',
                'pos': '',
                'trigger': '',
                'support': '',
                'resistance': '',
                'risk': '',
                'catalyst': '',
            }
            # 提取方向
            if '卖出' in line:
                rec['advice'] = '卖出'
            elif '买入' in line:
                rec['advice'] = '买入'
            elif '持有' in line or '观望' in line:
                rec['advice'] = '持有'

            # 看后续几行
            j = i + 1
            while j < len(lines) and j < i + 8:
                sub = lines[j].strip()
                if re.match(r'^[🔴🟢🟡]', sub):
                    break  # 下一个标的开始
                if '支撑' in sub and '/' in sub:
                    parts = sub.split('/')
                    for p in parts:
                        p = p.strip()
                        if '支撑' in p:
                            rec['support'] = p.replace('支撑', '').strip()
                        if '阻力' in p:
                            rec['resistance'] = p.replace('阻力', '').strip()
                elif sub.startswith('触发:'):
                    rec['trigger'] = sub.replace('触发:', '').strip()
                elif sub.startswith('风险：') or sub.startswith('风险:'):
                    rec['risk'] = sub.replace('风险：', '').replace('风险:', '').strip()
                elif sub.startswith('催化：') or sub.startswith('催化:'):
                    rec['catalyst'] = sub.replace('催化：', '').replace('催化:', '').strip()
                # 外盘±1行跳过
                j += 1

            records.append(rec)
        i += 1

    return records


def validate_records(records: list[dict]) -> tuple[bool, list[str]]:
    """
    Schema 校验：逐标的必需字段 + 合理性检查。
    与 generate_trade_signals.py 中的 validate_records 保持一致。
    """
    errors = []
    required_fields = ['name', 'price', 'advice', 'trigger']
    valid_advice = {'买入', '卖出', '持有', 'hold'}

    is_new_stock = lambda r: '首日纳入' in str(r.get('trigger', ''))

    for i, r in enumerate(records):
        name = r.get('name', f'#{i}')
        new_stock = is_new_stock(r)

        for f in required_fields:
            val = r.get(f, '')
            if not val or val == '-':
                if new_stock and f in ('price', 'trigger'):
                    continue
                errors.append(f"{name}: 缺失字段 {f} (值={val!r})")

        advice_raw = r.get('advice', '')
        advice_clean = advice_raw.replace('**', '').strip()
        if advice_clean not in valid_advice:
            errors.append(f"{name}: 交易建议异常 ({advice_raw!r})")

        if not new_stock:
            pos_str = r.get('pos', '').replace('%', '').strip()
            if pos_str and pos_str != '-':
                try:
                    pos_val = float(pos_str)
                    if pos_val < 0 or pos_val > 100:
                        errors.append(f"{name}: 仓位异常 ({pos_str})")
                except (ValueError, TypeError):
                    pass  # 仓位非必填

            price_str = r.get('price', '').replace('¥', '').replace(',', '').strip()
            if price_str and price_str != '-':
                try:
                    price_val = float(price_str)
                    if price_val <= 0:
                        errors.append(f"{name}: 价格异常 ({price_str})")
                except (ValueError, TypeError):
                    errors.append(f"{name}: 价格非数值 ({price_str})")

    # 全局：至少一半标的有有效触发条件
    valid_triggers = sum(1 for r in records
                         if r.get('trigger') and r['trigger'] != '-'
                         and '首日纳入' not in r.get('trigger', ''))
    if len(records) >= 4 and valid_triggers < len(records) / 2:
        errors.append(f"全局: 有效触发条件不足 ({valid_triggers}/{len(records)})")

    return len(errors) == 0, errors


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"pass": False, "errors": ["用法: validate_schema.py <report_file>"]}))
        sys.exit(1)

    report_file = sys.argv[1]
    if not os.path.exists(report_file):
        print(json.dumps({"pass": False, "errors": [f"文件不存在: {report_file}"]}))
        sys.exit(1)

    with open(report_file) as f:
        text = f.read()

    records = parse_report(text)
    if not records:
        print(json.dumps({"pass": False, "errors": ["未解析到任何标的记录"]}))
        sys.exit(1)

    passed, errors = validate_records(records)
    result = {
        "pass": passed,
        "errors": errors,
        "record_count": len(records),
        "records": [{"name": r["name"], "advice": r["advice"], "price": r["price"],
                      "trigger": r["trigger"][:50] if r.get("trigger") else ""}
                     for r in records],
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
