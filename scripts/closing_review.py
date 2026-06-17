#!/usr/bin/env python3
"""
收盘复盘 — 比照今日研判 vs 实际走势，逐标的检查触发条件穿越情况。
认知闭环：方向验证 + 幅度评估 + 触发条件穿越检测 → 推送「谈股论金奔富」群。

v2.6.0: 收盘价数据准确性三重保障 — 新浪时间戳校验 + 腾讯多源交叉 + 数据源溯源摘要
v1: 基础实现 — 覆盖外盘研判方向验证 + 9标的触发穿越 + 认知模式记录
"""

import os, sys, json, re, requests, subprocess
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
OVERSEAS_DIR = "/root/.openclaw/workspace/projects/overseas-morning-brief"
CHAT_ID = "cidY4mlx+J2kNFpTiWFgQ0gkg=="

NOW = datetime.now(TZ)
TODAY_TAG = NOW.strftime("%Y%m%d")
TODAY_DATE = NOW.strftime("%Y-%m-%d")

# ── Sina realtime ────────────────────────────────
SINA_MAP = {
    "sh000016": ("上证50"), "sh000300": ("沪深300"), "sh000688": ("科创50"),
    "sh601288": ("农业银行"), "sh601988": ("中国银行"), "sh600036": ("招商银行"),
    "sh600795": ("国电电力"), "sz000066": ("中国长城"), "sh600562": ("国睿科技"),
    "sh562500": ("中证机器人"),
}

ALL_NAMES = ["上证50", "沪深300", "科创50", "农业银行", "中国银行",
             "招商银行", "国电电力", "中国长城", "国睿科技", "中证机器人"]


# ── 指数 → AKShare symbol 映射（多源交叉比对用） ──
AKSHARE_INDEX_MAP = {
    "上证50": "sh000016",
    "沪深300": "sh000300",
    "科创50": "sh000688",
}


def _fetch_akshare_close(index_name):
    """从AKShare获取指数收盘价作为交叉校验源，失败或日期不匹配返回None"""
    try:
        import akshare as ak
        from datetime import datetime, timezone, timedelta
        symbol = AKSHARE_INDEX_MAP.get(index_name)
        if not symbol:
            return None
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        # 必须匹配今天日期才参与校验
        akshare_date = str(latest['date'])[:10]
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        if akshare_date != today_str:
            return None
        return float(latest['close'])
    except Exception:
        return None


TENCENT_MAP = {
    "上证50": "sh000016", "沪深300": "sh000300", "科创50": "sh000688",
    "农业银行": "sh601288", "中国银行": "sh601988", "招商银行": "sh600036",
    "国电电力": "sh600795", "中国长城": "sz000066", "国睿科技": "sh600562",
    "中证机器人": "sh562500",
}

def _fetch_tencent_close_price(name):
    """从腾讯实时行情获取收盘价，失败返回 None"""
    code = TENCENT_MAP.get(name)
    if not code:
        return None
    try:
        resp = requests.get(f"http://qt.gtimg.cn/q={code}", timeout=5)
        resp.encoding = "gbk"
        m = re.search(r'="(.+)"', resp.text)
        if not m:
            return None
        fields = m.group(1).split("~")
        if len(fields) < 35:
            return None
        # 腾讯: fields[3]=当前价, fields[30]=日期, fields[31]=时间
        ts_date = fields[30] if len(fields) > 30 else ""
        price = float(fields[3]) if fields[3] and fields[3] != "0.000" else None
        return {"price": price, "date": ts_date}
    except Exception:
        return None

def fetch_close_prices():
    """从新浪获取今日收盘价，多源交叉校验"""
    codes = list(SINA_MAP.keys())
    url = "http://hq.sinajs.cn/list=" + ",".join(codes)
    resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
    resp.encoding = "gbk"

    prices = {}
    today_str = NOW.strftime("%Y-%m-%d")

    for line in resp.text.strip().split("\n"):
        m = re.search(r'hq_str_(\w+)="(.+)"', line)
        if not m:
            continue
        code, data = m.group(1), m.group(2)
        name = SINA_MAP.get(code)
        if not name:
            continue
        fields = data.split(",")

        # ① 时间戳校验 — 防止新浪返回过期数据
        sina_date = fields[30] if len(fields) > 30 else ""
        if sina_date and sina_date != today_str:
            print(f"[时间戳校验] {name}: 新浪返回{sina_date}≠{today_str}，数据过期，跳过")
            continue

        price = float(fields[3]) if fields[3] and fields[3] != "0.000" else float(fields[1])
        prev = float(fields[2]) if fields[2] else 0
        high = float(fields[4]) if fields[4] else 0
        low = float(fields[5]) if fields[5] else 0
        data_source = "sina"

        # ② 指数：AKShare交叉校验
        if name in AKSHARE_INDEX_MAP:
            akshare_close = _fetch_akshare_close(name)
            if akshare_close is not None:
                deviation = abs(price - akshare_close) / akshare_close * 100 if akshare_close else 0
                if deviation > 0.5:
                    print(f"[交叉校验] {name}: 新浪={price:.2f} vs AKShare={akshare_close:.2f} 偏差={deviation:.2f}%，采用AKShare")
                    price = akshare_close
                    data_source = "akshare"

        # ③ 个股：腾讯交叉校验
        if data_source == "sina" and name in TENCENT_MAP:
            tencent = _fetch_tencent_close_price(name)
            if tencent and tencent.get("price") and tencent.get("date") == today_str:
                deviation = abs(price - tencent["price"]) / tencent["price"] * 100 if tencent["price"] else 0
                if deviation > 0.5:
                    # 取两者中与昨收更合理的方向
                    sina_chg = (price - prev) / prev * 100 if prev else 0
                    tc_prev = float(fields[2]) if fields[2] else 0
                    tc_chg = (tencent["price"] - tc_prev) / tc_prev * 100 if tc_prev else 0
                    if abs(tc_chg) < abs(sina_chg * 2):  # 腾讯变化更合理
                        print(f"[交叉校验] {name}: 新浪={price:.2f} vs 腾讯={tencent['price']:.2f} 偏差={deviation:.2f}%，采用腾讯")
                        price = tencent["price"]
                        data_source = "tencent"

        chg_pct = (price / prev - 1) * 100 if prev else 0
        prices[name] = {
            "price": round(price, 2), "chg_pct": round(chg_pct, 2),
            "high": round(high, 2), "low": round(low, 2),
            "source": data_source,
        }

    # ④ 完整性断言摘要
    if prices:
        sources = {}
        for n, v in prices.items():
            s = v.get("source", "?")
            sources[s] = sources.get(s, 0) + 1
        src_summary = ", ".join(f"{s}:{c}" for s, c in sources.items())
        print(f"  收盘价来源: {src_summary} | 总计{len(prices)}只")

    return prices


