#!/usr/bin/env python3
"""
盘中价格监控 — 实时比价支撑/阻力位，穿越时推送提醒到「谈股论金奔富」群。
触发: cron 每 5 分钟 (09:30-11:30, 13:00-15:00)
数据: 新浪财经实时行情（主） + 智兔数服（备份）
"""

import os, json, re, subprocess, requests, sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
OPENCLAW_BIN = "/root/.nvm/versions/node/v22.22.0/bin/openclaw"

SIGNALS_FILE = f"{PROJECT_DIR}/reports/trade_signals_{datetime.now(TZ).strftime('%Y%m%d')}.md"

# 新浪 API 标的映射：代码 → (sina_code, 名称)
SINA_MAP = {
    "sh000016": ("000016", "上证50"),
    "sh000300": ("000300", "沪深300"),
    "sh000688": ("000688", "科创50"),
    "sh601288": ("601288", "农业银行"),
    "sh601988": ("601988", "中国银行"),
    "sh600036": ("600036", "招商银行"),
    "sh600795": ("600795", "国电电力"),
    "sz000066": ("000066", "中国长城"),
    "sh600562": ("600562", "国睿科技"),
}

# 智兔 API（备份）
ZHITU_BASE = "https://api.zhituapi.com"
ZHITU_TOKEN = "B0794D…73A9"

def load_thresholds():
    """从今日交易推荐报告提取支撑/阻力位
    
    支持两种格式:
    1. 表格格式: | 标的 | ... | 支撑 | 阻力 |
    2. 段落格式: 🟡 标的名  ... → 持有\n  支撑xxx / 阻力yyy
    """
    thresholds = {}
    if not os.path.exists(SIGNALS_FILE):
        return thresholds

    with open(SIGNALS_FILE) as f:
        content = f.read()

    # 先尝试段落格式: 匹配 "支撑xxx / 阻力yyy" 行
    # 标的名称在前一行（如 "🟡 农业银行  6.68  ..."）
    lines = content.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        # 匹配支撑/阻力行
        m = re.search(r'支撑([\d.]+)\s*/\s*阻力([\d.]+)', line)
        if not m:
            continue
        support = float(m.group(1))
        resistance = float(m.group(2))

        # 向前找最近的标的名称行（最多回溯 3 行）
        name = None
        for j in range(i-1, max(i-4, -1), -1):
            prev = lines[j].strip()
            if not prev or prev.startswith('---') or prev.startswith('>'):
                continue
            # 匹配: 🟡 农业银行  6.68  乖离+3.0% →  持有
            nm = re.match(r'[🟡🟢🔴🟠⚪]\s*(\S+)', prev)
            if nm:
                name = nm.group(1)
                break

        if name:
            thresholds[name] = {
                'support': support,
                'resistance': resistance
            }

    # 如果段落格式提取到了，直接返回（优先）
    if thresholds:
        return thresholds

    # 降级: 尝试表格格式（兼容旧版报告）
    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith('|') or '标的' in line or '---' in line:
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 8:
            continue
        name = parts[1]
        support = parts[4]
        resistance = parts[5]
        try:
            thresholds[name] = {
                'support': float(support),
                'resistance': float(resistance)
            }
        except ValueError:
            pass

    return thresholds

def fetch_sina_prices():
    """从新浪财经获取实时价格（免费，无限额）"""
    prices = {}
    sina_codes = list(SINA_MAP.keys())
    
    for i in range(0, len(sina_codes), 3):
        batch = sina_codes[i:i+3]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        try:
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
            resp.encoding = "gbk"
            
            for line in resp.text.strip().split("\n"):
                match = re.search(r'hq_str_(\w+)="(.+)"', line)
                if not match:
                    continue
                sina_code = match.group(1)
                fields = match.group(2).split(",")
                
                if sina_code not in SINA_MAP or len(fields) < 6:
                    continue
                
                code, name = SINA_MAP[sina_code]
                
                if sina_code.startswith("sh0") or sina_code.startswith("sz3"):  # 指数
                    price = float(fields[1])
                    prev_close = float(fields[2])
                    high = float(fields[4])
                    low = float(fields[5])
                else:  # 个股
                    price = float(fields[3]) if fields[3] != '0.000' else float(fields[1])
                    prev_close = float(fields[2])
                    high = float(fields[4])
                    low = float(fields[5])
                
                change_pct = (price - prev_close) / prev_close * 100 if prev_close != 0 else 0
                
                prices[name] = {
                    'price': price,
                    'change_pct': round(change_pct, 2),
                    'high': high,
                    'low': low,
                    'prev_close': prev_close,
                }
        except Exception as e:
            print(f"[sina batch error] {batch}: {e}", file=sys.stderr)
    
    return prices

def fetch_zhitu_prices():
    """从智兔获取实时价格（备份）"""
    prices = {}
    for code, (_, name) in SINA_MAP.items():
        try:
            resp = requests.get(
                f"{ZHITU_BASE}/hs/quote/{code.replace('sh','').replace('sz','')}",
                params={"token": ZHITU_TOKEN},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 0 and 'data' in data:
                    q = data['data']
                    prices[name] = {
                        'price': float(q.get('close', q.get('price', 0))),
                        'change_pct': float(q.get('changePercent', 0)),
                        'high': float(q.get('high', 0)),
                        'low': float(q.get('low', 0)),
                        'prev_close': 0,
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

        if price <= support:
            gap = (support - price) / price * 100
            alerts.append({
                'name': name, 'price': price, 'type': '支撑',
                'level': support, 'gap': gap,
                'msg': f"🔴 {name} 跌破支撑 {support}（现价 {price:.2f}，破位 {gap:.1f}%）"
            })

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
        if now - last_time > 30 * 60:
            state[key] = now
            new_alerts.append(a)

    with open(state_file, 'w') as f:
        json.dump(state, f)

    return new_alerts

ALERT_FILE = f"{PROJECT_DIR}/logs/latest_price_alerts.txt"

def push_alerts(alerts):
    """将预警写入文件，由 Gateway Cron agent 读取后通过 announce 推送"""
    if not alerts:
        # 无预警时清理旧文件
        if os.path.exists(ALERT_FILE):
            os.remove(ALERT_FILE)
        return

    lines = ["⚡ 盘中价格预警"]
    for a in alerts:
        lines.append(f"- {a['msg']}")

    with open(ALERT_FILE, 'w') as f:
        f.write('\n'.join(lines))

def main():
    now = datetime.now(TZ)

    # 只在交易时段运行
    t = now.time()
    morning = t >= datetime.strptime("09:30", "%H:%M").time() and t <= datetime.strptime("11:30", "%H:%M").time()
    afternoon = t >= datetime.strptime("13:00", "%H:%M").time() and t <= datetime.strptime("15:00", "%H:%M").time()
    if not morning and not afternoon:
        return

    thresholds = load_thresholds()
    if not thresholds:
        return

    # 主数据源：新浪
    prices = fetch_sina_prices()
    if not prices:
        # 备用：智兔
        prices = fetch_zhitu_prices()
    if not prices:
        return

    alerts = check_breaches(prices, thresholds)
    new_alerts = dedup_alerts(alerts)
    push_alerts(new_alerts)

if __name__ == '__main__':
    main()
