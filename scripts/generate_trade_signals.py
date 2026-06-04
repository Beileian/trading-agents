#!/usr/bin/env python3
"""
交易推荐 Markdown 表格生成 | 含刘晨明乖离率指标 + 趋势方向
乖离率体系: BIAS_20 判断偏离 → 5日趋势方向 → 修正支撑/阻力触发逻辑
原则: 支撑≠买进, 阻力≠卖出。乖离率告诉你"车开多快"。
"""

import sys, os, pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

TICKER_CACHE = {
    '000016.SH': '000016.SH-daily.csv', '000300.SH': '000300.SH-daily.csv',
    '000688.SH': '000688.SH-daily.csv', '601288.SH': '601288.SS-daily.csv',
    '601988.SH': '601988.SS-daily.csv', '600036.SH': '600036.SS-daily.csv',
    '600795.SH': '600795.SH-daily.csv', '000066.SZ': '000066.SZ-daily.csv',
    '600562.SH': '600562.SH-daily.csv',
}

TICKERS = [
    ("000016.SH", "上证50"), ("000300.SH", "沪深300"),
    ("000688.SH", "科创50"), ("601288.SH", "农业银行"),
    ("601988.SH", "中国银行"), ("600036.SH", "招商银行"),
    ("600795.SH", "国电电力"), ("000066.SZ", "中国长城"),
    ("600562.SH", "国睿科技"),
]

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

def calc_bias_direction(symbol):
    """
    返回 (bias_20_value, bias_direction_icon)
    bias_direction: ↑乖离扩大 ↓乖离收敛 →乖离持平
    基于最近5个交易日的BIAS_20变化方向
    """
    cache_file = TICKER_CACHE.get(symbol, '')
    cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
    if not os.path.exists(cache_path):
        return '-', ''

    try:
        df = pd.read_csv(cache_path, parse_dates=['Date'])
        df = df.set_index('Date').sort_index()
        close = df['Close']
        ma20 = close.rolling(20).mean()

        # 最近20个交易日的BIAS序列
        bias_series = (close - ma20) / ma20 * 100
        bias_series = bias_series.dropna()

        if len(bias_series) < 6:
            b20 = bias_series.iloc[-1]
            result = f"{b20:+.1f}%"
            if b20 > 5:  result += '⚠️'
            elif b20 < -5: result += '💡'
            return result, '→'

        latest = bias_series.iloc[-1]
        prev5 = bias_series.iloc[-6:-1].mean()

        # 趋势方向
        delta = latest - prev5
        if delta > 0.5:
            direction = '↑'   # 乖离在扩大（加速偏离）
        elif delta < -0.5:
            direction = '↓'   # 乖离在收敛（回归均线）
        else:
            direction = '→'   # 持平

        result = f"{latest:+.1f}%"
        if latest > 5:
            result += '⚠️'
        elif latest < -5:
            result += '💡'

        return result, direction
    except:
        return '-', ''

