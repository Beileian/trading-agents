#!/usr/bin/env python3
"""
交易推荐 Markdown 表格生成 | 含刘晨明乖离率指标 + 趋势方向
乖离率体系: BIAS_20 判断偏离 → 5日趋势方向 → 修正支撑/阻力触发逻辑
原则: 支撑≠买进, 阻力≠卖出。乖离率告诉你"车开多快"。

v2: 集成外盘研判信号 + IMA 知识库观点 → 触发条件列落地到对应标的
"""

import sys, os, re, json, pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
OVERSEAS_DIR = "/root/.openclaw/workspace/projects/overseas-morning-brief"


def load_calibration():
    """Load yesterday's structured review state for feedback into today's recommendations."""
    state_file = f"{PROJECT_DIR}/logs/cognition_state.json"
    if not os.path.exists(state_file):
        return None, None, None
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return None, None, None

    last = state.get("last_review", {})
    hint = last.get("calibration_hint")
    metrics = state.get("metrics", {})

    # Extract rolling metrics
    oa = metrics.get("overseas_direction_accuracy", {})
    sm = metrics.get("sell_misrate", {})
    bh = metrics.get("breach_hit_rate", {})

    rolling = {
        "overseas_5d_acc": oa.get("rolling_5d"),
        "overseas_label": oa.get("label", "neutral"),
        "sell_misrate_3d": sm.get("rolling_3d"),
        "sell_label": sm.get("label", "neutral"),
        "breach_3d_avg": bh.get("rolling_3d"),
        "breach_label": bh.get("label", "neutral"),
    }

    return hint, last, rolling

# 标的 → 外盘/板块映射关键词（用于匹配外盘信号和 IMA 观点）
TICKER_SECTOR_MAP = {
    '000016.SH': ['上证50', '大盘蓝筹', '金融', '权重', '上证'],
    '000300.SH': ['沪深300', '大盘', '蓝筹', '核心资产'],
    '000688.SH': ['科创50', '科创', '科技', '纳斯达克', '纳指', '创业板', '成长'],
    '601288.SH': ['农业银行', '银行', '金融', '红利', '高股息'],
    '601988.SH': ['中国银行', '银行', '金融', '红利', '高股息'],
    '600036.SH': ['招商银行', '银行', '金融', '零售银行'],
    '600795.SH': ['国电电力', '电力', '能源', '公用事业', '红利'],
    '000066.SZ': ['中国长城', '信创', '国产替代', '科技', '军工'],
    '600562.SH': ['国睿科技', '军工', '雷达', '国防'],
}

TICKER_CACHE = {
    '000016.SH': '000016.SH-daily.csv', '000300.SH': '000300.SH-daily.csv',
    '000688.SH': '000688.SH-daily.csv', '601288.SH': '601288.SS-daily.csv',
    '601988.SH': '601988.SS-daily.csv', '600036.SH': '600036.SS-daily.csv',
    '600795.SH': '600795.SH-daily.csv', '000066.SZ': '000066.SZ-daily.csv',
    '600562.SH': '600562.SH-daily.csv',
}

TICKERS = [
    ("000016.SH", "上证50"), ("000300.SH", "沪深300"),
    ("000688.SH", "科创50"), ("601288.SH", "农业银行"),
    ("601988.SH", "中国银行"), ("600036.SH", "招商银行"),
    ("600795.SH", "国电电力"), ("000066.SZ", "中国长城"),
    ("600562.SH", "国睿科技"),
]


def extract(section, key):
    for line in section.split('\n'):
        line = line.strip()
        if not line.startswith('|') or not line.endswith('|'):
            continue
        clean = line.replace('**', '')
        parts = [p.strip() for p in clean.split('|')]
        if len(parts) >= 3 and parts[1] == key:
            return parts[2]
    return '-'


