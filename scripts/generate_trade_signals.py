#!/usr/bin/env python3
"""
托董交易推荐系统 v2.4.0 — Schema校验 + 自动重试 + 实时开盘价

v2.4.0: 实时开盘价三重保障 — 腾讯时间戳校验 + 新浪交叉 + 昨收fallback
v2.3.0: Schema校验 + 自动重试（Harness第一步）
乖离率体系: BIAS_20 判断偏离 → 5日趋势方向 → 修正支撑/阻力触发逻辑
原则: 支撑≠买进, 阻力≠卖出。乖离率告诉你"车开多快"。
"""

import sys, os, re, json, pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
OVERSEAS_DIR = "/root/.openclaw/workspace/projects/overseas-morning-brief"


def load_calibration():
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


# 标的 → 外盘/板块映射关键词
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
    '562500.SH': ['机器人', '机器人ETF', '自动化', '高端制造', '科技', '成长'],
}

TICKER_CACHE = {
    '000016.SH': '000016.SH-daily.csv', '000300.SH': '000300.SH-daily.csv',
    '000688.SH': '000688.SH-daily.csv', '601288.SH': '601288.SS-daily.csv',
    '601988.SH': '601988.SS-daily.csv', '600036.SH': '600036.SS-daily.csv',
    '600795.SH': '600795.SH-daily.csv', '000066.SZ': '000066.SZ-daily.csv',
    '600562.SH': '600562.SH-daily.csv', '562500.SH': '562500.SH-daily.csv',
}

TICKERS = [
    ("000016.SH", "上证50"), ("000300.SH", "沪深300"),
    ("000688.SH", "科创50"), ("601288.SH", "农业银行"),
    ("601988.SH", "中国银行"), ("600036.SH", "招商银行"),
    ("600795.SH", "国电电力"), ("000066.SZ", "中国长城"),
    ("600562.SH", "国睿科技"), ("562500.SH", "机器人ETF"),
]

# ═══ 实时开盘价拉取（v2.4.0）═══
# 映射: ticker → (中文名, 腾讯代码)
PREMARKET_TENCENT = {
    "000016.SH": ("上证50", "sh000016"),
    "000300.SH": ("沪深300", "sh000300"),
    "000688.SH": ("科创50", "sh000688"),
    "601288.SH": ("农业银行", "sh601288"),
    "601988.SH": ("中国银行", "sh601988"),
    "600036.SH": ("招商银行", "sh600036"),
    "600795.SH": ("国电电力", "sh600795"),
    "000066.SZ": ("中国长城", "sz000066"),
    "600562.SH": ("国睿科技", "sh600562"),
}

def _load_close_snapshot() -> dict:
    """加载本地收盘快照（close_snapshot_YYYYMMDD.json），返回 {name: price}"""
    try:
        date_tag = datetime.now(TZ).strftime("%Y%m%d")
        # 优先当日，兜底前日
        for tag in [date_tag, (datetime.now(TZ) - timedelta(days=1)).strftime("%Y%m%d")]:
            fpath = os.path.join(os.path.dirname(__file__), "..", "reports", f"close_snapshot_{tag}.json")
            if os.path.exists(fpath):
                with open(fpath) as f:
                    return json.load(f)
    except Exception:
        pass
    return {}

