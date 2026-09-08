#!/usr/bin/env python3
"""TimesFM calibration backtest on Jin Qiao watchlist.
Outputs per-stock JSON + summary table to stdout.

v3.0 升级（2026-09-08，托董确认）：
- 模型 2.5-200M → 3.0（google/timesfm-3.0-pytorch，Non-Commercial License，金桥自用）
- 必须用 venv 解释器运行: /root/.venv-timesfm3/bin/python（系统 python3 仍是 timesfm 2.0.1 无 3.0 类）
- 留出回测对照结论: 20d MAPE 7.19%→6.44%，方向命中 39.2%→53.6%（p=0.009），见 logs/timesfm_3v2_20260908/RESULTS.md
- JSON schema 与 2.5 版完全一致（windows_detail 字段名不变），下游 generate_trade_signals /
  style_rotation_signals / check_data_timeliness 零改动；仅新增顶层 "model_repo" 键
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/data/cache"
OUT = "/root/.openclaw/workspace/projects/trading-agents/logs/timesfm_calibration"
MODEL_REPO = "google/timesfm-3.0-pytorch"

WATCHLIST = [
    ("000016.SH", "上证50"), ("000300.SH", "沪深300"), ("000688.SH", "科创50"),
    ("601288.SH", "农业银行"), ("601988.SH", "中国银行"), ("600036.SH", "招商银行"),
    ("600795.SH", "国电电力"), ("000066.SZ", "中国长城"), ("600562.SH", "国睿科技"),
]

HORIZONS = [5, 10, 20]
SLIDE = 60
CTX = 256

def resolve_csv(code):
    # v3: 穷举所有后缀组合匹配 data/cache/*-daily.csv
    prefix = code.replace(".SH", "").replace(".SS", "").replace(".SZ", "")
    try_names = sorted(os.listdir(CACHE_DIR))
    for try_name in reversed(try_names):
        if not try_name.startswith(prefix) or not try_name.endswith("-daily.csv"):
            continue
        path = os.path.join(CACHE_DIR, try_name)
        if os.path.exists(path):
            return path
    for d in [CACHE_DIR, CACHE_DIR.replace("data/cache", "logs/cache")]:
        if not os.path.isdir(d):
            continue
        for try_name in os.listdir(d):
            if try_name.startswith(prefix) and "daily" not in try_name and try_name.endswith(".csv"):
                return os.path.join(d, try_name)
    return None

def main():
    try:
        from timesfm import TimesFM3Forecaster
    except ImportError:
        print("FATAL: 当前解释器无 TimesFM3Forecaster。请用 /root/.venv-timesfm3/bin/python 运行本脚本。",
              file=sys.stderr)
        sys.exit(2)

    os.makedirs(OUT, exist_ok=True)

    print(f"Loading {MODEL_REPO} ...", flush=True)
    t_load = time.time()
    forecaster = TimesFM3Forecaster.from_pretrained(MODEL_REPO)
    print(f"Model ready ({time.time()-t_load:.0f}s).\n", flush=True)

    t_total_start = time.time()

    for code, name in WATCHLIST:
        csv_path = resolve_csv(code)
        if not csv_path:
            print(f"SKIP {name}: no cache", flush=True)
            continue

        df = pd.read_csv(csv_path)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date")
        close = df["Close"].values
        dates = df["Date"].values

        if len(close) < CTX + 20:
            print(f"SKIP {name}: too short ({len(close)}d)", flush=True)
            continue

        windows = []
        t_stock = time.time()
        for start in range(0, len(close) - CTX - 20, SLIDE):
            ctx_end_idx = start + CTX - 1
            ctx = close[start:start + CTX]
            actual = close[start + CTX : start + CTX + 20]
            w = {"ctx_end": str(dates[ctx_end_idx])[:10], "last_close": float(ctx[-1])}

            if len(actual) < HORIZONS[-1]:
                break
            out = forecaster.predict(context=ctx.astype(np.float32),
                                     horizon=HORIZONS[-1], return_quantiles=True)
            point = np.array(out.forecast).flatten()          # (20,)
            quant = np.array(out.quantiles)                   # (20,9)，索引4=median，0=p10，8=p90
            for h in HORIZONS:
                p, a = float(point[h - 1]), float(actual[h - 1])
                w[f"fc_{h}d"] = round(p, 2)
                w[f"actual_{h}d"] = round(a, 2)
                err = (p - a) / a * 100
                w[f"err_{h}d%"] = round(float(err), 2)
                w[f"p10_{h}d"] = round(float(quant[h - 1, 0]), 2)
                w[f"p50_{h}d"] = round(float(quant[h - 1, 4]), 2)
                w[f"p90_{h}d"] = round(float(quant[h - 1, 8]), 2)
            windows.append(w)

        # Summary
        summary = {}
        for h in HORIZONS:
            errs_abs = [abs(w[f"err_{h}d%"]) for w in windows if f"err_{h}d%" in w]
            biases = [w[f"err_{h}d%"] for w in windows if f"err_{h}d%" in w]
            coverages = []
            for w in windows:
                if f"p10_{h}d" not in w:
                    continue
                a = w[f"actual_{h}d"]
                coverages.append(1 if w[f"p10_{h}d"] <= a <= w[f"p90_{h}d"] else 0)
            summary[f"mape_{h}d"] = round(np.mean(errs_abs), 2) if errs_abs else None
            summary[f"bias_{h}d"] = round(np.mean(biases), 2) if biases else None
            summary[f"ci80_coverage_{h}d"] = round(np.mean(coverages), 2) if coverages else None

        elapsed = time.time() - t_stock
        result = {"code": code, "name": name, "model_repo": MODEL_REPO,
                  "windows": len(windows), "windows_detail": windows, "summary": summary}
        with open(os.path.join(OUT, f"{code}.json"), "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"{name:<8} {len(windows):>2} windows ({elapsed:.0f}s) | "
              f"MAPE 5d={summary.get('mape_5d','-')}% 10d={summary.get('mape_10d','-')}% 20d={summary.get('mape_20d','-')}% | "
              f"CI80={summary.get('ci80_coverage_20d','-')} Bias={summary.get('bias_20d','-')}%", flush=True)

    total_elapsed = time.time() - t_total_start
    print(f"\nTotal: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)", flush=True)

    print("\n" + "=" * 80)
    print(f"{'标的':<8} {'MAPE5d':>7} {'MAPE10d':>7} {'MAPE20d':>7} {'CI80_20d':>8} {'Bias20d':>8} 评价")
    print("-" * 80)
    for code, name in WATCHLIST:
        fname = os.path.join(OUT, f"{code}.json")
        if not os.path.exists(fname):
            continue
        r = json.load(open(fname))
        s = r["summary"]
        m5 = s.get("mape_5d", "-")
        m10 = s.get("mape_10d", "-")
        m20 = s.get("mape_20d", "-")
        ci = s.get("ci80_coverage_20d", "-")
        bias = s.get("bias_20d", "-")
        if isinstance(m20, (int, float)):
            if m20 < 3: rating = "★★★ 可用"
            elif m20 < 8: rating = "★★ 参考"
            else: rating = "★ 噪声"
        else:
            rating = "-"
        print(f"{name:<8} {str(m5):>7} {str(m10):>7} {str(m20):>7} {str(ci):>8} {str(bias):>8}  {rating}")
    print("-" * 80)

if __name__ == "__main__":
    main()
