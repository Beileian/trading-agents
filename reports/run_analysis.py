#!/usr/bin/env python3
"""
Multi-stock technical analysis with DeepSeek API.
Incrementally writes results to avoid OOM.
"""

import csv
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, date
from pathlib import Path

# ── Config ──────────────────────────────────────────────
CACHE_DIR = Path("/root/.openclaw/workspace/projects/trading-agents/logs/cache")
REPORT_PATH = Path("/root/.openclaw/workspace/projects/trading-agents/reports")
REPORT_PATH.mkdir(parents=True, exist_ok=True)

DEEPSEEK_API_KEY = "sk-2fe…42a0"  # placeholder — will read from env or direct
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

STOCKS = [
    ("000001.SZ", "平安银行"),
    ("600519.SS", "贵州茅台"),
    ("000858.SZ", "五粮液"),
    ("300033.SZ", "同花顺"),
]

TODAY = date.today()
REPORT_FILE = REPORT_PATH / f"trading_analysis_{TODAY.strftime('%Y%m%d')}.md"


# ── Helpers ─────────────────────────────────────────────

def load_csv(symbol):
    fname = CACHE_DIR / f"{symbol}-YFin-data-2021-06-04-2026-06-04.csv"
    rows = []
    with open(fname) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "date": r["Date"],
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(r["Close"]),
                "volume": float(r["Volume"]),
            })
    # Ensure chronological order (oldest first)
    rows.sort(key=lambda r: r["date"])
    return rows


def sma(values, window):
    """Simple moving average; returns None for insufficient data."""
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def rsi(values, period=14):
    """RSI over `period` days."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        gains.append(delta if delta > 0 else 0)
        losses.append(-delta if delta < 0 else 0)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def daily_returns(closes):
    """Log returns."""
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def annualized_volatility(returns, trading_days=252):
    """Annualized volatility from daily log returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(trading_days) * 100  # percent


def rolling_volatility_30d(returns):
    """30-day volatility (annualized) from daily log returns."""
    if len(returns) < 30:
        return annualized_volatility(returns)
    return annualized_volatility(returns[-30:])


def percent_change(old, new):
    if old == 0:
        return 0.0
    return (new - old) / old * 100


# ── Compute Indicators ──────────────────────────────────

def compute_indicators(rows):
    closes = [r["close"] for r in rows]
    lows = [r["low"] for r in rows]
    highs = [r["high"] for r in rows]
    returns = daily_returns(closes)

    # Price now
    last_close = closes[-1]
    last_date = rows[-1]["date"]

    # Year range
    year_ago_idx = None
    for i, r in enumerate(rows):
        if r["date"] >= f"{TODAY.year - 1}-{TODAY.month:02d}-{TODAY.day:02d}":
            year_ago_idx = i
            break
    if year_ago_idx is None:
        year_ago_idx = 0

    year_closes = closes[year_ago_idx:]
    ytd_high = max(year_closes)
    ytd_low = min(year_closes)
    ytd_change = percent_change(closes[year_ago_idx], last_close)

    # Week / Month
    week_idx = max(0, len(closes) - 5)
    month_idx = max(0, len(closes) - 21)
    week_change = percent_change(closes[week_idx], last_close)
    month_change = percent_change(closes[month_idx], last_close)

    # MAs
    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)

    # RSI
    rsi14 = rsi(closes, 14)

    # Volatility 30d
    vol30 = rolling_volatility_30d(returns)

    # Distance from year high/low (from current price perspective)
    # How far below year high: (high - close) / close * 100  (positive = below high)
    dist_high = (ytd_high - last_close) / last_close * 100 if last_close != 0 else 0
    # How far above year low: (close - low) / low * 100  (positive = above low)
    dist_low = (last_close - ytd_low) / ytd_low * 100 if ytd_low != 0 else 0

    # Last 30 close prices
    last_30_closes = closes[-30:] if len(closes) >= 30 else closes

    return {
        "symbol": rows[0].get("_symbol", ""),
        "name": rows[0].get("_name", ""),
        "last_date": last_date,
        "last_close": last_close,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "rsi14": rsi14,
        "vol30": vol30,
        "week_change": week_change,
        "month_change": month_change,
        "ytd_change": ytd_change,
        "ytd_high": ytd_high,
        "ytd_low": ytd_low,
        "dist_from_high_pct": dist_high,  # positive = above high
        "dist_from_low_pct": dist_low,    # positive = above low
        "last_30_closes": last_30_closes,
    }


# ── DeepSeek API Call ───────────────────────────────────

