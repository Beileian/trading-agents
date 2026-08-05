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
from price_fetcher import PriceFetcher

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
    "sh562500": ("562500", "机器人ETF"),
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
    """从多数据源获取实时价格。指数优先东方财富(本地→VPS)，个股用新浪。"""
    prices = {}
    sina_codes = list(SINA_MAP.keys())

    # ── VPS代理拉取东方财富（硅谷IP不被阻断）──
    def _east_via_vps(east_codes: list) -> dict:
        """通过SSH到硅谷VPS拉取东方财富数据。east_codes 可以是 (ec, type) 元组列表"""
        import subprocess
        result = {}
        try:
            # 兼容旧格式(纯字符串列表)和新格式((ec,type)列表)
            ec_formatted = []
            for item in east_codes:
                if isinstance(item, tuple):
                    ec_formatted.append(list(item))
                else:
                    ec_formatted.append([item, 'stock'])
            codes_json = json.dumps(ec_formatted)
            vps_script = '''
import urllib.request, json, sys
east_codes = json.loads(sys.argv[1])
results = {}
for item in east_codes:
    ec, etype = item[0], item[1] if len(item) > 1 else "stock"
    if not ec:
        continue
    url = "https://push2.eastmoney.com/api/qt/stock/get?secid=" + ec + "&fields=f43,f44,f45,f46,f47,f57,f58,f60,f170"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get("data"):
            dd = data["data"]
            div = 1000 if etype == "etf" else 100
            results[dd.get("f57", ec)] = {
                "price": dd.get("f43", 0) / div if dd.get("f43") else None,
                "high": dd.get("f44", 0) / div if dd.get("f44") else None,
                "low": dd.get("f45", 0) / div if dd.get("f45") else None,
                "open": dd.get("f46", 0) / div if dd.get("f46") else None,
                "prev_close": dd.get("f60", 0) / div if dd.get("f60") else None,
                "volume": dd.get("f47", 0),
                "change_pct": dd.get("f170", 0) / 100 if dd.get("f170") else None,
            }
    except Exception:
        continue
print(json.dumps(results, ensure_ascii=False))
'''
            proc = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                 "root@49.51.33.96", "python3", "-c", vps_script, codes_json],
                capture_output=True, text=True, timeout=15
            )
            if proc.returncode == 0 and proc.stdout.strip():
                result = json.loads(proc.stdout.strip())
        except Exception as e:
            print(f"[eastmoney VPS] SSH fallback failed: {e}", file=sys.stderr)
        return result

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
            east_codes.append('')
        # Note: price_watch SINA_MAP不含562500，本条仅在日后加入ETF时生效

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

    # ── 东方财富 VPS代理（硅谷IP不被阻断）──
    if not east_prices and any(ec for ec in east_codes if ec):
        try:
            vps_data = _east_via_vps(east_codes)
            if vps_data:
                east_prices.update(vps_data)
                print(f"[eastmoney VPS] 获取{len(vps_data)}只标的实时数据", file=sys.stderr)
        except Exception as ve:
            print(f"[eastmoney VPS] 代理失败: {ve}", file=sys.stderr)

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
                    # 东方财富不可用，新浪fallback
                    # 新浪指数：fields[1]=今开, fields[2]=昨收, fields[3]=当前价, fields[4]=最高, fields[5]=最低
                    open_price = float(fields[1])
                    prev_close = float(fields[2])
                    # fields[3]在交易时段是实时价(2026-06-26实测: 4894.09)
                    current = float(fields[3]) if fields[3] and fields[3] != '0.000' else None
                    if current and current > 0:
                        price = current
                    else:
                        price = open_price  # 非交易时段fallback
                    high = float(fields[4]) if fields[4] else price
                    low = float(fields[5]) if fields[5] else price
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

