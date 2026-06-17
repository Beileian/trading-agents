#!/usr/bin/env python3
"""
外盘信号提取 v2.0 — 从 morning_brief 提取结构化信号供开盘推送使用。
项目: 金桥量化 v2.5.0
输入: reports/morning_brief_YYYY-MM-DD.md（由AI研判生成）
输出: reports/overseas_signal_YYYY-MM-DD.md（精简版，注入交易推荐报告）

v2.0: 金桥仓库自包含 + morning_brief 不可用时降级到新浪实时美股指数
v1: 基础版（依赖 overseas-morning-brief 项目路径）
"""

import os, sys, re
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
REPORT_DIR = os.path.join(PROJECT_DIR, "reports")
OVERSEAS_DIR = "/root/.openclaw/workspace/projects/overseas-morning-brief"
OVERSEAS_REPORT_DIR = os.path.join(OVERSEAS_DIR, "reports")


def _fallback_overseas_signal(date_str):
    """morning_brief 不可用时，从腾讯行情拉取美股指数生成降级信号"""
    import requests
    result = {
        "direction": "无数据",
        "confidence": "低",
        "key_signals": [],
        "anomaly_alerts": ["morning_brief不存在，外盘研判链条断裂，以下为实时数据降级信号"],
        "conflict_notes": [],
        "summary_line": "",
    }
    try:
        # 拉道琼斯、纳指、标普500
        indices = [
            ("usDJI", "道琼斯"), ("usIXIC", "纳斯达克"), ("usINX", "标普500")
        ]
        url = "http://hq.sinajs.cn/list=" + ",".join([i[0] for i in indices])
        resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        resp.encoding = "gbk"
        signals_parts = []
        bulls = 0
        bears = 0
        for line in resp.text.strip().split("\n"):
            m = re.search(r'hq_str_\w+="(.+)"', line)
            if not m:
                continue
            fields = m.group(1).split(",")
            if len(fields) < 4:
                continue
            name = fields[0]
            price = fields[1]   # 现价
            chg = fields[2]     # 涨跌额
            chg_pct = fields[3] # 涨跌幅
            if chg and float(chg) > 0:
                bulls += 1
            elif chg and float(chg) < 0:
                bears += 1
            signals_parts.append(f"{name} {price} ({chg_pct}%)")

        if bulls > bears:
            result["direction"] = "偏多"
        elif bears > bulls:
            result["direction"] = "偏空"
        else:
            result["direction"] = "中性"

        if signals_parts:
            result["key_signals"].append({
                "title": " ".join(signals_parts),
                "body": f"降级信号：{date_str} 隔夜外盘研判未生成，从新浪实时行情拉取指数数据。"
            })
            result["summary_line"] = f"外盘信号降级模式：{'/'.join(signals_parts)}"
    except Exception as e:
        result["key_signals"].append({"title": f"降级也失败: {e}"})
    return result


