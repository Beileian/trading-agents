#!/usr/bin/env python3
"""
缠论收盘复盘引擎 v1.0 — proxy_research 模式
基于日线数据走 8 步工作流：级别→结构→状态→信号→动作→过滤器→回测→自检
不调 LLM，纯规则引擎。数据不足时自动降级标注。

来源: chanlun-trading-system SKILL.md (strict_chanlun_audit gate)
"""

import os, sys, json
import pandas as pd
import numpy as np

PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

TICKER_CACHE = {
    '000016.SH': '000016.SH-daily.csv', '000300.SH': '000300.SH-daily.csv',
    '000688.SH': '000688.SH-daily.csv', '601288.SH': '601288.SS-daily.csv',
    '601988.SH': '601988.SS-daily.csv', '600036.SH': '600036.SS-daily.csv',
    '600795.SH': '600795.SH-daily.csv', '000066.SZ': '000066.SZ-daily.csv',
    '600562.SH': '600562.SH-daily.csv', '562500.SH': '562500.SH-daily.csv',
}

NAME_TO_TICKER = {
    "上证50": "000016.SH", "沪深300": "000300.SH", "科创50": "000688.SH",
    "农业银行": "601288.SH", "中国银行": "601988.SH", "招商银行": "600036.SH",
    "国电电力": "600795.SH", "中国长城": "000066.SZ", "国睿科技": "600562.SH",
    "机器人ETF": "562500.SH",
}


def load_daily(symbol: str) -> pd.DataFrame | None:
    """加载日线数据"""
    cache_file = TICKER_CACHE.get(symbol, '')
    cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
    if not os.path.exists(cache_path):
        return None
    df = pd.read_csv(cache_path, parse_dates=['Date'])
    df = df.set_index('Date').sort_index()
    if len(df) < 60:
        return None
    return df


def find_fenxing(df: pd.DataFrame) -> list[dict]:
    """
    日线级别分型识别 (顶分型/底分型)。
    条件：中间K线高>左右K线高 & 中间K线低>左右K线低 → 顶分型
         中间K线低<左右K线低 & 中间K线高<左右K线高 → 底分型
    返回 [{type: top|bottom, index: date, price: float}, ...]
    """
    fenxing = []
    high = df['High'].values
    low = df['Low'].values
    idx = df.index
    for i in range(2, len(df) - 2):
        # 顶分型
        if high[i] > max(high[i-1], high[i+1]) and \
           high[i] > max(high[i-2], high[i+2]):
            fenxing.append({'type': 'top', 'date': idx[i], 'price': float(high[i]), 'idx': i})
        # 底分型
        if low[i] < min(low[i-1], low[i+1]) and \
           low[i] < min(low[i-2], low[i+2]):
            fenxing.append({'type': 'bottom', 'date': idx[i], 'price': float(low[i]), 'idx': i})
    return fenxing


def build_bi_from_fenxing(fenxing: list[dict]) -> list[dict]:
    """
    从分型构建笔。一笔=相邻的顶底交替。
    过滤: 顶底之间至少间隔1根K线，且笔的幅度≥3%（日线级别有效笔）。
    返回 [{type: up|down, start: {date,price}, end: {date,price}, amplitude: float}, ...]
    """
    if len(fenxing) < 2:
        return []
    bi_list = []
    current = fenxing[0]
    for f in fenxing[1:]:
        if f['type'] == current['type']:
            # 同向分型，取更极端的
            if f['type'] == 'top' and f['price'] > current['price']:
                current = f
            elif f['type'] == 'bottom' and f['price'] < current['price']:
                current = f
            continue
        # 顶底交替
        if f['idx'] - current['idx'] < 2:
            continue  # 至少间隔1根K线
        amp = abs(f['price'] - current['price']) / current['price']
        if amp < 0.03:
            continue  # 无效笔（<3%幅度）
        bi_type = 'down' if current['type'] == 'top' else 'up'
        bi_list.append({
            'type': bi_type,
            'start': {'date': str(current['date'].date()) if hasattr(current['date'], 'date') else str(current['date']),
                      'price': current['price']},
            'end': {'date': str(f['date'].date()) if hasattr(f['date'], 'date') else str(f['date']),
                    'price': f['price']},
            'amplitude': round(amp * 100, 1),
        })
        current = f
    return bi_list


