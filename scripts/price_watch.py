#!/usr/bin/env python3
"""
盘中价格监控 — 实时比价支撑/阻力位，穿越时推送提醒到「谈股论金奔富」群。
触发: cron 每 5 分钟 (09:30-11:30, 13:00-15:00)
数据: 智兔数服实时快照
"""

import os, json, subprocess, requests
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

# 今日交易推荐表（由开盘前分析生成）
SIGNALS_FILE = f"{PROJECT_DIR}/reports/trade_signals_{datetime.now(TZ).strftime('%Y%m%d')}.md"

# 智兔 API 配置
ZHITU_BASE = "https://api.zhituapi.com"
ZHITU_TOKEN = "B0794D…73A9"

# 标的映射：代码 → 名称
WATCHLIST = {
    "000016": "上证50",
    "000300": "沪深300",
    "000688": "科创50",
    "601288": "农业银行",
    "601988": "中国银行",
    "600036": "招商银行",
    "600795": "国电电力",
    "000066": "中国长城",
    "600562": "国睿科技",
}

def load_thresholds():
    """从今日交易推荐报告提取支撑/阻力位"""
    thresholds = {}
    if not os.path.exists(SIGNALS_FILE):
        return thresholds

    with open(SIGNALS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line.startswith('|') or '标的' in line or '---' in line:
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 6:
                continue
            name = parts[1]
            # columns: 标的 | 现价 | 乖离 | 支撑 | 阻力 | 操作 | 仓位 | 触发条件
            if len(parts) >= 6:
                support = parts[4]
                resistance = parts[5]
                try:
                    sup_val = float(support)
                    res_val = float(resistance)
                    thresholds[name] = {'support': sup_val, 'resistance': res_val}
                except ValueError:
                    pass
    return thresholds

def fetch_prices():
    """从智兔获取实时价格"""
    prices = {}
    for code, name in WATCHLIST.items():
        try:
            resp = requests.get(
                f"{ZHITU_BASE}/hs/quote/{code}",
                params={"token": ZHITU_TOKEN},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 0 and 'data' in data:
                    quote = data['data']
                    prices[name] = {
                        'price': float(quote.get('close', quote.get('price', 0))),
                        'change_pct': float(quote.get('changePercent', quote.get('change_percent', 0))),
                        'high': float(quote.get('high', 0)),
                        'low': float(quote.get('low', 0)),
                        'volume': quote.get('volume', ''),
                    }
        except:
            pass
    return prices

def check_breaches(prices, thresholds):
    """检测价格穿越支撑/阻力位"""
    alerts = []
    for name, t in thresholds.items():
        if name not in prices:
            continue
        price = prices[name]['price']
        support = t['support']
        resistance = t['resistance']

        # 跌破支撑
        if price <= support:
            gap = (support - price) / price * 100
            alerts.append({
                'name': name, 'price': price, 'type': '支撑',
                'level': support, 'gap': gap,
                'msg': f"🔴 {name} 跌破支撑 {support}（现价 {price:.2f}，破位 {gap:.1f}%）"
            })

        # 突破阻力
        if price >= resistance:
            gap = (price - resistance) / resistance * 100
            alerts.append({
                'name': name, 'price': price, 'type': '阻力',
                'level': resistance, 'gap': gap,
                'msg': f"🟢 {name} 突破阻力 {resistance}（现价 {price:.2f}，超涨 {gap:.1f}%）"
            })

    return alerts

def dedup_alerts(alerts):
    """去重：同一标的同方向 30 分钟内不重复"""
    state_file = f"{PROJECT_DIR}/logs/price_alerts_state.json"
    os.makedirs(os.path.dirname(state_file), exist_ok=True)

    state = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)

    now = datetime.now(TZ).timestamp()
    new_alerts = []

    for a in alerts:
        key = f"{a['name']}_{a['type']}"
        last_time = state.get(key, 0)
        if now - last_time > 30 * 60:  # 30分钟冷却
            state[key] = now
            new_alerts.append(a)

    with open(state_file, 'w') as f:
        json.dump(state, f)

    return new_alerts

def push_alerts(alerts):
    """推送告警到群"""
    if not alerts:
        return

    chat_id = "cidY4mlx+J2kNFpTiWFgQ0gkg=="
    lines = []
    lines.append("## ⚡ 盘中价格预警")
    lines.append("")
    for a in alerts:
        lines.append(f"- {a['msg']}")

    msg = '\n'.join(lines)

    try:
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'dingtalk-connector',
            '--target', f'chat:{chat_id}',
            '--message', msg,
        ], timeout=15, capture_output=True)
    except:
        pass

def main():
    now = datetime.now(TZ)

    # 只在交易时段运行
    t = now.time()
    morning = t >= datetime.strptime("09:30", "%H:%M").time() and t <= datetime.strptime("11:30", "%H:%M").time()
    afternoon = t >= datetime.strptime("13:00", "%H:%M").time() and t <= datetime.strptime("15:00", "%H:%M").time()
    if not morning and not afternoon:
        return  # 非交易时段，静默退出

    thresholds = load_thresholds()
    if not thresholds:
        return  # 今日报告未生成

    prices = fetch_prices()
    if not prices:
        return  # 行情获取失败

    alerts = check_breaches(prices, thresholds)
    new_alerts = dedup_alerts(alerts)
    push_alerts(new_alerts)

if __name__ == '__main__':
    main()
