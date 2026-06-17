#!/usr/bin/env python3
"""
金桥共享行情模块 v1.0 — 统一行情拉取 + 交叉校验 + 降级策略
项目: 金桥量化 v2.5.0

所有脚本通过此模块获取行情，不各自实现拉取/校验/降级逻辑。

用法:
  from market_data import fetch_realtime, fetch_close_prices, fetch_open_prices

  # 实时价（盘中预警用）
  prices = fetch_realtime()  # {中文名: {price, change_pct, high, low, source}}

  # 收盘价（收盘复盘用）
  closes = fetch_close_prices()  # {中文名: {price, chg_pct, high, low, source}}

  # 开盘价（开盘推送用）
  opens = fetch_open_prices()  # {ticker: {price, source}}
"""

import os, re, json, requests
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))

# ─── 标的映射（单一来源，所有脚本共用）───
# 中文名 → (新浪代码, 腾讯代码, Yahoo代码)
SYMBOL_MAP = {
    "上证50":    ("sh000016", "sh000016", "000016.SS"),
    "沪深300":   ("sh000300", "sh000300", "000300.SS"),
    "科创50":    ("sh000688", "sh000688", "000688.SS"),
    "农业银行":  ("sh601288", "sh601288", "601288.SS"),
    "中国银行":  ("sh601988", "sh601988", "601988.SS"),
    "招商银行":  ("sh600036", "sh600036", "600036.SS"),
    "国电电力":  ("sh600795", "sh600795", "600795.SS"),
    "中国长城":  ("sz000066", "sz000066", "000066.SZ"),
    "国睿科技":  ("sh600562", "sh600562", "600562.SS"),
    "中证机器人": ("sh562500", "sh562500", "562500.SS"),
}

SINA_CODES = [v[0] for v in SYMBOL_MAP.values()]
TENCENT_CODES = [v[1] for v in SYMBOL_MAP.values()]
SINA_TO_NAME = {v[0]: k for k, v in SYMBOL_MAP.items()}
TENCENT_TO_NAME = {v[1]: k for k, v in SYMBOL_MAP.items()}


# ─── 基础拉取函数 ───

def _fetch_sina_realtime(codes=None):
    """从新浪实时行情拉取，返回 {sina_code: {fields, date}}"""
    if codes is None:
        codes = SINA_CODES
    url = "http://hq.sinajs.cn/list=" + ",".join(codes)
    resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
    resp.encoding = "gbk"
    data = {}
    for line in resp.text.strip().split("\n"):
        m = re.search(r'hq_str_(\w+)="(.+)"', line)
        if not m:
            continue
        code, raw = m.group(1), m.group(2)
        fields = raw.split(",")
        if len(fields) < 6:
            continue
        ts_date = fields[30] if len(fields) > 30 else ""
        data[code] = {
            "fields": fields,
            "date": ts_date,
        }
    return data


def _fetch_tencent_realtime(codes=None):
    """从腾讯实时行情拉取，返回 {code: {fields, date}}"""
    if codes is None:
        codes = TENCENT_CODES
    url = "http://qt.gtimg.cn/q=" + ",".join(codes)
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    data = {}
    for line in resp.text.strip().split("\n"):
        m = re.search(r'="(.+)"', line)
        if not m:
            continue
        fields = m.group(1).split("~")
        if len(fields) < 35:
            continue
        ts_date = fields[30] if len(fields) > 30 else ""
        data[fields[2]] = {  # fields[2]=纯数字代码
            "fields": fields,
            "date": ts_date,
        }
    return data


def _is_today(ts_date):
    """检查时间戳是否等于今天"""
    if not ts_date:
        return False
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return ts_date == today


# ─── 公开接口 ───