def identify_zhongshu(bi_list: list[dict]) -> list[dict]:
    """
    从笔序列识别中枢。
    中枢=连续至少3笔重叠区间。
    返回 [{zg: float, zd: float, mid: float, bi_count: int, start_date, end_date}, ...]
    """
    if len(bi_list) < 3:
        return []
    zhongshu_list = []
    for i in range(len(bi_list) - 2):
        # 取连续3笔
        b1, b2, b3 = bi_list[i], bi_list[i+1], bi_list[i+2]
        # 计算3笔的重叠区间
        highs = []
        lows = []
        for b in [b1, b2, b3]:
            highs.append(max(b['start']['price'], b['end']['price']))
            lows.append(min(b['start']['price'], b['end']['price']))
        zg = min(highs)  # 中枢上沿 = 最低的高点
        zd = max(lows)   # 中枢下沿 = 最高的低点
        if zg > zd:
            zhongshu_list.append({
                'zg': round(zg, 2), 'zd': round(zd, 2),
                'mid': round((zg + zd) / 2, 2),
                'bi_count': 3,
                'start_date': b1['start']['date'],
                'end_date': b3['end']['date'],
            })
    return zhongshu_list


def build_chanlun_summary(prices: dict, thresholds: dict) -> list[str]:
    """
    缠论收盘复盘主函数 — 返回报告行列表。

    对每只标的走 8 步工作流 (proxy_research 模式):
      1. 背景/级别设定
      2. 结构 (分型→笔→中枢)
      3. 状态 (当前处于中枢的什么位置)
      4. 信号 (买卖点候选)
      5. 动作建议
      6. 技术过滤器
      7. 回测代理
      8. 自检
    """
    lines = []
    lines.append("**④b 缠论结构简评**")
    lines.append(f"*proxy_research — 日线级代理分析，无30m/5m触发级确认。非严格缠论，仅供结构参考。*")
    lines.append("")

    for name in ["上证50", "沪深300", "科创50", "农业银行", "中国银行",
                 "招商银行", "国电电力", "中国长城", "国睿科技", "机器人ETF"]:
        ticker = NAME_TO_TICKER.get(name)
        if not ticker:
            continue
        df = load_daily(ticker)
        if df is None:
            continue

        close = df['Close']
        latest_price = prices.get(name, {}).get("price", float(close.iloc[-1]))
        chg_pct = prices.get(name, {}).get("chg_pct", 0)
        t = thresholds.get(name, {})
        today_op = t.get("op", "hold")
        today_support = t.get("support")
        today_resistance = t.get("resistance")

        # ── Step 1: 级别设定 ──
        review_level = "日线"
        trade_level = "日线笔"
        confirm_level = "30分钟"  # 缺数据
        trigger_level = "5分钟"   # 缺数据
        definition_mode = "proxy_research"
        structure_completeness = "partial"
        approximation_loss = "缺少confirm/trigger级别数据，中枢由3笔重叠代理识别"

        # ── Step 2: 结构 (分型→笔→中枢) ──
        fenxing = find_fenxing(df)
        bi_list = build_bi_from_fenxing(fenxing)
        zhongshu_list = identify_zhongshu(bi_list)

        # 最近有效中枢
        recent_zs = zhongshu_list[-1] if zhongshu_list else None
        recent_bi = bi_list[-1] if bi_list else None
        recent_3bi = bi_list[-3:] if len(bi_list) >= 3 else bi_list

        # ── Step 3: 状态判定 ──
        state = classify_state(latest_price, chg_pct, recent_zs, recent_bi, bi_list, close)

        # ── Step 4: 信号 ──
        signal = identify_signal(state, latest_price, recent_zs, recent_bi, bi_list, today_support, today_resistance)

        # ── Step 5: 动作 ──
        action = map_action(signal, state, today_op)

        # ── Step 6: 过滤器 ──
        filters = apply_filters(df, latest_price, name)

        # ── Step 7: 回测代理 ──
        backtest = backtest_proxy(state, bi_list, latest_price)

        # ── Step 8: 自检 ──
        selfcheck = self_check(state, signal, action, definition_mode)

        # ── 组装输出 ──
        zs_str = f"中枢 {recent_zs['zd']}-{recent_zs['zg']}" if recent_zs else "无有效中枢"
        bi_str = f"{len(bi_list)}笔"
        amp_str = f"最近笔: {recent_bi['type']} {recent_bi['amplitude']}%" if recent_bi else ""

        lines.append(
            f"**{name}** {price_str(latest_price, chg_pct)} | "
            f"{state['label']} | {signal['label']} | {action['label']}"
        )
        lines.append(f"> 级别: {review_level}/{trade_level} | {definition_mode} | {zs_str} | {bi_str} | {amp_str}")
        lines.append(f"> 结构: {state['detail']}")
        lines.append(f"> 信号: {signal['detail']} | 动作: {action['detail']}")
        if filters:
            lines.append(f"> 过滤器: {' | '.join(filters)}")
        if backtest:
            lines.append(f"> 回测: {backtest}")
        lines.append(f"> 自检: {selfcheck}")
        lines.append("")

    return lines