def fetch_real_open_prices():
    """
    从腾讯+新浪拉取今日开盘价，四重保障：
    ① 腾讯实时行情（主源）
    ② 新浪实时行情（交叉校验）
    ③ 本地收盘快照 close_snapshot_{date}.json（当日/昨日）
    ④ 腾讯/新浪 prev_close fallback（网络异常时）
    """
    import requests
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    snapshot = _load_close_snapshot()
    result = {}

    # 腾讯批量拉取
    tc_codes = [v[1] for v in PREMARKET_TENCENT.values()]
    tc_url = "http://qt.gtimg.cn/q=" + ",".join(tc_codes)
    tc_data = {}
    try:
        resp = requests.get(tc_url, timeout=8)
        resp.encoding = "gbk"
        for line in resp.text.strip().split("\n"):
            m = re.search(r'="(.+)"', line)
            if not m:
                continue
            fields = m.group(1).split("~")
            if len(fields) < 35:
                continue
            ts_date = fields[30] if len(fields) > 30 else ""
            open_price = float(fields[5]) if fields[5] and fields[5] != "0.000" else None
            prev_close = float(fields[4]) if fields[4] else None
            tc_data[fields[2]] = {
                "open": open_price, "prev_close": prev_close,
                "date": ts_date, "source": "tencent"
            }
    except Exception as e:
        print(f"[开盘价] 腾讯拉取失败: {e}")

    # 新浪交叉校验
    sina_codes = []
    sina_name_map = {}
    for ticker, (name, tc_code) in PREMARKET_TENCENT.items():
        sina_code = tc_code  # 同格式
        sina_codes.append(sina_code)
        sina_name_map[sina_code] = (ticker, name)

    sina_data = {}
    try:
        resp = requests.get("http://hq.sinajs.cn/list=" + ",".join(sina_codes),
                          headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        resp.encoding = "gbk"
        for line in resp.text.strip().split("\n"):
            m = re.search(r'hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            code, raw = m.group(1), m.group(2)
            fields = raw.split(",")
            if len(fields) < 6:
                continue
            sina_date = fields[30] if len(fields) > 30 else ""
            open_price = float(fields[1]) if fields[1] and fields[1] != "0.000" else None
            prev_close = float(fields[2]) if fields[2] else None
            sina_data[code] = {
                "open": open_price, "prev_close": prev_close,
                "date": sina_date, "source": "sina"
            }
    except Exception as e:
        print(f"[开盘价] 新浪拉取失败: {e}")

    # 合并裁决
    for ticker, (name, tc_code) in PREMARKET_TENCENT.items():
        tc = tc_data.get(tc_code[2:], {})  # 腾讯key是纯数字
        # 也尝试带前缀匹配
        if not tc:
            for k, v in tc_data.items():
                if tc_code[2:] in k:
                    tc = v
                    break
        sina = sina_data.get(tc_code, {})

        open_price = None
        source = "fallback"

        # ① 优先腾讯（时间戳匹配）
        if tc.get("open") and tc.get("date") == today_str:
            open_price = tc["open"]
            source = "tencent"

        # ② 新浪交叉校验
        if sina.get("open") and sina.get("date") == today_str:
            if open_price is None:
                open_price = sina["open"]
                source = "sina"
            else:
                # 偏差 > 0.5% 时取腾讯（主源优先）
                deviation = abs(open_price - sina["open"]) / sina["open"] * 100
                if deviation > 0.5:
                    print(f"[开盘价交叉] {name}: 腾讯={open_price:.2f} vs 新浪={sina['open']:.2f} Δ{deviation:.2f}%，保留腾讯")
                    source = "tencent:cross"

        # ③ fallback 到本地收盘快照（最可靠）
        if open_price is None and name in snapshot:
            open_price = snapshot[name]
            source = "snapshot"
            print(f"[开盘价] {name}: fallback到本地快照{open_price:.2f}")
        
        # ④ 兜底 fallback 到腾讯/新浪 prev_close
        if open_price is None:
            open_price = tc.get("prev_close") or sina.get("prev_close")
            if open_price:
                source = "prev_close"
                print(f"[开盘价] {name}: 无今日开盘数据，fallback到昨收{open_price:.2f}")

        if open_price:
            result[ticker] = {"price": round(open_price, 2), "source": source}

    # 打摘要
    sources = {}
    for v in result.values():
        s = v.get("source", "?")
        sources[s] = sources.get(s, 0) + 1
    src_line = ", ".join(f"{s}:{c}" for s, c in sources.items())
    print(f"  开盘价来源: {src_line} | 总计{len(result)}只")
    return result

# 无内容关键词（板块标签而非实体名称）
NON_ENTITY_KW = set([
    '上证', '大盘', '蓝筹', '金融', '权重', '科技', '纳斯达克', '纳指', '创业板', '成长',
    '银行', '红利', '高股息', '电力', '能源', '公用事业', '信创', '国产替代', '军工',
    '雷达', '国防', '零售银行', '核心资产', '高端制造', '自动化', '机器人', '科创',
])


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


def calc_vwap_support_resistance(symbol: str) -> tuple[float | None, float | None]:
    """
    基于 VWAP（成交量加权均价）计算支撑/阻力位，替代纯均线偏移。
    通达信筹码分布思路：支撑=近期高量区加权均价，阻力=近期高量区均价上沿。

    方法:
      - 取近20日数据，计算每日 VWAP = (High+Low+Close)/3 * Volume 加权
      - 支撑位 = 20日VWAP - 0.5×ATR(14)
      - 阻力位 = 20日VWAP + 0.5×ATR(14)
      - VWAP 本身作为中枢位，支撑/阻力各偏移半倍ATR
    返回 (support, resistance)，失败返回 (None, None)
    """
    cache_file = TICKER_CACHE.get(symbol, '')
    cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
    if not os.path.exists(cache_path):
        return None, None
    try:
        df = pd.read_csv(cache_path, parse_dates=['Date'])
        df = df.set_index('Date').sort_index()
        df = df.tail(60)  # 取近60日保证ATR稳定
        if len(df) < 14:
            return None, None

        # 每日典型价格
        df['typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        # 20日VWAP
        df['tv'] = df['typical'] * df['Volume']
        df['cum_tv'] = df['tv'].rolling(20).sum()
        df['cum_vol'] = df['Volume'].rolling(20).sum()
        df['vwap20'] = df['cum_tv'] / df['cum_vol']

        # ATR(14)
        df['tr'] = pd.concat([
            df['High'] - df['Low'],
            abs(df['High'] - df['Close'].shift(1)),
            abs(df['Low'] - df['Close'].shift(1))
        ], axis=1).max(axis=1)
        df['atr14'] = df['tr'].rolling(14).mean()

        latest = df.iloc[-1]
        vwap = latest['vwap20']
        atr = latest['atr14']
        if pd.isna(vwap) or pd.isna(atr) or atr <= 0:
            return None, None

        support = round(vwap - 0.5 * atr, 2)
        resistance = round(vwap + 0.5 * atr, 2)
        return support, resistance
    except Exception:
        return None, None


def validate_support_resistance(llm_support: float | None, llm_resistance: float | None,
                                current_price: float, vwap_support: float, vwap_resistance: float) -> tuple[str, float | None, float | None]:
    """
    验证 LLM 给的支撑/阻力是否合理，不合理则用 VWAP 修正。
    规则:
      1. 支撑位 > 当前价 → 不合理（支撑应在价下）
      2. 阻力位 < 当前价 → 不合理（阻力应在价上）
      3. 支撑到阻力间距 < 0.3×VWAP间距 → 太窄
      4. 支撑到阻力间距 > 4×VWAP间距 → 太宽
      5. LLM支撑阻力中点偏离VWAP中枢超过1.2×VWAP半带宽 → 修正
    """
    if llm_support is None or llm_resistance is None:
        return 'no_data', None, None
    if vwap_support is None or vwap_resistance is None:
        return 'ok', llm_support, llm_resistance

    llm_gap = llm_resistance - llm_support
    if llm_gap <= 0:
        return 'invalid', vwap_support, vwap_resistance

    vwap_gap = vwap_resistance - vwap_support
    if vwap_gap <= 0:
        return 'ok', llm_support, llm_resistance

    vwap_mid = (vwap_support + vwap_resistance) / 2
    vwap_half = vwap_gap / 2

    llm_mid = (llm_support + llm_resistance) / 2
    mid_deviation = abs(llm_mid - vwap_mid) / vwap_half

    verdict = 'ok'

    # 规则1: 支撑位在价上
    if current_price and llm_support > current_price:
        verdict = 'corrected'
    # 规则2: 阻力位在价下
    if current_price and llm_resistance < current_price:
        verdict = 'corrected'
    # 规则3: 间距太窄
    if llm_gap < vwap_gap * 0.3:
        verdict = 'corrected'
    # 规则4: 间距太宽
    if llm_gap > vwap_gap * 4:
        verdict = 'corrected'
    # 规则5: 中枢偏移
    if mid_deviation > 1.2:
        verdict = 'corrected'

    if verdict != 'ok':
        return verdict, vwap_support, vwap_resistance
    return 'ok', llm_support, llm_resistance


def calc_volatility_adaptive_position(symbol: str, base_pos: float, total_temp_signal: str = 'neutral') -> float:
    """
    P5 波动率自适应仓位。
    基于 60d vs 250d 年化波动率比例动态调整仓位：
      - 高波动标的 (ratio>1.5) → 降低仓位到 max(base_pos*0.6, 5%)
      - 低波动标的 (ratio<0.7) → 可适当提高到 min(base_pos*1.3, 30%)
      - 正常波动 → 保持base_pos
    总仓由市场温度信号调整：偏热→总仓上限70%，偏冷→60%，中性→80%
    返回调整后仓位百分比。
    """
    import numpy as np
    cache_file = TICKER_CACHE.get(symbol, '')
    cache_path = os.path.join(PROJECT_DIR, 'data', 'cache', cache_file)
    if not os.path.exists(cache_path) or base_pos <= 0:
        return base_pos
    try:
        df = pd.read_csv(cache_path)
        if len(df) < 60:
            return base_pos
        close = df['Close']
        rets = close.pct_change().dropna()
        ann_factor = np.sqrt(252)
        vol_60d = float(rets.iloc[-60:].std() * ann_factor) if len(rets) >= 60 else float(rets.std() * ann_factor)
        vol_250d = float(rets.std() * ann_factor) if len(rets) < 250 else float(rets.iloc[-250:].std() * ann_factor)
        if vol_250d <= 0:
            return base_pos

        vol_ratio = vol_60d / vol_250d
        if vol_ratio > 1.3:
            adjusted = max(base_pos * 0.7, 5.0)
            tag = 'high_vol'
        elif vol_ratio < 0.85:
            adjusted = min(base_pos * 1.2, 30.0)
            tag = 'low_vol'
        else:
            adjusted = base_pos
            tag = 'normal'

        # 市场温度总仓调整
        if total_temp_signal == 'warm':
            cap = 0.70
        elif total_temp_signal == 'cold':
            cap = 0.60
        else:
            cap = 0.80
        # 总仓上限体现在最终仓位：单票仓位×总仓系数
        adjusted = min(adjusted, cap * 100)
        adjusted = round(adjusted, 0)
        return adjusted if adjusted > 0 else base_pos
    except Exception:
        return base_pos


def get_total_temp_signal(mkt_temp) -> str:
    """从市场温度计对象提取总体温度信号"""
    if mkt_temp and mkt_temp.summary:
        s = mkt_temp.summary
        if '偏热' in s:
            return 'warm'
        elif '偏冷' in s:
            return 'cold'
        elif '中性' in s:
            return 'neutral'
    return 'neutral'


def calc_bias_direction(symbol):
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
        if latest > 5:  result += '⚠️'
        elif latest < -5: result += '💡'
        return result, direction
    except:
        return '-', ''


def adjust_trigger(advice, support, resistance, bias_val, direction, calibration_hint=None):
    """
    生成触发条件建议，定位从"指令"改为"锚定+自检"。
    用户风格：中长周期价值趋势持有者，非短周期动量交易。
    """
    if advice == '-' or '出' not in advice and '入' not in advice and '持' not in advice:
        return '-'

    try:
        b = float(bias_val.replace('%', '').replace('⚠️', '').replace('💡', '').strip())
    except:
        b = 0

    calibration_suffix = ""
    if calibration_hint == "sell_hold_bias" and '出' in advice:
        calibration_suffix = " [复盘:卖出高误判]"
    elif calibration_hint == "overseas_unreliable" and '持' in advice:
        calibration_suffix = " [复盘:外盘连续偏离]"
    elif calibration_hint == "high_volatility" and ('入' in advice or '出' in advice):
        calibration_suffix = " [复盘:波动加剧]"

    base = None

    if '持' in advice:
        if b > 2 and direction == '↑':
            base = f"浮盈区间高位，可考虑部分获利"
        elif b > 2 and direction == '↓':
            base = f"乖离收敛中，趋势健康，持有"
        elif b < -2 and direction == '↓':
            base = f"估值区间下沿，关注加仓窗口"
        elif b < -2 and direction == '↑':
            base = f"超跌反弹中，持有观察，确认趋势后再动"
        else:
            base = f"区间震荡，持有观察"

    elif '出' in advice:
        if b > 2 and direction == '↑':
            base = f"乖离+阻力双重压力，检查持仓逻辑是否仍成立"
        elif b < -2 and direction == '↓':
            base = f"破位下行，检查基本面是否变化"
        elif direction == '↓':
            base = f"压力区间下移，关注{support}能否守住"
        else:
            base = f"关注{support}支撑有效性"

    elif '入' in advice:
        if b < -2 and direction == '↓':
            base = f"超跌区间，{support}附近可考虑分批建仓"
        elif b < -2 and direction == '↑':
            base = f"超跌后反弹确认，回踩{support}可轻仓试探"
        elif b > 2 and direction == '↑':
            base = f"乖离偏高，追高风险大，等回调至{support}再关注"
        else:
            base = f"回踩{support}附近可考虑"

    if base is None:
        return '-'
    return base + calibration_suffix


def load_overseas_signal(date_str: str) -> str | None:
    y = date_str[:4]; m = date_str[4:6]; d = date_str[6:8]
    iso = f"{y}-{m}-{d}"
    # 优先读金桥本地（由金桥 extract_signal.py 生成）
    path = os.path.join(PROJECT_DIR, "reports", f"overseas_signal_{iso}.md")
    if not os.path.exists(path):
        # fallback: overseas-morning-brief 旧址
        path = os.path.join(OVERSEAS_DIR, "reports", f"overseas_signal_{iso}.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def extract_overseas_direction_value(overseas_text: str | None) -> int:
    """
    从外盘信号文本提取方向关键词映射为数值信号：+1=偏多, -1=偏空, 0=中性。
    """
    if not overseas_text:
        return 0
    text_lower = overseas_text.lower()
    # 偏多关键词
    bullish_kw = ['偏多', '看多', '强势', '反弹', '上涨', '逆势', '修复', '走强']
    # 偏空关键词
    bearish_kw = ['偏空', '看空', '弱势', '下跌', '回调', '承压', '暴跌', '跳空']
    bullish_score = sum(1 for kw in bullish_kw if kw in text_lower)
    bearish_score = sum(1 for kw in bearish_kw if kw in text_lower)
    if bullish_score > bearish_score:
        return 1
    elif bearish_score > bullish_score:
        return -1
    # 中性：检查研判方向行
    m = re.search(r"\*\*研判方向\*\*:\s*(.+?)(?:\s*\||\n)", overseas_text)
    if m:
        direction = m.group(1).strip()
        if any(kw in direction for kw in ['偏多', '看多']):
            return 1
        if any(kw in direction for kw in ['偏空', '看空']):
            return -1
    return 0


def compute_bayes_adjusted_predictions(overseas_text: str | None) -> dict:
    """
    贝叶斯更新：读取TimesFM校准数据中各标的P50 prediction，
    结合外盘方向信号做简单贝叶斯调整。
    返回 {symbol_name: {'original_p50': x, 'adjusted_p50': y, 'sigma': z}, ...}。
    若无TimesFM数据或外盘无方向信号，返回空dict。
    """
    import glob
    import numpy as np
    overseas_signal = extract_overseas_direction_value(overseas_text)
    if overseas_signal == 0:
        return {}

    cal_dir = os.path.join(PROJECT_DIR, 'logs', 'timesfm_calibration')
    cal_files = glob.glob(os.path.join(cal_dir, '*.json'))
    if not cal_files:
        return {}

    results = {}
    for cf in cal_files:
        try:
            with open(cf) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        code = d.get('code', '')
        symbol_name = d.get('name', os.path.basename(cf).replace('.json', ''))
        windows = d.get('windows_detail', [])
        if not windows:
            continue

        last_win = windows[-1]
        # P50 prediction = fc_5d (TimesFM 点预测)
        original_p50 = last_win.get('fc_5d')
        if original_p50 is None:
            continue

        # 估算sigma: 用预测区间的半宽作为波动率近似
        p10 = last_win.get('p10_5d')
        p90 = last_win.get('p90_5d')
        if p10 is not None and p90 is not None and p90 > p10:
            # P90-P10 ≈ 2.56σ for normal → σ ≈ (p90-p10)/2.56
            sigma = (p90 - p10) / 2.56
        else:
            sigma = original_p50 * 0.02  # 默认2%波动

        # 简单贝叶斯更新: adjusted_p50 = original_p50 + overseas_signal * 0.3 * sigma
        adjusted_p50 = original_p50 + overseas_signal * 0.3 * sigma

        if abs(adjusted_p50 - original_p50) / original_p50 > 0.01:
            results[symbol_name] = {
                'original_p50': round(original_p50, 2),
                'adjusted_p50': round(adjusted_p50, 2),
                'sigma': round(sigma, 2),
                'overseas_signal': overseas_signal,
            }

    return results


def load_ima_opinions(date_str: str) -> str | None:
    path = os.path.join(PROJECT_DIR, "reports", f"opinions_{date_str}.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def extract_ima_sentence_for_stock(opinions_text: str, stock_name: str) -> str:
    """
    从IMA文章全文中提取与标的相关的50字以内内容摘要（非标题）。
    优先找包含标的名称的完整句子。
    """
    if not opinions_text:
        return ""

    search_names = [stock_name]
    # 追加非通用实体关键词
    extra_kws = [k for k in TICKER_SECTOR_MAP.get(
        {v: k for k, v in TICKERS}.get(stock_name, ''), [])
        if k not in NON_ENTITY_KW]
    search_names.extend(extra_kws)

    # 按文章段落块搜索
    blocks = opinions_text.split('\n\n')
    for block in blocks:
        for sn in search_names:
            if sn not in block:
                continue
            # 按句号或换行拆句子
            sentences = re.split(r'[。\n]', block)
            for s in sentences:
                s = s.strip().lstrip('- *#>').strip()
                # 跳过元数据/标题行
                if any(skip in s for skip in ['权重衰减', 'w=', '[202', '笔记:',
                        '作者:', '时间:', '日期:', '标签:', '来源:', '原文链接']):
                    continue
                if any(s.startswith(skip) for skip in ['笔记', '来自', '原创', '时间']):
                    continue
                # 清洗 markdown 标记
                s = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', s)
                s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
                s = s.replace('**', '')
                if sn in s and len(s) > 10:
                    # 截断50字
                    if len(s) > 50:
                        # CJK感知截断：在48字以内找最后一个,。！？处截断
                        chunk = s[:48]
                        for sep in ',，。！？、':
                            idx = chunk.rfind(sep)
                            if idx >= 20:
                                return chunk[:idx+1] + '..'
                        return chunk + '..' 
                    return s
    return ""


def match_external_signals(symbol: str, overseas_text: str | None,
                           opinions_text: str | None) -> tuple[str, str]:
    """匹配外盘和IMA，返回 (风险描述, 催化描述) 各50-100字"""
    risk_desc = ""
    catalyst_desc = ""
    name = dict(TICKERS).get(symbol, '')

    def clean_md(t):
        t = re.sub(r'\*\*([^*]+)\*\*', r'', t)
        t = re.sub(r'\*\*', '', t)
        return t.strip()

    # 提取外盘 bullet 行
    if overseas_text:
        overseas_lower = overseas_text.lower()
        is_bearish = any(w in overseas_lower for w in ['偏空', '暴跌', '跳空'])
        # 注意: '承压'可能出现在中性方向里（如"科技板块承压"），不作为单向判断
        is_bullish = any(w in overseas_lower for w in ['偏多', '反弹', '逆势', '修复'])
        is_clear = is_bearish or is_bullish  # 只有方向明确时才匹配

        bullets = []
        for l in overseas_text.split(chr(10)):
            ls = l.strip()
            if ls.startswith('- ') or ls.startswith('* '):
                cl = clean_md(ls.lstrip('- *'))
                if len(cl) > 10 and not any(cl.startswith(s) for s in ['关键信号', '信号摘要', '研判内容', '风险提示']):
                    bullets.append(cl)

        if is_clear and is_bearish and bullets:
            for b in bullets:
                if any(w in b.lower() for w in ['跌', '空', '承压', '纳指下跌', '暴跌']):
                    risk_desc = b
                    break
            if not risk_desc:
                risk_desc = bullets[0]
        elif is_clear and is_bullish and bullets:
            for b in bullets:
                if any(w in b.lower() for w in ['涨', '多', '反弹', '修复', '新高', '走强', 'vix', '降']):
                    catalyst_desc = b
                    break
            if not catalyst_desc:
                catalyst_desc = bullets[0]

    # IMA 知识库
    ima_text = extract_ima_sentence_for_stock(opinions_text, name)
    if ima_text:
        ima_clean = clean_md(ima_text)
        ima_clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'', ima_clean)
        if any(w in ima_clean for w in ['跌', '空', '承压', '风险', '下探', '回落', '减持', '卖出']):
            if risk_desc:
                risk_desc = risk_desc + "；" + ima_clean[:80]
            else:
                risk_desc = ima_clean[:100]
        else:
            catalyst_desc = ima_clean[:100]

    def trunc(text, max_len=100):
        if not text or len(text) <= max_len:
            return text or ""
        for sep in '。！？':
            idx = text[:max_len].rfind(sep)
            if idx >= 30:
                return text[:idx+1]
        for sep in '，,':
            idx = text[:max_len].rfind(sep)
            if idx >= 30:
                return text[:idx+1] + '..'
        return text[:max_len-2] + '..'

    return trunc(risk_desc), trunc(catalyst_desc)


def validate_records(records: list) -> tuple[bool, list[str]]:
    """
    Schema 校验：逐标的必需字段 + 合理性检查。
    返回 (通过, 错误列表)
    """
    errors = []
    required_fields = ['name', 'price', 'advice', 'pos', 'trigger']
    valid_advice = {'买入', '卖出', '持有', 'hold'}
    
    is_new_stock = lambda r: '首日纳入' in str(r.get('trigger', ''))
    
    for i, r in enumerate(records):
        name = r.get('name', f'#{i}')
        new_stock = is_new_stock(r)
        # 必需字段非空
        for f in required_fields:
            val = r.get(f, '')
            if not val or val == '-':
                # 首日纳入：price/pos/trigger 允许占位
                if new_stock and f in ('price', 'pos', 'trigger'):
                    continue
                errors.append(f"{name}: 缺失字段 {f} (值={val})")
        
        # 建议合法性
        advice_raw = r.get('advice', '')
        advice_clean = advice_raw.replace('**', '').strip()
        if advice_clean not in valid_advice:
            errors.append(f"{name}: 交易建议异常 ({advice_raw})")
        
        # 仓位合理性 (0-100)
        if not new_stock:
            pos_str = r.get('pos', '').replace('%', '').strip()
            try:
                pos_val = float(pos_str)
                if pos_val < 0 or pos_val > 100:
                    errors.append(f"{name}: 仓位异常 ({pos_str})")
            except (ValueError, TypeError):
                errors.append(f"{name}: 仓位非数值 ({pos_str})")
        
        # 价格合理性 (正数)
        if not new_stock:
            price_str = r.get('price', '').replace('¥', '').replace(',', '').strip()
            if price_str and price_str != '-':
                try:
                    price_val = float(price_str)
                    if price_val <= 0:
                        errors.append(f"{name}: 价格异常 ({price_str})")
                except (ValueError, TypeError):
                    errors.append(f"{name}: 价格非数值 ({price_str})")
    
    # 全局检查：至少一半标的有有效触发条件（非首日纳入）
    valid_triggers = sum(1 for r in records if r.get('trigger') and r['trigger'] != '-' and '首日纳入' not in r.get('trigger', ''))
    if len(records) >= 4 and valid_triggers < len(records) / 2:
        errors.append(f"全局: 有效触发条件不足 ({valid_triggers}/{len(records)})")
    
    return len(errors) == 0, errors


def build_synthesis_paragraph(mkt_temp, overseas_text, records):
    """
    全局合成判断：聚合温度计、外盘、TimesFM分位、个股乖离率、波动率体制切换。
    返回 1-3 句话（≤150字）的合成判断字符串，或空字符串表示无法合成。
    """
    import glob
    lines = []

    # 1. 温度计方向
    temp_direction = None  # 'warm', 'cold', 'neutral'
    if mkt_temp and mkt_temp.summary:
        s = mkt_temp.summary
        if '偏热' in s:
            temp_direction = 'warm'
        elif '偏冷' in s:
            temp_direction = 'cold'
        elif '中性' in s:
            temp_direction = 'neutral'

    # 2. 外盘方向
    overseas_direction = extract_overseas_direction_value(overseas_text)

    # 3. TimesFM 分位：各标的最近一期 P50 相对 latest_close
    cal_dir = os.path.join(PROJECT_DIR, 'logs', 'timesfm_calibration')
    cal_files = glob.glob(os.path.join(cal_dir, '*.json'))
    timesfm_bullish = 0
    timesfm_bearish = 0
    timesfm_p90_plus = 0
    timesfm_p10_minus = 0
    timesfm_total = 0
    for cf in cal_files:
        try:
            with open(cf) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        windows = d.get('windows_detail', [])
        if not windows:
            continue
        last_win = windows[-1]
        last_close = last_win.get('last_close')
        p50 = last_win.get('fc_5d')
        p90 = last_win.get('p90_5d')
        p10 = last_win.get('p10_5d')
        if last_close and p50:
            timesfm_total += 1
            if p50 > last_close:
                timesfm_bullish += 1
            elif p50 < last_close:
                timesfm_bearish += 1
            if p90 and p50 >= p90:
                timesfm_p90_plus += 1
            if p10 and p50 <= p10:
                timesfm_p10_minus += 1

    # 4. 个股乖离率方向
    bias_bull = sum(1 for r in records if r.get('bias', '') and '↑' in str(r.get('bias', '')))
    bias_bear = sum(1 for r in records if r.get('bias', '') and '↓' in str(r.get('bias', '')))

    # 5. 波动率体制切换告警
    regime_alerts = []
    state_file = f"{PROJECT_DIR}/logs/cognition_state.json"
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            ra = state.get('last_review', {}).get('regime_shift_alert')
            if ra:
                regime_alerts.append(ra)
        except (json.JSONDecodeError, KeyError):
            pass

    # ── 合成规则 ──
    # Rule 1: 温度偏热 + 外盘偏空
    if temp_direction == 'warm' and overseas_direction == -1:
        lines.append("温度偏热但外盘偏空，短期谨慎，估值偏贵但情绪支撑")
    # Rule 2: 温度偏冷 + 多数个股 TimesFM P10 附近
    elif temp_direction == 'cold' and (timesfm_p10_minus >= timesfm_total * 0.5 if timesfm_total else False):
        lines.append("估值便宜但趋势未确认，关注企稳信号")
    # Rule 3: 温度偏热 + 多数个股 P90 以上
    elif temp_direction == 'warm' and (timesfm_p90_plus >= timesfm_total * 0.5 if timesfm_total else False):
        lines.append("警惕情绪透支，多数标的处于预测上沿，注意仓位风险")
    # Rule 4: 波动率切换告警
    elif regime_alerts:
        for a in regime_alerts[:1]:
            sym = a.get('symbol', '某标的')
            lines.append(f"{sym}波动结构变化，历史回测参考价值降低")

    # 默认：方向一致性
    if not lines:
        signals = []
        if temp_direction == 'warm':
            signals.append('温度偏热')
        elif temp_direction == 'cold':
            signals.append('温度偏冷')
        if overseas_direction == 1:
            signals.append('外盘偏多')
        elif overseas_direction == -1:
            signals.append('外盘偏空')
        if timesfm_total:
            if timesfm_bullish > timesfm_bearish:
                signals.append('TimesFM多数偏多')
            elif timesfm_bearish > timesfm_bullish:
                signals.append('TimesFM多数偏空')
        if bias_bull > bias_bear:
            signals.append('乖离率多数偏强')
        elif bias_bear > bias_bull:
            signals.append('乖离率多数偏弱')

        if signals:
            bullish_count = sum(1 for s in signals if any(w in s for w in ['偏热', '偏多', '偏强']))
            bearish_count = sum(1 for s in signals if any(w in s for w in ['偏冷', '偏空', '偏弱']))
            if bullish_count > bearish_count:
                lines.append(f"多维度偏多（{'、'.join(signals)}），趋势延续但注意追高风险")
            elif bearish_count > bullish_count:
                lines.append(f"多维度偏空（{'、'.join(signals)}），防御为主，等待企稳信号")
            else:
                lines.append(f"信号分歧（{'、'.join(signals)}），方向不明宜观望")
        elif temp_direction:
            lines.append(f"市场温度{temp_direction}，无强外部信号，延续现有策略")

    if not lines:
        return ""

    return '\n'.join(lines)


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime('%Y%m%d')
    analysis_file = f"{PROJECT_DIR}/reports/trading_analysis_{date_str}.md"
    if not os.path.exists(analysis_file):
        print(f"报告未找到: {analysis_file}")
        sys.exit(1)
    with open(analysis_file) as f:
        text = f.read()

    overseas_text = load_overseas_signal(date_str)
    opinions_text = load_ima_opinions(date_str)
    calibration_hint, last_review, rolling_metrics = load_calibration()

    # ── 外盘冲击量化：贝叶斯更新TimesFM P50 ──
    bayes_adjusted = compute_bayes_adjusted_predictions(overseas_text)

    records = []
    for symbol, name in TICKERS:
        start = text.find(f"### {symbol}")
        if start < 0:
            # 首日纳入无分析数据
            records.append({
                'name': name, 'price': '-', 'bias': '-',
                'support': '-', 'resistance': '-',
                'trend': '-', 'advice': 'hold', 'pos': '-',
                'trigger': '首日纳入，暂无数据', 'risk': '', 'catalyst': '',
            })
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

        tech_trigger = adjust_trigger(advice, support, resistance, bias_val, bias_dir, calibration_hint)
        risk_desc, catalyst_desc = match_external_signals(symbol, overseas_text, opinions_text)
        reasoning = extract(section, '简要理由')
        records.append({
            'name': name, 'price': price, 'bias': bias_display,
            'support': support, 'resistance': resistance,
            'trend': trend, 'advice': advice, 'pos': pos,
            'trigger': tech_trigger, 'risk': risk_desc, 'catalyst': catalyst_desc,
            'reasoning': reasoning,
            '_extracted_price': price,  # 保留原始提取值供价格校准
        })

    # ── 实时开盘价覆盖（v2.4.0）──
    real_opens = fetch_real_open_prices()
    for r in records:
        for ticker, name in TICKERS:
            if r["name"] == name and ticker in real_opens:
                old_price = r["price"]
                r["price"] = f"{real_opens[ticker]['price']:.2f}"
                r["_open_source"] = real_opens[ticker]["source"]
                if old_price and old_price != "-" and old_price != r["price"]:
                    print(f"  [开盘价覆盖] {name}: {old_price}→{r['price']}")
                break

    # ── 提前获取市场温度（供仓位+合成使用）──
    mkt_temp = None
    try:
        from style_rotation_signals import compute_market_temperature, format_compact_for_push
        mkt_temp = compute_market_temperature()
    except Exception as e:
        print(f"[signals] style_rotation unavailable: {e}", file=sys.stderr)

    # ── VWAP支撑/阻力修正（P0: 通达信筹码分布思路）──
    vwap_corrections = 0
    for i, r in enumerate(records):
        if r['name'] in [n for _, n in TICKERS]:
            symbol = [s for s, n2 in TICKERS if n2 == r['name']][0]
            vwap_s, vwap_r = calc_vwap_support_resistance(symbol)
            if vwap_s is None or vwap_r is None:
                continue
            try:
                sup_val = float(r['support']) if r['support'] and r['support'] != '-' else None
                res_val = float(r['resistance']) if r['resistance'] and r['resistance'] != '-' else None
                price_val = float(r['price']) if r['price'] and r['price'] != '-' else None
            except (ValueError, TypeError):
                sup_val, res_val, price_val = None, None, None
            verdict, new_s, new_r = validate_support_resistance(sup_val, res_val, price_val, vwap_s, vwap_r)
            if verdict != 'ok':
                r['support'] = str(new_s)
                r['resistance'] = str(new_r)
                r['_vwap_corrected'] = True
                vwap_corrections += 1
                print(f"  [VWAP修正] {r['name']}: LLM支撑={sup_val}/阻力={res_val} → VWAP支撑={new_s}/阻力={new_r} (原因:{verdict})")
    if vwap_corrections:
        print(f"  共 {vwap_corrections} 只标的采用VWAP修正")    

    # ── P5: 波动率自适应仓位调整 ──
    total_signal = get_total_temp_signal(mkt_temp)
    pos_adjustments = 0
    for i, r in enumerate(records):
        if r['name'] in [n for _, n in TICKERS]:
            symbol = [s for s, n2 in TICKERS if n2 == r['name']][0]
            try:
                old_pos = float(r['pos']) if r['pos'] and r['pos'] != '-' else 10
            except (ValueError, TypeError):
                old_pos = 10
            new_pos = calc_volatility_adaptive_position(symbol, old_pos, total_signal)
            if abs(new_pos - old_pos) > 1:
                r['pos'] = f"{new_pos:.0f}%"
                r['_pos_adjusted'] = True
                pos_adjustments += 1
                print(f"  [仓位调整] {r['name']}: {old_pos:.0f}%→{new_pos:.0f}% (温度:{total_signal})")
    if pos_adjustments:
        print(f"  共 {pos_adjustments} 只标的仓位调整")    

    # ── 价格硬保护: 强制对齐实时行情（防止LLM幻觉/Cron环境旧数据污染）──
    # 如果 record 中的价格与 real_opens 偏差 >1%，强制覆盖
    for r in records:
        for ticker, name in TICKERS:
            if r["name"] == name:
                # 获取权威价格: 优先实时开盘价 > 技术分析最新价 > 日线缓存昨收
                authoritative_price = None
                if ticker in real_opens:
                    authoritative_price = real_opens[ticker]['price']
                else:
                    try:
                        authoritative_price = float(r.get('_extracted_price', 0)) if r.get('_extracted_price') else None
                    except (ValueError, TypeError):
                        pass
                
                if authoritative_price and r['price'] and r['price'] != '-':
                    try:
                        displayed = float(r['price'])
                        dev = abs(displayed - authoritative_price) / authoritative_price * 100
                        if dev > 1.0:
                            print(f"  [价格校准] {name}: {r['price']}→{authoritative_price:.2f} (偏差{dev:.1f}%>1%)")
                            r['price'] = f"{authoritative_price:.2f}"
                    except (ValueError, TypeError):
                        pass
                elif authoritative_price and (not r['price'] or r['price'] == '-'):
                    r['price'] = f"{authoritative_price:.2f}"
                break

    # ── Schema 校验 ──
    passed, errors = validate_records(records)
    if not passed:
        print("\n⚠️ Schema 校验失败:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(2)

    # ═══════════════════════════════════════════════
    #  构建段落式输出
    # ═══════════════════════════════════════════════
    lines = []
    lines.append("交易推荐 · 开盘前推送")
    lines.append("")

    # ── 全局合成判断（聚合全部信号）──
    # mkt_temp 已在VWAP修正阶段获取

    synthesis = build_synthesis_paragraph(mkt_temp, overseas_text, records)
    if synthesis:
        lines.append(f"【合成判断】{synthesis}")
        lines.append("")

    # ── 市场温度与风格水位 (Phase 1) ──
    if mkt_temp and mkt_temp.summary:
        lines.append(mkt_temp.summary)
        compact = format_compact_for_push(mkt_temp)
        if compact:
            lines.append(compact)
        lines.append("")
    elif not mkt_temp:
        pass  # 已在 try/except 打印错误

    # ── P2 板块联动 (通达信"发现"功能思路) ──
    sector_info = {}
    try:
        from style_rotation_signals import compute_sector_linkage
        sector_info = compute_sector_linkage(mkt_temp)
        sl = sector_info.get('summary_line', '')
        if sl:
            lines.append(sl)
            lines.append("")
    except Exception as e:
        print(f"[signals] sector_linkage unavailable: {e}", file=sys.stderr)

    # ── 复盘摘要 200-300字 ──
    if last_review and last_review.get("review_summary"):
        lines.append(last_review["review_summary"])
    elif last_review and last_review.get("date"):
        # fallback: short format
        cal_parts = [f"复盘 {last_review['date']}"]
        if last_review.get("direction_match"):
            cal_parts.append(f"外盘{last_review.get('overseas_predicted','')}→{last_review.get('overseas_actual','')}")
        if last_review.get("cognitive_tag"):
            cal_parts.append(last_review['cognitive_tag'])
        lines.append(" · ".join(cal_parts))

    # ── 外盘摘要 200-300字 ──
    if overseas_text:
        lines.append("")
        # 取研判方向
        direction_match = re.search(r"\*\*研判方向\*\*:\s*(.+?)(?:\s*\|)", overseas_text)
        overseas_summary_lines = []
        if direction_match:
            overseas_summary_lines.append(f"隔夜外盘研判：{direction_match.group(1)}。")
        # 取信号部分（多行）
        in_signal = False
        signal_text = []
        for l in overseas_text.split('\n'):
            if any(w in l for w in ['关键信号', '信号摘要', '研判内容']):
                in_signal = True
                continue
            if in_signal and l.strip().startswith('- '):
                cl = l.strip().lstrip('- *').strip()
                cl = re.sub(r'\*\*(.+?)\*\*', r'\1', cl)
                if len(cl) > 10:
                    signal_text.append(cl)
            elif in_signal and not l.strip():
                break  # empty line after signal block
        if not signal_text:
            # fallback: markdown horizontal rule separated blocks
            blocks = overseas_text.split('\n---')
            for block in blocks:
                for l in block.split('\n'):
                    l = l.strip()
                    if l.startswith('- ') and '📊' not in l and len(l) > 20:
                        cl = re.sub(r'\*\*([^*]+)\*\*', r'\1', l.lstrip('- *'))
                        signal_text.append(cl)
                    if len(signal_text) >= 4:
                        break
                if signal_text:
                    break

        for s in signal_text:
            overseas_summary_lines.append(s + "。")
        # 拼接
        overseas_summary = "".join(overseas_summary_lines)
        # 去掉剩余 markdown 标记
        overseas_summary = re.sub(r'\*\*', '', overseas_summary)

        if len(overseas_summary) < 100:
            raw_lines = [l.strip() for l in overseas_text.split('\n') if len(l.strip()) > 30
                      and not l.strip().startswith('#') and not l.strip().startswith('|')
                      and '研报' not in l]
            for rl in raw_lines[:5]:
                clean = re.sub(r'\*\*|[📊🚨✅❌🔥⚠️📈📉📰🌐🔴🟢🟡]', '', rl).strip()
                clean = re.sub(r'^[>\s]+', '', clean)
                if clean and clean not in overseas_summary:
                    overseas_summary += clean + "。"
                    if len(overseas_summary) >= 250:
                        break
        # 截断300字的最后一个完整句子
        if len(overseas_summary) > 300:
            for sep in '。！？':
                idx = overseas_summary[:300].rfind(sep)
                if idx >= 150:
                    overseas_summary = overseas_summary[:idx+1]
                    break
            else:
                overseas_summary = overseas_summary[:298] + '..'
        lines.append(overseas_summary)
    
    lines.append("")

    sell = hold = buy = 0
    for r in records:
        if '出' in r['advice']:
            op_icon = '🔴 '
            op_label = '卖出'
            sell += 1
        elif '入' in r['advice']:
            op_icon = '🟢 '
            op_label = '买入'
            buy += 1
        else:
            op_icon = '🟡 '
            op_label = '持有'
            hold += 1

        # 标的段落 — 含仓位
        header = f"{op_icon}{r['name']}  {r['price']}"
        if r['bias'] and r['bias'] != '-':
            header += f"  乖离{r['bias']}"
        if r['pos'] and r['pos'] != '-' and r['pos'] != '0%':
            header += f"  仓位{r['pos']}"
        header += f"  {op_label}"
        lines.append(header)

        # 支撑/阻力
        sup_res = []
        if r['support'] and r['support'] != '-':
            sup_res.append(f"支撑{r['support']}")
        if r['resistance'] and r['resistance'] != '-':
            sup_res.append(f"阻力{r['resistance']}")
        if sup_res:
            lines.append(f"  {' / '.join(sup_res)}")

        # 触发条件
        if r['trigger'] and r['trigger'] != '-':
            lines.append(f"  触发: {r['trigger']}")

        # 风险/催化 分拆
        risk_lines = []
        if r['risk']:
            risk_lines.append(r['risk'])
        # 复盘缝入
        if last_review and last_review.get("date"):
            cal_notes = []
            if r['name'] in last_review.get("sell_wrong_names", []):
                cal_notes.append("上日卖出误判收涨")
            for b in last_review.get("breach_names", []):
                if r['name'] == b:
                    cal_notes.append("上日触发穿越")
            if cal_notes:
                risk_lines.append("复盘：" + "、".join(cal_notes))
        if risk_lines:
            lines.append(f"  风险：{' | '.join(risk_lines)}")

        if r['catalyst']:
            lines.append(f"  催化：{r['catalyst']}")

        # 推理摘要（来自DeepSeek技术分析）
        reasoning = r.get('reasoning', '')
        if reasoning and reasoning != '-':
            # 截断80字，保持推送简洁
            short = reasoning if len(reasoning) <= 80 else reasoning[:77] + '…'
            lines.append(f"  {short}")

        # 外盘冲击量化（贝叶斯更新）
        if bayes_adjusted and r['name'] in bayes_adjusted:
            ba = bayes_adjusted[r['name']]
            lines.append(f"  （外盘±1调整后：{ba['adjusted_p50']:.2f}）")

        lines.append("")

    # 汇总
    sigs = []
    if sell: sigs.append(f"卖出{sell}")
    if hold: sigs.append(f"持有{hold}")
    if buy:  sigs.append(f"买入{buy}")
    lines.append(" · ".join(sigs))
    lines.append("")
    lines.append("乖离↑加速偏离 ↓回归均线 →持平 | AI模拟分析，不构成投资建议")

    report = '\n'.join(lines)
    output_file = f"{PROJECT_DIR}/reports/trade_signals_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(report)
    print(report)
    print(f"\n✓ {output_file}")


if __name__ == '__main__':
    main()
