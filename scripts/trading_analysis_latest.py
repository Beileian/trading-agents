#!/usr/bin/env python3
"""
7-Symbol Trading Analysis & DeepSeek Decision Report Generator
No langchain/langgraph. Uses requests for DeepSeek API.
Incremental write to prevent OOM.
"""

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symbols_config

import requests

# ── Config ──────────────────────────────────────────────
CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/logs/cache"
REPORT_PATH = "/root/.openclaw/workspace/projects/trading-agents/reports/trading_analysis_20260623.md"
OPINIONS_PATH = "/root/.openclaw/workspace/projects/trading-agents/reports/opinions_20260623.md"
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

DEEPSEEK_KEY = _load_deepseek_key()
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"

SYMBOLS = symbols_config.SYMBOLS

TODAY_STR = "2026-06-23"

# ── Helpers ──────────────────────────────────────────────

def load_csv(symbol):
    """Load CSV, return list of dicts sorted by date ascending."""
    fname = f"{symbol}-YFin-data-2021-06-04-2026-06-04.csv"
    fpath = os.path.join(CACHE_DIR, fname)
    rows = []
    with open(fpath, "r") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["Close"] = float(r["Close"])
            r["High"] = float(r["High"])
            r["Low"] = float(r["Low"])
            r["Open"] = float(r["Open"])
            r["Volume"] = float(r["Volume"])
            rows.append(r)
    # Already sorted by date in CSV, but ensure
    rows.sort(key=lambda x: x["Date"])
    return rows


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values, window):
    """Exponential moving average."""
    if len(values) < 2:
        return values[-1] if values else None
    k = 2 / (window + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def volatility(closes, window=30):
    """Annualized 30-day volatility."""
    if len(closes) < window:
        return None
    subset = closes[-window:]
    returns = [(subset[i] / subset[i - 1] - 1) for i in range(1, len(subset))]
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0
    return math.sqrt(var) * math.sqrt(252) * 100  # annualized %


def compute_metrics(rows, symbol, stype):
    closes = [r["Close"] for r in rows]
    highs = [r["High"] for r in rows]
    lows = [r["Low"] for r in rows]
    dates = [r["Date"] for r in rows]

    last_close = closes[-1]
    year_high = max(highs)
    year_low = min(lows)
    # Find year subset (since start of 2026)
    ytd = [c for r, c in zip(rows, closes) if r["Date"] >= "2026-01-01"]
    ytd_start = ytd[0] if ytd else last_close
    ytd_change = (last_close / ytd_start - 1) * 100 if ytd_start else 0

    # Weekly (last 5 trading days)
    week_close = closes[-6] if len(closes) >= 6 else closes[0]
    week_change = (last_close / week_close - 1) * 100

    # Monthly (last ~22 trading days)
    month_close = closes[-22] if len(closes) >= 22 else closes[0]
    month_change = (last_close / month_close - 1) * 100

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    rsi14 = rsi(closes, 14)
    vol30 = volatility(closes, 30)

    dist_high = (last_close / year_high - 1) * 100
    dist_low = (last_close / year_low - 1) * 100

    metrics = OrderedDict()
    metrics["最新价"] = f"{last_close:.2f}"
    metrics["MA5"] = f"{ma5:.2f}" if ma5 else "N/A"
    metrics["MA20"] = f"{ma20:.2f}" if ma20 else "N/A"
    metrics["MA60"] = f"{ma60:.2f}" if ma60 else "N/A"
    metrics["RSI(14)"] = f"{rsi14:.1f}" if rsi14 else "N/A"
    metrics["30日波动率"] = f"{vol30:.2f}%" if vol30 else "N/A"
    metrics["周涨跌"] = f"{week_change:+.2f}%"
    metrics["月涨跌"] = f"{month_change:+.2f}%"
    metrics["年内涨跌"] = f"{ytd_change:+.2f}%"
    metrics["距年内高点"] = f"{dist_high:+.2f}%"
    metrics["距年内低点"] = f"{dist_low:+.2f}%"

    # Additional info for AI context
    extra = {
        "last_close": last_close,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": rsi14,
        "vol30": vol30,
        "week_change": week_change,
        "month_change": month_change,
        "ytd_change": ytd_change,
        "dist_high": dist_high,
        "dist_low": dist_low,
        "year_high": year_high,
        "year_low": year_low,
        "closes": closes,
        "dates": dates,
    }
    return metrics, extra


def build_prompt(symbol, name, stype, metrics, extra):
    """Build DeepSeek prompt for one symbol."""
    sys_prompt = """你是一位专业的A股技术分析师和交易决策顾问。请根据以下技术指标给出简洁分析。

行业背景要求：结合标的所属行业，一句话说明当前该行业的核心驱动或压力（如政策变化、利率环境、资金风格偏好）。如果是宽基指数，说明当前市场风格的宏观逻辑。

具体风险要求：指出一个可指认的、影响该标的近期走势的具体风险事件或因素（如解禁高峰、政策细则未落地、成本端波动、竞争格局变化）。使用具体数字或时间窗口，不使用笼统措辞。"""

    # Price vs MAs
    price = extra["last_close"]
    ma_status = []
    for ma_name, ma_val in [("MA5", extra["ma5"]), ("MA20", extra["ma20"]), ("MA60", extra["ma60"])]:
        if ma_val:
            rel = "上方" if price > ma_val else "下方"
            ma_status.append(f"{ma_name}({ma_val:.2f}){rel}")
    ma_str = "、".join(ma_status)

    user_prompt = f"""请分析以下A股标的并给出交易建议：

**标的**: {symbol} {name}（{'指数' if stype == 'index' else '个股'}）
**最新价**: {metrics['最新价']}
**均线位置**: {ma_str}
**RSI(14)**: {metrics['RSI(14)']}
**30日年化波动率**: {metrics['30日波动率']}
**周涨跌**: {metrics['周涨跌']}
**月涨跌**: {metrics['月涨跌']}
**年内涨跌**: {metrics['年内涨跌']}
**年内最高**: {extra['year_high']:.2f}
**年内最低**: {extra['year_low']:.2f}
**距年内高点**: {metrics['距年内高点']}
**距年内低点**: {metrics['距年内低点']}

请用JSON格式回复，不要包含任何其他内容：
{{"趋势": "看涨/看跌/震荡", "支撑位": "价格", "阻力位": "价格", "建议": "买入/卖出/持有", "仓位": 数字百分比(0-100), "行业背景": "≤60字当前行业核心逻辑", "具体风险": "≤60字可指认的风险事件", "理由": "≤100字简要理由"}}"""

    return sys_prompt, user_prompt


def call_deepseek(system_prompt, user_prompt, max_retries=3):
    """Call DeepSeek API with retry."""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                content = content.strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                return json.loads(content)
            else:
                print(f"  DeepSeek API error (attempt {attempt+1}): {resp.status_code} {resp.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  DeepSeek API exception (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def format_metrics_table(metrics):
    lines = []
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    for k, v in metrics.items():
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)


def extract_market_sentiment(opinions_path):
    """Extract a condensed market sentiment summary from opinions file."""
    if not os.path.exists(opinions_path):
        return "_无可用的外部观点数据_"

    with open(opinions_path, "r") as f:
        content = f.read()

    # Parse articles: format is **title** [date] w=0.xx [emoji]
    import re
    lines = content.split("\n")
    themes = []
    
    for line in lines:
        # Match: **title** [YYYY-MM-DD] w=N.NN optional_emoji
        match = re.match(r'\*\*(.+?)\*\*\s+\[(\d{4}-\d{2}-\d{2})\]\s+w=([\d.]+)', line)
        if match:
            title = match.group(1).strip()
            date = match.group(2)
            weight = float(match.group(3))
            if weight >= 0.70:
                # Determine source from section context (check previous headings)
                source = "其他"
                themes.append((source, title, weight, date))

    # Also parse from the IMA stdout-style output embedded in the markdown
    # Lines like: [2026-06-04] [二小姐笔记] w=1.00 ██████████
    for line in lines:
        match2 = re.match(r'\[(\d{4}-\d{2}-\d{2})\]\s+\[([^\]]+)\]\s+w=([\d.]+)', line)
        if match2:
            date = match2.group(1)
            source = match2.group(2)
            weight = float(match2.group(3))
            # Title is on the next line that's not a continuation
            # For now, use the plain source info
            if weight >= 0.70 and source != "其他":
                # Check if we already have this entry
                exists = any(t[0] == source and t[2] == weight for t in themes)
                if not exists:
                    themes.append((source, "(见原文)", weight, date))

    # Also search for section headers to categorize
    # Look for patterns like ### 🎯 ETF发车/实战信号
    section_map = {}
    current_section = ""
    for i, line in enumerate(lines):
        if line.startswith("### ") and not line.startswith("#### "):
            current_section = line.replace("### ", "").strip()
        # Match article title lines with weights
        match = re.match(r'\*\*(.+?)\*\*\s+\[(\d{4}-\d{2}-\d{2})\]\s+w=([\d.]+)', line)
        if match and current_section:
            title = match.group(1).strip()
            date = match.group(2)
            weight = float(match.group(3))
            if weight >= 0.70:
                # Update existing entry with section info
                for j, (s, t, w, d) in enumerate(themes):
                    if t == title:
                        themes[j] = (current_section, t, w, d)
                        break

    # Deduplicate
    seen = set()
    unique_themes = []
    for s, t, w, d in themes:
        key = (t, d)
        if key not in seen:
            seen.add(key)
            unique_themes.append((s, t, w, d))
    themes = unique_themes

    # Build summary
    summary_lines = []
    summary_lines.append("### 市场情绪摘要\n")
    summary_lines.append(f"**近7日关键外部观点（权重≥0.70，共{len(themes)}篇）**：\n")
    for source, title, w, date in themes:
        cat = source if source else "其他"
        summary_lines.append(f"- [{date}] w={w:.2f} [{cat}] — {title}")
    
    if themes:
        # Sentiment stats
        summary_lines.append("\n#### 情绪分布")
        bear_words = ['跌', '跌停', '亏损', '崩', '陷阱', '埋人', '跑输', '退出', '难受', '套牢', '双杀']
        bull_words = ['涨', '新高', '牛', '反弹', '受宠', '机会', '爆发']
        bull = 0; bear = 0; neut = 0
        for _, title, _, _ in themes:
            lower = title
            b_score = sum(1 for w in bull_words if w in lower)
            br_score = sum(1 for w in bear_words if w in lower)
            if br_score > b_score:
                bear += 1
            elif b_score > br_score:
                bull += 1
            else:
                neut += 1
        summary_lines.append(f"偏多:{bull} | 偏空:{bear} | 中性:{neut}")
        total_w = sum(w for _, _, w, _ in themes)
        summary_lines.append(f"累计权重: {total_w:.2f}")
    
    summary_lines.append("\n> 以上观点来自 IMA 知识库已保存的公众号文章，不代表投资建议。")

    return "\n".join(summary_lines)


# ── Main ──────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  股票技术分析报告生成 — {TODAY_STR}")
    print("=" * 60)

    report_sections = []

    # Header
    header = f"""# 📊 A股技术分析与交易决策报告

**生成日期**: {TODAY_STR}
**数据范围**: 2021-06-04 至 2026-06-23（约5年日线）
**分析标的**: 7只（3指数 + 4个股）
**分析模型**: DeepSeek-chat

---

## 📈 标的技术分析

"""
    report_sections.append(header)

    for symbol, name, stype in SYMBOLS:
        print(f"\n{'─' * 50}")
        print(f"  分析: {symbol} {name}")
        print(f"{'─' * 50}")

        # Load data
        rows = load_csv(symbol)
        print(f"  加载 {len(rows)} 条日线数据")

        # Compute metrics
        metrics, extra = compute_metrics(rows, symbol, stype)
        print(f"  技术指标计算完成")

        # Call DeepSeek
        print(f"  调用 DeepSeek API...")
        sys_prompt, user_prompt = build_prompt(symbol, name, stype, metrics, extra)
        decision = call_deepseek(sys_prompt, user_prompt)

        if decision is None:
            # Fallback if API fails
            decision = {
                "趋势": "无法判断",
                "支撑位": "N/A",
                "阻力位": "N/A",
                "建议": "持有",
                "仓位": 0,
                "理由": "API调用失败，请手动分析"
            }
            print(f"  ⚠️ API调用失败，使用默认值")

        print(f"  趋势: {decision.get('趋势', 'N/A')} | 建议: {decision.get('建议', 'N/A')} | 仓位: {decision.get('仓位', 'N/A')}%")

        # Build section
        section = f"""### {symbol} — {name}

#### 技术指标概览

{format_metrics_table(metrics)}

#### DeepSeek 交易决策

| 维度 | 判断 |
|------|------|
| **趋势判断** | {decision.get('趋势', 'N/A')} |
| **支撑位** | {decision.get('支撑位', 'N/A')} |
| **阻力位** | {decision.get('阻力位', 'N/A')} |
| **交易建议** | **{decision.get('建议', 'N/A')}** |
| **建议仓位** | {decision.get('仓位', 'N/A')}% |
| **行业背景** | {decision.get('行业背景', 'N/A')} |
| **具体风险** | {decision.get('具体风险', 'N/A')} |
| **简要理由** | {decision.get('理由', 'N/A')} |

---

"""
        report_sections.append(section)

        # Incremental write (append mode after header)
        with open(REPORT_PATH, "w") as f:
            f.write("".join(report_sections))

        # Small delay between API calls
        if symbol != SYMBOLS[-1][0]:
            time.sleep(0.5)

    # ── Market Sentiment Section ─────────────────────────
    print(f"\n{'─' * 50}")
    print(f"  添加市场情绪章节...")
    sentiment = extract_market_sentiment(OPINIONS_PATH)
    sentiment_section = f"""## 🌍 市场情绪参考

{sentiment}

---

*报告由自动化分析系统生成，仅供参考，不构成投资建议。*
"""
    report_sections.append(sentiment_section)

    # Final write
    with open(REPORT_PATH, "w") as f:
        f.write("".join(report_sections))

    print(f"\n{'=' * 60}")
    print(f"  报告已生成: {REPORT_PATH}")
    print(f"{'=' * 60}")

    # Output full report to stdout
    with open(REPORT_PATH, "r") as f:
        print(f.read())


if __name__ == "__main__":
    main()
