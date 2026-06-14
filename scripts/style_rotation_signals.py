#!/usr/bin/env python3
"""
风格轮动信号系统 v1.0.0 — Phase 1: 市场温度计

三层架构：
  Layer 1 - 市场温度: 股债性价比 / 偏股基金情绪 / 五年之锚 (本期)
  Layer 2 - 风格偏斜: 大小盘/成长价值轮动三棱镜 (Phase 2)
  Layer 3 - 跨市场: A/H红利 / 中美双视角 (Phase 3)

对接: generate_trade_signals.py (嵌入开盘推送头部)
      closing_review.py (复盘对比信号变化)
"""

import os, sys, json, math
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import akshare as ak

TZ = timezone(timedelta(hours=8))
NOW = lambda: datetime.now(TZ)
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
DATA_CACHE = os.path.join(PROJECT_DIR, "data", "cache")

# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

class SignalLight:
    def __init__(self, name, value, pct_rank, signal, note="",
                 def_conf=3, data_conf=3):
        self.name = name
        self.value = value
        self.pct_rank = pct_rank
        self.signal = signal  # 'warm'|'neutral'|'cold'|'overheat'|'oversold'
        self.note = note
        self.def_conf = def_conf    # 指标定义置信度 1-5
        self.data_conf = data_conf  # 本次数据取值置信度 1-5

    def emoji(self):
        m = {'overheat': '🔴', 'warm': '🟠', 'neutral': '⚪',
             'cold': '🔵', 'oversold': '💎'}
        return m.get(self.signal, '⚪')

    def stars(self, n):
        return '★' * n + '☆' * (5 - n)

    def conf_tag(self):
        return f"定义{self.stars(self.def_conf)} 数据{self.stars(self.data_conf)}"


class MarketTemperature:
    def __init__(self):
        self.date = NOW().strftime("%Y-%m-%d")
        self.signals: list[SignalLight] = []
        self.summary = ""

    def add(self, light: SignalLight):
        self.signals.append(light)

    def brief(self):
        parts = [f"{s.emoji()} {s.name}: {s.value}" for s in self.signals]
        return " | ".join(parts)

    def to_dict(self):
        return {
            "date": self.date,
            "signals": [{"name": s.name, "value": s.value, "pct_rank": round(s.pct_rank, 1),
                          "signal": s.signal, "note": s.note,
                          "def_conf": s.def_conf, "data_conf": s.data_conf} for s in self.signals],
            "summary": self.summary
        }


# ═══════════════════════════════════════════════════════════════
# 数据拉取层
# ═══════════════════════════════════════════════════════════════

def _fetch_index_hist(symbol: str, name: str) -> Optional[pd.Series]:
    """获取指数日线历史，返回收盘价Series。
    symbol: sh000300 / sz930950
    sh前缀用 stock_zh_index_daily；sz前缀尝试同接口，失败降级腾讯源。
    """
    cache_file = os.path.join(DATA_CACHE, f"idx_{symbol.replace('.','_')}.csv")
    if os.path.exists(cache_file):
        try:
            cached = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if len(cached) > 0:
                last_date = cached.index[-1].to_pydatetime().date()
                today = NOW().date()
                if last_date >= today or today.weekday() >= 5:
                    return pd.Series(cached['close'].values, index=pd.to_datetime(cached.index))
        except Exception:
            pass

    df = None
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
    except Exception:
        pass

    # sz指数降级到腾讯源
    if df is None or df.empty:
        try:
            df = ak.stock_zh_index_daily_tx(symbol=symbol)
        except Exception:
            pass

    if df is None or df.empty:
        print(f"[style_rotation] fetch {name}({symbol}) failed", file=sys.stderr)
        return None

    s = pd.Series(df['close'].values, index=pd.to_datetime(df['date']))
    s = s.sort_index()
    os.makedirs(DATA_CACHE, exist_ok=True)
    out = s.copy()
    out.index = out.index.strftime('%Y-%m-%d')
    out.to_csv(cache_file, index_label='date')
    return s


