#!/usr/bin/env python3
"""
认知闭环评估报告生成器（Step 1: 手动评估用）

从 cognition_dataset.jsonl 读取历史记录，产出结构化评估摘要。
运行方式: python3 eval_summary.py [--days 10]
"""

import json, sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone, timedelta

PROJECT_DIR = Path("/root/.openclaw/workspace/projects/trading-agents")
DATASET = PROJECT_DIR / "data" / "evaluation" / "cognition_dataset.jsonl"

TZ = timezone(timedelta(hours=8))


def load_dataset():
    if not DATASET.exists():
        return []
    records = []
    with open(DATASET) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def eval_summary(records, days=None):
    if days:
        records = records[-days:]

    total = len(records)
    if total == 0:
        print("无评估数据")
        return

    # ── 外盘研判准确率 ──
    dir_matches = [r for r in records if r["output"].get("direction_match")]
    dir_ok = sum(1 for r in dir_matches if r["output"]["direction_match"] == "吻合")
    dir_acc = dir_ok / len(dir_matches) * 100 if dir_matches else 0

    # ── 卖出建议准确率 ──
    sell_wrong_events = sum(1 for r in records if r["output"].get("sell_wrong_names"))
    sell_wrong_names = []
    for r in records:
        sell_wrong_names.extend(r["output"].get("sell_wrong_names", []))

    # ── 极端波动频率 ──
    extreme_days = sum(1 for r in records if r["output"].get("extreme_stocks"))
    extreme_all = []
    for r in records:
        extreme_all.extend(r["output"].get("extreme_stocks", []))

    # ── 价格穿越频率 ──
    breach_days = sum(1 for r in records if r["output"].get("breach_names"))
    breach_all = []
    for r in records:
        breach_all.extend(r["output"].get("breach_names", []))

    # ── 认知标签分布 ──
    tags = Counter(r.get("cognitive_tag", "未知") for r in records)

    # ── 市场温度信号统计 ──
    temp_signals = {}
    for r in records:
        for name, signal in r["input"].get("temperature", {}).items():
            if name not in temp_signals:
                temp_signals[name] = []
            temp_signals[name].append(signal)

    # ── Print Summary ──
    print(f"# 认知闭环评估报告 ({records[0]['date']} ~ {records[-1]['date']}, {total}个交易日)")
    print()

    print(f"## 1. 外盘研判准确率")
    print(f"- 方向吻合: {dir_ok}/{len(dir_matches)} 天 ({dir_acc:.0f}%)")
    print()

    print(f"## 2. 卖出建议准确率")
    print(f"- 有卖出误判的天数: {sell_wrong_events}/{total}")
    if sell_wrong_names:
        c = Counter(sell_wrong_names)
        print(f"- 误判标的（累计）: {', '.join(f'{k}x{v}' for k,v in c.most_common())}")
    print()

    print(f"## 3. 极端波动")
    print(f"- 出现极端波动的天数: {extreme_days}/{total}")
    if extreme_all:
        c = Counter(extreme_all)
        print(f"- 极端波动标的: {', '.join(f'{k}x{v}' for k,v in c.most_common())}")
    print()

    print(f"## 4. 价格穿越")
    print(f"- 触发穿越的天数: {breach_days}/{total}")
    if breach_all:
        c = Counter(breach_all)
        print(f"- 穿越标的: {', '.join(f'{k}x{v}' for k,v in c.most_common())}")
    print()

    print(f"## 5. 认知标签分布")
    for tag, cnt in tags.most_common():
        print(f"- {tag}: {cnt}天")
    print()

    print(f"## 6. 温度计信号统计")
    for name, signals in temp_signals.items():
        sc = Counter(signals)
        dist = ", ".join(f"{k}x{v}" for k, v in sc.most_common())
        print(f"- {name}: {dist}")
    print()

    # ── 逐日明细 ──
    print(f"## 7. 逐日明细")
    print("| 日期 | 外盘研判 | 实际方向 | 三大指数 | 卖出误判 | 穿越 | 标签 |")
    print("|------|---------|---------|---------|---------|------|------|")
    for r in records:
        inp = r["input"]
        out = r["output"]
        date = r["date"]
        overseas = inp.get("overseas_signal", "-") or "-"
        am = out.get("direction_match", "-") or "-"
        idx = f"SH50 {out.get('sh50_chg', '?')} HS300 {out.get('hs300_chg', '?')} KC50 {out.get('kc50_chg', '?')}"
        sell_w = ",".join(out.get("sell_wrong_names", [])) or "-"
        breach = ",".join(out.get("breach_names", [])) or "-"
        tag = r.get("cognitive_tag", "-") or "-"
        print(f"| {date} | {overseas} | {am} | {idx} | {sell_w} | {breach} | {tag} |")


def main():
    days = None
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=")[1])
    records = load_dataset()
    eval_summary(records, days)


if __name__ == "__main__":
    main()
