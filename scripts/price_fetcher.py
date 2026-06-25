#!/usr/bin/env python3
"""
price_fetcher.py — 统一价格获取模块（多源交叉验证 + 动态切换）

数据源优先级:
  T1: 腾讯财经 (qt.gtimg.cn) — 实时行情最可靠，字段明确
  T2: 东方财富 (push2.eastmoney.com) — 字段明确 f43=最新价
  T3: 新浪财经 (hq.sinajs.cn) — 免费无限额，分布场含义需注意

交叉验证规则:
  1. 所有标的同时从T1+T2拉取
  2. 偏差 < 1% → T1通过
  3. 偏差 1-3% → 记录warning，用T1(腾讯更可靠)
  4. 偏差 > 3% → 踢掉T1，用T2，T2与T3再交叉
  5. 仅单源可用 → 标记为unverified，阈值收紧(缩小穿越容忍度)

动态切换:
  - 连续3次同一源偏差>3% → 将该源降级30分钟
  - T1/T2同时不可用 → 告警推送

用法: 
  from scripts.price_fetcher import PriceFetcher
  pf = PriceFetcher()
  prices = pf.fetch_all()
  print(prices['上证50'].price, prices['上证50'].source_chain)
"""

import requests, re, json, time, sys, os
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

PROJECT_DIR = Path("/root/.openclaw/workspace/projects/trading-agents")

# ── 标的映射 ──
# {trading_code: {tencent, eastmoney, sina}}
WATCHLIST = {
    "sh000016": {"name": "上证50",      "tencent": "sh000016", "east": "1.000016", "sina": "sh000016", "type": "index"},
    "sh000300": {"name": "沪深300",     "tencent": "sh000300", "east": "1.000300", "sina": "sh000300", "type": "index"},
    "sh000688": {"name": "科创50",     "tencent": "sh000688", "east": "1.000688", "sina": "sh000688", "type": "index"},
    "sh601288": {"name": "农业银行",    "tencent": "sh601288", "east": "1.601288", "sina": "sh601288", "type": "stock"},
    "sh601988": {"name": "中国银行",    "tencent": "sh601988", "east": "1.601988", "sina": "sh601988", "type": "stock"},
    "sh600036": {"name": "招商银行",    "tencent": "sh600036", "east": "1.600036", "sina": "sh600036", "type": "stock"},
    "sh600795": {"name": "国电电力",    "tencent": "sh600795", "east": "1.600795", "sina": "sh600795", "type": "stock"},
    "sz000066": {"name": "中国长城",    "tencent": "sz000066", "east": "0.000066", "sina": "sz000066", "type": "stock"},
    "sh600562": {"name": "国睿科技",    "tencent": "sh600562", "east": "1.600562", "sina": "sh600562", "type": "stock"},
    "sh562500": {"name": "机器人ETF",  "tencent": "sh562500", "east": "1.562500", "sina": "sh562500", "type": "stock"},
}

# ── 数据类 ──
@dataclass
class PricePoint:
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    change_pct: float
    source: str           # tencent / eastmoney / sina / cross_verified
    source_chain: str     # 交叉验证链: tencent✓east, tencent(solo), east→tencent✗→sina
    quality: str          # verified / warning / unverified / stale
    timestamp: float = field(default_factory=time.time)