def load_thresholds():
    """从今日交易推荐报告读取支撑/阻力/操作/仓位
    兼容两种格式：段落式（v2.4.0+）和表格（旧版）"""
    signals_file = f"{PROJECT_DIR}/reports/trade_signals_{TODAY_TAG}.md"
    if not os.path.exists(signals_file):
        return {}

    thresholds = {}
    with open(signals_file) as f:
        text = f.read()

    # 段落式格式：
    # 🟡 上证50  2913.99  乖离+0.3% ↑  持有
    #   支撑2887.94 / 阻力2915.90
    #   触发: 突破2915.90加仓 / 跌破2887.94减仓
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 匹配标的行：有价格和操作建议
        m = re.match(r'^[🟢🟡🔴🟠⚪]\s*(.+?)\s+([\d.]+)\s+乖离', line)
        if m:
            name = m.group(1).strip()
            # 下一行：支撑/阻力
            sup = None
            res = None
            op = "hold"
            trigger = ""
            pos = 0
            # 从当前行提取操作建议
            if '卖出' in line or '卖出' in line:
                op = 'sell'
            elif '买入' in line or '买入' in line:
                op = 'buy'
            # 看后续2行
            for j in range(i+1, min(i+4, len(lines))):
                sub = lines[j].strip()
                if '支撑' in sub and '/' in sub:
                    parts = re.findall(r'([\d.]+)', sub)
                    if len(parts) >= 2:
                        sup = float(parts[0])
                        res = float(parts[1])
                if '触发' in sub:
                    trigger = sub.replace('触发:', '').replace('触发：', '').strip()
                # 风险行跳过
                if sub.startswith('风险'):
                    continue
            if name and (sup or res):
                thresholds[name] = {
                    "support": sup,
                    "resistance": res,
                    "op": op,
                    "pos": pos,
                    "trigger": trigger,
                }
        i += 1

    if thresholds:
        return thresholds

    # fallback：旧版表格格式
    for line in text.split('\n'):
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
        op_raw = parts[6] if len(parts) > 6 else ""
        op = "hold"
        if "卖出" in op_raw:
            op = "sell"
        elif "买入" in op_raw:
            op = "buy"
        try:
            pos_str = parts[7].strip() if len(parts) > 7 else "0%"
            m = re.search(r'\d+', pos_str)
            pos = int(m.group()) if m else 0
        except (ValueError, IndexError):
            pos = 0
        trigger = parts[8] if len(parts) > 8 else ""
        thresholds[name] = {
            "support": support,
            "resistance": resistance,
            "op": op,
            "pos": pos,
            "trigger": trigger,
        }
    return thresholds


def load_overseas_direction():
    """从外盘研判信号文件提取方向"""
    overseas_file = f"{OVERSEAS_DIR}/reports/overseas_signal_{TODAY_DATE}.md"
    if not os.path.exists(overseas_file):
        return None

    with open(overseas_file) as f:
        text = f.read()

    m = re.search(r"\*\*研判方向\*\*:\s*(.+?)(?:\s*\||\n)", text)
    return m.group(1).strip() if m else None


def load_overseas_confidence():
    """从外盘信号文件提取置信度"""
    overseas_file = f"{OVERSEAS_DIR}/reports/overseas_signal_{TODAY_DATE}.md"
    if not os.path.exists(overseas_file):
        return None

    with open(overseas_file) as f:
        text = f.read()

    m = re.search(r"\*\*置信度\*\*:\s*(.+)", text)
    return m.group(1).strip() if m else None


def check_breaches(prices, thresholds):
    """检测触发条件穿越"""
    breaches = []
    for name, t in thresholds.items():
        if name not in prices:
            continue
        p = prices[name]

        # Support breach
        if t["support"] is not None and p["price"] <= t["support"]:
            gap = (t["support"] - p["price"]) / p["price"] * 100
            breaches.append({
                "name": name, "direction": "support", "gap": round(gap, 1),
                "level": t["support"],
                "summary": f"🔴 {name} 跌破支撑 {t['support']}（收 {p['price']:.2f}，破位 {gap:.1f}%）",
            })

        # Resistance breach
        if t["resistance"] is not None and p["price"] >= t["resistance"]:
            gap = (p["price"] - t["resistance"]) / t["resistance"] * 100
            breaches.append({
                "name": name, "direction": "resistance", "gap": round(gap, 1),
                "level": t["resistance"],
                "summary": f"🟢 {name} 突破阻力 {t['resistance']}（收 {p['price']:.2f}，超涨 {gap:.1f}%）",
            })
    return breaches


