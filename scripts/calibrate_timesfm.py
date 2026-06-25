#!/usr/bin/env python3
"""TimesFM calibration backtest on Jin Qiao watchlist.
Outputs per-stock JSON + summary table to stdout.
"""
import os, sys, json, time, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/data/cache"
OUT = "/root/.openclaw/workspace/projects/trading-agents/logs/timesfm_calibration"

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
    # fallback: legacy YFin-data files in data/cache then logs/cache
    for d in [CACHE_DIR, CACHE_DIR.replace("data/cache", "logs/cache")]:
        if not os.path.isdir(d):
            continue
        for try_name in os.listdir(d):
            if try_name.startswith(prefix) and "daily" not in try_name and try_name.endswith(".csv"):
                return os.path.join(d, try_name)
    return None

def main():
    os.makedirs(OUT, exist_ok=True)

    print("Loading TimesFM 2.5 (200M) ...", flush=True)
    import torch
    import timesfm
    torch.set_float32_matmul_precision("high")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    model.compile(timesfm.ForecastConfig(
        max_context=1024, max_horizon=60, normalize_inputs=True,
        use_continuous_quantile_head=True, force_flip_invariance=True,
        infer_is_positive=True, fix_quantile_crossing=True,
    ))
    print("Model ready.\n", flush=True)

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

            for h in HORIZONS:
                if len(actual) < h:
                    break
                point, quantile = model.forecast(horizon=h, inputs=[ctx])
                point = np.array(point).flatten()
                quantile = np.array(quantile).squeeze()

                w[f"fc_{h}d"] = round(float(point[-1]), 2)
                w[f"actual_{h}d"] = round(float(actual[h-1]), 2)
                err = (point[-1] - actual[h-1]) / actual[h-1] * 100
                w[f"err_{h}d%"] = round(float(err), 2)
                if quantile.ndim > 1:
                    w[f"p10_{h}d"] = round(float(quantile[-1, 1]), 2)
                    w[f"p90_{h}d"] = round(float(quantile[-1, 9]), 2)
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
        result = {"code": code, "name": name, "windows": len(windows), "windows_detail": windows, "summary": summary}
        with open(os.path.join(OUT, f"{code}.json"), "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"{name:<8} {len(windows):>2} windows ({elapsed:.0f}s) | "
              f"MAPE 5d={summary.get('mape_5d','-')}% 10d={summary.get('mape_10d','-')}% 20d={summary.get('mape_20d','-')}% | "
              f"CI80={summary.get('ci80_coverage_20d','-')} Bias={summary.get('bias_20d','-')}%", flush=True)

    total_elapsed = time.time() - t_total_start
    print(f"\nTotal: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)", flush=True)

    # Final summary table
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
        # Rating
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
