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

def _load_deepseek_key():
    import os as _os
    key = _os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = "/root/.openclaw/workspace/projects/trading-agents/.env"
    if _os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key
    return ""

DEEPSEEK_API_KEY = _load_deepseek_key()
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

def _generate_fallback_analysis(ind):
    """API不可用时的规则化fallback分析，基于技术指标数值自动生成模板文案。"""
    name = ind['name']
    price = ind['last_close']
    ma5 = ind['ma5'] or 0
    ma20 = ind['ma20'] or 0
    ma60 = ind['ma60'] or 0
    rsi14 = ind['rsi14'] or 50
    week_chg = ind['week_change'] or 0
    month_chg = ind['month_change'] or 0
    
    # 趋势判断
    if ma5 > ma20 > ma60:
        trend = "看涨"
        trend_detail = "均线多头排列"
    elif ma5 < ma20 < ma60:
        trend = "看跌"
        trend_detail = "均线空头排列"
    elif ma5 > ma20 and ma20 < ma60:
        trend = "震荡"
        trend_detail = "短期修复但中期承压"
    elif ma5 < ma20 and ma20 > ma60:
        trend = "震荡"
        trend_detail = "短期回调但中期未破"
    else:
        trend = "震荡"
        trend_detail = "均线交织，方向不明"
    
    # RSI 信号
    if rsi14 > 70:
        rsi_signal = "RSI超买"
    elif rsi14 < 30:
        rsi_signal = "RSI超卖"
    else:
        rsi_signal = "RSI中性"
    
    # 支撑/阻力（基于MA20±2×ATR估算）
    closes_30 = ind['last_30_closes']
    if len(closes_30) >= 20:
        atr_est = sum(abs(closes_30[i] - closes_30[i-1]) for i in range(1, min(21, len(closes_30)))) / min(20, len(closes_30)-1)
    else:
        atr_est = price * 0.02
    support = round(ma20 - atr_est * 2, 2)
    resistance = round(ma20 + atr_est * 2, 2)
    
    # 交易建议
    if trend == "看涨" and rsi14 < 60:
        advice = "买入"
    elif trend == "看跌" and rsi14 > 40:
        advice = "卖出"
    else:
        advice = "持有"
    
    # 仓位建议
    if trend == "看涨":
        pos = min(int(50 + (60 - rsi14) * 1.5), 80) if rsi14 < 70 else 20
    elif trend == "看跌":
        pos = max(int(30 - (rsi14 - 40) * 1.5), 5) if rsi14 > 40 else 50
    else:
        pos = 30
    
    # 理由
    parts = [f"{rsi_signal}({rsi14:.0f})"]
    if week_chg > 3:
        parts.append(f"周涨{week_chg:+.1f}%")
    elif week_chg < -3:
        parts.append(f"周跌{week_chg:+.1f}%")
    if month_chg > 5:
        parts.append(f"月涨{month_chg:+.1f}%需警惕追高风险")
    elif month_chg < -5:
        parts.append(f"月跌{month_chg:+.1f}%关注超跌反弹")
    parts.append(trend_detail)
    reason = "，".join(parts[:3])
    
    return f"""趋势判断：{trend}
支撑位：{support:.2f} - {support+atr_est:.2f} 元
阻力位：{resistance-atr_est:.2f} - {resistance:.2f} 元
交易建议：{advice}
仓位建议：{pos}%
理由：{reason}"""


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
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是一位专业A股技术分析师，严格基于提供的技术指标数据进行分析，不做主观臆断。输出简洁、精确。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
    }

    last_error = None
    # 指数退避重试: 1s, 3s, 5s
    for attempt, delay in enumerate([1, 3, 5]):
        try:
            req = urllib.request.Request(
                DEEPSEEK_URL,
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            # 保护: 如果返回内容过短或明显是错误，不当作有效响应
            if len(content.strip()) < 15:
                raise ValueError(f"响应过短: {content!r}")
            return content
        except Exception as e:
            last_error = e
            if attempt < 2:  # 不是最后一次
                import time
                time.sleep(delay)
    
    # 所有重试失败 → 脚本内 rule-based fallback
    print(f"  ⚠️ {indicators['name']}: API重试3次仍失败({last_error})，使用规则化fallback分析")
    return f"【fallback】{_generate_fallback_analysis(indicators)}"


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
    is_fallback = analysis_text.startswith("【fallback】")
    if is_fallback:
        section.append("> ⚙️ 规则化分析（API未可用时的自动兜底）")
        section.append("")
        section.append(analysis_text.replace("【fallback】", "").strip())
    else:
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