def price_str(price: float, chg: float) -> str:
    arrow = "↑" if chg > 0 else "↓" if chg < 0 else "→"
    return f"{price:.2f} {arrow}{abs(chg):.1f}%"


def classify_state(price, chg_pct, zs, recent_bi, bi_list, close):
    """Step 3: 判定当前在趋势/盘整/中枢的什么位置"""
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean())
    trend_up = ma20 > ma60 and price > ma20
    trend_down = ma20 < ma60 and price < ma20

    if zs:
        zd, zg, zmid = zs['zd'], zs['zg'], zs['mid']
        above_zs = price > zg
        below_zs = price < zd
        in_zs = zd <= price <= zg
        near_high = price >= zmid and price <= zg
        near_low = price <= zmid and price >= zd

        if above_zs:
            if trend_up and recent_bi and recent_bi['type'] == 'up':
                return {'label': '🟢 中枢突破·延续', 'detail': f'价格{price:.2f}>中枢上沿{zg}，均线多头+最近笔向上，上行趋势延续'}
            elif trend_up and recent_bi and recent_bi['type'] == 'down':
                return {'label': '🟡 中枢突破·回踩', 'detail': f'价格{price:.2f}>中枢上沿{zg}，均线多头但最近笔向下回落，观察是否回踩中枢{zg}站稳'}
            elif recent_bi and recent_bi['type'] == 'down':
                return {'label': '🔴 中枢上方回落', 'detail': f'价格{price:.2f}>中枢上沿{zg}但最近笔向下{recent_bi["amplitude"]}%，均线未确认多头，警惕假突破后回落'}
            else:
                return {'label': '⚠️ 中枢上破待确认', 'detail': f'价格{price:.2f}>中枢上沿{zg}，但均线未确认多头，观察回踩中枢是否站稳'}
        elif below_zs:
            if trend_down:
                return {'label': '🔴 中枢下破', 'detail': f'价格{price:.2f}<中枢下沿{zd}，均线空头排列，下行趋势延续'}
            else:
                return {'label': '🟡 中枢下破待确认', 'detail': f'价格{price:.2f}<中枢下沿{zd}，均线未确认空头，观察是否快速拉回'}
        elif in_zs:
            if near_high:
                return {'label': '🟡 中枢上部震荡', 'detail': f'价格{price:.2f}在中枢{zg}-{zd}内偏上，关注突破{zg}或回落{zmid}'}
            elif near_low:
                return {'label': '🟡 中枢下部震荡', 'detail': f'价格{price:.2f}在中枢{zg}-{zd}内偏下，关注跌破{zd}或反弹{zmid}'}
            else:
                return {'label': '🟡 中枢中轴震荡', 'detail': f'价格{price:.2f}在中枢中轴附近，方向不明'}
    else:
        if trend_up:
            return {'label': '🟢 上升趋势(无中枢)', 'detail': f'均线多头排列，价格{price:.2f}>MA20({ma20:.2f})'}
        elif trend_down:
            return {'label': '🔴 下降趋势(无中枢)', 'detail': f'均线空头排列，价格{price:.2f}<MA20({ma20:.2f})'}
        else:
            return {'label': '⚪ 无趋势(无中枢)', 'detail': f'均线缠绕，无明确方向'}


