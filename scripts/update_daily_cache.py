#!/usr/bin/env python3
"""
A股日线缓存更新脚本 — 拉取最新交易数据追加到本地 CSV
v1.2: 新浪主源 → 腾讯兜底 → push2his 第三兜底（多源交叉保障）

数据源: 新浪财经 K线（主）→ 腾讯日K线（兜底1）→ 东方财富 push2his 日K线（兜底2）
用法: python3 update_daily_cache.py
"""

import sys, os, time, json, urllib.request
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/data/cache"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symbols_config

TICKERS = symbols_config.TICKER_SINA_MAP


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


def fetch_push2his_daily(ticker: str) -> list[dict]:
    """从东方财富 push2his 日K线接口获取最新日线数据（web_fetch 可用通道）
    
    push2his 是东方财富历史K线接口，klt=101=日K，lmt=3=最近3根。
    当天日K在盘中会动态更新收盘价字段，等效于实时报价。
    此接口不同 push2 实时行情——它不需要浏览器级headers，直连可用。
    """
    import symbols_config
    secid = symbols_config.TICKER_EM_MAP.get(ticker)
    if not secid:
        return []
    
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt=3"
    )
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if not data or data.get('rc') != 0 or not data.get('data'):
            return []
        
        klines = data['data'].get('klines', [])
        records = []
        for kline in klines:
            parts = kline.split(',')
            if len(parts) < 11:
                continue
            try:
                records.append({
                    "Date": pd.to_datetime(parts[0]),
                    "Open": float(parts[1]),
                    "Close": float(parts[2]),
                    "High": float(parts[3]),
                    "Low": float(parts[4]),
                    "Volume": float(parts[5]),
                })
            except (ValueError, IndexError):
                continue
        return records
    except Exception as e:
        print(f"  [WARN] {ticker} push2his 失败: {e}")
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
        # 新浪失败 → 收盘快照兜底（closing_review.py 前一天收盘写入）
        ticker_name = None
        for csv_key, sina_sym in TICKERS.items():
            if sina_sym == symbol:
                ticker_name = csv_key
                break
        if ticker_name:
            name_map = {"000016.SH": "上证50", "000300.SH": "沪深300", "000688.SH": "科创50",
                        "601288.SH": "农业银行", "601988.SH": "中国银行", "600036.SH": "招商银行",
                        "600795.SH": "国电电力", "000066.SZ": "中国长城", "600562.SH": "国睿科技"}
            cn_name = name_map.get(ticker_name)
            if cn_name:
                yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y%m%d")
                snapshot_file = f"{os.path.dirname(CACHE_DIR)}/reports/close_snapshot_{yesterday}.json"
                if os.path.exists(snapshot_file):
                    with open(snapshot_file) as f:
                        snap = json.load(f)
                    if cn_name in snap:
                        print(f"  [FALLBACK] 新浪失败，从收盘快照追加 {cn_name}={snap[cn_name]}")
                        today_date = datetime.now(TZ).date()
                        new_data = [{
                            "Date": pd.to_datetime(today_date),
                            "Open": snap[cn_name],
                            "High": snap[cn_name],
                            "Low": snap[cn_name],
                            "Close": snap[cn_name],
                            "Volume": 0,
                        }]
        # 快照也失败 → push2his 兜底（东方财富日K，含当天动态收盘价）
        if not new_data and ticker_name:
            print(f"  [FALLBACK2] 快照失败，尝试 push2his 东方财富日K")
            new_data = fetch_push2his_daily(ticker_name)
    if not new_data:
        print(f"  [WARN] {symbol} 无新数据（新浪+快照+push2his均失败）")
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
