#!/usr/bin/env python3
"""
A股日线缓存更新脚本 — 拉取最新交易数据追加到本地 CSV
从新浪接口获取日K线（非实时行情），对比 CSV 最后日期后增量追加

数据源: 新浪财经 K线接口，支持历史日线，免 API key
用法: python3 update_daily_cache.py
"""

import sys, os, time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/data/cache"

TICKERS = {
    "000016.SH": "sh000016",
    "000300.SH": "sh000300",
    "000688.SH": "sh000688",
    "601288.SH": "sh601288",
    "601988.SH": "sh601988",
    "600036.SH": "sh600036",
    "600795.SH": "sh600795",
    "000066.SZ": "sz000066",
    "600562.SH": "sh600562",
    "562500.SH": "sh562500",
}


def fetch_sina_daily(symbol: str) -> list[dict]:
    """从新浪K线接口获取日线数据"""
    # 新浪日K线接口: https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=30"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "gbk"
        data = resp.json()
        if not data or not isinstance(data, list):
            return []
        records = []
        for item in data:
            try:
                records.append({
                    "Date": pd.to_datetime(item["day"]),
                    "Open": float(item["open"]),
                    "High": float(item["high"]),
                    "Low": float(item["low"]),
                    "Close": float(item["close"]),
                    "Volume": float(item["volume"]),
                })
            except (KeyError, ValueError):
                continue
        return records
    except Exception as e:
        print(f"  [WARN] {symbol} 新浪K线失败: {e}")
        return []


def find_cache_path(cache_key: str) -> str | None:
    """根据 TICKER 映射找到实际缓存文件"""
    # 直接匹配
    base = os.path.join(CACHE_DIR, cache_key)
    if os.path.exists(base):
        return base
    # 尝试 -daily.csv 后缀
    alt = cache_key.replace(".SH", ".SH-daily.csv").replace(".SS", ".SS-daily.csv").replace(".SZ", ".SZ-daily.csv")
    alt_path = os.path.join(CACHE_DIR, alt)
    if os.path.exists(alt_path):
        return alt_path
    # 直接匹配不带后缀
    for f in os.listdir(CACHE_DIR):
        if f.startswith(cache_key.replace(".SH", "").replace(".SS", "").replace(".SZ", "")):
            return os.path.join(CACHE_DIR, f)
    return None


def update_cache(cache_file: str, symbol: str) -> int:
    """增量追加新数据到缓存文件，返回新增行数"""
    cache_key = cache_file  # keep original key for lookup
    cache_path = find_cache_path(cache_key)
    if cache_path is None:
        print(f"  [WARN] 缓存文件不存在: {cache_key}")
        return 0

    # 读取现有缓存
    try:
        existing = pd.read_csv(cache_path, parse_dates=["Date"])
        existing = existing.set_index("Date").sort_index()
    except Exception:
        print(f"  [WARN] 读取缓存失败: {cache_file}")
        return 0

    last_date = existing.index.max().date()
    today = datetime.now(TZ).date()

    if last_date >= today:
        print(f"  ✓ 已是最新 ({last_date})")
        return 0

    # 从新浪获取最新数据
    new_data = fetch_sina_daily(symbol)
    if not new_data:
        return 0

    new_df = pd.DataFrame(new_data).set_index("Date").sort_index()

    # 只保留 last_date 之后的新数据
    new_df = new_df[new_df.index > pd.Timestamp(last_date)]

    if new_df.empty:
        print(f"  → 无新增数据 (上次: {last_date})")
        return 0

    # 合并
    combined = pd.concat([existing, new_df])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()

    # 写入
    combined.to_csv(cache_path, date_format="%Y-%m-%d")
    added = len(new_df)
    new_dates = [d.strftime("%m/%d") for d in new_df.index.date]

    # 检查是否有交易日缺口
    all_dates = combined.index.date
    expected_missing = (today - max(all_dates)).days
    gap_note = f"，距今天还有 {expected_missing} 个自然日未更新" if expected_missing > 1 else ""

    print(f"  ✓ +{added} 条 ({', '.join(new_dates)}){gap_note}")
    return added


def main():
    total_added = 0
    updated = 0
    fresh = 0

    for csv_file, sina_sym in TICKERS.items():
        print(f"[{csv_file}]")
        added = update_cache(csv_file, sina_sym)
        if added > 0:
            total_added += added
            updated += 1
        elif added == 0:
            fresh += 1

    print(f"\n总计: {total_added} 条新增 · {updated} 个缓存更新 · {fresh} 个已最新")


if __name__ == "__main__":
    main()