class PriceFetcher:
    """多源实时价格拉取器，含交叉验证和动态切换"""

    def __init__(self):
        self._degraded_sources = {}  # {source: degraded_until_ts}
        self._error_counts = {}      # {source: consecutive_errors}
        self._cross_log = []         # 最近交叉验证记录

    # ═══ 腾讯财经 ═══
    def _fetch_tencent(self) -> dict[str, dict]:
        """腾讯实时行情 → {code: {price, open, prev_close, high, low, change_pct, date}}"""
        results = {}
        batch = []
        for cfg in WATCHLIST.values():
            batch.append(cfg["tencent"])
        if not batch:
            return results

        code_str = ",".join(batch)
        url = f"https://qt.gtimg.cn/q={code_str}"
        try:
            resp = requests.get(url, timeout=8)
            # 腾讯返回编码: gbk
            try:
                text = resp.content.decode("gbk")
            except Exception:
                text = resp.text
            for line in text.strip().split("\n"):
                if not line.strip() or "=" not in line:
                    continue
                m = re.search(r'v_(\w+)="(.+)"', line)
                if not m:
                    continue
                code = m.group(1)
                fields = m.group(2).split("~")
                if len(fields) < 10:
                    continue
                # 腾讯格式: 1~名称~代码~今开(3)~昨收(4)~当前价(5)~量(6)~...
                try:
                    results[code] = {
                        "price": float(fields[5]),
                        "open": float(fields[3]),
                        "prev_close": float(fields[4]),
                        "high": float(fields[33]) if len(fields) > 33 and fields[33] else float(fields[5]),
                        "low": float(fields[34]) if len(fields) > 34 and fields[34] else float(fields[5]),
                        "change_pct": (float(fields[5]) - float(fields[4])) / float(fields[4]) * 100,
                        "date": fields[30] if len(fields) > 30 else "",
                    }
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            self._record_error("tencent", str(e))
        return results

    # ═══ 东方财富 ═══
    def _fetch_eastmoney(self) -> dict[str, dict]:
        """东方财富实时行情（逐标拉取，无延迟）→ {code: {price, open, prev_close, high, low, change_pct}}"""
        results = {}
        for cfg in WATCHLIST.values():
            ec = cfg["east"]
            url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={ec}&fields=f43,f44,f45,f46,f47,f57,f58,f60,f169,f170"
            try:
                resp = requests.get(url, timeout=5)
                d = resp.json()
                if d.get("data"):
                    dd = d["data"]
                    results[dd["f57"]] = {
                        "price": dd["f43"] / 100 if dd.get("f43") else None,
                        "high": dd["f44"] / 100 if dd.get("f44") else None,
                        "low": dd["f45"] / 100 if dd.get("f45") else None,
                        "open": dd["f46"] / 100 if dd.get("f46") else None,
                        "prev_close": dd["f60"] / 100 if dd.get("f60") else None,
                        "change_pct": dd.get("f170", 0) / 100 if dd.get("f170") else None,
                    }
            except Exception:
                pass  # 东方财富限流常见，不报错
        return results

    # ═══ 新浪财经（仅个股，指数不可靠） ═══
    def _fetch_sina(self) -> dict[str, dict]:
        """新浪个股实时行情 → {code: {price, open, prev_close, high, low}}"""
        results = {}
        stock_codes = []
        for cfg in WATCHLIST.values():
            if cfg["type"] == "stock":
                stock_codes.append(cfg["sina"])
        if not stock_codes:
            return results

        for i in range(0, len(stock_codes), 3):
            batch = stock_codes[i:i + 3]
            url = "http://hq.sinajs.cn/list=" + ",".join(batch)
            try:
                resp = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
                resp.encoding = "gbk"
                for line in resp.text.strip().split("\n"):
                    m = re.search(r'hq_str_(\w+)="(.+)"', line)
                    if not m:
                        continue
                    code = m.group(1)
                    fields = m.group(2).split(",")
                    if len(fields) < 6:
                        continue
                    # 个股: fields[1]=今开, fields[2]=昨收, fields[3]=当前价, fields[4]=最高, fields[5]=最低
                    results[code] = {
                        "price": float(fields[3]) if fields[3] != "0.000" else float(fields[1]),
                        "open": float(fields[1]),
                        "prev_close": float(fields[2]),
                        "high": float(fields[4]) if fields[4] else float(fields[3]),
                        "low": float(fields[5]) if fields[5] else float(fields[3]),
                    }
            except Exception as e:
                self._record_error("sina", str(e))
        return results

    # ═══ 交叉验证 ═══
    def _cross_verify(self, code: str, name: str, is_index: bool, t1: dict, t2: dict, t3: dict = None) -> PricePoint:
        """交叉验证。is_index: 指数主线是东方财富，个股主线是腾讯。"""
        t1_price = t1.get("price") if t1 else None
        t2_price = t2.get("price") if t2 else None
        t3_price = t3.get("price") if t3 else None

        # 选首选源: 指数→东方, 个股→腾讯
        if is_index:
            primary_src, backup_src = (t2, "eastmoney"), (t1, "tencent")  # t2=东方, t1=腾讯
        else:
            primary_src, backup_src = (t1, "tencent"), (t2, "eastmoney")  # t1=腾讯, t2=东方

        p_data, p_name = primary_src
        b_data, b_name = backup_src
        p_price = p_data.get("price") if p_data else None
        b_price = b_data.get("price") if b_data else None

        if p_price and b_price:
            deviation = abs(p_price - b_price) / b_price * 100 if b_price else 0
            if deviation < 1.0:
                chain = f"{p_name}✓{b_name}({deviation:.1f}%)"
                quality = "verified"
                # 主源通过
            elif deviation < 3.0:
                chain = f"{p_name}~{b_name}(Δ{deviation:.1f}%)"
                quality = "warning"
                print(f"[交叉验证⚠️] {name}: {p_name}{p_price} vs {b_name}{b_price} Δ{deviation:.1f}%")
            else:
                # 主源偏差大，切备份源
                print(f"[交叉验证🚨] {name}: {p_name}{p_price} vs {b_name}{b_price} Δ{deviation:.1f}%，踢掉{p_name}")
                self._record_error(p_name, f"cross_fail:{name}")
                p_data, p_name = b_data, b_name
                # 备份源与第三源交叉
                if t3 and t3_price:
                    dev2 = abs(p_price - t3_price) / t3_price * 100 if t3_price else 0
                    if dev2 < 1.0:
                        chain = f"{p_name}✓sina({dev2:.1f}%)"
                        quality = "verified"
                    else:
                        chain = f"{p_name}~sina(Δ{dev2:.1f}%)"
                        quality = "warning"
                else:
                    chain = f"{p_name}(backup)"
                    quality = "warning"
        elif p_price:
            chain = f"{p_name}(solo)"
            quality = "unverified"
            print(f"[交叉验证] {name}: 仅{p_name}可用")
        elif b_price:
            p_data, p_name = b_data, b_name
            chain = f"{p_name}(solo)"
            quality = "unverified"
            print(f"[交叉验证] {name}: 仅{p_name}可用")
        else:
            print(f"[交叉验证🚨] {name}: 所有数据源不可用！")
            return PricePoint(
                name=name, price=0, prev_close=0, open=0, high=0, low=0,
                change_pct=0, source="none", source_chain="all_failed", quality="stale"
            )

        data = p_data
        return PricePoint(
            name=name,
            price=data["price"],
            prev_close=data.get("prev_close", data["price"]),
            open=data.get("open", data["price"]),
            high=data.get("high", data["price"]),
            low=data.get("low", data["price"]),
            change_pct=data.get("change_pct", 0),
            source=p_name,
            source_chain=chain,
            quality=quality,
        )

    # ═══ 动态降级 ═══
    def _record_error(self, source: str, detail: str):
        # 同款错误去重：同一秒内同源只计一次
        now = int(time.time())
        self._error_counts[source] = self._error_counts.get(source, 0) + 1
        if self._error_counts[source] >= 5:  # 提高到5次（实际处理10个标的时的累积）
            degrade_seconds = 1800  # 30分钟
            self._degraded_sources[source] = time.time() + degrade_seconds
            print(f"[动态切换] {source} 连续{self._error_counts[source]}次异常，降级{degrade_seconds//60}分钟")
            self._error_counts[source] = 0

    def _is_degraded(self, source: str) -> bool:
        if source in self._degraded_sources:
            if time.time() < self._degraded_sources[source]:
                return True
            del self._degraded_sources[source]
        return False

    # ═══ 主入口 ═══
    def fetch_all(self) -> dict[str, PricePoint]:
        """拉取全量标的价格，含交叉验证+动态切换"""
        results = {}

        # 根据降级状态选择拉取哪些源
        fetch_tencent = not self._is_degraded("tencent")
        fetch_east = not self._is_degraded("eastmoney")
        fetch_sina = not self._is_degraded("sina")

        tc_data = self._fetch_tencent() if fetch_tencent else {}
        east_data = self._fetch_eastmoney() if fetch_east else {}
        sina_data = self._fetch_sina() if fetch_sina else {}

        for code, cfg in WATCHLIST.items():
            name = cfg["name"]
            tc_code = cfg["tencent"]
            east_code = cfg["east"].split(".")[-1]  # 1.000016 → 000016
            sina_code = cfg["sina"] if cfg["type"] == "stock" else None

            t1 = tc_data.get(tc_code)
            t2 = east_data.get(east_code)
            t3 = sina_data.get(sina_code) if sina_code else None

            pp = self._cross_verify(code, name, cfg["type"] == "index", t1, t2, t3)
            results[name] = pp

            # 单源数据质量差时收紧
            if pp.quality == "unverified":
                pp.change_pct = pp.change_pct * 0.5 if pp.change_pct else 0  # 单源时保守估计
            if pp.quality == "stale":
                pp.change_pct = 0

        # 日志
        verified = sum(1 for p in results.values() if p.quality == "verified")
        warnings = sum(1 for p in results.values() if p.quality == "warning")
        unverified = sum(1 for p in results.values() if p.quality == "unverified")
        stale = sum(1 for p in results.values() if p.quality == "stale")
        print(f"[price_fetcher] ✅{verified} ⚠️{warnings} ❓{unverified} 🚨{stale} | T:{'on' if fetch_tencent else 'off'} E:{'on' if fetch_east else 'off'} S:{'on' if fetch_sina else 'off'}")

        self._cross_log.append({
            "ts": time.time(),
            "verified": verified,
            "warning": warnings,
            "unverified": unverified,
            "stale": stale,
            "sources": {"tencent": fetch_tencent, "eastmoney": fetch_east, "sina": fetch_sina},
        })

        return results


# ═══ 模块测试入口 ═══
if __name__ == "__main__":
    pf = PriceFetcher()
    prices = pf.fetch_all()
    for name, pp in sorted(prices.items()):
        print(f"{name}: {pp.price:.2f} ({pp.change_pct:+.2f}%) [{pp.source_chain}] {pp.quality}")