def summarize_cognition(prices, thresholds, overseas_dir, overseas_conf):
    """认知模式摘要：方向验证 + 标的交叉对比 + 触发穿越"""
    lines = []

    # ── 1. 外盘方向验证 ──
    if overseas_dir:
        bearish = "偏空" in overseas_dir or "看跌" in overseas_dir
        bullish = "偏多" in overseas_dir or "看涨" in overseas_dir

        index_codes = {"上证50": None, "沪深300": None, "科创50": None}
        for k in index_codes:
            if k in prices:
                index_codes[k] = prices[k]["chg_pct"]

        vals = [v for v in index_codes.values() if v is not None]
        avg_idx_chg = sum(vals) / len(vals) if vals else 0

        if avg_idx_chg < -0.5:
            actual_dir = "偏空"
        elif avg_idx_chg > 0.5:
            actual_dir = "偏多"
        else:
            actual_dir = "震荡"

        dir_match = "吻合" if (
            (bearish and actual_dir == "偏空")
            or (bullish and actual_dir == "偏多")
        ) else "偏离"

        lines.append(f"外盘研判：预判 {overseas_dir}（置信度 {overseas_conf or '?'}）→ 实际 {actual_dir} {dir_match}")
        lines.append(f"三大指数：上证50 {index_codes['上证50']:+.2f}% / 沪深300 {index_codes['沪深300']:+.2f}% / 科创50 {index_codes['科创50']:+.2f}%（均 {avg_idx_chg:+.2f}%）")

    # ── 2. 极端波动检测 ──
    extreme_stocks = []
    for name, p in prices.items():
        if abs(p["chg_pct"]) >= 3:
            extreme_stocks.append(f"{name} {p['chg_pct']:+.2f}%")
    if extreme_stocks:
        lines.append(f"极端波动（|涨跌|≥3%）：{'，'.join(extreme_stocks)}")

    # ── 3. 标的交叉对比：早盘建议 vs 实际走势 ──
    sell_wrong = []
    for name in ALL_NAMES:
        if name not in prices or name not in thresholds:
            continue
        p = prices[name]
        t = thresholds[name]
        op = t.get("op", "")
        chg = p["chg_pct"]
        if op == "sell" and chg > 0.5:
            sell_wrong.append(f"{name} +{chg:.1f}%")
    if sell_wrong:
        lines.append(f"卖出误判：{len(sell_wrong)} 只（{'，'.join(sell_wrong)}）")

    # ── 4. 触发穿越 ──
    breaches = check_breaches(prices, thresholds)
    if breaches:
        b_names = {b['name'] for b in breaches}
        b_lines = [b['summary'] for b in breaches]
        lines.append(f"触发穿越：{len(breaches)} 条 — {'；'.join(b_lines)}")
    else:
        lines.append("触发穿越：无")

    return lines


    lines = []
    lines.append("**⑥ 市场温度计**")
    lines.append("")
    
    try:
        import sys
        sys.path.insert(0, os.path.join(PROJECT_DIR, 'scripts'))
        from style_rotation_signals import (
            compute_market_temperature, DATA_CACHE
        )
        
        temp = compute_market_temperature()
        if not temp.signals:
            lines.append("信号模块未产出任何指标")
            return lines
        
        # 逐信号报告，每行一条
        for s in temp.signals:
            note = ""
            if s.data_conf <= 2:
                note = " [低置信]"
            elif s.data_conf == 3:
                note = " [替代源]"
            lines.append(f"{s.emoji()} {s.name}: {s.value} {s.conf_tag()}{note}")
        
        lines.append("")
        
        # 与昨日信号对比
        today_tag = NOW.strftime("%Y%m%d")
        today_file = os.path.join(PROJECT_DIR, 'logs', 'cognition_daily', f'{today_tag}.json')
        changes = []
        if os.path.exists(today_file):
            with open(today_file) as f:
                prev = json.load(f)
            prev_signals = prev.get('market_temperature', {}).get('signals', [])
            if prev_signals:
                for s in temp.signals:
                    for ps in prev_signals:
                        if ps.get('name') == s.name:
                            dv_old = ps.get('value', '')
                            dv_new = s.value
                            if dv_old != dv_new:
                                changes.append(f"{s.emoji()} {s.name}: {dv_old} -> {dv_new}")
                            break
        if changes:
            lines.append(f"温度计变化: {'; '.join(changes)}")
            lines.append("")
        
        # 将当日信号写入 cognition_daily 供明天对比
        daily_dir = os.path.join(PROJECT_DIR, 'logs', 'cognition_daily')
        os.makedirs(daily_dir, exist_ok=True)
        daily_file = os.path.join(daily_dir, f'{today_tag}.json')
        existing = {}
        if os.path.exists(daily_file):
            try:
                with open(daily_file) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing['market_temperature'] = temp.to_dict()
        with open(daily_file, 'w') as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        
    except Exception as e:
        lines.append(f"温度计获取失败: {e}")
    
    lines.append("")
    return lines


