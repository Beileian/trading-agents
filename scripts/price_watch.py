#!/usr/bin/env python3
"""
盘中价格监控 v2.1.0 — 实时比价支撑/阻力位，穿越时推送提醒。
项目: 金桥量化 v2.5.0
触发: cron 每 5 分钟 (09:30-11:30, 13:00-15:00)

v2.1.0: 钉钉机器人API直推 + 去重跨日隔离
v1: 基础版（subprocess/print/announce 三版迭代）
数据: 新浪财经实时行情
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
    """从多数据源获取实时价格。指数用东方财富（字段明确），个股用新浪（免费无限额）。"""
    prices = {}
    sina_codes = list(SINA_MAP.keys())

    # ── 批量取东方财富实时行情（用于指数+个股双校验） ──
    east_codes = []
    for sc in sina_codes:
        code, name = SINA_MAP[sc]
        # 东方财富市场标记: 沪市=1, 深市=0, 指数=1
        if sc.startswith('sh'):
            east_codes.append(f'1.{code.replace(".SH","")}' if '.SH' in code else f'1.{code}')
        elif sc.startswith('sz'):
            east_codes.append(f'0.{code.replace(".SZ","")}')
        else:
            east_codes.append('')  # 非统一代码

    # 东方财富批量查询（一次取所有）
    east_prices = {}
    try:
        east_batch = ','.join(east_codes)
        url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={east_batch}&fields=f43,f44,f45,f46,f47,f57,f58,f60,f169,f170'
        resp = requests.get(url, timeout=5)
        # 批量模式可能失败，逐个尝试
        for ec in east_codes:
            if not ec:
                continue
            try:
                url2 = f'https://push2.eastmoney.com/api/qt/stock/get?secid={ec}&fields=f43,f44,f45,f46,f47,f57,f58,f60,f169,f170'
                r = requests.get(url2, timeout=3)
                d = r.json()
                if d.get('data'):
                    dd = d['data']
                    east_prices[dd['f57']] = {
                        'price': dd['f43'] / 100 if dd.get('f43') else None,
                        'high': dd['f44'] / 100 if dd.get('f44') else None,
                        'low': dd['f45'] / 100 if dd.get('f45') else None,
                        'open': dd['f46'] / 100 if dd.get('f46') else None,
                        'prev_close': dd['f60'] / 100 if dd.get('f60') else None,
                        'volume': dd.get('f47', 0),
                        'change_pct': dd.get('f170', 0) / 100 if dd.get('f170') else None,
                    }
            except Exception:
                continue
    except Exception as e:
        print(f"[eastmoney error] {e}", file=sys.stderr)

    # ── 新浪个股数据 ──
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
                is_index = sina_code.startswith("sh0") or sina_code.startswith("sz3")

                # 获取东方财富数据（如果可用）
                east_code = code.split('.')[-1] if '.' in code else code
                east_data = east_prices.get(east_code, {})

                if is_index and east_data.get('price') is not None:
                    # 指数优先用东方财富（字段明确，f43=最新价）
                    price = east_data['price']
                    prev_close = east_data['prev_close']
                    high = east_data['high']
                    low = east_data['low']
                    open_price = east_data['open']
                    change_pct = east_data['change_pct']
                    volume = east_data['volume']
                elif is_index:
                    # 东方财富不可用，新浪fallback: fields[1]=今开, fields[2]=昨收
                    open_price = float(fields[1])
                    prev_close = float(fields[2])
                    price = open_price  # ⚠️ 新浪指数无实时价，仅标记
                    high = float(fields[4]) if fields[4] else open_price
                    low = float(fields[5]) if fields[5] else open_price
                    change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
                    volume = float(fields[8]) if fields[8] else 0
                else:
                    # 个股用新浪（fields[3]=当前价确认正确）
                    price = float(fields[3]) if fields[3] != '0.000' else float(fields[1])
                    prev_close = float(fields[2])
                    high = float(fields[4])
                    low = float(fields[5])
                    change_pct = (price - prev_close) / prev_close * 100 if prev_close != 0 else 0
                    open_price = float(fields[1]) if len(fields) > 1 and fields[1] else price
                    volume = float(fields[8]) if len(fields) > 8 and fields[8] else 0

                prices[name] = {
                    'price': price,
                    'change_pct': round(change_pct, 2),
                    'high': high,
                    'low': low,
                    'prev_close': prev_close,
                    'open': open_price,
                    'volume': volume,
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


def check_intraday_anomalies(prices):
    """
    通达信盘中异动信号检测 (P1).
    三组信号：
      1. 935放量 — 9:35前后5分钟窗口内成交量 > 5日均量的2倍
      2. 开盘拉升 — 开盘后价格从开盘点位上涨 > 2% (高开回补创新高)
      3. 尾盘拉升 — 14:50后价格从日内低点拉回 > 1.5%
    返回异动告警列表。
    """
    import pandas as pd
    alerts = []
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")
    t = now.time()
    minutes = t.hour * 60 + t.minute

    # 反查 SINA_MAP: name → sina_code
    name_to_code = {v[1]: k for k, v in SINA_MAP.items()}

    for name, pdata in prices.items():
        if name not in name_to_code:
            continue
        sina_code = name_to_code[name]
        price = pdata.get('price', 0)
        volume = pdata.get('volume', 0)
        open_price = pdata.get('open', 0)
        low = pdata.get('low', price)

        if price <= 0:
            continue

        # ── 信号1: 935放量 ──
        # 只在 9:30-9:45 窗口内检测
        if 9*60+30 <= minutes <= 9*60+45 and volume > 0:
            # 用缓存数据计算5日均量
            code = None
            for ticker, n in [("000016.SH", "上证50"), ("000300.SH", "沪深300"),
                              ("000688.SH", "科创50"), ("601288.SH", "农业银行"),
                              ("601988.SH", "中国银行"), ("600036.SH", "招商银行"),
                              ("600795.SH", "国电电力"), ("000066.SZ", "中国长城"),
                              ("600562.SH", "国睿科技")]:
                if n == name:
                    code = ticker
                    break
            if code:
                cache_map = {
                    '000016.SH': '000016.SH-daily.csv', '000300.SH': '000300.SH-daily.csv',
                    '000688.SH': '000688.SH-daily.csv', '601288.SH': '601288.SS-daily.csv',
                    '601988.SH': '601988.SS-daily.csv', '600036.SH': '600036.SS-daily.csv',
                    '600795.SH': '600795.SH-daily.csv', '000066.SZ': '000066.SZ-daily.csv',
                    '600562.SH': '600562.SH-daily.csv',
                }
                cache_file = cache_map.get(code, '')
                cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
                if os.path.exists(cache_path):
                    try:
                        df = pd.read_csv(cache_path)
                        avg_vol_5d = df['Volume'].tail(5).mean()
                        if avg_vol_5d > 0 and volume > avg_vol_5d * 2:
                            ratio = volume / avg_vol_5d
                            alerts.append({
                                'name': name, 'price': price,
                                'type': '935异动',
                                'msg': f"⚡ {name} 935放量（成交{volume/1e8:.1f}亿，5日均量{avg_vol_5d/1e8:.1f}亿，倍数{ratio:.1f}x）"
                            })
                    except Exception:
                        pass

        # ── 信号2: 开盘拉升（高开回补创新高模式）──
        # 条件: 非指数 + 当前价从开盘价拉升 > 2%
        if open_price > 0:
            open_rise = (price - open_price) / open_price * 100
            if open_rise > 2:
                alerts.append({
                    'name': name, 'price': price,
                    'type': '开盘异动',
                    'msg': f"🔥 {name} 开盘拉升+{open_rise:.1f}%（从{open_price:.2f}→{price:.2f}）"
                })

        # ── 信号3: 尾盘拉升 ──
        # 条件: 14:50后 + 从日内低点拉回 > 1.5%
        if 14*60+50 <= minutes <= 15*60 and low > 0:
            recovery = (price - low) / low * 100
            if recovery > 1.5:
                alerts.append({
                    'name': name, 'price': price,
                    'type': '尾盘异动',
                    'msg': f"📈 {name} 尾盘拉升+{recovery:.1f}%（低{low:.2f}→{price:.2f}）"
                })

    return alerts

def dedup_alerts(alerts):
    """去重：同一标的同方向 30 分钟内不重复，跨日自动清理旧状态"""
    state_file = f"{PROJECT_DIR}/logs/price_alerts_state.json"
    os.makedirs(os.path.dirname(state_file), exist_ok=True)

    today = datetime.now(TZ).strftime("%Y%m%d")
    now = datetime.now(TZ).timestamp()

    # 读取旧状态，清理非今日记录
    state = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            raw = json.load(f)
        for key, val in raw.items():
            # 新格式: "name_type_date" → 直接带日期标签
            # 旧格式兼容: "name_type" → 加今日标签
            if isinstance(val, dict):
                # 新格式: {"ts": ..., "date": ...}
                if val.get("date") == today and now - val.get("ts", 0) < 30 * 60:
                    state[key] = val
            else:
                # 旧格式: 纯时间戳，加日期标签
                if now - val < 30 * 60:
                    state[f"{key}|{today}"] = {"ts": val, "date": today}

    new_alerts = []
    for a in alerts:
        key = f"{a['name']}_{a['type']}|{today}"
        # 也查兼容旧key
        old_key = f"{a['name']}_{a['type']}"
        last_time = 0
        if key in state:
            last_time = state[key].get("ts", state[key]) if isinstance(state[key], dict) else state[key]
        elif old_key in state:
            # 兼容读
            if isinstance(state.get(old_key), dict):
                if state[old_key].get("date") == today:
                    last_time = state[old_key].get("ts", 0)
            else:
                last_time = state.get(old_key, 0)
        if now - last_time > 30 * 60:
            state[key] = {"ts": now, "date": today}
            new_alerts.append(a)

    with open(state_file, 'w') as f:
        json.dump(state, f)

    return new_alerts

# 钉钉推送统一入口（复用 send_to_dingtalk 模块的密钥管理）
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from send_to_dingtalk import send_markdown

def push_alerts(alerts):
    """通过 send_to_dingtalk 统一模块推送预警到群"""
    if not alerts:
        return

    now = datetime.now(TZ)
    hour = now.hour
    if hour >= 14:
        title = "⚡ 尾盘监测"
        header = "⚡ 尾盘监测"
    elif hour >= 13:
        title = "⚡ 午后监测"
        header = "⚡ 午后监测"
    elif hour >= 10:
        title = "⚡ 盘中价格预警"
        header = "⚡ 盘中价格预警"
    else:
        title = "⚡ 早盘价格预警"
        header = "⚡ 早盘价格预警"

    lines = [header]
    for a in alerts:
        lines.append(f"- {a['msg']}")
    text = '\n'.join(lines)

    try:
        send_markdown(text, title=title)
        print(f"[price_watch] 预警已推送: {len(alerts)} 条")
    except Exception as e:
        print(f"[price_watch] 推送异常: {e}")

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
    anomaly_alerts = check_intraday_anomalies(prices)
    all_alerts = alerts + anomaly_alerts
    new_alerts = dedup_alerts(all_alerts)
    push_alerts(new_alerts)

if __name__ == '__main__':
    main()