def _fetch_csindex_value(index_code: str) -> Optional[pd.DataFrame]:
    """stock_zh_index_value_csindex 获取估值数据(股息率/PE)。
    index_code: 不带前缀，如 '000922'"""
    try:
        df = ak.stock_zh_index_value_csindex(symbol=index_code)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        print(f"[style_rotation] csindex {index_code} failed: {e}", file=sys.stderr)
        return None


def _fetch_bond_10y() -> Optional[float]:
    """10年期国债收益率 (%)"""
    try:
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        for col in df.columns:
            if '10' in col and ('国债' in col or '中国' in col):
                val = pd.to_numeric(latest[col], errors='coerce')
                if not pd.isna(val) and 0 < val < 20:
                    return float(val)
        return None
    except Exception as e:
        print(f"[style_rotation] bond_10y failed: {e}", file=sys.stderr)
        return None


def pct_rank(series: pd.Series, current: float) -> float:
    """当前值在历史序列中的分位数(0-100)"""
    if series is None or len(series) < 2:
        return 50.0
    return float((series < current).mean() * 100)


def signal_from_rank(pct: float) -> str:
    """分位数 → 信号颜色"""
    if pct > 90: return 'overheat'
    if pct > 75: return 'warm'
    if pct < 10: return 'oversold'
    if pct < 25: return 'cold'
    return 'neutral'


# ═══════════════════════════════════════════════════════════════
# 指标一: 股债性价比 (FED模型, 中国视角)
# ═══════════════════════════════════════════════════════════════

