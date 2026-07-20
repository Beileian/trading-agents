#!/usr/bin/env python3
"""TimesFM zero-shot on all Jin Qiao watchlist stocks."""
import os, sys, time, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/logs/cache"

# Jin Qiao watchlist (from generate_trade_signals.py PREMARKET_TENCENT)
WATCHLIST = [
    ("000016.SH", "上证50", True),
    ("000300.SH", "沪深300", True),
    ("000688.SH", "科创50", True),
    ("601288.SH", "农业银行", False),
    ("601988.SH", "中国银行", False),
    ("600036.SH", "招商银行", False),
    ("600795.SH", "国电电力", False),
    ("000066.SZ", "中国长城", False),
    ("600562.SH", "国睿科技", False),
]

# Map .SH -> .SS for Yahoo Finance cache files
def resolve_csv(code):
    """Map watchlist code to YFin cache file."""
    for suffix in [code, code.replace(".SH", ".SS")]:
        candidates = sorted(
            [f for f in os.listdir(CACHE_DIR) if f.startswith(suffix) and f.endswith(".csv") and "daily" not in f],
            reverse=True
        )
        if candidates:
            return os.path.join(CACHE_DIR, candidates[0])
    return None

# Pre-check available
available = []
unavailable = []
for code, name, is_index in WATCHLIST:
    path = resolve_csv(code)
    if path:
        available.append((code, name, is_index, path))
    else:
        unavailable.append((code, name))

print("=" * 60)
print("TimesFM 2.5 — 金桥关注标的全量测试")
print("=" * 60)
print(f"\n可用标的: {len(available)}/{len(WATCHLIST)}")
if unavailable:
    for code, name in unavailable:
        print(f"  ✗ {name} ({code}) — 无缓存")

# --- Load TimesFM ---
print("\n⏳ Loading TimesFM 2.5 (200M) ...")
t0 = time.time()
import torch
import timesfm

torch.set_float32_matmul_precision("high")
model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
    "google/timesfm-2.5-200m-pytorch"
)
model.compile(
    timesfm.ForecastConfig(
        max_context=1024,
        max_horizon=60,
        normalize_inputs=True,
        use_continuous_quantile_head=True,
        force_flip_invariance=True,
        infer_is_positive=True,
        fix_quantile_crossing=True,
    )
)
print(f"   Loaded in {time.time() - t0:.1f}s")

# --- Run on each stock ---
HORIZONS = [5, 10, 20]
results = []

for code, name, is_index, csv_path in available:
    df = pd.read_csv(csv_path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")
    close = df["Close"].values

    if len(close) < 256:
        ctx_len = len(close) - 20
    else:
        ctx_len = 256

    ctx = close[-ctx_len:]
    last_close = ctx[-1]

    row = {"code": code, "name": name, "type": "指数" if is_index else "个股", "last": last_close, "days": len(close)}

    for h in HORIZONS:
        point, quantile = model.forecast(horizon=h, inputs=[ctx])
        if hasattr(point, 'numpy'):
            point = point.numpy()
        if hasattr(quantile, 'numpy'):
            quantile = quantile.numpy()
        point = np.array(point).flatten()
        quantile = np.array(quantile).squeeze()

        row[f"fc_{h}d"] = round(float(point[-1]), 2)
        # quantile: [mean, p10, p20, ..., p90]
        row[f"p10_{h}d"] = round(float(quantile[-1, 1]), 2)
        row[f"p50_{h}d"] = round(float(quantile[-1, 5]), 2)
        row[f"p90_{h}d"] = round(float(quantile[-1, 9]), 2)

        # Return calc
        ret = (point[-1] - last_close) / last_close * 100
        row[f"ret_{h}d%"] = round(float(ret), 2)

    results.append(row)
    print(f"  ✓ {name} ({code}) — {len(close)}d data, last={last_close:.2f}")

# --- Print summary table ---
print("\n" + "=" * 80)
print(f"{'标的':<10} {'类型':<6} {'最新价':>10} {'5d预测':>10} {'5d涨跌%':>8} {'P10-P90':>15} {'10d预测':>10} {'10d涨跌%':>8} {'20d预测':>10} {'20d涨跌%':>8}")
print("-" * 80)

for r in results:
    p10_5 = r.get("p10_5d", 0)
    p90_5 = r.get("p90_5d", 0)
    band_5 = f"{p10_5:.0f}-{p90_5:.0f}"
    print(f"{r['name']:<10} {r['type']:<6} {r['last']:>10.2f} {r.get('fc_5d',0):>10.2f} {r.get('ret_5d%',0):>+7.2f}% {band_5:>15} {r.get('fc_10d',0):>10.2f} {r.get('ret_10d%',0):>+7.2f}% {r.get('fc_20d',0):>10.2f} {r.get('ret_20d%',0):>+7.2f}%")

print("-" * 80)

# --- Resource ---
import psutil
mem = psutil.Process().memory_info()
print(f"\n📈 Peak RSS: {mem.rss/1024/1024:.0f}MB")