def identify_signal(state, price, zs, recent_bi, bi_list, support, resistance):
    """Step 4: 识别买卖点候选 (proxy_research)"""
    if not zs:
        return {'label': '—', 'detail': '无中枢，暂不识别买卖点'}

    zd, zg = zs['zd'], zs['zg']
    above_zs = price > zg
    below_zs = price < zd

    if below_zs and _has_prior_trend(bi_list, 'down'):
        # 中枢下方 + 前有下跌趋势 → 一买候选
        return {'label': '1B候选', 'detail': f'中枢下沿{zd}下方，前有下跌结构，关注底分型止跌确认一买'}
    elif below_zs:
        return {'label': '⚠️ 低吸观察', 'detail': f'中枢下沿{zd}下方，但前无完整下跌趋势，不确认为一买'}

    if price > zd and price < zg and _has_prior_trend(bi_list, 'up') and _near_low(price, zd, zg):
        # 中枢内 + 前有上涨趋势 + 回调到中枢下沿附近 → 二买候选
        return {'label': '2B候选', 'detail': f'回调至中枢下沿{zd}附近，前有上涨趋势，关注底分型确认二买'}

    if above_zs and _has_prior_trend(bi_list, 'up'):
        # 中枢上方 + 上涨趋势 → 三买候选 (需要回抽确认)
        return {'label': '3B候选', 'detail': f'突破中枢上沿{zg}，前有上涨趋势，等待回踩{zg}不破确认三买'}

    if above_zs:
        # 突破但无趋势
        return {'label': '⚠️ 突破待确认', 'detail': f'突破中枢上沿{zg}但前无完整上涨趋势，不确认为三买'}

    # 卖点侧
    if above_zs and _has_prior_trend(bi_list, 'up'):
        sell_candidates = _find_sell_point(bi_list, price, zg)
        if sell_candidates:
            return sell_candidates

    return {'label': '—', 'detail': '当前价格在中枢内，无明确买卖点信号'}


def _has_prior_trend(bi_list, direction):
    """检查最近是否有同向倾向。日线代理：放宽为最近3笔中至少2笔同向"""
    if len(bi_list) < 3:
        return False
    recent = bi_list[-3:]
    if direction == 'up':
        return sum(1 for b in recent if b['type'] == 'up') >= 2
    else:
        return sum(1 for b in recent if b['type'] == 'down') >= 2


def _near_low(price, zd, zg):
    """价格是否接近中枢下沿 (下1/3区域)"""
    return price <= zd + (zg - zd) / 3


def _find_sell_point(bi_list, price, zg):
    """识别卖点"""
    if len(bi_list) < 2:
        return None
    last_two = bi_list[-2:]
    # 最近一笔是向下的 → 可能一卖/二卖
    if last_two[-1]['type'] == 'down':
        return {'label': '1S/2S风险', 'detail': f'最近一笔向下{last_two[-1]["amplitude"]}%，价格{price:.2f}在中枢上沿{zg}附近，关注是否形成卖点'}
    return None