def calc_bias_direction(symbol):
    """
    返回 (bias_20_value, bias_direction_icon)
    bias_direction: ↑乖离扩大 ↓乖离收敛 →乖离持平
    基于最近5个交易日的BIAS_20变化方向
    """
    cache_file = TICKER_CACHE.get(symbol, '')
    cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
    if not os.path.exists(cache_path):
        return '-', ''

    try:
        df = pd.read_csv(cache_path, parse_dates=['Date'])
        df = df.set_index('Date').sort_index()
        close = df['Close']
        ma20 = close.rolling(20).mean()

        bias_series = (close - ma20) / ma20 * 100
        bias_series = bias_series.dropna()

        if len(bias_series) < 6:
            b20 = bias_series.iloc[-1]
            result = f"{b20:+.1f}%"
            if b20 > 5:  result += '⚠️'
            elif b20 < -5: result += '💡'
            return result, '→'

        latest = bias_series.iloc[-1]
        prev5 = bias_series.iloc[-6:-1].mean()

        delta = latest - prev5
        if delta > 0.5:
            direction = '↑'
        elif delta < -0.5:
            direction = '↓'
        else:
            direction = '→'

        result = f"{latest:+.1f}%"
        if latest > 5:
            result += '⚠️'
        elif latest < -5:
            result += '💡'

        return result, direction
    except:
        return '-', ''


def adjust_trigger(advice, support, resistance, bias_val, direction, calibration_hint=None):
    """根据乖离率修正触发条件，叠加上日复盘校准信号"""
    if advice == '-' or '出' not in advice and '入' not in advice and '持' not in advice:
        return '-'

    try:
        b = float(bias_val.replace('%', '').replace('⚠️', '').replace('💡', '').strip())
    except:
        b = 0

    # ── 复盘校准：卖出建议高误判 → 当日卖出建议降级为持有-观望 ──
    calibration_suffix = ""
    if calibration_hint == "sell_hold_bias" and '出' in advice:
        calibration_suffix = " ⚠️上日复盘：卖出高误判，仓位谨慎"
    elif calibration_hint == "overseas_unreliable" and '持' in advice:
        calibration_suffix = " ⚠️上日复盘：外盘连续偏离，轻外盘重内盘"
    elif calibration_hint == "high_volatility" and ('入' in advice or '出' in advice):
        calibration_suffix = " ⚠️上日复盘：连续穿越，波动加剧"

    base = None

    if '持' in advice:
        if b < -2 and direction == '↓':
            base = f"回调{int(abs(b))}%接近支撑{support}，反弹可期 → 可分批买入"
        elif b > 2 and direction == '↑':
            base = f"乖离{resistance}扩大 + 接近阻力 → 减仓规避"
        elif b < -2 and direction == '↑':
            base = f"加速下跌中，暂勿抄底 → 等{support}企稳"
        elif b > 2 and direction == '↓':
            base = f"乖离收敛中，趋势健康 → 持有"
        else:
            base = f"突破{resistance}加仓 / 跌破{support}减仓"

    elif '出' in advice:
        if b > 2 and direction == '↑':
            base = f"乖离+阻力双重压力 → 跌破{support}坚决止损"
        elif direction == '↓':
            base = f"乖离收敛中，反弹可减亏 → {support}~{resistance}分批出"
        else:
            base = f"跌破{support}止损"

    elif '入' in advice:
        if b < -2 and direction == '↓':
            base = f"超跌+乖离收敛 → {support}附近建仓"
        elif b > 2 and direction == '↑':
            base = f"乖离偏高，追高风险 → 等回调至{support}再入"
        else:
            base = f"回踩{support}买入"

    if base is None:
        return '-'
    return base + calibration_suffix


# ═══════════════════════════════════════════════
#  v2: 外盘研判 + IMA 知识库信号落地
# ═══════════════════════════════════════════════

