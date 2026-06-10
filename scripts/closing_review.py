#!/usr/bin/env python3
"""
收盘复盘 — 比照今日研判 vs 实际走势，逐标的检查触发条件穿越情况。
认知闭环：方向验证 + 幅度评估 + 触发条件穿越检测 → 推送「谈股论金奔富」群。

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


def fetch_close_prices():
    """从新浪获取今日收盘价 & 涨跌幅"""
    codes = list(SINA_MAP.keys())
    url = "http://hq.sinajs.cn/list=" + ",".join(codes)
    resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
    resp.encoding = "gbk"

    prices = {}
    for line in resp.text.strip().split("\n"):
        m = re.search(r'hq_str_(\w+)="(.+)"', line)
        if not m:
            continue
        code, data = m.group(1), m.group(2)
        name = SINA_MAP.get(code)
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

        chg_pct = (price / prev - 1) * 100 if prev else 0
        prices[name] = {
            "price": round(price, 2), "chg_pct": round(chg_pct, 2),
            "high": round(high, 2), "low": round(low, 2),
        }
    return prices


def load_thresholds():
    """从今日交易推荐报告读取支撑/阻力/操作/仓位"""
    signals_file = f"{PROJECT_DIR}/reports/trade_signals_{TODAY_TAG}.md"
    if not os.path.exists(signals_file):
        return {}

    thresholds = {}
    with open(signals_file) as f:
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

            # Extract operation from column 6 (1-indexed: name|price|bias|support|resist|op|pos|trigger)
            op_raw = parts[6] if len(parts) > 6 else ""
            op = "hold"
            if "卖出" in op_raw:
                op = "sell"
            elif "买入" in op_raw:
                op = "buy"

            # Position: extract from column 8 (e.g. "30%" -> 30)
            try:
                pos_str = parts[7].strip() if len(parts) > 7 else "0%"
                # 取第一个数字部分
                m = re.search(r'\d+', pos_str)
                pos = int(m.group()) if m else 0
            except (ValueError, IndexError):
                pos = 0

            # Extract trigger from column 8
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

        lines.append(f"外盘研判：预判 {overseas_dir}（置信度 {overseas_conf or '?'}）→ 实际 {actual_dir} {dir_match}，三大指数平均 {avg_idx_chg:+.2f}%")

    # ── 2. 标的交叉对比：早盘建议 vs 实际走势 ──
    sell_wrong = []  # 建议卖出但实际收涨
    hold_right = []  # 建议持有且方向合理
    for name in ALL_NAMES:
        if name not in prices or name not in thresholds:
            continue
        p = prices[name]
        t = thresholds[name]
        op = t.get("op", "")
        chg = p["chg_pct"]
        if op == "sell" and chg > 0.5:
            sell_wrong.append(f"{name} +{chg:.1f}%")
        elif op == "hold" and abs(chg) < 2:
            hold_right.append(name)
    if sell_wrong:
        lines.append(f"早盘卖出建议 {len(sell_wrong)} 只标的实际收涨：{'，'.join(sell_wrong)}")

    # ── 3. 极端波动检测 ──
    extreme_stocks = []
    for name, p in prices.items():
        if abs(p["chg_pct"]) >= 3:
            extreme_stocks.append(f"{name} {p['chg_pct']:+.2f}%")
    if extreme_stocks:
        lines.append(f"极端波动（|涨跌|≥3%）：{'，'.join(extreme_stocks)}")

    # ── 4. 触发穿越 ──
    breaches = check_breaches(prices, thresholds)
    if breaches:
        b_lines = [b['summary'] for b in breaches]
        lines.append(f"触发穿越 {len(breaches)} 条：{'；'.join(b_lines)}")
    else:
        lines.append("触发穿越：无")

    return lines


def build_report(prices, thresholds, overseas_dir, overseas_conf):
    """构建收盘复盘报告 — 三段式结构"""
    lines = []
    lines.append(f"## 收盘复盘 · {NOW.strftime('%-m/%-d')}")
    lines.append("")

    # ── 第一段：今日收盘数据 ──
    lines.append("### 今日收盘")
    lines.append("")
    lines.append("| 标的 | 今收 | 涨跌% | 日内高 | 日内低 | 早盘建议 | 仓位 |")
    lines.append("|------|------|-------|--------|--------|----------|------|")

    for name in ALL_NAMES:
        if name not in prices:
            continue
        p = prices[name]
        t = thresholds.get(name, {})
        op_map = {"sell": "🔴卖出", "buy": "🟢买入", "hold": "🟡持有"}
        op_display = op_map.get(t.get("op", ""), "-")
        pos_display = f"{t.get('pos', '-')}%" if t.get("pos", 0) > 0 else f"{t.get('pos', '-')}%"
        lines.append(
            f"| {name} | {p['price']:.2f} | {p['chg_pct']:+.2f}% "
            f"| {p['high']:.2f} | {p['low']:.2f} "
            f"| {op_display} | {pos_display} |"
        )
    lines.append("")

    # ── 第二段：研判验证 + 标的交叉对比 + 触发穿越 ──
    cog_lines = summarize_cognition(prices, thresholds, overseas_dir, overseas_conf)
    for cl in cog_lines:
        lines.append(cl)
    lines.append("")

    # ── 第三段：今日认知增量 ──
    morning_file = f"{OVERSEAS_DIR}/reports/morning_brief_{TODAY_DATE}.md"
    tag = "无"
    if os.path.exists(morning_file):
        with open(morning_file) as f:
            mb = f.read()
        if "VIX" in mb:
            tag = "VIX驱动型"
        elif "非农" in mb or "就业" in mb:
            tag = "宏观数据驱动型"
        elif "财报" in mb:
            tag = "财报驱动型"
        else:
            tag = "技术面驱动型"

    # 从 cross-check 结果提取今日增量
    sell_wrong_count = 0
    for name in ALL_NAMES:
        if name not in prices or name not in thresholds:
            continue
        p = prices[name]
        t = thresholds[name]
        if t.get("op") == "sell" and p["chg_pct"] > 0.5:
            sell_wrong_count += 1

    insights = [f"今日认知：{tag}"]
    if sell_wrong_count >= 2:
        insights.append(f"卖出建议集体失效（{sell_wrong_count}只收涨），反弹日信号")
    breaches = check_breaches(prices, thresholds)
    if not breaches:
        insights.append("无触发穿越，盘面平稳")

    lines.append(f"认知标签：{' | '.join(insights)}")
    lines.append("")

    # ── 第四段：本周认知积累（追加式） ──
    weekly_file = f"{PROJECT_DIR}/logs/cognition_weekly_{NOW.strftime('%Y-W%U')}.md"
    os.makedirs(os.path.dirname(weekly_file), exist_ok=True)
    today_entry = f"- {NOW.strftime('%m/%d')} {tag}"
    if breaches:
        b_names = list(set(b['name'] for b in breaches))
        today_entry += f"，穿越: {'、'.join(b_names)}"
    if sell_wrong_count >= 2:
        today_entry += f"，卖出集体失效({sell_wrong_count}只)"

    # 追加今日条目
    existing = []
    if os.path.exists(weekly_file):
        with open(weekly_file) as f:
            existing = [l.strip() for l in f if l.strip().startswith("-")]
    # 避免重复追加（当天已写）
    existing_dates = {l[2:7] for l in existing if len(l) > 7}
    today_key = NOW.strftime('%m/%d')
    if today_key not in existing_dates:
        existing.append(today_entry)
    # 只保留最近5天
    existing = existing[-5:]
    with open(weekly_file, "w") as f:
        f.write(f"# 本周认知积累 ({NOW.strftime('%Y-W%U')})\n")
        for e in existing:
            f.write(e + "\n")
        f.write("\n")

    if len(existing) > 1:
        lines.append("本周认知：")
        for e in existing:
            lines.append(e)

    return "\n".join(lines)


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

    # ── 追加报告索引行 ──
    index_file = f"{PROJECT_DIR}/reports/INDEX.md"
    index_date = NOW.strftime('%Y-%m-%d')
    index_weekday = ["周一","周二","周三","周四","周五","周六","周日"][NOW.weekday()]

    # Build index entry
    op_counts = {"sell": 0, "hold": 0, "buy": 0}
    for n in ALL_NAMES:
        if n in thresholds:
            op = thresholds[n].get("op", "")
            if op == "sell": op_counts["sell"] += 1
            elif op == "buy": op_counts["buy"] += 1
            else: op_counts["hold"] += 1
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

    return hint


def main():
    # 只在交易日运行，15:05 之后
    t = NOW.time()
    if t < datetime.strptime("15:05", "%H:%M").time():
        return

    print(f"[{NOW.strftime('%H:%M:%S')}] 收盘复盘开始...")

    prices = fetch_close_prices()
    print(f"  收盘价: {len(prices)} 只标的获取成功")

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