def fetch_realtime():
    """
    获取全部标的最新的实时价（盘中预警用）
    返回: {中文名: {price, change_pct, high, low, source}}
    数据源: 新浪主源 + 腾讯交叉校验
    """
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    sina = _fetch_sina_realtime()
    tencent = _fetch_tencent_realtime()
    result = {}

    for name, (sina_code, tc_code, _) in SYMBOL_MAP.items():
        sd = sina.get(sina_code, {})
        td = tencent.get(tc_code[2:], {})  # 腾讯key是纯数字
        # 也尝试带前缀匹配
        if not td:
            for k, v in tencent.items():
                if tc_code[2:] in k:
                    td = v
                    break

        price = None
        source = "none"
        prev_close = 0

        # 新浪 fields: [1]=今开, [2]=昨收, [3]=当前, [4]=高, [5]=低
        s_fields = sd.get("fields", [])
        s_date = sd.get("date", "")
        t_fields = td.get("fields", [])
        t_date = td.get("date", "")

        # 主源：新浪
        if s_fields and _is_today(s_date):
            try:
                price = float(s_fields[3]) if s_fields[3] and s_fields[3] != "0.000" else float(s_fields[1])
                prev_close = float(s_fields[2]) if len(s_fields) > 2 and s_fields[2] else 0
                source = "sina"
            except (ValueError, IndexError):
                pass

        # 腾讯交叉（偏差>0.5%时检查）
        if t_fields and _is_today(t_date) and price and price > 0:
            try:
                tc_price = float(t_fields[3]) if t_fields[3] and t_fields[3] != "0.000" else None
                if tc_price and tc_price > 0:
                    deviation = abs(price - tc_price) / tc_price * 100
                    if deviation > 0.5:
                        # 取与昨收更合理的一方
                        tc_prev = float(t_fields[4]) if len(t_fields) > 4 and t_fields[4] else 0
                        s_chg = abs(price - prev_close) / prev_close if prev_close else 0
                        t_chg = abs(tc_price - tc_prev) / tc_prev if tc_prev else 0
                        if t_chg < s_chg * 2:
                            price = tc_price
                            source = "tencent:cross"
            except (ValueError, IndexError):
                pass

        # 腾讯兜底（新浪失败时）
        if price is None and t_fields and _is_today(t_date):
            try:
                price = float(t_fields[3]) if t_fields[3] and t_fields[3] != "0.000" else None
                if price:
                    source = "tencent"
            except (ValueError, IndexError):
                pass

        if price is None:
            continue

        # 涨跌幅
        chg_pct = (price / prev_close - 1) * 100 if prev_close else 0
        high = low = 0
        try:
            high = float(s_fields[4]) if s_fields and len(s_fields) > 4 and s_fields[4] else 0
            low = float(s_fields[5]) if s_fields and len(s_fields) > 5 and s_fields[5] else 0
        except (ValueError, IndexError):
            pass

        result[name] = {
            "price": round(price, 2),
            "change_pct": round(chg_pct, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "source": source,
        }

    return result


def fetch_close_prices():
    """
    获取收盘价（收盘复盘用）
    返回: {中文名: {price, chg_pct, high, low, source}}
    """
    # 收盘后实时行情字段[3]就是收盘价，与 fetch_realtime 逻辑完全相同
    prices = fetch_realtime()
    # 转换key名以兼容 closing_review.py 的旧接口
    result = {}
    for name, v in prices.items():
        result[name] = {
            "price": v["price"],
            "chg_pct": v["change_pct"],
            "high": v["high"],
            "low": v["low"],
            "source": v["source"],
        }
    return result


def fetch_open_prices():
    """
    获取开盘价（开盘推送用）
    返回: {ticker: {price, source}}
    注意: ticker 使用 Yahoo 格式 (e.g. '000016.SH')
    """
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    sina = _fetch_sina_realtime()
    tencent = _fetch_tencent_realtime()
    result = {}

    yahoo_ticker_map = {v[2]: k for k, v in SYMBOL_MAP.items() if v[2]}

    for name, (sina_code, tc_code, yahoo_code) in SYMBOL_MAP.items():
        sd = sina.get(sina_code, {})
        td = tencent.get(tc_code[2:], {})
        if not td:
            for k, v in tencent.items():
                if tc_code[2:] in k:
                    td = v
                    break

        open_price = None
        source = "fallback"

        s_fields = sd.get("fields", [])
        s_date = sd.get("date", "")
        t_fields = td.get("fields", [])
        t_date = td.get("date", "")

        # 腾讯: fields[5]=今开
        if t_fields and _is_today(t_date):
            try:
                open_price = float(t_fields[5]) if t_fields[5] and t_fields[5] != "0.000" else None
                if open_price:
                    source = "tencent"
            except (ValueError, IndexError):
                pass

        # 新浪: fields[1]=今开，交叉校验
        if s_fields and _is_today(s_date):
            try:
                sina_open = float(s_fields[1]) if s_fields[1] and s_fields[1] != "0.000" else None
                if sina_open:
                    if open_price is None:
                        open_price = sina_open
                        source = "sina"
                    else:
                        deviation = abs(open_price - sina_open) / sina_open * 100
                        if deviation > 0.5:
                            # 保留腾讯（主源优先）
                            source = "tencent:cross"
            except (ValueError, IndexError):
                pass

        # fallback 到昨收
        if open_price is None:
            try:
                open_price = float(t_fields[4]) if t_fields and len(t_fields) > 4 and t_fields[4] else None
                if not open_price and s_fields:
                    open_price = float(s_fields[2]) if s_fields[2] and s_fields[2] != "0.000" else None
                if open_price:
                    source = "prev_close"
            except (ValueError, IndexError):
                pass

        if open_price and yahoo_code:
            result[yahoo_code] = {
                "price": round(open_price, 2),
                "source": source,
            }

    return result
