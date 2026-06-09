#!/usr/bin/env python3
"""
盘中价格监控 — 检测价格穿越支撑/阻力位，输出告警到 stdout。
由 Gateway Cron agentTurn 每 5 分钟调用，AI 汇总告警 → delivery 推送到群。
不自行推送（移除 openclaw message send 依赖，避免 crontab node: not found 问题）。
"""

import os, json, re, requests
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

SIGNALS_FILE = f"{PROJECT_DIR}/reports/trade_signals_{datetime.now(TZ).strftime('%Y%m%d')}.md"

SINA_MAP = {
    "sh000016": ("000016", "上证50"), "sh000300": ("000300", "沪深300"),
    "sh000688": ("000688", "科创50"), "sh601288": ("601288", "农业银行"),
    "sh601988": ("601988", "中国银行"), "sh600036": ("600036", "招商银行"),
    "sh600795": ("600795", "国电电力"), "sz000066": ("000066", "中国长城"),
    "sh600562": ("600562", "国睿科技"),
}


def load_thresholds():
    thresholds = {}
    if not os.path.exists(SIGNALS_FILE):
        return thresholds
    with open(SIGNALS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|") or "标的" in line or "---" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 9:
                continue
            name = parts[1]
            try:
                support = float(parts[4]) if parts[4] and parts[4] != "-" else None
            except ValueError:
                support = None
            try:
                resistance = float(parts[5]) if parts[5] and parts[5] != "-" else None
            except ValueError:
                resistance = None
            if support or resistance:
                thresholds[name] = {"support": support, "resistance": resistance}
    return thresholds


def fetch_sina_prices():
    prices = {}
    sina_codes = list(SINA_MAP.keys())
    for i in range(0, len(sina_codes), 3):
        batch = sina_codes[i:i+3]
        url = "http://hq.sinajs.cn/list=" + ",".join(batch)
        try:
            resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                m = re.search(r'hq_str_(\w+)="(.+)"', line)
                if not m:
                    continue
                code, data = m.group(1), m.group(2)
                name = SINA_MAP.get(code, {}).get("name") if isinstance(SINA_MAP.get(code), dict) else SINA_MAP.get(code)
                # Find name from SINA_MAP
                name = None
                for k, v in SINA_MAP.items():
                    if k == code:
                        name = v[1]
                        break
                if not name:
                    continue
                fields = data.split(",")
                if code.startswith("sh0") or code.startswith("sz0"):
                    price = float(fields[1]) if fields[1] else 0
                    prev = float(fields[2]) if fields[2] else 0
                    high = float(fields[4]) if fields[4] else 0
                    low = float(fields[5]) if fields[5] else 0
                else:
                    price = float(fields[3]) if fields[3] and fields[3] != "0.000" else float(fields[1])
                    prev = float(fields[2]) if fields[2] else 0
                    high = float(fields[4]) if fields[4] else 0
                    low = float(fields[5]) if fields[5] else 0
                chg = (price / prev - 1) * 100 if prev else 0
                prices[name] = {
                    "price": round(price, 2), "chg_pct": round(chg, 2),
                    "high": round(high, 2), "low": round(low, 2),
                }
        except Exception as e:
            print(f"[sina error] {e}", flush=True)
    return prices


def check_breaches(prices, thresholds):
    alerts = []
    for name, t in thresholds.items():
        if name not in prices:
            continue
        price = prices[name]["price"]
        if t["support"] is not None and price <= t["support"]:
            gap = (t["support"] - price) / price * 100
            alerts.append(f"🔴 {name} 跌破支撑 {t['support']}（现价 {price:.2f}，破位 {gap:.1f}%）")
        if t["resistance"] is not None and price >= t["resistance"]:
            gap = (price - t["resistance"]) / t["resistance"] * 100
            alerts.append(f"🟢 {name} 突破阻力 {t['resistance']}（现价 {price:.2f}，超涨 {gap:.1f}%）")
    return alerts


def dedup_alerts_by_flip(alerts, prices, thresholds):
    """每个标的的支撑/阻力位只在状态翻转时推送一次。
    - 从"未穿越"→"已穿越"：推送
    - 持续穿越中：不推送
    - 回到另一侧后再次穿越：重新推送
    
    额外保护：全局冷却 30 分钟，任何推送后冷却期内不再推送。
    """
    state_file = f"{PROJECT_DIR}/logs/price_breach_state.json"
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
    else:
        state = {}

    # 全局冷却检查：距离上次推送不到30分钟，直接静默
    last_push_ts = state.get("__last_push_ts__", 0)
    now_ts = datetime.now(TZ).timestamp()
    if now_ts - last_push_ts < 1800:  # 30分钟
        return []  # 冷却期，不推送

    new_alerts = []
    for a in alerts:
        # 解析告警内容：🔴/🟢 名称 跌破/突破 价位
        import re as re2
        m = re2.match(r"([🔴🟢])\\s+(.+?)\\s+(跌破支撑|突破阻力)\\s+([\\d.]+)", a)
        if not m:
            new_alerts.append(a)
            continue
        direction, name, action, level = m.group(1), m.group(2), m.group(3), m.group(4)
        key = f"{name}|{action}|{level}"

        if "支撑" in action:
            # 检查是否还在支撑下方
            if name in prices and prices[name]["price"] > float(level):
                # 价格已经回到支撑上方，但告警还在报——说明数据有延迟，跳过
                continue
        else:
            # 检查是否还在阻力上方
            if name in prices and prices[name]["price"] < float(level):
                continue

        last_status = state.get(key)
        if last_status == "breached":
            # 之前已穿越，检查是否已回到另一侧（翻转回撤）
            if "支撑" in action:
                if name in prices and prices[name]["price"] > float(level):
                    state[key] = "normal"  # 翻回，允许下次穿越再推送
            else:
                if name in prices and prices[name]["price"] < float(level):
                    state[key] = "normal"
            continue  # 持续穿越中，不重复推送

        # 首次穿越 → 推送
        state[key] = "breached"
        new_alerts.append(a)

    # 有新的穿越告警 → 记录推送时间戳
    if new_alerts:
        state["__last_push_ts__"] = now_ts

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
    return new_alerts


def main():
    now = datetime.now(TZ)
    t = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    morning_end = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    afternoon_end = datetime.strptime("15:00", "%H:%M").time()

    in_session = (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)
    if not in_session:
        print("NO_ALERT: outside trading hours", flush=True)
        return

    thresholds = load_thresholds()
    if not thresholds:
        print("NO_ALERT: no trade_signals file found", flush=True)
        return

    prices = fetch_sina_prices()
    if not prices:
        print("NO_ALERT: price fetch failed", flush=True)
        return

    alerts = check_breaches(prices, thresholds)
    new_alerts = dedup_alerts_by_flip(alerts, prices, thresholds)

    if new_alerts:
        print("## ⚡ 盘中价格预警", flush=True)
        for a in new_alerts:
            print(f"- {a}", flush=True)
    else:
        print("NO_ALERT", flush=True)


if __name__ == "__main__":
    main()
