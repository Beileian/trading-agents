#!/usr/bin/env python3
"""
生成群聊推送用的 Markdown 表格分析报告。
格式: 顶部标题 → 数据表格 → 趋势摘要 → 观点摘要 → 免责
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
            vals = [p for p in parts[2:] if p]
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
        if line.startswith('### ') and len(line.split()) >= 2 and any(x in line.split()[1] for x in ['.SH', '.SS']):
            if current_symbol:
                sections[current_symbol] = '\n'.join(current_section)
            current_symbol = line.split()[1].strip()
            current_section = [line]
        elif current_symbol:
            current_section.append(line)
    
    if current_symbol:
        sections[current_symbol] = '\n'.join(current_section)
    
    # ── Build Markdown report ──
    lines = []
    lines.append(f"## 📊 A股开盘前分析 · {date_str}")
    lines.append("")
    
    # Data table
    lines.append("| 标的 | 最新价 | RSI | 年内涨跌 | 趋势 | 建议 |")
    lines.append("|------|--------|-----|----------|------|------|")
    
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
    
    for symbol, name in ticker_order:
        sec = sections.get(symbol, '')
        if not sec:
            lines.append(f"| {name} | - | - | - | ⚠️ | 数据缺失 |")
            continue
        
        price = extract_table_val(sec, '最新价').strip('¥').strip()
        rsi = extract_table_val(sec, 'RSI')
        ytd = extract_table_val(sec, '年内涨跌')
        trend = extract_decision_val(sec, '趋势判断')
        advice = extract_decision_val(sec, '交易建议')
        pos = extract_decision_val(sec, '建议仓位')
        
        if "看跌" in trend:
            trend_icon = "📉看跌"
        elif "看涨" in trend:
            trend_icon = "📈看涨"
        else:
            trend_icon = "📊震荡"
        
        if "卖出" in advice:
            advice_icon = "🔴卖出"
            sell_count += 1
        elif "买入" in advice:
            advice_icon = "🟢买入"
            buy_count += 1
        else:
            advice_icon = "🟡持有"
            hold_count += 1
        
        lines.append(f"| {name} | ¥{price} | {rsi} | {ytd} | {trend_icon} | {advice_icon} |")
    
    lines.append("")
    lines.append(f"🔴 偏空 {sell_count}只 | 🟡 震荡 {hold_count}只 | 🟢 偏多 {buy_count}只")
    
    # Key signals
    if sell_count:
        bears = [f"**{n}**" for s, n in ticker_order 
                 if "卖出" in extract_decision_val(sections.get(s, ''), '交易建议')]
        lines.append(f"⚠️ 卖出信号: {', '.join(bears)}")
    
    lines.append("")
    
    # IMA opinions (compact)
    if os.path.exists(opinions_file):
        with open(opinions_file) as f:
            op_text = f.read()
        # Find high-weight entries
        high_weights = []
        for line in op_text.split('\n'):
            for w_str in ['w=1.00', 'w=0.85']:
                if w_str in line and '**' in line:
                    parts = line.split('**')
                    if len(parts) >= 3:
                        title = parts[1].strip().strip('*').strip()
                        if title and title not in [t for _, t in high_weights]:
                            high_weights.append((w_str, title))
                            break
            if len(high_weights) >= 3:
                break
        
        if high_weights:
            lines.append("**今日高权重观点**")
            for w, title in high_weights:
                icon = "🔥" if "1.00" in w else "⭐"
                lines.append(f"- {icon} {title}")
            lines.append("")
    
    lines.append("")
    lines.append("> ⚠️ AI模拟分析 · 不构成投资建议")
    lines.append(f"> 🕐 {datetime.now(TZ).strftime('%m-%d %H:%M')} · DeepSeek-chat")
    
    report = '\n'.join(lines)
    
    output_file = f"{PROJECT_DIR}/reports/compact_report_{date_str}.md"
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(report)
    print(f"\n✓ {output_file}")

if __name__ == '__main__':
    main()
