#!/usr/bin/env python3
"""
缓存同步校验 — 第一层防线
抽查 data/cache/*-daily.csv vs logs/cache/*YFin-data-*-2026-06-04.csv 收盘价一致性

用法: python3 verify_cache_sync.py
退出码 0 = 一致, 1 = 偏差超阈值
"""

import sys, os, csv

CACHE_SRC = "/root/.openclaw/workspace/projects/trading-agents/data/cache"
CACHE_DST = "/root/.openclaw/workspace/projects/trading-agents/logs/cache"

# .SH/.SS/.SZ → -daily.csv 文件名映射（与 TICKER_CACHE 同步）
SYMBOL_MAP = {
    "000016.SH": "000016.SH-daily.csv",
    "000300.SH": "000300.SH-daily.csv",
    "000688.SH": "000688.SH-daily.csv",
    "601288.SH": "601288.SS-daily.csv",
    "601988.SH": "601988.SS-daily.csv",
    "600036.SH": "600036.SS-daily.csv",
    "600795.SH": "600795.SH-daily.csv",
    "000066.SZ": "000066.SZ-daily.csv",
    "600562.SH": "600562.SH-daily.csv",
    "562500.SH": "562500.SH-daily.csv",
}

MAX_DEVIATION_PCT = 1.0  # 阈值：偏差超过1%报错


def last_close(csv_path):
    """读取 CSV 最后一行的收盘价"""
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return None, None
    last = rows[-1]
    return last.get("Date", ""), float(last["Close"])


def main():
    failures = []

    for sym, daily_file in SYMBOL_MAP.items():
        src_path = os.path.join(CACHE_SRC, daily_file)
        if not os.path.exists(src_path):
            print(f"  [SKIP] {sym}: 源文件不存在 {daily_file}")
            continue

        # 旧 YFin-data 文件名: {symbol}-YFin-data-2021-06-04-2026-06-04.csv
        dst_file = f"{sym}-YFin-data-2021-06-04-2026-06-04.csv"
        dst_path = os.path.join(CACHE_DST, dst_file)
        if not os.path.exists(dst_path):
            print(f"  [SKIP] {sym}: 目标文件不存在 {dst_file}")
            continue

        src_date, src_close = last_close(src_path)
        dst_date, dst_close = last_close(dst_path)

        if src_close is None or dst_close is None:
            print(f"  [FAIL] {sym}: 无法读取数据")
            failures.append(f"{sym}: 无法读取")
            continue

        if src_date != dst_date:
            print(f"  [FAIL] {sym}: 日期不一致 src={src_date} dst={dst_date}")
            failures.append(f"{sym}: 日期不一致")
            continue

        deviation = abs(src_close - dst_close) / src_close * 100
        status = "OK" if deviation < MAX_DEVIATION_PCT else "FAIL"
        print(f"  [{status}] {sym}: {src_close:.2f} vs {dst_close:.2f} (偏差 {deviation:.2f}%)")

        if deviation >= MAX_DEVIATION_PCT:
            failures.append(f"{sym}: 价格偏差 {deviation:.2f}% (src={src_close:.2f} dst={dst_close:.2f})")

    if failures:
        print(f"\n校验失败 ({len(failures)} 项):")
        for f in failures:
            print(f"  • {f}")
        sys.exit(1)

    print(f"\n全部 {len(SYMBOL_MAP)} 只标的校验通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
