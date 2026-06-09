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
}


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
    for name in ["上证50", "沪深300", "科创50", "农业银行", "中国银行",
                  "招商银行", "国电电力", "中国长城", "国睿科技"]:
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

    for name in ["上证50", "沪深300", "科创50", "农业银行", "中国银行",
                  "招商银行", "国电电力", "中国长城", "国睿科技"]:
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
    for name in ["上证50", "沪深300", "科创50", "农业银行", "中国银行",
                  "招商银行", "国电电力", "中国长城", "国睿科技"]:
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
    print(f"\n✓ 收盘复盘完成")


if __name__ == "__main__":
    main()
