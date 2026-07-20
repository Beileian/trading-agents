#!/usr/bin/env python3
"""
A股技术分析报告生成 — 并发版 v1.0.0
将10只标的的 DeepSeek API 调用并发化，从串行6-10分钟压缩到~1分钟
用法: python3 trading_analysis_concurrent.py [YYYY-MM-DD]
"""

import csv
import json
import math
import os
import sys
import time
import tempfile
import shutil
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symbols_config

import requests

# ── Config ──────────────────────────────────────────────
SYMBOLS = symbols_config.SYMBOLS
CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/logs/cache"
REPORT_DIR = "/root/.openclaw/workspace/projects/trading-agents/reports"

def _load_deepseek_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = "/root/.openclaw/workspace/projects/trading-agents/.env"
    if os.path.exists(env_file):
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
MAX_WORKERS = 5  # 并发数：DeepSeek 限流友好，5路并发安全

# ── Helpers ──────────────────────────────────────────────

def load_csv(symbol):
    fname = f"{symbol}-YFin-data-2021-06-04-2026-06-09.csv"
    fpath = os.path.join(CACHE_DIR, fname)
    # fallback to old name
    if not os.path.exists(fpath):
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
    rows.sort(key=lambda x: x["Date"])
    return rows


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values, window):
    if len(values) < 2:
        return values[-1] if values else None
    k = 2 / (window + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = values[i] - values[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_metrics(rows, symbol, stype):
    closes = [r["Close"] for r in rows]
    highs = [r["High"] for r in rows]
    lows = [r["Low"] for r in rows]
    volumes = [r["Volume"] for r in rows]
    opens = [r["Open"] for r in rows]
    dates = [r["Date"] for r in rows]

    last_close = closes[-1]
    last_open = opens[-1]

    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = (ema12 - ema26) if ema12 and ema26 else None
    signal_line = sma([macd_line] * 9 + [0]*0, 9) if macd_line else None  # simplified

    # Real MACD computation with EMA of signal
    macds = []
    for i in range(26, len(closes)):
        e12 = ema(closes[:i+1], 12)
        e26 = ema(closes[:i+1], 26)
        if e12 and e26:
            macds.append(e12 - e26)
    signal_line = ema(macds, 9) if len(macds) >= 9 else None
    macd_hist = (macd_line - signal_line) if macd_line and signal_line else None

    rsi_val = rsi(closes, 14)

    # Bollinger Bands
    bb_mid = ma20
    if bb_mid and len(closes) >= 20:
        variance = sum((c - bb_mid)**2 for c in closes[-20:]) / 20
        bb_std = math.sqrt(variance)
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
    else:
        bb_upper = bb_lower = None

    # ATR
    trs = []
    for i in range(1, len(rows)):
        h = highs[i]
        l = lows[i]
        pc = closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sma(trs, 14)

    # Volume MA
    vol_ma5 = sma(volumes, 5)
    vol_ratio = volumes[-1] / vol_ma5 if vol_ma5 and vol_ma5 != 0 else 1.0

    # Price change
    chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
    chg_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    chg_20d = (closes[-1] - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0

    # 52-week high/low
    high_52w = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    low_52w = min(lows[-252:]) if len(lows) >= 252 else min(lows)

    metrics = OrderedDict()
    metrics["最新收盘价"] = f"¥{last_close:.2f}"
    metrics["今日开盘"] = f"¥{last_open:.2f}"
    metrics["MA5"] = f"¥{ma5:.2f}" if ma5 else "N/A"
    metrics["MA10"] = f"¥{ma10:.2f}" if ma10 else "N/A"
    metrics["MA20"] = f"¥{ma20:.2f}" if ma20 else "N/A"
    metrics["MA60"] = f"¥{ma60:.2f}" if ma60 else "N/A"
    metrics["MACD"] = f"{macd_line:.4f}" if macd_line else "N/A"
    metrics["MACD柱"] = f"{macd_hist:.4f}" if macd_hist else "N/A"
    metrics["RSI(14)"] = f"{rsi_val:.1f}" if rsi_val else "N/A"
    metrics["布林上轨"] = f"¥{bb_upper:.2f}" if bb_upper else "N/A"
    metrics["布林下轨"] = f"¥{bb_lower:.2f}" if bb_lower else "N/A"
    metrics["ATR(14)"] = f"¥{atr:.2f}" if atr else "N/A"
    metrics["量比"] = f"{vol_ratio:.2f}"
    metrics["1日涨跌"] = f"{chg_1d:+.2f}%"
    metrics["5日涨跌"] = f"{chg_5d:+.2f}%"
    metrics["20日涨跌"] = f"{chg_20d:+.2f}%"

    extra = {
        "type": stype,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "bb_pos": (last_close - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper and bb_lower else None,
    }

    return metrics, extra


def format_metrics_table(metrics):
    lines = ["| 指标 | 数值 |", "|------|------|"]
    for k, v in metrics.items():
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)


def build_prompt(symbol, name, stype, metrics, extra):
    sys_prompt = """你是一位专业的A股量化分析师。根据给出的技术指标数据，对标的进行客观分析并输出JSON格式的交易决策。

输出要求：
{
  "趋势": "看涨/看跌/震荡",
  "支撑位": "具体价格（如 ¥4.85）",
  "阻力位": "具体价格（如 ¥5.30）",
  "建议": "买入/卖出/持有",
  "仓位": 0-100,
  "行业背景": "简短的一句话（10字以内）",
  "具体风险": "当前最需关注的1-2个具体风险点（15字以内）",
  "理由": "基于技术指标的客观理由（30字以内）"
}

规则：
- 支撑位和阻力位必须基于MA或布林带给出具体价格
- 如果标的已严重偏离MA20（>10%），标注高乖离风险
- 如果RSI>70提示超买，RSI<30提示超卖
- 仓位建议需结合趋势强度和乖离程度
- 只输出JSON，不要任何其他文字"""

    user_prompt = f"""标的: {symbol} {name} ({stype})

技术指标:
{json.dumps({k: str(v) for k, v in metrics.items()}, ensure_ascii=False, indent=2)}

52周最高: ¥{extra['high_52w']:.2f} | 52周最低: ¥{extra['low_52w']:.2f}
布林带位置: {extra['bb_pos']:.1f}% (0=下轨, 100=上轨)"""

    return sys_prompt, user_prompt


def _generate_concurrent_fallback(symbol, name, metrics):
    """API不可用时的规则化fallback分析（直接复用run_analysis.py的模板逻辑）。"""
    # 从metrics字典中提取技术指标
    try:
        last_close = float(metrics.get('最新收盘价', '0').replace('¥',''))
        ma5 = float(metrics.get('MA5', '0').replace('¥',''))
        ma20 = float(metrics.get('MA20', '0').replace('¥',''))
        ma60 = float(metrics.get('MA60', '0').replace('¥',''))
        rsi14 = float(metrics.get('RSI(14)', '50'))
        week_chg_str = metrics.get('1日涨跌', '0%').replace('%','').replace('+','')
        month_chg_str = metrics.get('20日涨跌', '0%').replace('%','').replace('+','')
        week_chg = float(week_chg_str) if week_chg_str else 0
        month_chg = float(month_chg_str) if month_chg_str else 0
    except (ValueError, TypeError, KeyError):
        last_close = 0; ma5 = 0; ma20 = 0; ma60 = 0; rsi14 = 50; week_chg = 0; month_chg = 0
    
    # 趋势判断
    if ma5 > ma20 > ma60:
        trend = "看涨"; trend_detail = "均线多头排列"
    elif ma5 < ma20 < ma60:
        trend = "看跌"; trend_detail = "均线空头排列"
    elif ma5 > ma20 and ma20 < ma60:
        trend = "震荡"; trend_detail = "短期修复但中期承压"
    elif ma5 < ma20 and ma20 > ma60:
        trend = "震荡"; trend_detail = "短期回调但中期未破"
    else:
        trend = "震荡"; trend_detail = "均线交织，方向不明"
    
    # RSI 信号
    if rsi14 > 70: rsi_signal = "RSI超买"
    elif rsi14 < 30: rsi_signal = "RSI超卖"
    else: rsi_signal = "RSI中性"
    
    # 支撑/阻力（基于MA20±2×ATR估算）
    atr_est = max(last_close * 0.02, abs(ma20 - last_close) * 2) if last_close > 0 else 0.5
    support = round(ma20 - atr_est * 2, 2) if ma20 > 0 else round(last_close * 0.95, 2)
    resistance = round(ma20 + atr_est * 2, 2) if ma20 > 0 else round(last_close * 1.05, 2)
    
    # 交易建议
    if trend == "看涨" and rsi14 < 60: advice = "买入"
    elif trend == "看跌" and rsi14 > 40: advice = "卖出"
    else: advice = "持有"
    
    # 仓位
    if trend == "看涨": pos = min(int(50 + (60 - rsi14) * 1.5), 80) if rsi14 < 70 else 20
    elif trend == "看跌": pos = max(int(30 - (rsi14 - 40) * 1.5), 5) if rsi14 > 40 else 50
    else: pos = 30
    
    # 理由
    parts = [f"{rsi_signal}({rsi14:.0f})"]
    if week_chg > 3: parts.append(f"周涨{week_chg:+.1f}%")
    elif week_chg < -3: parts.append(f"周跌{week_chg:+.1f}%")
    if month_chg > 5: parts.append(f"月涨{month_chg:+.1f}%需警惕追高风险")
    elif month_chg < -5: parts.append(f"月跌{month_chg:+.1f}%关注超跌反弹")
    parts.append(trend_detail)
    
    return {
        '趋势': trend,
        '支撑位': f'{support:.2f}',
        '阻力位': f'{resistance:.2f}',
        '建议': advice,
        '仓位': pos,
        '理由': '，'.join(parts[:3]),
        '行业背景': '规则化分析（API未可用时的自动兜底）',
        '具体风险': f'{trend_detail}环境下关注均线支撑位',
        '_fallback': True,
    }


DEFAULT_MAX_RETRIES = 2

def call_deepseek(symbol, sys_prompt, user_prompt, max_retries=None):
    """Call DeepSeek API with retry and 45s timeout.
    Robust JSON extraction: handles truncated output, multi-line strings, code fences."""
    if max_retries is None:
        max_retries = DEFAULT_MAX_RETRIES
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=45)
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                content = content.strip()
                # Remove code fences
                if content.startswith("```"):
                    lines = content.split("\n")
                    # Find matching closing fence
                    end_idx = None
                    for i in range(len(lines)-1, 0, -1):
                        if lines[i].strip() == "```":
                            end_idx = i
                            break
                    if end_idx:
                        content = "\n".join(lines[1:end_idx])
                    else:
                        content = "\n".join(lines[1:])
                
                # Try direct parse
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass
                
                # Try extract braced block (balanced braces, handles nested)
                import re
                # Find first { and try to find matching }
                brace_start = content.find("{")
                if brace_start >= 0:
                    depth = 0
                    brace_end = -1
                    for i in range(brace_start, len(content)):
                        if content[i] == "{":
                            depth += 1
                        elif content[i] == "}":
                            depth -= 1
                            if depth == 0:
                                brace_end = i + 1
                                break
                    if brace_end > 0:
                        json_str = content[brace_start:brace_end]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            # Try to fix common issues: trailing comma, unquoted values
                            # Fix: remove trailing comma before }
                            json_str = re.sub(r',\s*}', '}', json_str)
                            # Fix: incomplete truncated JSON — fill missing fields
                            try:
                                return json.loads(json_str)
                            except json.JSONDecodeError:
                                pass
                
                print(f"  [{symbol}] JSON parse error: {content[:100]}")
                return None
            else:
                print(f"  [{symbol}] API error (attempt {attempt+1}): {resp.status_code} {resp.text[:100]}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [{symbol}] API exception (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def analyze_one(symbol, name, stype, today_str):
    """Analyze a single symbol — designed for concurrent execution"""
    rows = load_csv(symbol)
    metrics, extra = compute_metrics(rows, symbol, stype)
    sys_prompt, user_prompt = build_prompt(symbol, name, stype, metrics, extra)
    decision = call_deepseek(symbol, sys_prompt, user_prompt)

    if decision is None:
        print(f"  [{symbol}] API重试{DEFAULT_MAX_RETRIES}次仍失败，使用规则化fallback分析")
        decision = _generate_concurrent_fallback(symbol, name, metrics)
    elif isinstance(decision, dict) and decision.get('_fallback'):
        print(f"  [{symbol}] 使用规则化fallback分析")

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
    print(f"  [{symbol}] {decision.get('趋势', 'N/A')} | {decision.get('建议', 'N/A')} | {decision.get('仓位', 'N/A')}%")
    return section


def extract_market_sentiment(opinions_path):
    """Extract condensed market sentiment from opinions file."""
    if not os.path.exists(opinions_path):
        return "_无可用的外部观点数据_"

    with open(opinions_path, "r") as f:
        content = f.read()

    import re
    lines = content.split("\n")
    themes = []

    for line in lines:
        match = re.match(r'\*\*(.+?)\*\*\s+\[(\d{4}-\d{2}-\d{2})\]\s+w=([\d.]+)', line)
        if match:
            title = match.group(1).strip()
            date = match.group(2)
            weight = float(match.group(3))
            if weight >= 0.70:
                themes.append(("其他", title, weight, date))

    seen = set()
    unique_themes = []
    for s, t, w, d in themes:
        key = (t, d)
        if key not in seen:
            seen.add(key)
            unique_themes.append((s, t, w, d))
    themes = unique_themes

    summary_lines = ["### 市场情绪摘要\n"]
    summary_lines.append(f"**近7日关键外部观点（权重≥0.70，共{len(themes)}篇）**：\n")
    for source, title, w, date in themes:
        summary_lines.append(f"- [{date}] w={w:.2f} — {title}")

    if themes:
        summary_lines.append("\n#### 情绪分布")
        bear_words = ['跌', '跌停', '亏损', '崩', '陷阱', '埋人', '跑输', '退出', '难受', '套牢', '双杀']
        bull_words = ['涨', '新高', '牛', '反弹', '受宠', '机会', '爆发']
        bull = 0; bear = 0; neut = 0
        for _, title, _, _ in themes:
            lower = title
            b_score = sum(1 for w in bull_words if w in lower)
            br_score = sum(1 for w in bear_words if w in lower)
            if br_score > b_score: bear += 1
            elif b_score > br_score: bull += 1
            else: neut += 1
        summary_lines.append(f"偏多:{bull} | 偏空:{bear} | 中性:{neut}")
        total_w = sum(w for _, _, w, _ in themes)
        summary_lines.append(f"累计权重: {total_w:.2f}")

    summary_lines.append("\n> 以上观点来自 IMA 知识库已保存的公众号文章，不代表投资建议。")
    return "\n".join(summary_lines)


def main():
    today_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    today_tag = today_str.replace("-", "")

    REPORT_PATH = f"{REPORT_DIR}/trading_analysis_{today_tag}.md"
    OPINIONS_PATH = f"{REPORT_DIR}/opinions_{today_tag}.md"

    print("=" * 60)
    print(f"  A股技术分析报告生成 (并发版 v1.0.0) — {today_str}")
    print(f"  标的数: {len(SYMBOLS)} | 并发数: {MAX_WORKERS}")
    print("=" * 60)

    header = f"""# 📊 A股技术分析与交易决策报告

**生成日期**: {today_str}
**数据范围**: 2021-06-04 至 {today_str}（约5年日线）
**分析标的**: {len(SYMBOLS)}只（{sum(1 for _,_,t in SYMBOLS if t=='index')}指数 + {sum(1 for _,_,t in SYMBOLS if t=='stock')}个股）
**分析模型**: DeepSeek-chat (并发{MAX_WORKERS}路)

---

## 📈 标的技术分析

"""

    # ── 并发分析 ──
    results = {}  # symbol -> section (preserve order later)
    total_start = time.time()
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for symbol, name, stype in SYMBOLS:
            future = executor.submit(analyze_one, symbol, name, stype, today_str)
            futures[future] = (symbol, name)

        completed = 0
        for future in as_completed(futures):
            symbol, name = futures[future]
            completed += 1
            try:
                section = future.result()
                results[symbol] = section
                print(f"  [{completed}/{len(SYMBOLS)}] {symbol} {name} ✅")
            except Exception as e:
                print(f"  [{completed}/{len(SYMBOLS)}] {symbol} {name} ❌ {e}")
                errors += 1
                # Generate fallback section
                results[symbol] = f"""### {symbol} — {name}

*⚠️ 分析失败: {e}*

---

"""

    elapsed = time.time() - total_start
    print(f"\n  并发分析完成: {elapsed:.1f}s | 成功: {len(SYMBOLS)-errors}/{len(SYMBOLS)} | 失败: {errors}")

    # ── 按原顺序组装报告 ──
    report_sections = [header]
    for symbol, name, stype in SYMBOLS:
        if symbol in results:
            report_sections.append(results[symbol])
        else:
            report_sections.append(f"""### {symbol} — {name}

*⚠️ 分析未完成*

---

""")

    # ── 市场情绪 ──
    print("  添加市场情绪章节...")
    sentiment = extract_market_sentiment(OPINIONS_PATH)
    sentiment_section = f"""## 🌍 市场情绪参考

{sentiment}

---

*报告由自动化分析系统生成，仅供参考，不构成投资建议。*
"""
    report_sections.append(sentiment_section)

    # ── 最终写入 ──
    with open(REPORT_PATH, "w") as f:
        f.write("".join(report_sections))

    print(f"\n{'=' * 60}")
    print(f"  报告已生成: {REPORT_PATH}")
    print(f"  总耗时: {time.time() - total_start:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