def adjust_trigger(advice, support, resistance, bias_val, direction):
    """
    根据乖离率修正触发条件。
    支撑位 + 负乖离收敛 → 买入时机更好
    阻力位 + 正乖离扩大 → 卖出时机更好
    """

    if advice == '-' or '出' not in advice and '入' not in advice and '持' not in advice:
        return '-'

    try:
        b = float(bias_val.replace('%', '').replace('⚠️', '').replace('💡', '').strip())
    except:
        b = 0

    # 持有情况下的双向信号
    if '持' in advice:
        # 负乖离 + 接近支撑 → 倾向买入
        if b < -2 and direction == '↓':
            return f"回调{int(abs(b))}%接近支撑{support}，反弹可期 → 可分批买入"
        # 正乖离 + 接近阻力 → 倾向卖出
        if b > 2 and direction == '↑':
            return f"乖离{resistance}扩大 + 接近阻力 → 减仓规避"
        # 负乖离 + 远离支撑(还在跌) → 观望
        if b < -2 and direction == '↑':
            return f"加速下跌中，暂勿抄底 → 等{support}企稳"
        # 正乖离 + 远离阻力(还在涨) → 持有
        if b > 2 and direction == '↓':
            return f"乖离收敛中，趋势健康 → 持有"
        return f"突破{resistance}加仓 / 跌破{support}减仓"

    # 卖出信号
    if '出' in advice:
        if b > 2 and direction == '↑':
            return f"乖离+阻力双重压力 → 跌破{support}坚决止损"
        if direction == '↓':
            return f"乖离收敛中，反弹可减亏 → {support}~{resistance}分批出"
        return f"跌破{support}止损"

    # 买入信号
    if '入' in advice:
        if b < -2 and direction == '↓':
            return f"超跌+乖离收敛 → {support}附近建仓"
        if b > 2 and direction == '↑':
            return f"乖离偏高，追高风险 → 等回调至{support}再入"
        return f"回踩{support}买入"

    return '-'

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime('%Y%m%d')
    analysis_file = f"{PROJECT_DIR}/reports/trading_analysis_{date_str}.md"
    if not os.path.exists(analysis_file):
        print(f"❌ 报告未找到: {analysis_file}")
        sys.exit(1)
    with open(analysis_file) as f:
        text = f.read()

    records = []
    for symbol, name in TICKERS:
        start = text.find(f"### {symbol}")
        if start < 0:
            continue
        section = text[start:]
        for other_sym, _ in TICKERS:
            if other_sym != symbol:
                pos = section.find(f"### {other_sym}", 10)
                if pos > 0:
                    section = section[:pos]
                    break

        price = extract(section, '最新价').replace('¥', '').strip()
        bias_val, bias_dir = calc_bias_direction(symbol)
        bias_display = f"{bias_val} {bias_dir}" if bias_dir else bias_val
        support = extract(section, '支撑位').replace('¥', '').strip()
        resistance = extract(section, '阻力位').replace('¥', '').strip()
        trend = extract(section, '趋势判断')
        advice = extract(section, '交易建议')
        pos = extract(section, '建议仓位')

        # 乖离率修正触发条件
        trigger = adjust_trigger(advice, support, resistance, bias_val, bias_dir)

        records.append({
            'name': name, 'price': price, 'bias': bias_display,
            'support': support, 'resistance': resistance,
            'trend': trend, 'advice': advice, 'pos': pos, 'trigger': trigger,
        })

    # 建表
    lines = []
    lines.append("## 📊 交易推荐 · 开盘前推送")
    lines.append("")
    lines.append("| 标的 | 现价 | 乖离(20) | 支撑 | 阻力 | 操作 | 仓位 | 触发条件 |")
    lines.append("|------|------|----------|------|------|------|------|----------|")

    sell = hold = buy = 0
    for r in records:
        adv_first = r['advice'][0] if r['advice'] else ''
        if '出' in r['advice']:
            op = '🔴卖出'
            sell += 1
        elif '入' in r['advice']:
            op = '🟢买入'
            buy += 1
        else:
            op = '🟡持有'
            hold += 1
        lines.append(f"| {r['name']} | {r['price']} | {r['bias']} | {r['support']} | {r['resistance']} | {op} | {r['pos']} | {r['trigger']} |")

    lines.append("")
    sigs = []
    if sell: sigs.append(f"🔴卖出 {sell}只")
    if hold: sigs.append(f"🟡持有 {hold}只")
    if buy:  sigs.append(f"🟢买入 {buy}只")
    lines.append(" | ".join(sigs))
    lines.append("")
    lines.append("> 乖离↑=加速偏离 ↓=回归均线 →=持平 | ⚠️ AI模拟分析 · 不构成投资建议")

    report = '\n'.join(lines)
    output_file = f"{PROJECT_DIR}/reports/trade_signals_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(report)
    print(report)
    print(f"\n✓ {output_file}")

if __name__ == '__main__':
    main()