def review_signal_quality() -> list[str]:
    """市场温度计 + 昨日对比 + 本周认知写入，返回报告行列表"""
    lines = []
    lines.append("**⑥ 市场温度计**")
    lines.append("")
    
    try:
        import sys
        sys.path.insert(0, os.path.join(PROJECT_DIR, 'scripts'))
        from style_rotation_signals import (
            compute_market_temperature, DATA_CACHE
        )
        
        temp = compute_market_temperature()
        if not temp.signals:
            lines.append("信号模块未产出任何指标")
            return lines
        
        for s in temp.signals:
            note = ""
            if s.data_conf <= 2:
                note = " [低]"
            elif s.data_conf == 3:
                note = " [替]"
            lines.append(f"{s.emoji()} {s.name}: {s.value} {s.conf_tag()}{note}")
        
        lines.append("")
        
        # 与昨日对比
        today_tag = NOW.strftime("%Y%m%d")
        today_file = os.path.join(PROJECT_DIR, 'logs', 'cognition_daily', f'{today_tag}.json')
        if os.path.exists(today_file):
            with open(today_file) as f:
                prev = json.load(f)
            prev_signals = prev.get('market_temperature', {}).get('signals', [])
            changes = []
            if prev_signals:
                for s in temp.signals:
                    for ps in prev_signals:
                        if ps.get('name') == s.name:
                            if ps.get('value', '') != s.value:
                                changes.append(f"{s.emoji()} {s.name}: {ps['value']} -> {s.value}")
                            break
            if changes:
                lines.append(f"较昨日: {'; '.join(changes)}")
                lines.append("")
        
        # 写入 cognition_daily 供明天对比
        daily_dir = os.path.join(PROJECT_DIR, 'logs', 'cognition_daily')
        os.makedirs(daily_dir, exist_ok=True)
        daily_file = os.path.join(daily_dir, f'{today_tag}.json')
        existing = {}
        if os.path.exists(daily_file):
            try:
                with open(daily_file) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing['market_temperature'] = temp.to_dict()
        with open(daily_file, 'w') as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        
    except Exception as e:
        lines.append(f"温度计获取失败: {e}")
    
    lines.append("")
    return lines