def extract_signals(brief_text: str) -> dict:
    """从研判文本中提取关键信号摘要"""
    result = {
        "direction": "中性",  # 偏多/偏空/中性
        "confidence": "中",   # 高/中/低
        "key_signals": [],
        "anomaly_alerts": [],
        "conflict_notes": [],
        "summary_line": "",
    }

    lines = brief_text.strip().split("\n")

    # 提取信号编号
    current_signal = None
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 匹配信号编号
        sig_match = re.match(r"^\d+\.\s+(.+)", line)
        if sig_match:
            if current_signal:
                result["key_signals"].append(current_signal)
            current_signal = {"title": sig_match.group(1), "body": ""}
            continue

        if current_signal is not None and current_signal["body"] == "":
            current_signal["body"] = line
            continue

        if current_signal is not None and not re.match(r"^\d+\.\s+", line) and "📊" not in line and "⚠️" not in line:
            if len(current_signal["body"]) < 200:
                current_signal["body"] += " " + line

    if current_signal and current_signal["title"]:
        result["key_signals"].append(current_signal)

    # 提取综合研判方向
    for i, line in enumerate(lines):
        if "综合研判" in line:
            # 取下一行
            for j in range(i+1, min(i+4, len(lines))):
                summary = lines[j].strip()
                if summary:
                    result["summary_line"] = summary
                    break

    # 方向判断 — 优先从综合研判行提取显式方向词
    direction = None
    for line in lines:
        if "综合研判" in line:
            continue
        if "偏多" in line or "看涨" in line:
            direction = "偏多"
        elif "偏空" in line or "看跌" in line:
            direction = "偏空"
        elif "中性" in line or "震荡" in line:
            if direction is None:
                direction = "中性"

    if direction:
        result["direction"] = direction
    else:
        # 降级到关键词计数
        full_text = brief_text.lower()
        bearish = sum(1 for w in ["暴跌", "下跌", "承压", "偏空", "恐慌", "抛售", "低开", "跳空"] if w in full_text)
        bullish = sum(1 for w in ["反弹", "偏多", "独立行情", "支撑", "企稳", "贪婪", "逆势", "高开", "修复"] if w in full_text)

        if bearish > bullish + 2:
            result["direction"] = "偏空"
        elif bullish > bearish + 2:
            result["direction"] = "偏多"
        else:
            result["direction"] = "中性/分歧"

    # 异常标注
    if "触发阈值" in brief_text or "极端" in brief_text:
        result["anomaly_alerts"].append("隔夜外盘出现异常波动，需关注盘中情绪释放")

    if "逆向信号" in brief_text or "他人恐惧" in brief_text:
        result["anomaly_alerts"].append("恐慌情绪极端化，触发逆向信号（他人恐惧我贪婪）")

    # 冲突检测
    if "冲突" in brief_text and "⚠️" in brief_text:
        result["conflict_notes"].append("外盘信号与知识库观点存在方向冲突，综合研判未自动裁决")

    return result


def format_signal_section(signals: dict) -> str:
    """生成交易推荐报告中可注入的外盘信号小节"""
    lines = []
    lines.append("## 🌐 隔夜外盘信号")
    lines.append("")
    lines.append(f"**研判方向**: {signals['direction']} | **置信度**: {signals['confidence']}")

    if signals["anomaly_alerts"]:
        for a in signals["anomaly_alerts"]:
            lines.append(f"⚠️ {a}")

    if signals["conflict_notes"]:
        for c in signals["conflict_notes"]:
            lines.append(f"⚡ {c}")

    if signals["key_signals"]:
        lines.append("")
        lines.append("**关键信号**:")
        for s in signals["key_signals"][:3]:  # 最多3条
            lines.append(f"- {s['title']}")

    if signals["summary_line"]:
        lines.append("")
        lines.append(f"> {signals['summary_line']}")

    lines.append("")
    return "\n".join(lines)


def main():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if len(sys.argv) > 1:
        today = sys.argv[1]

    brief_file = os.path.join(OVERSEAS_REPORT_DIR, f"morning_brief_{today}.md")
    out_file = os.path.join(REPORT_DIR, f"overseas_signal_{today}.md")

    if not os.path.exists(brief_file):
        print(f"⚠️ 研判文件不存在: {brief_file}")
        # 降级：直接从腾讯/Sina拉美股指数数据生成基础信号
        fallback = _fallback_overseas_signal(today)
        with open(out_file, "w") as f:
            f.write(format_signal_section(fallback))
        print(f"空信号已写入: {out_file}")
        return

    with open(brief_file) as f:
        text = f.read()

    signals = extract_signals(text)
    section = format_signal_section(signals)

    with open(out_file, "w") as f:
        f.write(section)

    print(f"外盘信号提取完成: {out_file}")
    print(f"  方向: {signals['direction']}")
    print(f"  信号数: {len(signals['key_signals'])}")
    print(f"  异常: {len(signals['anomaly_alerts'])} 条")


if __name__ == "__main__":
    main()
