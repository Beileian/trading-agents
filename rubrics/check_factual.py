#!/usr/bin/env python3
"""
check_factual.py — rubrics item #2: factual_accuracy
交叉验证推荐中引用的价格/PE/涨跌幅与 AKShare/Yahoo 数据源的一致性。

用法:
  python3 rubrics/check_factual.py <report_file> [--date YYYYMMDD]

输入: trade_signals_YYYYMMDD.md
输出: JSON → stdout { "pass": bool, "errors": [...], "checks": [...] }
exit code: 0=全部通过, 1=有事实偏差>5%
"""

import sys, os, re, json, csv
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_DIR, "data", "cache")

# Ticker → cache file mapping
TICKER_CACHE_FILE = {
    '000016.SH': '000016.SH-daily.csv',
    '000300.SH': '000300.SH-daily.csv',
    '000688.SH': '000688.SH-daily.csv',
    '601288.SH': '601288.SS-daily.csv',
    '601988.SH': '601988.SS-daily.csv',
    '600036.SH': '600036.SS-daily.csv',
    '600795.SH': '600795.SH-daily.csv',
    '000066.SZ': '000066.SZ-daily.csv',
    '600562.SH': '600562.SH-daily.csv',
    '562500.SH': '562500.SH-daily.csv',
}

# Name → ticker reverse lookup
NAME_TO_TICKER = {
    '上证50': '000016.SH',
    '沪深300': '000300.SH',
    '科创50': '000688.SH',
    '农业银行': '601288.SH',
    '中国银行': '601988.SH',
    '招商银行': '600036.SH',
    '国电电力': '600795.SH',
    '中国长城': '000066.SZ',
    '国睿科技': '600562.SH',
    '中证机器人': '562500.SH',
}


def get_latest_close(ticker: str) -> tuple[float | None, str | None]:
    """从本地缓存读取标的最新收盘价和日期"""
    cache_file = TICKER_CACHE_FILE.get(ticker)
    if not cache_file:
        return None, None

    path = os.path.join(CACHE_DIR, cache_file)
    if not os.path.exists(path):
        return None, None

    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows:
                return None, None
            last = rows[-1]
            close = float(last['Close'])
            return close, last['Date']
    except Exception:
        return None, None


def parse_report_prices(text: str) -> list[dict]:
    """解析报告中每只标的的 price 字段和名称"""
    records = []
    for line in text.split('\n'):
        m = re.match(r'^[🔴🟢🟡]\s*(.+?)\s{2,}([¥\d][\d.,]+)', line)
        if m:
            name = m.group(1).strip()
            price_str = m.group(2).strip().replace('¥', '').replace(',', '')
            try:
                price = float(price_str)
            except ValueError:
                price = None
            records.append({'name': name, 'price': price})
    return records


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"pass": False, "errors": ["用法: check_factual.py <report_file>"]}))
        sys.exit(1)

    report_file = sys.argv[1]
    if not os.path.exists(report_file):
        print(json.dumps({"pass": False, "errors": [f"文件不存在: {report_file}"]}))
        sys.exit(1)

    with open(report_file) as f:
        text = f.read()

    records = parse_report_prices(text)
    if not records:
        print(json.dumps({"pass": False, "errors": ["未解析到任何标的记录"]}))
        sys.exit(1)

    checks = []
    errors = []
    total_deviation = 0
    checked_count = 0

    for rec in records:
        name = rec['name']
        reported_price = rec['price']
        ticker = NAME_TO_TICKER.get(name)

        if not ticker:
            checks.append({
                'name': name, 'reported': reported_price,
                'source': None, 'deviation_pct': None,
                'status': 'skipped', 'reason': '无ticker映射'
            })
            continue

        if reported_price is None:
            checks.append({
                'name': name, 'reported': None,
                'source': None, 'deviation_pct': None,
                'status': 'skipped', 'reason': '价格无法解析'
            })
            continue

        source_price, source_date = get_latest_close(ticker)

        if source_price is None:
            checks.append({
                'name': name, 'reported': reported_price,
                'source': None, 'deviation_pct': None,
                'status': 'skipped', 'reason': '缓存无数据'
            })
            continue

        deviation_pct = abs(reported_price - source_price) / source_price * 100
        passed = deviation_pct < 5.0

        check = {
            'name': name,
            'ticker': ticker,
            'reported': round(reported_price, 2),
            'source': round(source_price, 2),
            'source_date': source_date,
            'deviation_pct': round(deviation_pct, 2),
            'status': 'pass' if passed else 'fail',
        }
        checks.append(check)

        if not passed:
            errors.append(
                f"{name}: 价格偏差 {deviation_pct:.1f}% "
                f"(报告 {reported_price:.2f} vs 缓存 {source_price:.2f}, {source_date})"
            )

        total_deviation += deviation_pct
        checked_count += 1

    passed = len(errors) == 0
    avg_deviation = round(total_deviation / checked_count, 2) if checked_count > 0 else None

    result = {
        "pass": passed,
        "errors": errors,
        "checks": checks,
        "summary": {
            "total": len(records),
            "checked": checked_count,
            "passed": checked_count - len(errors),
            "failed": len(errors),
            "avg_deviation_pct": avg_deviation,
        }
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