def call_deepseek(indicators, api_key):
    closes_str = ", ".join(f"{c:.2f}" for c in indicators["last_30_closes"])

    prompt = f"""你是一位专业A股技术分析师。请基于以下数据对{indicators['name']}（{indicators['symbol']}）进行技术分析并给出交易建议。

【技术指标】
- 最新收盘价: {indicators['last_close']:.2f} 元
- 最新交易日: {indicators['last_date']}
- MA5: {indicators['ma5']:.2f}
- MA20: {indicators['ma20']:.2f}
- MA60: {indicators['ma60']:.2f}
- RSI(14): {indicators['rsi14']:.1f}
- 30日年化波动率: {indicators['vol30']:.1f}%
- 周涨跌: {indicators['week_change']:+.2f}%
- 月涨跌: {indicators['month_change']:+.2f}%
- 年内涨跌: {indicators['ytd_change']:+.2f}%
- 年内最高价: {indicators['ytd_high']:.2f} 元
- 年内最低价: {indicators['ytd_low']:.2f} 元
- 距年内高点: -{indicators['dist_from_high_pct']:.2f}%（低于年内最高价）
- 距年内低点: +{indicators['dist_from_low_pct']:.2f}%（高于年内最低价）

【最近30个交易日收盘价序列】
{closes_str}

请按以下格式输出（纯文本，无需Markdown标记）：

趋势判断：（看涨/看跌/震荡）
支撑位：（基于实际数据的支撑价格区间，格式：X.XX - Y.YY 元）
阻力位：（基于实际数据的阻力价格区间，格式：X.XX - Y.YY 元）
交易建议：（买入/卖出/持有）
仓位建议：（0-100%）
理由：（≤100字简要理由）"""

    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一位专业A股技术分析师，严格基于提供的技术指标数据进行分析，不做主观臆断。输出简洁、精确。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
    }

    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        return content
    except Exception as e:
        return f"【API调用失败】{str(e)}"


# ── Format Report Section ───────────────────────────────

def format_section(indicators, analysis_text):
    """Format one stock section as Markdown."""
    ind = indicators
    symbol = ind["symbol"]
    name = ind["name"]

    section = []
    section.append(f"## {symbol} {name}")
    section.append("")
    section.append(f"**最新日期**: {ind['last_date']} | **收盘价**: {ind['last_close']:.2f} 元")
    section.append("")
    section.append("### 技术指标")
    section.append("")
    section.append("| 指标 | 数值 |")
    section.append("|------|------|")
    section.append(f"| MA5 | {ind['ma5']:.2f} |")
    section.append(f"| MA20 | {ind['ma20']:.2f} |")
    section.append(f"| MA60 | {ind['ma60']:.2f} |")
    section.append(f"| RSI(14) | {ind['rsi14']:.1f} |")
    section.append(f"| 30日年化波动率 | {ind['vol30']:.1f}% |")
    section.append(f"| 周涨跌 | {ind['week_change']:+.2f}% |")
    section.append(f"| 月涨跌 | {ind['month_change']:+.2f}% |")
    section.append(f"| 年内涨跌 | {ind['ytd_change']:+.2f}% |")
    section.append(f"| 年内最高价 | {ind['ytd_high']:.2f} 元 |")
    section.append(f"| 年内最低价 | {ind['ytd_low']:.2f} 元 |")
    section.append(f"| 距年内高点 | -{ind['dist_from_high_pct']:.2f}% |")
    section.append(f"| 距年内低点 | +{ind['dist_from_low_pct']:.2f}% |")
    section.append("")
    section.append("### AI 分析")
    section.append("")
    section.append(analysis_text.strip())
    section.append("")
    section.append("---")
    section.append("")
    return "\n".join(section)


# ── Main ────────────────────────────────────────────────

def main():
    # Read API key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("错误: 请设置 DEEPSEEK_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    # Write report header
    with open(REPORT_FILE, "w") as f:
        f.write(f"# A股技术分析报告 — {TODAY.strftime('%Y-%m-%d')}\n\n")
        f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**分析标的**: {len(STOCKS)} 只\n\n")
        f.write("> ⚠️ 免责声明: 本报告由AI生成，基于技术指标分析，不构成投资建议。投资有风险，入市需谨慎。\n\n")
        f.write("---\n\n")

    for symbol, name in STOCKS:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 分析 {symbol} {name}...", flush=True)

        # Load
        rows = load_csv(symbol)
        rows[0]["_symbol"] = symbol
        rows[0]["_name"] = name
        for r in rows:
            r["_symbol"] = symbol
            r["_name"] = name

        # Compute
        indicators = compute_indicators(rows)

        # API
        try:
            analysis = call_deepseek(indicators, api_key)
        except Exception as e:
            analysis = f"【分析失败】{str(e)}"

        # Write section
        section = format_section(indicators, analysis)
        with open(REPORT_FILE, "a") as f:
            f.write(section)

        print(f"  -> 完成 (收盘价: {indicators['last_close']:.2f})", flush=True)

    # Append footer
    with open(REPORT_FILE, "a") as f:
        f.write(f"\n*报告结束 — 基于截至 {TODAY.strftime('%Y-%m-%d')} 的数据*\n")

    # Print full report to stdout
    print("\n" + "=" * 60)
    with open(REPORT_FILE) as f:
        print(f.read())


if __name__ == "__main__":
    main()