def map_action(signal, state, today_op):
    """Step 5: 动作映射"""
    signal_label = signal['label']

    if '1B候选' in signal_label:
        return {'label': '👀 observe', 'detail': '一买候选，等待底分型确认+低级别触发后再考虑试探。失效：继续新低'}
    elif '2B候选' in signal_label:
        return {'label': '👀 observe', 'detail': '二买候选，等待中枢下沿底分型确认。失效：跌破中枢下沿后不回'}
    elif '3B候选' in signal_label:
        return {'label': '👀 observe', 'detail': '三买候选，等待回踩中枢上沿不破。失效：回踩跌回中枢内部'}
    elif '突破待确认' in signal_label:
        return {'label': '👀 observe', 'detail': '突破未确认，等待回踩或放量确认。不追高'}
    elif '低吸观察' in signal_label:
        return {'label': '👀 observe', 'detail': '中枢下方但结构不完备，观察但不操作'}
    elif '1S/2S风险' in signal_label:
        return {'label': '✂️ reduce', 'detail': '顶部卖点风险信号，观察是否确认。失效：价格重新站上中枢上沿'}
    elif '中枢突破' in state['label']:
        return {'label': '✅ hold', 'detail': '趋势向上，持有观察。失效：跌回中枢内部'}
    elif '中枢下破' in state['label']:
        return {'label': '✂️ reduce', 'detail': '趋势向下，减仓或止损。失效：快速拉回中枢'}
    else:
        return {'label': '👀 wait', 'detail': '方向不明，等待结构明朗'}


def apply_filters(df, price, name):
    """Step 6: 技术过滤器 (MACD/RSI/量/均线)"""
    filters = []
    close = df['Close']

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    macd_bar = 2 * (dif - dea)
    if macd_bar.iloc[-1] > 0 and macd_bar.iloc[-1] > macd_bar.iloc[-2]:
        filters.append('MACD红柱放大')
    elif macd_bar.iloc[-1] < 0 and macd_bar.iloc[-1] < macd_bar.iloc[-2]:
        filters.append('MACD绿柱放大⚠️')
    elif dif.iloc[-1] > dea.iloc[-1]:
        filters.append('MACD金叉')

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi_now = rsi.iloc[-1]
    if rsi_now > 70:
        filters.append(f'RSI超买({rsi_now:.0f})')
    elif rsi_now < 30:
        filters.append(f'RSI超卖({rsi_now:.0f})')

    # 成交量
    vol = df['Volume']
    vol_ma20 = vol.tail(20).mean()
    vol_now = vol.iloc[-1]
    if vol_now > vol_ma20 * 1.5:
        filters.append('放量')
    elif vol_now < vol_ma20 * 0.5:
        filters.append('缩量')

    # 均线
    ma20 = float(close.tail(20).mean())
    if price > ma20:
        filters.append(f'站上MA20({ma20:.2f})')
    else:
        filters.append(f'跌破MA20({ma20:.2f})⚠️')

    return filters


def backtest_proxy(state, bi_list, price):
    """Step 7: 回测代理 — 检查历史类似结构的胜率"""
    if len(bi_list) < 10:
        return None
    # 统计中枢突破后的成功率 (简化版: 突破中枢→下一笔方向)
    breakouts = 0
    successes = 0
    for i in range(3, len(bi_list) - 1):
        # 检测模式: 前一笔突破前一笔的高点 (简化突破检测)
        if bi_list[i]['type'] == 'up' and bi_list[i]['end']['price'] > bi_list[i-1]['start']['price']:
            breakouts += 1
            # 突破后下一笔继续向上 → 成功
            if i + 1 < len(bi_list) and bi_list[i+1]['type'] == 'up':
                successes += 1

    if breakouts >= 3:
        rate = successes / breakouts * 100
        return f"历史{breakouts}次突破，{successes}次延续({rate:.0f}%)"
    return None


def self_check(state, signal, action, mode):
    """Step 8: 自检 — 对照假阳性清单"""
    checks = []
    if 'no_zs' not in state.get('label', '') and signal['label'] == '—':
        checks.append('有中枢但无信号→正常(震荡市中常见)')
    if 'RSI超买' in str(action) and '3B' in signal['label']:
        checks.append('三买+RSI超买→假阳性高发，必须等回踩')
    if mode == 'proxy_research':
        checks.append('proxy_research→非严格缠论，需低级别触发确认')
    if not checks:
        return '通过'
    return ' | '.join(checks)