def load_overseas_signal(date_str: str) -> str | None:
    """加载外盘信号文件，返回纯文本内容"""
    # date_str 是 YYYYMMDD，需要转为 YYYY-MM-DD
    y = date_str[:4]; m = date_str[4:6]; d = date_str[6:8]
    iso = f"{y}-{m}-{d}"
    path = os.path.join(OVERSEAS_DIR, "reports", f"overseas_signal_{iso}.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def load_ima_opinions(date_str: str) -> str | None:
    """加载 IMA 观点文件"""
    path = os.path.join(PROJECT_DIR, "reports", f"opinions_{date_str}.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def match_external_signals(symbol: str, overseas_text: str | None,
                           opinions_text: str | None) -> tuple[list[str], str]:
    """
    匹配外盘和IMA信号。
    Returns: (风险标签列表, 知识库摘要50字以内)
    """
    risk_tags = []
    ima_summary = ""
    keywords = TICKER_SECTOR_MAP.get(symbol, [])

    # ── 外盘信号 → 风险标签 ──
    if overseas_text:
        overseas_lower = overseas_text.lower()
        if any(w in overseas_lower for w in ['偏空', '暴跌', '承压', '跳空']):
            overseas_direction = '偏空'
        elif any(w in overseas_lower for w in ['偏多', '反弹', '逆势', '修复', '独立行情']):
            overseas_direction = '偏多'
        else:
            overseas_direction = None

        matched_kws = [kw for kw in keywords if kw.lower() in overseas_lower]
        if matched_kws and overseas_direction:
            if overseas_direction == '偏空':
                risk_tags.append("🌐外盘偏空承压")
            elif overseas_direction == '偏多':
                risk_tags.append("🌐外盘偏多助力")

    # ── IMA 观点 → 知识库摘要 ──
    if opinions_text:
        opinion_lines = [l for l in opinions_text.split('\n') if any(
            kw in l for kw in keywords)]
        if opinion_lines:
            # 取第一条有效摘要，截断50字符
            import re as _re
            raw = ''
            for ol in opinion_lines:
                stripped = ol.strip().lstrip('- *> ').strip()
                # 跳过来源/权重/markdown链接等元数据行
                if any(skip in stripped for skip in ['笔记', '来自', '权重衰减', '时间:', '作者:', '日期:']):
                    continue
                # 去掉 markdown 链接 [text](url)
                stripped = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'', stripped)
                # 去掉 **bold** 标记
                stripped = _re.sub(r'\*\*([^*]+)\*\*', r'', stripped)
                # 去掉权重标记 w=0.xx | ⭐
                stripped = _re.sub(r'\s*w=[\d.]+\s*', '', stripped)
                # 去掉 [YYYY-MM-DD] 日期标签
                stripped = _re.sub(r'\s*\[[\d]{4}-[\d]{2}-[\d]{2}\]\s*', '', stripped)
                # 去掉残留的 ** 
                stripped = stripped.replace('**', '')
                stripped = _re.sub(r'\s*⭐\s*$', '', stripped)
                if len(stripped) > 8:
                    raw = stripped
                    break
            if not raw:
                raw = opinion_lines[0].strip().lstrip('- *> ').strip()
                raw = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'', raw)
                raw = _re.sub(r'\*\*([^*]+)\*\*', r'', raw)
            if len(raw) > 50:
                ima_summary = raw[:48] + ".."
            elif raw:
                ima_summary = raw

    return risk_tags, ima_summary


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime('%Y%m%d')
    analysis_file = f"{PROJECT_DIR}/reports/trading_analysis_{date_str}.md"
    if not os.path.exists(analysis_file):
        print(f"❌ 报告未找到: {analysis_file}")
        sys.exit(1)
    with open(analysis_file) as f:
        text = f.read()

    # 加载外部信号源
    overseas_text = load_overseas_signal(date_str)
    opinions_text = load_ima_opinions(date_str)

    # 加载上日复盘校准信号
    calibration_hint, last_review, rolling_metrics = load_calibration()

    records = []
    for symbol, name in TICKERS:
        start = text.find(f"### {symbol}")
        if start < 0:
            continue
        section = text[start:]
        for other_sym, _ in TICKERS:
            if other_sym != symbol:
                pos = section.find(f"### {other_sym}", 10)
                if pos > 0:
                    section = section[:pos]
                    break

        price = extract(section, '最新价').replace('¥', '').strip()
        bias_val, bias_dir = calc_bias_direction(symbol)
        bias_display = f"{bias_val} {bias_dir}" if bias_dir else bias_val
        support = extract(section, '支撑位').replace('¥', '').strip()
        resistance = extract(section, '阻力位').replace('¥', '').strip()
        trend = extract(section, '趋势判断')
        advice = extract(section, '交易建议')
        pos = extract(section, '建议仓位')

        # 技术面触发条件
        tech_trigger = adjust_trigger(advice, support, resistance, bias_val, bias_dir, calibration_hint)

        # 外部信号落地
        risk_tags, ima_summary = match_external_signals(symbol, overseas_text, opinions_text)

        records.append({
            'name': name, 'price': price, 'bias': bias_display,
            'support': support, 'resistance': resistance,
            'trend': trend, 'advice': advice, 'pos': pos,
            'trigger': tech_trigger, 'risk_tags': risk_tags, 'ima': ima_summary,
        })

    # ── 构建段落式输出 ──
    lines = []
    lines.append("## 📊 交易推荐 · 开盘前推送")
    lines.append("")

    # 上日复盘校准（表格上方一行）
    if last_review and last_review.get("date"):
        cal_parts = [f"📅 {last_review['date']}复盘"]
        if last_review.get("direction_match"):
            icon = "✓" if last_review["direction_match"] == "吻合" else "✗"
            cal_parts.append(f"外盘{icon}{last_review.get('overseas_predicted','--')}→{last_review.get('overseas_actual','--')}")
        if last_review.get("sell_wrong_names"):
            sw = ",".join(last_review['sell_wrong_names'])
        cal_parts.append(f"卖出误判{sw}")
        if last_review.get("breach_names"):
            bn = ",".join(last_review['breach_names'])
        cal_parts.append(f"穿越{bn}")
        if last_review.get("cognitive_tag"):
            cal_parts.append(last_review['cognitive_tag'])
        lines.append(" · ".join(cal_parts))

    # 外盘信号摘要
    if overseas_text:
        lines.append("")
        direction_match = re.search(r"\*\*研判方向\*\*:\s*(.+?)(?:\s*\|)", overseas_text)
        if direction_match:
            direction = direction_match.group(1)
            lines.append(f"🌐 隔夜外盘: **{direction}**")
            for l in overseas_text.split('\n'):
                if l.strip().startswith('- ') and '📊' not in l:
                    lines.append(f"> {l.strip()}")
                    break

    lines.append("")
    lines.append("---")

    sell = hold = buy = 0
    for r in records:
        if '出' in r['advice']:
            op_icon = '🔴'
            op_label = '卖出'
            sell += 1
        elif '入' in r['advice']:
            op_icon = '🟢'
            op_label = '买入'
            buy += 1
        else:
            op_icon = '🟡'
            op_label = '持有'
            hold += 1

        lines.append("")
        # 第一行：图标 + 名称 + 现价 + 乖离 + 操作(仓位)
        header = f"{op_icon} **{r['name']}** · {r['price']}"
        if r['bias'] and r['bias'] != '-':
            header += f" · 乖离 {r['bias']}"
        header += f" · {op_label} ({r['pos']})"
        lines.append(header)

        # 支撑/阻力行
        sup_res = []
        if r['support'] and r['support'] != '-':
            sup_res.append(f"支撑 {r['support']}")
        if r['resistance'] and r['resistance'] != '-':
            sup_res.append(f"阻力 {r['resistance']}")
        if sup_res:
            lines.append(f"　{' / '.join(sup_res)}")

        # 触发条件
        if r['trigger'] and r['trigger'] != '-':
            lines.append(f"　触发：{r['trigger']}")

        # 风险/催化
        risk_cat = []
        if r['risk_tags']:
            risk_cat.extend(r['risk_tags'])
        if r['ima']:
            risk_cat.append(f"📰{r['ima']}")

        # 复盘校准 → 缝入对应标的
        if last_review and last_review.get("date"):
            if r['name'] in last_review.get("sell_wrong_names", []):
                risk_cat.append("⚠️上日复盘：卖出建议误判")

        if risk_cat:
            lines.append(f"　风险/催化：{' · '.join(risk_cat)}")

    lines.append("")
    lines.append("---")
    sigs = []
    if sell: sigs.append(f"🔴卖出 {sell}只")
    if hold: sigs.append(f"🟡持有 {hold}只")
    if buy:  sigs.append(f"🟢买入 {buy}只")
    lines.append(" · ".join(sigs))
    lines.append("")
    lines.append("> 乖离↑加速偏离 ↓回归均线 →持平 | ⚠️ AI模拟分析 · 不构成投资建议")

    report = '\n'.join(lines)
    output_file = f"{PROJECT_DIR}/reports/trade_signals_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(report)
    print(report)
    print(f"\n✓ {output_file}")


if __name__ == '__main__':
    main()