def calc_equity_bond_spread() -> Optional[SignalLight]:
    """
    股债性价比 = 1/PE_TTM − 10年期国债收益率
    数据: csindex 沪深300 PE(TTM) + bond_zh_us_rate 10Y国债
    """
    try:
        cs_df = _fetch_csindex_value('000300')
        if cs_df is None or len(cs_df) == 0:
            return None

        last_row = cs_df.iloc[-1]
        pe_col = '市盈率1' if '市盈率1' in cs_df.columns else None
        if pe_col is None:
            return None

        pe_ttm = pd.to_numeric(last_row[pe_col], errors='coerce')
        if pd.isna(pe_ttm) or pe_ttm <= 0:
            return None
        pe_ttm = float(pe_ttm)

        bond_yield = _fetch_bond_10y()
        if bond_yield is None:
            return None

        spread = 100.0 / pe_ttm - bond_yield  # 1/PE 转为百分比后减去国债收益率(%)

        # 历史分位
        pe_series = pd.to_numeric(cs_df[pe_col], errors='coerce').dropna()
        rank = pct_rank(100.0 / pe_series - bond_yield, spread) if len(pe_series) > 5 else 50.0
        signal = signal_from_rank(rank)

        if rank > 80:
            note = f"股债性价比高({spread:.1f}%)，股票极具吸引力"
        elif rank > 60:
            note = f"股债性价比偏高({spread:.1f}%)，股票相对便宜"
        elif rank > 40:
            note = f"股债性价比中性({spread:.1f}%)"
        elif rank > 20:
            note = f"股债性价比偏低({spread:.1f}%)，股票偏贵"
        else:
            note = f"股债性价比极低({spread:.1f}%)，债券优于股票"

        return SignalLight("股债性价比", f"{spread:.1f}%", rank, signal, note,
                          def_conf=4, data_conf=2)

    except Exception as e:
        print(f"[style_rotation] equity_bond_spread: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标二: 偏股基金3年滚动年化收益
# ═══════════════════════════════════════════════════════════════

def calc_bias_fund_return() -> Optional[SignalLight]:
    """
    中证800(sh000906)3年滚动年化收益，近似偏股基金情绪。
    ≥30%泡沫 / ≤-10%底部。
    中证偏股基金(930950)深交所代码不可获取，用中证800替代。
    """
    try:
        fund = _fetch_index_hist('sh000906', '中证800')
        if fund is None or len(fund) < 500:
            return None

        last_price = float(fund.iloc[-1])
        start_idx = min(int(len(fund) * 0.6), len(fund) - 1)  # 约3年前
        start_price = float(fund.iloc[start_idx])

        if start_price <= 0 or last_price <= 0:
            return None

        days = int((fund.index[-1] - fund.index[start_idx]).total_seconds() / 86400)
        if days <= 0:
            days = 1095
        cagr = math.log(last_price / start_price) * (365.0 / days) * 100

        if cagr >= 30:
            signal, note = 'overheat', f"中证800三年年化{cagr:.1f}%，泡沫警告"
        elif cagr >= 15:
            signal, note = 'warm', f"中证800三年年化{cagr:.1f}%，偏热"
        elif cagr >= 0:
            signal, note = 'neutral', f"中证800三年年化{cagr:.1f}%，正常"
        elif cagr >= -10:
            signal, note = 'cold', f"中证800三年年化{cagr:.1f}%，偏冷"
        else:
            signal, note = 'oversold', f"中证800三年年化{cagr:.1f}%，底部区域"

        rank = 50.0
        if len(fund) > 756:
            rols = []
            for i in range(min(756, len(fund)), len(fund)):
                try:
                    w = fund.iloc[i-756:i]
                    if len(w) > 700:
                        d2 = int((w.index[-1] - w.index[0]).total_seconds() / 86400)
                        if d2 > 0:
                            r = math.log(float(w.iloc[-1])/float(w.iloc[0])) * (365/d2)
                            rols.append(r * 100)
                except Exception:
                    pass
            if rols:
                rank = pct_rank(pd.Series(rols), cagr)

        return SignalLight("偏股情绪", f"{cagr:.1f}%", rank, signal, note,
                          def_conf=3, data_conf=3)

    except Exception as e:
        print(f"[style_rotation] bias_fund: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标三: 红利息差
# ═══════════════════════════════════════════════════════════════

def calc_dividend_premium() -> Optional[SignalLight]:
    """
    红利股债息差 = 中证红利股息率 − 10年期国债收益率
    数据: csindex 000922 股息率 + bond_zh_us_rate
    """
    try:
        cs_df = _fetch_csindex_value('000922')
        if cs_df is None or len(cs_df) == 0:
            return None

        last_row = cs_df.iloc[-1]
        div_col = None
        for c in ['股息率1', '股息率']:
            if c in cs_df.columns:
                div_col = c
                break
        if div_col is None:
            return None

        div_yield = pd.to_numeric(last_row[div_col], errors='coerce')
        if pd.isna(div_yield) or div_yield <= 0:
            return None
        div_yield = float(div_yield)

        bond_yield = _fetch_bond_10y()
        if bond_yield is None:
            return None

        spread = div_yield - bond_yield

        # 历史分位
        rank = 50.0
        if len(cs_df) > 5 and div_col in cs_df.columns:
            hist_div = pd.to_numeric(cs_df[div_col], errors='coerce').dropna()
            if len(hist_div) > 5:
                rank = pct_rank(hist_div - bond_yield, spread)
        signal = signal_from_rank(rank)

        if spread > 3.0:
            note = f"红利息差{spread:.2f}%，极高溢价"
        elif spread > 2.0:
            note = f"红利息差{spread:.2f}%，高溢价"
        elif spread > 1.0:
            note = f"红利息差{spread:.2f}%，中等溢价"
        elif spread > 0:
            note = f"红利息差{spread:.2f}%，低溢价"
        else:
            note = f"红利息差{spread:.2f}%，负溢价"

        return SignalLight("红利息差", f"{spread:.2f}%", rank, signal, note,
                          def_conf=3, data_conf=2)

    except Exception as e:
        print(f"[style_rotation] dividend_premium: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标四: 五年之锚
# ═══════════════════════════════════════════════════════════════

def calc_five_year_anchor() -> Optional[SignalLight]:
    """
    中证A股全收益指数 vs 五年均线偏离度。
    低于均线=加仓 / 高于+15%=减仓。
    用沪深300指数近似（000805数据不全）。
    """
    try:
        # 优先用中证全指 000985，fallback 沪深300
        all_share = _fetch_index_hist('sh000985', '中证全指')
        if all_share is None or len(all_share) < 1260:
            all_share = _fetch_index_hist('sh000300', '沪深300')
        if all_share is None or len(all_share) < 1260:
            return None

        ma5y = all_share.rolling(1260).mean().dropna()
        if len(ma5y) < 2:
            return None

        current = float(all_share.iloc[-1])
        anchor = float(ma5y.iloc[-1])
        deviation = (current - anchor) / anchor * 100

        if deviation < -5:
            signal, note = 'oversold', f"偏离{deviation:.1f}%，深度低估，适宜加仓"
        elif deviation < 0:
            signal, note = 'cold', f"偏离{deviation:.1f}%，低估区域"
        elif deviation < 10:
            signal, note = 'neutral', f"偏离{deviation:.1f}%，正常区间"
        elif deviation < 15:
            signal, note = 'warm', f"偏离{deviation:.1f}%，偏高可暂停定投"
        else:
            signal, note = 'overheat', f"偏离{deviation:.1f}%，高估建议减仓"

        hist_dev = (all_share.iloc[-1260:] - ma5y.iloc[-len(ma5y):]) / ma5y.iloc[-len(ma5y):] * 100
        hist_dev = hist_dev.dropna()
        rank = pct_rank(hist_dev, deviation)

        return SignalLight("五年之锚", f"偏离{deviation:.1f}%", rank, signal, note,
                          def_conf=2, data_conf=4)

    except Exception as e:
        print(f"[style_rotation] five_year_anchor: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def compute_market_temperature() -> MarketTemperature:
    temp = MarketTemperature()

    for fn in [calc_equity_bond_spread, calc_bias_fund_return,
               calc_dividend_premium, calc_five_year_anchor]:
        try:
            result = fn()
            if result:
                temp.add(result)
        except Exception as e:
            print(f"[style_rotation] {fn.__name__}: {e}", file=sys.stderr)

    # 摘要
    if temp.signals:
        hot = sum(1 for s in temp.signals if s.signal in ('overheat', 'warm'))
        cold = sum(1 for s in temp.signals if s.signal in ('oversold', 'cold'))
        if cold > hot:
            temp.summary = f"市场温度：偏冷 | {cold}偏冷/{hot}偏热"
        elif hot > cold:
            temp.summary = f"市场温度：偏热 | {cold}偏冷/{hot}偏热"
        else:
            temp.summary = f"市场温度：中性 | {cold}偏冷/{hot}偏热"
    else:
        temp.summary = "市场温度数据暂不可用"

    return temp


def format_brief_for_push(temp: MarketTemperature) -> str:
    if not temp.signals:
        return ""
    lines = ["市场温度与风格水位"]
    for s in temp.signals:
        lines.append(f"{s.emoji()} {s.name}: {s.value}（{s.pct_rank:.0f}%分位）【{s.conf_tag()}】\n  → {s.note}")
    return "\n".join(lines)


def format_compact_for_push(temp: MarketTemperature) -> str:
    if not temp.signals:
        return ""
    items = [f"{s.emoji()}{s.name}={s.value}({s.pct_rank:.0f}%)[{s.def_conf}/{s.data_conf}]" for s in temp.signals]
    return " | ".join(items)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--brief', action='store_true')
    ap.add_argument('--compact', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-cache', action='store_true')
    args = ap.parse_args()

    if args.no_cache:
        import glob
        for f in glob.glob(os.path.join(DATA_CACHE, "idx_*.csv")):
            os.remove(f)
        print("Cache cleared.")

    temp = compute_market_temperature()

    if args.json:
        print(json.dumps(temp.to_dict(), ensure_ascii=False, indent=2))
    elif args.compact:
        print(format_compact_for_push(temp))
    else:
        print(format_brief_for_push(temp))