def check_breaches(prices, thresholds, pp_map=None, consistency_gate=1.5):
    """检测价格穿越支撑/阻力位。
    pp_map: {name: PricePoint} 用于源一致性门。
    consistency_gate: 主备源偏差%超过该值时不触发穿越预警（数据不可信），降级为状态提示。
    """
    alerts = []
    degraded = []  # 源偏差过大降级的状态提示（不推送为穿越预警）
    for name, t in thresholds.items():
        if name not in prices:
            continue
        price = prices[name]['price']
        support = t['support']
        resistance = t['resistance']

        # 源一致性门：偏差过大时数据不可信，不触发穿越
        dev = 0.0
        if pp_map and name in pp_map:
            dev = getattr(pp_map[name], 'deviation_pct', 0.0) or 0.0
        gate_breach = dev > consistency_gate

        if price <= support:
            gap = (support - price) / price * 100
            item = {
                'name': name, 'price': price, 'type': '支撑',
                'level': support, 'gap': gap,
                'msg': f"🔴 {name} 跌破支撑 {support}（现价 {price:.2f}，破位 {gap:.1f}%）"
            }
            if gate_breach:
                item['msg'] = f"⚠️ {name} 盘中触及支撑 {support}（现价 {price:.2f}，源偏差{dev:.1f}%，待确认）"
                degraded.append(item)
            else:
                alerts.append(item)

        if price >= resistance:
            gap = (price - resistance) / resistance * 100
            item = {
                'name': name, 'price': price, 'type': '阻力',
                'level': resistance, 'gap': gap,
                'msg': f"🟢 {name} 突破阻力 {resistance}（现价 {price:.2f}，超涨 {gap:.1f}%）"
            }
            if gate_breach:
                item['msg'] = f"⚠️ {name} 盘中触及阻力 {resistance}（现价 {price:.2f}，源偏差{dev:.1f}%，待确认）"
                degraded.append(item)
            else:
                alerts.append(item)

    return alerts, degraded


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
        # 条件: 非指数 + 当前价从开盘价拉升 > 2%，仅在开盘后 90 分钟内检测（避免午后重复触发）
        if open_price > 0 and 9*60+30 <= minutes <= 11*60:
            open_rise = (price - open_price) / open_price * 100
            if open_rise > 2:
                alerts.append({
                    'name': name, 'price': price,
                    'type': '开盘异动',
                    'msg': f"🔥 {name} 开盘拉升+{open_rise:.1f}%（从{open_price:.2f}→{price:.2f}）"
                })

        # ── 信号3: 尾盘拉升 ──
        # 条件: 14:30后 + 从日内低点拉回 > 1.5%（放宽至14:30便于尾盘异动捕捉）
        if 14*60+30 <= minutes <= 15*60 and low > 0:
            recovery = (price - low) / low * 100
            if recovery > 1.5:
                alerts.append({
                    'name': name, 'price': price,
                    'type': '尾盘异动',
                    'msg': f"📈 {name} 尾盘拉升+{recovery:.1f}%（低{low:.2f}→{price:.2f}）"
                })

    return alerts

def dedup_alerts(alerts, slot=None, namespace=""):
    """去重：同一标的同方向同时段内不重复（slot 维度），跨日自动清理旧状态。
    slot: early(早盘)/mid(盘中)/afternoon(午后)/tail(尾盘)，None 时按当前时间推断。
    namespace: 区分穿越/降级两条去重链（午后异动转降级提示用）。
    实现单标的单时段推送上限（每时段每类型最多 1 条）。"""
    state_file = f"{PROJECT_DIR}/logs/price_alerts_state.json"
    os.makedirs(os.path.dirname(state_file), exist_ok=True)

    today = datetime.now(TZ).strftime("%Y%m%d")
    now = datetime.now(TZ).timestamp()

    # 推断时段
    if slot is None:
        h = datetime.now(TZ).hour
        slot = 'tail' if h >= 14 else ('afternoon' if h >= 13 else ('mid' if h >= 10 else 'early'))

    # 读取旧状态，清理非今日记录
    state = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            raw = json.load(f)
        for key, val in raw.items():
            if isinstance(val, dict) and val.get("date") == today:
                state[key] = val

    new_alerts = []
    for a in alerts:
        key = f"{namespace}{a['name']}_{a['type']}_{slot}|{today}"
        last_ts = state.get(key, {}).get("ts", 0) if isinstance(state.get(key), dict) else 0
        if now - last_ts > 30 * 60:
            state[key] = {"ts": now, "date": today}
            new_alerts.append(a)

    with open(state_file, 'w') as f:
        json.dump(state, f)

    return new_alerts


