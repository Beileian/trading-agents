#!/usr/bin/env python3
"""
生成群聊卡片推送用的精简分析报告。
手机阅读优化：顶部结论 → 关键数据 → 外部观点
"""

import sys, os, re
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

def extract_table_val(section, key):
    """Extract value from | key | value | table row"""
    for line in section.split('\n'):
        line = line.strip()
        if key in line and line.startswith('|') and line.endswith('|'):
            parts = [p.strip() for p in line.split('|')]
            # The value is the second non-empty part (after the key)
            vals = [p for p in parts[2:] if p]  # skip leading empty + key
            if vals:
                return vals[0]
    return '-'

def extract_decision_val(section, key):
    """Extract from bold-wrapped decision table: | **key** | value |"""
    for line in section.split('\n'):
        line = line.strip()
        if key in line.replace('**','') and line.startswith('|') and line.endswith('|'):
            parts = [p.strip().strip('*') for p in line.split('|')]
            vals = [p for p in parts[2:] if p]
            if vals:
                return vals[0]
    return '-'

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime('%Y%m%d')
    
    analysis_file = f"{PROJECT_DIR}/reports/trading_analysis_{date_str}.md"
    opinions_file = f"{PROJECT_DIR}/reports/opinions_{date_str}.md"
    
    if not os.path.exists(analysis_file):
        print(f"❌ 报告未找到: {analysis_file}")
        sys.exit(1)
    
    with open(analysis_file) as f:
        analysis = f.read()
    
    # Split into per-ticker sections
    sections = {}
    current_symbol = None
    current_section = []
    
    for line in analysis.split('\n'):
        if line.startswith('### ') and '.SH' in line:
            if current_symbol:
                sections[current_symbol] = '\n'.join(current_section)
            current_symbol = line.split('### ')[1].split(' —')[0].strip()
            current_section = [line]
        elif line.startswith('### ') and '.SS' in line:
            if current_symbol:
                sections[current_symbol] = '\n'.join(current_section)
            current_symbol = line.split('### ')[1].split(' —')[0].strip()
            current_section = [line]
        elif current_symbol:
            current_section.append(line)
    
    if current_symbol:
        sections[current_symbol] = '\n'.join(current_section)
    
    # ── Build report ──
    lines = []
    lines.append(f"📊 A股开盘前分析 · {date_str}")
    lines.append("")
    
    lines.append("━━━ 技术面速览 ━━━")
    lines.append("")
    
    ticker_order = [
        ("000016.SH", "上证50"),
        ("000300.SH", "沪深300"),
        ("000688.SH", "科创50"),
        ("601288.SH", "农业银行"),
        ("601988.SH", "中国银行"),
        ("600036.SH", "招商银行"),
        ("600886.SH", "国投电力"),
    ]
    
    sell_count = hold_count = buy_count = 0
    signals = []
    
    for symbol, name in ticker_order:
        sec = sections.get(symbol, '')
        if not sec:
            lines.append(f"  ⚠ {name}: 数据缺失")
            continue
        
        price = extract_table_val(sec, '最新价')
        rsi = extract_table_val(sec, 'RSI')
        ytd = extract_table_val(sec, '年内涨跌')
        trend = extract_decision_val(sec, '趋势判断')
        advice = extract_decision_val(sec, '交易建议')
        pos = extract_decision_val(sec, '建议仓位')
        
        trend_icon = "📉" if "跌" in trend else "📊" if "震荡" in trend else "📈"
        
        if "卖出" in advice:
            adv = "🔴卖出"
            sell_count += 1
        elif "买入" in advice:
            adv = "🟢买入"
            buy_count += 1
        else:
            adv = "🟡持有"
            hold_count += 1
        
        line = f"{trend_icon} **{name}** ¥{price} | RSI {rsi} | YTD {ytd} | {adv} {pos}"
        lines.append(line)
        signals.append((name, trend, advice, pos))
    
    lines.append("")
    lines.append("━━━ 今日关注 ━━━")
    lines.append("")
    
    if sell_count:
        lines.append(f"🔴 偏空: {sell_count}只（上证50、农业银行）— 跌破所有均线")
    if buy_count:
        lines.append(f"🟢 偏多: {buy_count}只")
    if hold_count >= 5:
        lines.append(f"📊 多数震荡 ({hold_count}/7) — 观望为主")
    else:
        lines.append(f"📊 震荡 {hold_count}只 | 偏空 {sell_count}只 | 偏多 {buy_count}只")
    
    lines.append("")
    lines.append("━━━ 外部观点 ━━━")
    lines.append("")
    
    if os.path.exists(opinions_file):
        with open(opinions_file) as f:
            op_text = f.read()
        # Extract high-weight titles
        found = 0
        for line in op_text.split('\n'):
            if 'w=1.00' in line and '**' in line:
                title = line.split('**')[1].split('**')[0] if '**' in line else line[:60]
                lines.append(f"  🔥 {title}")
                found += 1
            elif 'w=0.85' in line and '**' in line and found < 4:
                title = line.split('**')[1].split('**')[0] if '**' in line else line[:60]
                lines.append(f"  ⭐ {title}")
                found += 1
            if found >= 4:
                break
    else:
        lines.append("  今日无IMA观点更新")
    
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("⚠️ AI模拟分析 · 不构成投资建议")
    lines.append(f"🕐 {datetime.now(TZ).strftime('%m-%d %H:%M')} · DeepSeek-chat")
    
    report = '\n'.join(lines)
    
    output_file = f"{PROJECT_DIR}/reports/compact_report_{date_str}.txt"
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(report)
    print(f"\n✓ {output_file}")

if __name__ == '__main__':
    main()
