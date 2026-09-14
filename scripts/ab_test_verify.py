#!/usr/bin/env python3
"""ab_test_verify.py — A/B 试点次日验证回写 v1.1（2026-09-14 托董确认）
对 ab_test/results.jsonl 中每个报告日的趋势判断，用 Sina 实际收盘涨跌幅机械对账，
结果回写到该日条目的 "check" 字段。禁止凭印象报命中率。

判定规则:
  看涨: pct > +0.05% 命中; pct < -0.05% 未中; 其间记 走平
  看跌: pct < -0.05% 命中; pct > +0.05% 未中; 其间记 走平
  震荡/中性/观望: |pct| <= 1.0% 命中, 否则未中

两种行情源:
  实时(默认/Sina): 当日 15:35 盘后跑今日条目，或显式传日期且该日=最近已收盘交易日
  缓存(CSV): --backfill 补跑历史条目，从 data/cache/{symbol}-daily.csv 取 D 日对 D-1 日收盘涨跌幅

用法: python3 scripts/ab_test_verify.py [YYYY-MM-DD] [--backfill]   # 默认最后一个无 check 的日期
退出码: 0=回写成功或无事可做(假日/已验证), 1=解析或行情获取失败
"""
import sys, os, re, json, time
import requests
import symbols_config

PROJ = "/root/.openclaw/workspace/projects/trading-agents"
OUT = f"{PROJ}/ab_test"
JSONL = f"{OUT}/results.jsonl"
FLAT_EPS = 0.05   # 走平容差 %
RANGE_BAND = 1.0  # 震荡带宽 %

MODEL_FILES = {"Flash": "analysis_deepseek-v4-flash_{d}.md", "M3": "analysis_MiniMax-M3_{d}.md"}


def parse_trends(path: str) -> dict:
    """从分析报告 md 提取 {symbol: 趋势判断}"""
    trends = {}
    cur = None
    for line in open(path, encoding="utf-8"):
        g = re.match(r"### (\S+) — (\S+)", line)
        if g:
            cur = g.group(1)
            continue
        if cur and "**趋势判断**" in line:
            d = re.search(r"\|\s*(看跌|看涨|中性|震荡|观望)[^\|]*\|", line)
            if d:
                trends[cur] = d.group(1)
    return trends


def fetch_pct() -> dict:
    """Sina 实时 → {symbol: {prev, close, pct}}；盘后/次日开盘前取到即最新收盘"""
    codes = [symbols_config.TICKER_SINA_MAP[s] for s, _, _ in symbols_config.SYMBOLS]
    resp = requests.get(
        f"https://hq.sinajs.cn/list={','.join(codes)}",
        headers={"Referer": "https://finance.sina.com.cn"},
        timeout=15,
    )
    resp.encoding = "gbk"
    by_sina = {}
    for line in resp.text.splitlines():
        m = re.match(r"var hq_str_(\w+)=\"([^\"]+)\"", line.strip())
        if not m or len(m.group(2).split(",")) < 4:
            continue
        data = m.group(2).split(",")
        prev, price = float(data[2]), float(data[3])
        if prev <= 0:
            continue
        by_sina[m.group(1)] = {"prev": prev, "close": price, "pct": round((price - prev) / prev * 100, 3)}
    return {s: by_sina[c] for s, _, _ in symbols_config.SYMBOLS
            for c in [symbols_config.TICKER_SINA_MAP[s]] if c in by_sina}


def grade(trend: str, pct: float) -> str:
    if trend == "看涨":
        return "命中" if pct > FLAT_EPS else ("未中" if pct < -FLAT_EPS else "走平")
    if trend == "看跌":
        return "命中" if pct < -FLAT_EPS else ("未中" if pct > FLAT_EPS else "走平")
    return "命中" if abs(pct) <= RANGE_BAND else "未中"


def cache_pct(date: str) -> dict:
    """历史补跑: 从日线缓存 CSV 取 {symbol: {prev, close, pct}}，D 日收盘 vs D-1 日收盘"""
    import csv
    cache = f"{PROJ}/data/cache"
    out = {}
    for symbol, _, _ in symbols_config.SYMBOLS:
        fpath = f"{cache}/{symbol}-daily.csv"
        if not os.path.exists(fpath):  # 缓存命名不一致: 部分股票用 .SS 后缀
            fpath = f"{cache}/{symbol.replace('.SH', '.SS')}-daily.csv"
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath) as f:
                rows = [r for r in csv.DictReader(f) if r.get("Close")]
        except Exception:
            continue
        dates = [r["Date"] for r in rows]
        if date not in dates:
            continue
        i = dates.index(date)
        if i == 0:
            continue
        prev, close = float(rows[i - 1]["Close"]), float(rows[i]["Close"])
        if prev <= 0:
            continue
        out[symbol] = {"prev": prev, "close": close, "pct": round((close - prev) / prev * 100, 3)}
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    backfill = "--backfill" in sys.argv
    date = args[0] if args else ""
    rows = [json.loads(l) for l in open(JSONL, encoding="utf-8") if l.strip()]
    if not rows:
        print("results.jsonl 为空"); return 1
    if not date:
        cand = [r for r in rows if "check" not in r]
        if not cand:
            print("所有条目均已验证，跳过"); return 0
        date = cand[-1]["date"]
    target = next((r for r in rows if r["date"] == date), None)
    if target is None:
        print(f"results.jsonl 无 {date} 条目"); return 1

    quotes = None
    if backfill:
        quotes = cache_pct(date)
    else:
        today = time.strftime("%Y-%m-%d")
        if date != today:
            print(f"[realtime] 待验证 {date} ≠ 今日 {today}，历史日期请用 --backfill"); return 0
        if time.strftime("%H%M") < "1530":
            print(f"[realtime] 未到收盘缓冲 15:30，跳过 {date}"); return 0
        quotes = fetch_pct()
    if not quotes:
        print(f"{date} 无可用行情（缓存缺日期？）"); return 1
    check = {"verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rule": f"flat±{FLAT_EPS}% range≤{RANGE_BAND}%", "quote": quotes, "models": {}}
    details = {}
    for label, fname in MODEL_FILES.items():
        path = f"{OUT}/{fname.format(d=date)}"
        if not os.path.exists(path):
            continue
        trends = parse_trends(path)
        detail, n_hit, n_miss, n_flat = {}, 0, 0, 0
        for sym, trend in trends.items():
            q = quotes.get(sym)
            if not q:
                detail[sym] = {"trend": trend, "result": "无行情"}
                continue
            r = grade(trend, q["pct"])
            detail[sym] = {"trend": trend, "pct": q["pct"], "result": r}
            n_hit += r == "命中"; n_miss += r == "未中"; n_flat += r == "走平"
        details[label] = detail
        check["models"][label] = {"hit": n_hit, "miss": n_miss, "flat": n_flat,
                                  "total": len(detail), "score": f"{n_hit}/{len(detail)}"}
    if not details:
        print(f"{date} 无分析报告可解析，跳过"); return 0
    check["details"] = details

    for i, r in enumerate(rows):
        if r["date"] == date:
            rows[i] = {**r, "check": check}
    tmp = JSONL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    os.replace(tmp, JSONL)
    for label in details:
        c = check["models"][label]
        print(f"  {label}: {c['score']} (未中{c['miss']} 走平{c['flat']})")
    print(f"已回写 check 字段: {date} → {JSONL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