def log_alerts_jsonl(alerts, source_info):
    """预警命中率闭环：每条预警写 JSONL，供收盘复盘比对。
    记录: ts, name, type, price, level, source, source_chain, deviation
    """
    if not alerts:
        return
    today = datetime.now(TZ).strftime("%Y%m%d")
    log_file = f"{PROJECT_DIR}/logs/price_alerts_{today}.jsonl"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    now_iso = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a') as f:
        for a in alerts:
            name = a['name']
            info = source_info.get(name, {})
            rec = {
                "ts": now_iso,
                "name": name,
                "type": a.get('type'),
                "price": a.get('price'),
                "level": a.get('level'),
                "source": info.get('source'),
                "source_chain": info.get('source_chain'),
                "deviation": info.get('deviation_pct'),
                "degraded": a.get('degraded', False),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# 钉钉推送统一入口（复用 send_to_dingtalk 模块的密钥管理）
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from send_to_dingtalk import send_markdown

def push_alerts(alerts, degraded=None, merged=False):
    """通过 send_to_dingtalk 统一模块推送预警到群。
    degraded: 源偏差过大的降级状态提示（不单独推送，仅合并进摘要）。
    merged: 时段摘要模式（午后/尾盘无穿越时合并为一条）。
    """
    alerts = alerts or []
    degraded = degraded or []
    if not alerts and not degraded:
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
    # 降级提示合并进摘要（状态提示，非穿越预警）
    if degraded and not alerts:
        lines.append("（源交叉偏差过大，穿越待确认）")
        for d in degraded:
            lines.append(f"- {d['msg']}")
    text = '\n'.join(lines)

    try:
        send_markdown(text, title=title)
        print(f"[price_watch] 预警已推送: {len(alerts)} 条" + (f" + {len(degraded)} 状态提示" if degraded else ""))
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

    # 主数据源：多源交叉验证（price_fetcher）
    pf = PriceFetcher()
    price_points = pf.fetch_all()
    pp_map = {}
    if not price_points:
        # 备用：智兔
        prices = fetch_zhitu_prices()
    else:
        # 转换 PricePoint → 旧prices格式（兼容check_breaches），同时保留 pp_map 供一致性门
        prices = {}
        for name, pp in price_points.items():
            if pp.quality == "stale" or pp.price == 0:
                continue
            prices[name] = {
                'price': pp.price,
                'change_pct': pp.change_pct,
                'high': pp.high,
                'low': pp.low,
                'prev_close': pp.prev_close,
                'open': pp.open,
                'volume': 0,
            }
            pp_map[name] = pp
        if not prices:
            prices = fetch_zhitu_prices()
    if not prices:
        return

    # 穿越预警 + 源一致性门（偏差>1.5% 降级为状态提示）
    alerts, degraded = check_breaches(prices, thresholds, pp_map=pp_map, consistency_gate=1.5)
    anomaly_alerts = check_intraday_anomalies(prices)

    # 噪音控制：异动信号（935/开盘/尾盘）早盘+盘中即时推；午后/尾盘转状态提示合并推送
    hour = now.hour
    anomaly_slot_allowed = hour < 14  # 午后(>=14)起异动不再即时推
    if anomaly_slot_allowed:
        push_target = alerts + anomaly_alerts
        degraded_target = degraded
    else:
        push_target = alerts
        degraded_target = degraded + [dict(a, degraded=True) for a in anomaly_alerts]
        if anomaly_alerts:
            print(f"[price_watch] 午后/尾盘异动 {len(anomaly_alerts)} 条转为状态提示（合并推送）")

    new_alerts = dedup_alerts(push_target)
    new_degraded = dedup_alerts(degraded_target, namespace="dg_")
    push_alerts(new_alerts, degraded=new_degraded)

    # 预警命中率闭环：记录 JSONL（穿越+降级+异动都记，供收盘复盘验证）
    source_info = {name: {
        'source': pp.source, 'source_chain': pp.source_chain,
        'deviation_pct': pp.deviation_pct,
    } for name, pp in pp_map.items()}
    log_alerts_jsonl(alerts + degraded + anomaly_alerts, source_info)

if __name__ == '__main__':
    main()
