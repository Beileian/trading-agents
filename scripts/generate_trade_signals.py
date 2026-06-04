#!/usr/bin/env python3
"""
生成交易推荐 Markdown 表格报告。
格式: 标题 + 7列表格（标的/现价/支撑/阻力/操作/仓位/触发条件） + 信号汇总 + 免责
AICard 流式渲染时支持 Markdown 表格语法。
"""

import sys, os, re
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime('%Y%m%d')
    
    analysis_file = f"{PROJECT_DIR}/reports/trading_analysis_{date_str}.md"
    
    if not os.path.exists(analysis_file):
        print(f"❌ 报告未找到: {analysis_file}")
        sys.exit(1)
    
    with open(analysis_file) as f:
        text = f.read()
    
    # ── 提取每个标的的关键数据 ──
    def extract(section, key):
        for line in section.split('\n'):
            line = line.strip()
            if not line.startswith('|') or not line.endswith('|'):
                continue
            clean = line.replace('**', '')
            parts = [p.strip() for p in clean.split('|')]
            if len(parts) >= 3 and parts[1] == key:
                return parts[2]
        return '-'
    
    tickers = [
        ("000016.SH", "上证50"),
        ("000300.SH", "沪深300"),
        ("000688.SH", "科创50"),
        ("601288.SH", "农业银行"),
        ("601988.SH", "中国银行"),
        ("600036.SH", "招商银行"),
        ("600886.SH", "国投电力"),
    ]
    
    records = []
    for symbol, name in tickers:
        start = text.find(f"### {symbol}")
        if start < 0:
            continue
        section = text[start:]
        for other_sym, _ in tickers:
            if other_sym != symbol:
                pos = section.find(f"### {other_sym}", 10)
                if pos > 0:
                    section = section[:pos]
                    break
    
        price = extract(section, '最新价').replace('¥','').strip()
        support = extract(section, '支撑位').replace('¥','').strip()
        resistance = extract(section, '阻力位').replace('¥','').strip()
        trend = extract(section, '趋势判断')
        advice = extract(section, '交易建议')
        pos = extract(section, '建议仓位')
        
        # Trigger condition
        if '出' in advice:
            trigger = f"跌破{support}止损"
        elif '入' in advice:
            trigger = f"回踩{support}买入"
        else:
            trigger = f"突破{resistance}加仓 / 跌破{support}减仓"
        
        records.append({
            'name': name, 'price': price, 'support': support,
            'resistance': resistance, 'trend': trend,
            'advice': advice, 'pos': pos, 'trigger': trigger,
        })
    
    # ── 构建 Markdown 表格报告 ──
    lines = []
    lines.append("## 📊 交易推荐 · 开盘前推送")
    lines.append("")
    lines.append("| 标的 | 现价 | 支撑 | 阻力 | 操作 | 仓位 | 触发条件 |")
    lines.append("|------|------|------|------|------|------|----------|")
    
    sell_count = hold_count = buy_count = 0
    
    for r in records:
        name = r['name']
        price = r['price']
        sup = r['support']
        res = r['resistance']
        advice = r['advice']
        pos = r['pos']
        trigger = r['trigger']
        
        if '出' in advice:
            op = '🔴卖出'
            sell_count += 1
        elif '入' in advice:
            op = '🟢买入'
            buy_count += 1
        else:
            op = '🟡持有'
            hold_count += 1
        
        lines.append(f"| {name} | {price} | {sup} | {res} | {op} | {pos} | {trigger} |")
    
    lines.append("")
    
    signals = []
    if sell_count:
        signals.append(f"🔴卖出 {sell_count}只")
    if hold_count:
        signals.append(f"🟡持有 {hold_count}只")
    if buy_count:
        signals.append(f"🟢买入 {buy_count}只")
    lines.append(" | ".join(signals))
    
    lines.append("")
    lines.append("> ⚠️ AI模拟分析 · 不构成投资建议")
    
    report = '\n'.join(lines)
    
    output_file = f"{PROJECT_DIR}/reports/trade_signals_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(report)
    print(f"\n✓ {output_file}")

if __name__ == '__main__':
    main()