def update_cognition_state(prices, thresholds, overseas_dir, overseas_conf):
    """Writes structured review metrics to cognition_state.json for next-day feedback."""
    state_file = f"{PROJECT_DIR}/logs/cognition_state.json"
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass

    # ── 1. Overseas direction match ──
    overseas_match = None
    overseas_actual = None
    if overseas_dir:
        bearish = "偏空" in overseas_dir
        bullish = "偏多" in overseas_dir
        idx_pcts = [prices[n]["chg_pct"] for n in ["上证50", "沪深300", "科创50"] if n in prices]
        avg_idx = sum(idx_pcts) / len(idx_pcts) if idx_pcts else 0
        if avg_idx < -0.5:
            overseas_actual = "偏空"
        elif avg_idx > 0.5:
            overseas_actual = "偏多"
        else:
            overseas_actual = "震荡"
        overseas_match = "吻合" if (
            (bearish and overseas_actual == "偏空") or
            (bullish and overseas_actual == "偏多")
        ) else "偏离"

    # ── 2. Sell mis-rate ──
    sell_wrong = []
    for name in ALL_NAMES:
        if name not in prices or name not in thresholds:
            continue
        t = thresholds[name]
        if t.get("op") == "sell" and prices[name]["chg_pct"] > 0.5:
            sell_wrong.append(name)
    total_sell = sum(1 for n in ALL_NAMES if n in thresholds and thresholds[n].get("op") == "sell")
    sell_misrate = len(sell_wrong) / total_sell if total_sell > 0 else 0

    # ── 3. Breach hit rate ──
    breaches = check_breaches(prices, thresholds)
    breach_count = len(breaches)
    breach_names = list(set(b["name"] for b in breaches))
    total_with_thresholds = sum(1 for n in ALL_NAMES if n in thresholds and (
        thresholds[n].get("support") is not None or thresholds[n].get("resistance") is not None
    ))
    breach_hit_rate = breach_count / total_with_thresholds if total_with_thresholds > 0 else 0

    # ── 4. Update rolling histories ──
    m = state.get("metrics", {})

    # Overseas accuracy
    oa = m.get("overseas_direction_accuracy", {})
    oa_hist = oa.get("history", [])
    oa_hist.append(1 if overseas_match == "吻合" else 0)
    oa_hist = oa_hist[-5:]
    oa_5d = sum(oa_hist) / len(oa_hist) if oa_hist else None
    oa["history"] = oa_hist
    oa["rolling_5d"] = oa_5d
    if oa_5d is not None:
        oa["label"] = "accurate" if oa_5d >= 0.6 else ("unreliable" if oa_5d <= 0.4 else "neutral")

    # Sell misrate
    sm = m.get("sell_misrate", {})
    sm_hist = sm.get("history", [])
    sm_hist.append(sell_misrate)
    sm_hist = sm_hist[-3:]
    sm_3d = sum(sm_hist) / len(sm_hist) if sm_hist else None
    sm["history"] = sm_hist
    sm["rolling_3d"] = sm_3d
    if sm_3d is not None:
        sm["label"] = "high_efficiency" if sm_3d <= 0.3 else ("low_efficiency" if sm_3d >= 0.5 else "neutral")

    # Breach hit rate
    bh = m.get("breach_hit_rate", {})
    bh_hist = bh.get("history", [])
    bh_hist.append(int(breach_count))
    bh_hist = bh_hist[-3:]
    bh_3d = sum(bh_hist) / len(bh_hist) if bh_hist else None
    bh["history"] = bh_hist
    bh["rolling_3d"] = bh_3d
    if bh_3d is not None:
        bh["label"] = "high_signal" if bh_3d >= 2 else ("low_signal" if bh_3d <= 0.5 else "neutral")

    state["metrics"] = {
        "overseas_direction_accuracy": oa,
        "sell_misrate": sm,
        "breach_hit_rate": bh,
    }

    # ── 5. Last review snapshot ──
    extreme_stocks = [f"{n} {prices[n]['chg_pct']:+.2f}%" for n in ALL_NAMES
                      if n in prices and abs(prices[n]["chg_pct"]) >= 3]

    # Cognitive tag
    morning_file = f"{OVERSEAS_DIR}/reports/morning_brief_{TODAY_DATE}.md"
    tag = "技术面驱动型"
    if os.path.exists(morning_file):
        with open(morning_file) as f:
            mb = f.read()
        if "VIX" in mb:
            tag = "VIX驱动型"
        elif "非农" in mb or "就业" in mb:
            tag = "宏观数据驱动型"
        elif "财报" in mb:
            tag = "财报驱动型"

    # Calibration hint for next-day recommendations
    hint = None
    if sm_3d is not None and sm_3d >= 0.5:
        hint = "sell_hold_bias"  # 卖出建议近期高误判 → 次日偏保守
    elif oa_5d is not None and oa_5d <= 0.4:
        hint = "overseas_unreliable"  # 外盘方向连续偏离 → 轻外盘重内盘
    elif bh_3d is not None and bh_3d >= 3:
        hint = "high_volatility"  # 穿越频繁 → 提高阈值敏感度

    state["last_review"] = {
        "date": TODAY_TAG,
        "overseas_predicted": overseas_dir,
        "overseas_actual": overseas_actual,
        "direction_match": overseas_match,
        "sell_wrong_count": len(sell_wrong),
        "sell_wrong_names": sell_wrong,
        "breach_count": breach_count,
        "breach_names": breach_names,
        "extreme_stocks": extreme_stocks,
        "cognitive_tag": tag,
        "calibration_hint": hint,
    }

    # ── 生成200-300字复盘摘要供次日推送 ──
    review_lines = []
    review_lines.append(f"{TODAY_TAG}收盘复盘。")

    # 指数表现
    idx_info = []
    for n in ['上证50', '沪深300', '科创50']:
        if n in prices:
            idx_info.append(f"{n} {prices[n]['chg_pct']:+.2f}%")
    if idx_info:
        review_lines.append(f"三大指数表现：{' / '.join(idx_info)}。")

    # 外盘验证
    if overseas_dir and overseas_actual:
        if overseas_match == '吻合':
            review_lines.append(f"盘前外盘研判{overseas_dir}，收盘方向吻合，外盘信号有效。")
        else:
            review_lines.append(f"盘前外盘研判{overseas_dir}，但收盘实际{overseas_actual}，方向偏离，外盘参考价值降权。")

    # 操作建议验证
    op_counts = {"sell": 0, "hold": 0, "buy": 0}
    for n in ALL_NAMES:
        if n in thresholds:
            op = thresholds[n].get("op", "")
            if op == "sell": op_counts["sell"] += 1
            elif op == "buy": op_counts["buy"] += 1
            else: op_counts["hold"] += 1
    total = sum(op_counts.values())
    review_lines.append(f"早盘{total}只标的推荐中，{op_counts['sell']}只卖出/{op_counts['hold']}只持有/{op_counts['buy']}只买入。")
    if sell_wrong:
        sw_str = "、".join(sell_wrong)
        review_lines.append(f"其中{sw_str}建议卖出但实际收涨，卖出判断面临反弹压力。")
    else:
        review_lines.append(f"卖出建议标的全部收跌，卖出信号有效。")

    # 穿越详情
    if breach_names:
        bn_str = "、".join(breach_names)
        vol_level = "波动剧烈" if breach_count >= 3 else "波动可控"
        review_lines.append(f"价格穿越方面，共触发{breach_count}条——"
                          f"{bn_str}日内击穿支撑或阻力，{vol_level}。")
    else:
        review_lines.append(f"无价格穿越触发，支撑阻力位保持有效。")

    # 极端波动
    if extreme_stocks:
        review_lines.append(f"极端波动（|涨跌|>=3%）：{'、'.join(extreme_stocks)}。")

    # 风格
    review_lines.append(f"当日市场风格：{tag}。")

    review_summary = "".join(review_lines)
    state["last_review"]["review_summary"] = review_summary

    state["last_updated"] = NOW.isoformat()
    state["last_trade_date"] = TODAY_TAG

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    # 每日快照存档 → 供周复盘纵向对比
    daily_dir = f"{PROJECT_DIR}/logs/cognition_daily"
    os.makedirs(daily_dir, exist_ok=True)
    daily_file = os.path.join(daily_dir, f"{TODAY_TAG}.json")
    with open(daily_file, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    # ── 追加报告索引行 ──
    index_file = f"{PROJECT_DIR}/reports/INDEX.md"
    index_date = NOW.strftime('%Y-%m-%d')
    index_weekday = ["周一","周二","周三","周四","周五","周六","周日"][NOW.weekday()]

    # Build index entry (reuse op_counts computed above for review_summary)
    op_str = f"{op_counts['sell']}卖出/{op_counts['hold']}持有"
    if op_counts['buy'] > 0:
        op_str += f"/{op_counts['buy']}买入"

    # Overseas
    overseas_str = overseas_dir or "--"
    if overseas_actual and overseas_match:
        overseas_str += f" → 实际{overseas_actual} {overseas_match}"

    # Sell wrong
    sell_str = ""
    if sell_wrong:
        sw = ",".join(sell_wrong)
        sell_str = f" | 卖出误判 {sw}"

    # Breach
    breach_str = ""
    if breach_names:
        bn = ",".join(breach_names)
        breach_str = f" | 触发穿越{breach_count}条（{bn}）"

    index_line = f"- [交易推荐](trade_signals_{TODAY_TAG}.md) — {op_str} | 外盘{overseas_str}{sell_str}{breach_str}"

    if os.path.exists(index_file):
        with open(index_file) as f:
            lines = f.readlines()
        # 找到"## {index_date}" 或插入新日期块
        new_lines = []
        date_header = f"## {index_date} {index_weekday}"
        found = False
        for i, line in enumerate(lines):
            if line.strip() == date_header:
                # 日期块已存在，追加报告行在交易推荐后
                new_lines.append(line)
                found = True
                continue
            if found and line.startswith("- [收盘复盘]"):
                # 已经有收盘复盘行，跳过
                new_lines.append(line)
                continue
            new_lines.append(line)
        if not found:
            # 插入到"## "开头的第一行之前（日期倒序）
            inserted = False
            for i, line in enumerate(new_lines):
                if line.startswith("## 202") and not inserted:
                    new_lines.insert(i, f"{date_header}\n\n{index_line}\n")
                    inserted = True
            if not inserted:
                new_lines.append(f"\n{date_header}\n\n{index_line}\n")
    else:
        new_lines = [
            "# 托董交易推荐系统 · 报告索引\n",
            "\n",
            "> 自动维护，每日收盘复盘时追加。点击文件名打开完整报告。\n",
            "\n",
            f"{date_header}\n",
            "\n",
            f"{index_line}\n",
            "\n",
        ]

    with open(index_file, "w") as f:
        f.writelines(new_lines)

    # ── 6. 追加评估数据集（Step 1: 认知闭环 → 手动评估基准） ──
    _append_eval_dataset(state, prices, thresholds, overseas_dir, overseas_conf, extreme_stocks, sell_wrong, breaches, tag)

    return hint


def _append_eval_dataset(state, prices, thresholds, overseas_dir, overseas_conf, extreme_stocks, sell_wrong, breaches, tag):
    """将当日结构化复盘数据追加到 cognition_dataset.jsonl"""
    from pathlib import Path
    eval_dir = Path(PROJECT_DIR) / "data" / "evaluation"
    eval_dir.mkdir(parents=True, exist_ok=True)
    dataset_file = eval_dir / "cognition_dataset.jsonl"

    # 加载今日温度计
    temp_signals = {}
    daily_file = Path(PROJECT_DIR) / "logs" / "cognition_daily" / f"{TODAY_TAG}.json"
    if daily_file.exists():
        try:
            with open(daily_file) as f:
                daily = json.load(f)
            for s in daily.get("market_temperature", {}).get("signals", []):
                temp_signals[s["name"]] = s["signal"]
        except Exception:
            pass

    # 提取交易推荐摘要
    total_sell = sum(1 for n in ALL_NAMES if n in thresholds and thresholds[n].get("op") == "sell")
    total_buy = sum(1 for n in ALL_NAMES if n in thresholds and thresholds[n].get("op") == "buy")
    total_hold = sum(1 for n in ALL_NAMES if n in thresholds and thresholds[n].get("op") == "hold"
                     and thresholds[n].get("op") not in ("sell", "buy"))

    # 实际方向
    overseas_actual = None
    if overseas_dir:
        idx_pcts = [prices[n]["chg_pct"] for n in ["上证50", "沪深300", "科创50"] if n in prices]
        avg_idx = sum(idx_pcts) / len(idx_pcts) if idx_pcts else 0
        if avg_idx < -0.5:
            overseas_actual = "偏空"
        elif avg_idx > 0.5:
            overseas_actual = "偏多"
        else:
            overseas_actual = "震荡"

    direction_match = "吻合" if (
        overseas_dir and overseas_actual and (
            ("偏多" in overseas_dir and overseas_actual == "偏多") or
            ("偏空" in overseas_dir and overseas_actual == "偏空")
        )
    ) else ("偏离" if overseas_dir and overseas_actual else None)

    # Build record
    record = {
        "date": TODAY_TAG,
        "input": {
            "overseas_signal": overseas_dir,
            "overseas_confidence": overseas_conf,
            "temperature": temp_signals if temp_signals else {},
            "trade_recommendations": f"{total_buy}只买入/{total_hold}只持有/{total_sell}只卖出",
        },
        "output": {
            "sh50_chg": round(prices["上证50"]["chg_pct"], 2) if "上证50" in prices else None,
            "hs300_chg": round(prices["沪深300"]["chg_pct"], 2) if "沪深300" in prices else None,
            "kc50_chg": round(prices["科创50"]["chg_pct"], 2) if "科创50" in prices else None,
            "extreme_stocks": extreme_stocks,
            "sell_wrong_names": sell_wrong,
            "breach_names": list(set(b["name"] for b in breaches)) if breaches else [],
            "direction_match": direction_match,
        },
        "cognitive_tag": tag,
    }

    # Deduplicate: skip if same date already exists
    existing_dates = set()
    if dataset_file.exists():
        with open(dataset_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_dates.add(json.loads(line)["date"])
                except Exception:
                    pass

    if TODAY_TAG not in existing_dates:
        with open(dataset_file, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  评估数据集: 追加 {TODAY_TAG}")



def build_report(prices, thresholds, overseas_dir, overseas_conf):
    """组装完整收盘复盘报告 — 8段式结构"""
    L = []  # 行列表
    
    # ═══ 标题 ═══
    L.append(f"# A股收盘复盘 · {TODAY_DATE}")
    L.append("")
    
    # ═══ 1. 今日指数 ═══
    L.append("**① 今日指数**")
    index_names = ["上证50", "沪深300", "科创50"]
    idx_parts = []
    for name in index_names:
        if name in prices:
            p = prices[name]
            idx_parts.append(f"{name} {p['price']:.2f}（{p['chg_pct']:+.2f}%）")
        else:
            idx_parts.append(f"{name} 无数据")
    L.append(" | ".join(idx_parts))
    L.append("")
    
    # ═══ 2. 外盘验证 ═══
    if overseas_dir:
        bearish = "偏空" in overseas_dir or "看跌" in overseas_dir
        bullish = "偏多" in overseas_dir or "看涨" in overseas_dir

        index_codes = {}
        for k in index_names:
            if k in prices:
                index_codes[k] = prices[k]["chg_pct"]

        vals = [v for v in index_codes.values() if v is not None]
        avg_idx_chg = sum(vals) / len(vals) if vals else 0

        if avg_idx_chg < -0.5:
            actual_dir = "偏空"
        elif avg_idx_chg > 0.5:
            actual_dir = "偏多"
        else:
            actual_dir = "震荡"

        dir_match = "吻合" if (
            (bearish and actual_dir == "偏空") or (bullish and actual_dir == "偏多")
        ) else "偏离"

        L.append("**② 外盘验证**")
        L.append(f"预判 {overseas_dir}（{overseas_conf or '?'}）→ 实际 {actual_dir}，{dir_match}（三大指数均 {avg_idx_chg:+.2f}%）")
        L.append("")
    
    # ═══ 3. 极端波动 ═══
    extreme = [f"{name} {p['chg_pct']:+.2f}%" for name, p in prices.items() if abs(p["chg_pct"]) >= 3]
    if extreme:
        L.append("**③ 极端波动**（|涨跌|≥3%）")
        L.append("，".join(extreme))
        L.append("")
    
    # ═══ 4. 卖出误判 ═══
    L.append("**④ 卖出误判**")
    sell_wrong = []
    for name in ALL_NAMES:
        if name not in prices or name not in thresholds:
            continue
        if thresholds[name].get("op") == "sell" and prices[name]["chg_pct"] > 0.5:
            sell_wrong.append(f"{name} +{prices[name]['chg_pct']:.1f}%")
    if sell_wrong:
        L.append(f"{len(sell_wrong)} 只：{'，'.join(sell_wrong)}")
    else:
        L.append("无")
    L.append("")
    
    # ═══ 5. 价格穿越 ═══
    L.append("**⑤ 价格穿越**")
    breaches = check_breaches(prices, thresholds)
    if breaches:
        b_parts = [b['summary'] for b in breaches]
        L.append(f"{len(breaches)} 条 — {'；'.join(b_parts)}")
    else:
        L.append("无")
    L.append("")
    
    # ═══ 6. 市场温度计 ═══
    try:
        temp_lines = review_signal_quality()
        # review_signal_quality 自带标题和内容
        L.extend(temp_lines)
    except Exception as e:
        L.append("**⑥ 市场温度计**")
        L.append(f"获取失败: {e}")
        L.append("")
    
    # ═══ 7. 本周认知 ═══
    weekly_file = f"{PROJECT_DIR}/logs/cognition_weekly_{NOW.strftime('%Y-W%U')}.md"
    # 写入今日认知条目
    morning_file = f"{PROJECT_DIR}/../overseas-morning-brief/reports/morning_brief_{TODAY_DATE}.md"
    tag = "技术面"
    if os.path.exists(morning_file):
        with open(morning_file) as f:
            mb = f.read()
        if "VIX" in mb:
            tag = "VIX驱动"
        elif "非农" in mb or "就业" in mb:
            tag = "宏观驱动"
    sell_wrong_count = sum(1 for n in ALL_NAMES if n in prices and n in thresholds
                          and thresholds[n].get("op") == "sell" and prices[n]["chg_pct"] > 0.5)
    today_entry = f"{NOW.strftime('%m/%d')} {tag}"
    if breaches:
        b_names = list({b['name'] for b in breaches})
        today_entry += f"，穿越 {'、'.join(b_names)}"
    if sell_wrong_count >= 2:
        today_entry += f"，卖出集体失效({sell_wrong_count})"
    os.makedirs(os.path.dirname(weekly_file), exist_ok=True)
    existing = []
    if os.path.exists(weekly_file):
        with open(weekly_file) as f:
            existing = [l.strip() for l in f if l.strip().startswith("-")]
    existing_dates = {l[2:7] for l in existing if len(l) > 7}
    today_key = NOW.strftime('%m/%d')
    if today_key not in existing_dates:
        existing.append(f"- {today_entry}")
    existing = existing[-5:]
    with open(weekly_file, "w") as f:
        f.write(f"# 本周认知积累 ({NOW.strftime('%Y-W%U')})\n")
        for e in existing:
            f.write(e + "\n")
        f.write("\n")
    if existing:
        L.append("**⑦ 本周认知**")
        for e in existing:
            L.append(e)
        L.append("")
    
    # ═══ 8. 虚拟盘 ═══
    paper_state_file = f"{PROJECT_DIR}/reports/paper_state.json"
    if os.path.exists(paper_state_file):
        try:
            import json as _json
            with open(paper_state_file) as _f:
                ps = _json.load(_f)
            nv = ps.get("net_value", 1.0)
            total = ps.get("total_assets", 0)
            pct = (nv - 1) * 100
            arrow = "↑" if nv >= 1.0 else "↓"
            L.append("**⑧ 虚拟盘**")
            L.append(f"净值 {nv:.4f} {arrow} · 总资产 ¥{total:,.0f} · 累计 {pct:+.2f}%")
            pos = ps.get("positions", [])
            if pos:
                L.append("")
                L.append("| 标的 | 股数 | 均价 | 现价 | 市值 | 浮盈 | 占比 |")
                L.append("|------|------|------|------|------|------|------|")
                for p in pos:
                    ratio = p["market_value"] / total * 100 if total else 0
                    L.append(f"| {p['name']} | {p['shares']} | {p['avg_cost']:.2f} | {p['current_price']:.2f} | {p['market_value']:.0f} | {p['unrealized_pnl']:+.0f} | {ratio:.1f}% |")
            L.append("")
        except Exception as e:
            L.append("**⑧ 虚拟盘**")
            L.append(f"读取失败: {e}")
            L.append("")
    
    # ═══ 脚注 ═══
    # 数据源校验摘要
    if prices:
        source_info = {}
        for n, v in prices.items():
            s = v.get("source", "?")
            source_info[s] = source_info.get(s, 0) + 1
        src_line = " · ".join(f"{s}({c})" for s, c in source_info.items())
        L.append(f"> 数据源: {src_line} | 时间戳校验: {TODAY_DATE}")
    L.append(f"> *收盘复盘 · {TODAY_DATE} · AI辅助分析，不构成投资建议*")
    
    return "\n".join(L)


def main():
    # 只在交易日运行，15:05 之后
    t = NOW.time()
    if t < datetime.strptime("15:05", "%H:%M").time():
        return

    print(f"[{NOW.strftime('%H:%M:%S')}] 收盘复盘开始...")

    prices = fetch_close_prices()
    print(f"  收盘价: {len(prices)} 只标的获取成功")

    # 写入收盘价快照 → paper_trading.py 复用
    close_snapshot = {name: val["price"] for name, val in prices.items()}
    snapshot_file = os.path.join(PROJECT_DIR, "reports", f"close_snapshot_{TODAY_TAG}.json")
    with open(snapshot_file, "w") as f:
        json.dump(close_snapshot, f, ensure_ascii=False)

    # 用收盘快照更新虚拟盘持仓现价（paper_trading close 在 review 之后执行，
    # 但复盘报告需要展示准确的当日市值）
    paper_state_file = os.path.join(PROJECT_DIR, "reports", "paper_state.json")
    if os.path.exists(paper_state_file):
        with open(paper_state_file) as f:
            ps = json.load(f)
        updated = False
        for p in ps.get("positions", []):
            name = p.get("name", "")
            if name in close_snapshot:
                new_price = close_snapshot[name]
                p["current_price"] = new_price
                p["market_value"] = round(p["shares"] * new_price, 2)
                p["unrealized_pnl"] = round(p["market_value"] - p["shares"] * p["avg_cost"], 2)
                updated = True
        if updated:
            pos_val = sum(p["market_value"] for p in ps["positions"])
            ps["total_assets"] = ps.get("cash", 0) + pos_val
            ps["net_value"] = round(ps["total_assets"] / ps.get("initial_assets", ps["total_assets"]), 4) if ps.get("initial_assets") else round(ps["total_assets"] / 100000, 4)
            with open(paper_state_file, "w") as f:
                json.dump(ps, f, ensure_ascii=False, indent=2)
            updated_count = len([p for p in ps["positions"] if p["name"] in close_snapshot])
            print(f"  虚拟盘快照更新: {updated_count} 只标的")

    thresholds = load_thresholds()
    print(f"  阈值加载: {len(thresholds)} 只标的")

    overseas_dir = load_overseas_direction()
    overseas_conf = load_overseas_confidence()
    print(f"  外盘研判方向: {overseas_dir}")

    report = build_report(prices, thresholds, overseas_dir, overseas_conf)

    # 写复盘报告到文件
    review_file = f"{PROJECT_DIR}/reports/closing_review_{TODAY_TAG}.md"
    os.makedirs(os.path.dirname(review_file), exist_ok=True)
    with open(review_file, "w") as f:
        f.write(report)
    print(f"  报告写入: {review_file}")

    # 输出到 stdout 供 cron 日志
    print(report)

    # 写入结构化复盘状态 → 供次日开盘前推荐算法喂回
    hint = update_cognition_state(prices, thresholds, overseas_dir, overseas_conf)
    if hint:
        print(f"  认知校准标签: {hint}")

    print(f"\n✓ 收盘复盘完成")


if __name__ == "__main__":
    main()
