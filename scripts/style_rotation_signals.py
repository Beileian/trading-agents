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
                 def_conf=3, data_conf=3, data_freshness=""):
        self.name = name
        self.value = value
        self.pct_rank = pct_rank
        self.signal = signal  # 'warm'|'neutral'|'cold'|'overheat'|'oversold'
        self.note = note
        self.def_conf = def_conf    # 指标定义置信度 1-5
        self.data_conf = data_conf  # 本次数据取值置信度 1-5
        self.data_freshness = data_freshness  # 数据截止日期描述

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
        self.decision = ""
        self.contradictions = []

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
                          "def_conf": s.def_conf, "data_conf": s.data_conf,
                          "data_freshness": s.data_freshness} for s in self.signals],
            "summary": self.summary,
            "decision": self.decision,
            "contradictions": self.contradictions
        }

    def analyze_contradictions(self):
        """检测五指标间的信号矛盾并生成解读（含多尺度嵌套：个股分位→板块联动→大盘温度）"""
        if len(self.signals) < 3:
            return
        hot_signals = [s for s in self.signals if s.signal in ('overheat', 'warm')]
        cold_signals = [s for s in self.signals if s.signal in ('oversold', 'cold')]

        contradictions = []
        # 股债性价比 vs 五年之锚：估值便宜但短期涨太多
        eq = next((s for s in self.signals if s.name == '股债性价比'), None)
        anchor = next((s for s in self.signals if s.name == '五年之锚'), None)
        if eq and anchor and eq.signal in ('warm', 'overheat') and anchor.signal in ('warm', 'overheat'):
            contradictions.append("股债性价比说「股票便宜」+ 五年之锚说「短期涨太多」→ 不矛盾：中长期估值有支撑但短期追高风险大，建议持有不加仓")
        elif eq and anchor and eq.signal in ('warm',) and anchor.signal in ('oversold', 'cold'):
            contradictions.append("股债性价比偏高但价格低于五年均线 → 真正的低估机会，可以积极加仓")
        elif eq and anchor and eq.signal in ('cold', 'oversold') and anchor.signal in ('overheat', 'warm'):
            contradictions.append("股贵+价高 → 双重警告，应该减仓或观望")

        # 融资情绪过热 vs 偏股情绪中性或冷
        margin = next((s for s in self.signals if s.name == '融资情绪'), None)
        bias = next((s for s in self.signals if s.name == '偏股情绪'), None)
        if margin and bias and margin.signal in ('overheat', 'warm') and bias.signal in ('cold', 'oversold', 'neutral'):
            contradictions.append("融资情绪热但偏股情绪中性 → 杠杆资金在追但长期趋势未跟上，警惕短期回调")

        # ── 多尺度嵌套：个股TimesFM分位 → 板块联动 → 大盘温度 ──
        self._add_stock_level_contradictions(contradictions, hot_signals, cold_signals)

        self.contradictions = contradictions

    def _add_stock_level_contradictions(self, contradictions, hot_signals, cold_signals):
        """读取TimesFM校准数据，检测个股分位与大盘温度的错位"""
        import glob
        import os as _os
        cal_dir = _os.path.join(PROJECT_DIR, 'logs', 'timesfm_calibration')
        cal_files = glob.glob(_os.path.join(cal_dir, '*.json'))
        if not cal_files:
            return

        low_count = 0  # 处于预测分布低位的标的数
        high_count = 0  # 处于预测分布高位的标的数
        total_stocks = 0
        stock_details = []

        for cf in cal_files:
            try:
                with open(cf) as f:
                    d = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            # 优先用 CI80_coverage 或 point_forecast；否则降级到最后一窗的 fc_5d / p10-p90
            forecast = d.get('point_forecast') or d.get('recent_forecast')
            ci80 = d.get('CI80_coverage')
            windows = d.get('windows_detail', [])
            if not windows:
                continue

            last_win = windows[-1]
            fc = last_win.get('fc_5d')
            p10 = last_win.get('p10_5d')
            p90 = last_win.get('p90_5d')
            actual = last_win.get('actual_5d')

            if fc is None or p10 is None or p90 is None:
                continue
            if actual is None:
                actual = fc  # fallback: 用预测中点
            if p90 <= p10:
                continue

            # 计算 actual 在 [p10, p90] 中的分位
            position_pct = (actual - p10) / (p90 - p10) * 100
            symbol_name = d.get('name', _os.path.basename(cf).replace('.json', ''))
            total_stocks += 1

            if position_pct <= 20:
                low_count += 1
                stock_details.append(f"{symbol_name}(P{position_pct:.0f})")
            elif position_pct >= 80:
                high_count += 1
                stock_details.append(f"{symbol_name}(P{position_pct:.0f})")

        if total_stocks < 3:
            return

        low_ratio = low_count / total_stocks
        high_ratio = high_count / total_stocks

        # 大盘偏热 but 个股在低位 → 局部机会
        if len(hot_signals) >= 3 and low_ratio >= 0.5:
            contradictions.append(
                "大盘温度偏热但多数个股处于预测分布低位 → 局部机会存在，建议精选标的"
            )
        elif len(hot_signals) >= 2 and low_ratio >= 0.6:
            contradictions.append(
                "大盘温度偏热但多数个股处于预测分布低位 → 局部机会存在，建议精选标的"
            )

        # 大盘偏冷 but 个股在高位 → 警惕情绪透支
        if len(cold_signals) >= 3 and high_ratio >= 0.5:
            contradictions.append(
                "大盘偏冷但个股已脱离预测区间高位 → 警惕情绪透支"
            )
        elif len(cold_signals) >= 2 and high_ratio >= 0.6:
            contradictions.append(
                "大盘偏冷但个股已脱离预测区间高位 → 警惕情绪透支"
            )

    def make_decision(self):
        """基于五指标综合生成一句话行动建议"""
        if not self.signals:
            self.decision = "数据不足，无法判断"
            return
        hot = sum(1 for s in self.signals if s.signal in ('overheat', 'warm'))
        cold = sum(1 for s in self.signals if s.signal in ('oversold', 'cold'))

        # 检查五年之锚是否极端
        anchor = next((s for s in self.signals if s.name == '五年之锚'), None)
        anchor_extreme = anchor and anchor.signal == 'overheat'

        if cold >= 3:
            self.decision = "🟢 市场偏冷，可以逐步加仓"
        elif hot >= 3:
            if anchor_extreme:
                self.decision = "🔴 市场偏热+价格严重偏离五年均线，建议控制仓位等待回调"
            else:
                self.decision = "🟠 市场偏热，持有不加仓，关注回调买入机会"
        elif hot >= 2 and cold >= 2:
            self.decision = "⚪ 信号分歧，维持现有仓位不变"
        else:
            self.decision = "⚪ 市场中性，维持现有策略"


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
    index_code: 不带前缀，如 '000922'
    AKShare在进程中首次调用时获取最新数据，后续同接口调用返回缓存。
    为保障红利息差拿到最新股息率，calc_dividend_premium 先于 equity_bond_spread 调用。
    """
    try:
        import akshare as ak
        df = ak.stock_zh_index_value_csindex(symbol=index_code)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        print(f"[style_rotation] csindex {index_code} failed: {e}", file=sys.stderr)
        return None


def _fetch_tencent_index_pe(index_code: str) -> Optional[float]:
    """从腾讯财经接口拉取指数PE(TTM)，字段[39]，每日更新"""
    try:
        import urllib.request
        url = f'http://qt.gtimg.cn/q=sh{index_code}'
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.qq.com'})
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('gbk')
        parts = text.split('~')
        if len(parts) > 39:
            pe_val = float(parts[39]) if parts[39] else None
            if pe_val and pe_val > 0 and pe_val < 500:
                return pe_val
        return None
    except Exception as e:
        print(f"[style_rotation] tencent PE {index_code}: {e}", file=sys.stderr)
        return None
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
    数据: 腾讯财经PE(实时) + bond_zh_us_rate 10Y国债
    历史分位: csindex PE历史序列(20日) + 腾讯实时PE弹性扩展
    """
    try:
        # 优先用腾讯财经实时PE
        pe_ttm = _fetch_tencent_index_pe('000300')
        data_conf = 4  # 腾讯PE每日更新, 置信度高
        
        if pe_ttm is None:
            # 降级到csindex
            cs_df = _fetch_csindex_value('000300')
            if cs_df is None or len(cs_df) == 0:
                return None
            last_row = cs_df.iloc[0]  # csindex返回日期降序
            pe_col = '市盈率1' if '市盈率1' in cs_df.columns else None
            if pe_col is None:
                return None
            pe_ttm = pd.to_numeric(last_row[pe_col], errors='coerce')
            if pd.isna(pe_ttm) or pe_ttm <= 0:
                return None
            pe_ttm = float(pe_ttm)
            data_conf = 2  # csindex滞后, 置信度低

        bond_yield = _fetch_bond_10y()
        if bond_yield is None:
            return None

        spread = 100.0 / pe_ttm - bond_yield  # 1/PE 转为百分比后减去国债收益率(%)

        # 历史分位: 用csindex历史PE序列 + 当前腾讯PE弹性扩展
        rank = 50.0
        cs_df = _fetch_csindex_value('000300')
        if cs_df is not None and len(cs_df) > 5:
            pe_col = '市盈率1' if '市盈率1' in cs_df.columns else None
            if pe_col:
                hist_pe = pd.to_numeric(cs_df[pe_col], errors='coerce').dropna()
                if len(hist_pe) > 5:
                    # 将腾讯PE加入序列末尾以校准分位
                    hist_pe_ext = pd.concat([hist_pe, pd.Series([pe_ttm])])
                    rank = pct_rank(100.0 / hist_pe_ext - bond_yield, spread)

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

        freshness = ""
        if data_conf >= 4:
            freshness = f"腾讯实时PE (PE={pe_ttm:.1f})"
        else:
            freshness = f"csindex PE (滞后)"
        return SignalLight("股债性价比", f"{spread:.1f}%", rank, signal, note,
                          def_conf=4, data_conf=data_conf, data_freshness=freshness)

    except Exception as e:
        print(f"[style_rotation] equity_bond_spread: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标二: 偏股基金3年滚动年化收益
# ═══════════════════════════════════════════════════════════════

def calc_bias_fund_return() -> Optional[SignalLight]:
    """
    中证800(sh000906)3年滚动年化收益，近似偏股基金情绪。
    ≥30%泡沫 / ≤-10%底部（Zhang & Wong 2020框架：低夏普市场择时可行）。
    严格按1095天窗口计算，不按比例。
    """
    try:
        fund = _fetch_index_hist('sh000906', '中证800')
        if fund is None or len(fund) < 500:
            return None

        last_price = float(fund.iloc[-1])
        last_date = fund.index[-1]
        # 严格3年前：1095个自然日
        THREE_YEARS_AGO = last_date - pd.Timedelta(days=1095)
        start_loc = fund.index.searchsorted(THREE_YEARS_AGO)
        if isinstance(start_loc, np.ndarray):
            start_loc = start_loc[0] if len(start_loc) > 0 else 0
        start_idx = max(0, int(start_loc))
        if start_idx >= len(fund):
            start_idx = 0
        start_price = float(fund.iloc[start_idx])

        if start_price <= 0 or last_price <= 0:
            return None

        days = int((last_date - fund.index[start_idx]).total_seconds() / 86400)
        if days <= 300:  # 数据不足1年，不靠谱
            return None
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

        # 历史分位：用过去所有3年滚动窗口
        rank = 50.0
        if len(fund) > 756:
            rols = []
            for i in range(756, len(fund)):
                try:
                    w = fund.iloc[i-756:i]
                    if len(w) > 700:
                        d2 = int((w.index[-1] - w.index[0]).total_seconds() / 86400)
                        if d2 > 365:
                            r = math.log(float(w.iloc[-1])/float(w.iloc[0])) * (365/d2)
                            rols.append(r * 100)
                except Exception:
                    pass
            if rols:
                rank = pct_rank(pd.Series(rols), cagr)

        last_data_date = str(last_date)[:10] if hasattr(last_date, 'strftime') else str(last_date)[:10]
        freshness = f"中证800日线 (至{last_data_date})"
        return SignalLight("偏股情绪", f"{cagr:.1f}%", rank, signal, note,
                          def_conf=3, data_conf=3, data_freshness=freshness)

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
    csindex数据更新滞后（通常每月一次），需检测时效性
    """
    try:
        cs_df = _fetch_csindex_value('000922')
        if cs_df is None or len(cs_df) == 0:
            return None

        last_row = cs_df.iloc[0]  # csindex返回日期降序，iloc[0]是最新
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

        # 数据时效性检测
        cs_date = last_row.get('日期') if '日期' in last_row.index else None
        stale_days = 0
        if cs_date is not None:
            try:
                cs_dt = pd.to_datetime(cs_date).date() if not isinstance(cs_date, (datetime,)) else cs_date
                if hasattr(cs_dt, 'date'):
                    cs_dt = cs_dt.date()
                stale_days = (NOW().date() - cs_dt).days
            except Exception:
                pass

        bond_yield = _fetch_bond_10y()
        if bond_yield is None:
            return None

        spread = div_yield - bond_yield

        # 数据时效性评估：csindex每个交易日更新，≤2天视为及时
        data_conf = 4  # 日频数据，默认高置信度
        freshness_note = f"csindex日频 (PE={cs_date})" if cs_date else "csindex日频"
        if stale_days > 5:
            data_conf = 2   # 超过一周未更新，异常
            freshness_note = f"csindex日频 (截止{stale_days}天前)"
            print(f"[style_rotation] csindex 股息率 stale {stale_days}天", file=sys.stderr)

        # 分位：csindex仅~20条缓存，序列太短分位不可靠
        # 实际更新周期为工作日日频，有30+条以上即可计算分位
        rank = 50.0
        if len(cs_df) >= 30 and div_col in cs_df.columns:
            hist_div = pd.to_numeric(cs_df[div_col], errors='coerce').dropna()
            if len(hist_div) >= 30:
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
                          def_conf=3, data_conf=data_conf, data_freshness=freshness_note)

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

        freshness = f"{'中证全指' if len(all_share) > 2000 else '沪深300'}5年MA (至{str(all_share.index[-1])[:10]})"
        return SignalLight("五年之锚", f"偏离{deviation:.1f}%", rank, signal, note,
                          def_conf=2, data_conf=4, data_freshness=freshness)

    except Exception as e:
        print(f"[style_rotation] five_year_anchor: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标五: 融资情绪
# ═══════════════════════════════════════════════════════════════

def _fetch_total_margin_balance() -> Optional[pd.Series]:
    """获取沪深两市融资余额合计，返回日频Series"""
    cache_file = os.path.join(DATA_CACHE, "margin_total.csv")
    if os.path.exists(cache_file):
        try:
            cached = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if len(cached) > 0:
                last_date = cached.index[-1].to_pydatetime().date()
                today = NOW().date()
                if last_date >= today or today.weekday() >= 5:
                    return pd.Series(cached['balance'].values, index=pd.to_datetime(cached.index))
        except Exception:
            pass

    try:
        sh = ak.macro_china_market_margin_sh()
        sz = ak.macro_china_market_margin_sz()
        if sh is None or sh.empty or sz is None or sz.empty:
            return None

        sh['日期'] = pd.to_datetime(sh['日期'])
        sz['日期'] = pd.to_datetime(sz['日期'])

        sh_balance = sh.set_index('日期')['融资余额']
        sz_balance = sz.set_index('日期')['融资余额']

        total = (sh_balance + sz_balance).dropna()
        total = total.sort_index()

        os.makedirs(DATA_CACHE, exist_ok=True)
        out = total.copy()
        out.index = out.index.strftime('%Y-%m-%d')
        pd.DataFrame({'balance': out.values}, index=out.index).to_csv(cache_file, index_label='date')
        return total
    except Exception as e:
        print(f"[style_rotation] margin fetch: {e}", file=sys.stderr)
        return None


def calc_margin_sentiment() -> Optional[SignalLight]:
    """
    融资情绪: 全市场融资余额3个月变化率。
    融资余额快速上升=杠杆过热, 融资余额快速下降=恐慌出逃。
    参考阈值: >10% 过热, <-10% 恐慌。
    """
    try:
        total = _fetch_total_margin_balance()
        if total is None or len(total) < 60:
            return None

        current = float(total.iloc[-1])
        prev_3m_idx = max(0, len(total) - 60)
        prev_3m = float(total.iloc[prev_3m_idx])

        if prev_3m <= 0:
            return None

        chg_3m = (current - prev_3m) / prev_3m * 100

        if chg_3m > 10:
            signal, note = 'overheat', f"融资余额近3月+{chg_3m:.1f}%，杠杆过热"
        elif chg_3m > 5:
            signal, note = 'warm', f"融资余额近3月+{chg_3m:.1f}%，杠杆偏热"
        elif chg_3m > -5:
            signal, note = 'neutral', f"融资余额近3月{chg_3m:+.1f}%，杠杆正常"
        elif chg_3m > -10:
            signal, note = 'cold', f"融资余额近3月{chg_3m:.1f}%，杠杆收缩"
        else:
            signal, note = 'oversold', f"融资余额近3月{chg_3m:.1f}%，恐慌去杠杆"

        # 历史分位
        rank = 50.0
        if len(total) > 120:
            rols = []
            for i in range(120, len(total)):
                try:
                    r = (float(total.iloc[i]) - float(total.iloc[i-60])) / float(total.iloc[i-60]) * 100
                    rols.append(r)
                except Exception:
                    pass
            if rols:
                rank = pct_rank(pd.Series(rols), chg_3m)

        last_data_date = str(total.index[-1])[:10]
        freshness = f"两市融资余额 (至{last_data_date})"
        return SignalLight("融资情绪", f"{chg_3m:+.1f}%", rank, signal, note,
                          def_conf=3, data_conf=4, data_freshness=freshness)

    except Exception as e:
        print(f"[style_rotation] margin_sentiment: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# 指标六: 板块联动 (P2 通达信"发现"功能思路)
# ═══════════════════════════════════════════════════════════════

def compute_sector_linkage(mkt_temp: MarketTemperature | None = None) -> dict:
    """
    从申万一级行业指数找出当日强势板块, 与金桥自选池交叉匹配。
    返回: {
        'strong_sectors': [(板块名, 涨跌幅%), ...],  # Top 3
        'linked_stocks': [(标的名, 板块名, 板块涨跌幅), ...],  # 在强势板块的自选
        'summary_line': str  # 一句话摘要供推送用
    }
    """
    from symbols_config import SYMBOL_SECTOR_MAP, SWS_SECTOR_INDEX
    try:
        # 缓存: 同日/同交易日不重复请求（周末用周五缓存）
        import json as _json
        cache_file = os.path.join(DATA_CACHE, 'sw_sector_daily.json')
        today = NOW()
        today_str = today.strftime('%Y%m%d')
        # 周末回退到最近交易日
        cache_date = today_str
        if today.weekday() >= 5:
            offset = today.weekday() - 4
            cache_date = (today - timedelta(days=offset)).strftime('%Y%m%d')
        sector_pct = {}
        cache_valid = False
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cache_data = _json.load(f)
                cached_date = cache_data.get('date', '')
                if cached_date == cache_date or cached_date == today_str:
                    sector_pct = cache_data.get('data', {})
                    cache_valid = True
            except Exception:
                pass

        if not cache_valid:
            for code, name in SWS_SECTOR_INDEX.items():
                try:
                    df = ak.index_hist_sw(symbol=code, period='day')
                    if df is not None and len(df) >= 2:
                        today_close = float(df['收盘'].iloc[-1])
                        yesterday_close = float(df['收盘'].iloc[-2])
                        if yesterday_close > 0:
                            pct = (today_close - yesterday_close) / yesterday_close * 100
                            sector_pct[name] = round(pct, 2)
                except Exception:
                    continue
            # 写入缓存
            if sector_pct:
                os.makedirs(DATA_CACHE, exist_ok=True)
                with open(cache_file, 'w') as f:
                    _json.dump({'date': today_str, 'data': sector_pct}, f)

        if not sector_pct:
            return {'strong_sectors': [], 'linked_stocks': [], 'summary_line': ''}
        # Top 3 强势板块
        sorted_sectors = sorted(sector_pct.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_sectors[:3]
        strong_set = {s[0] for s in top3}
        # 匹配自选
        linked = []
        for stock_name, sw_code in SYMBOL_SECTOR_MAP.items():
            if sw_code is None:
                continue
            sector_name = SWS_SECTOR_INDEX.get(sw_code, '')
            if sector_name in strong_set and sector_name in sector_pct:
                linked.append((stock_name, sector_name, sector_pct[sector_name]))
        # 构建摘要
        top_names = '、'.join([f'{n}({p:+.1f}%)' for n, p in top3])
        summary = ''
        if linked:
            linked_names = '、'.join([s[0] for s in linked])
            summary = f"板块联动：强势板块 {top_names} | 自选中 {linked_names} 在强势板块内"
        else:
            summary = f"板块联动：强势板块 {top_names} | 自选暂无标的在Top3板块内"
        return {
            'strong_sectors': top3,
            'linked_stocks': linked,
            'summary_line': summary,
        }
    except Exception as e:
        print(f"[style_rotation] sector_linkage: {e}", file=sys.stderr)
        return {'strong_sectors': [], 'linked_stocks': [], 'summary_line': ''}

def compute_market_temperature() -> MarketTemperature:
    temp = MarketTemperature()

    # 红利息差必须第一个调（csindex 000922），否则会被 equity_bond_spread 的 000300 缓存污染
    for fn in [calc_dividend_premium, calc_equity_bond_spread, calc_bias_fund_return,
               calc_five_year_anchor, calc_margin_sentiment]:
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

    temp.analyze_contradictions()
    temp.make_decision()
    return temp


def format_brief_for_push(temp: MarketTemperature) -> str:
    if not temp.signals:
        return ""
    lines = []
    lines.append("**⑥ 市场温度计**")
    lines.append("")
    for s in temp.signals:
        sig_name = {'overheat':'过热','warm':'偏热','neutral':'中性','cold':'偏冷','oversold':'极冷'}.get(s.signal, s.signal)
        freshness_str = f" [{s.data_freshness}]" if s.data_freshness else ""
        # 数据置信度低时标注
        conf_warn = ""
        if s.data_conf <= 1:
            conf_warn = " ⚠️数据过期"
        elif s.data_conf == 2:
            conf_warn = " ℹ️数据滞后"
        lines.append(f"{s.emoji()} {s.name}: {s.value}（{s.pct_rank:.0f}%分位, {sig_name}）{conf_warn}{freshness_str}")
        lines.append(f"  → {s.note}")
    lines.append("")
    if temp.decision:
        lines.append(f"📋 {temp.decision}")
        lines.append("")
    if temp.contradictions:
        lines.append("⚡ 信号矛盾:")
        for c in temp.contradictions:
            lines.append(f"  • {c}")
        lines.append("")
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
