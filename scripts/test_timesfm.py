#!/usr/bin/env python3
"""TimesFM zero-shot quick test on Jin Qiao stock data."""
import os, sys, time, json, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

# --- 1. Select a stock ---
CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/logs/cache"
stocks = [
    ("600519.SS", "贵州茅台"),
    ("000858.SZ", "五粮液"),
    ("000001.SZ", "平安银行"),
    ("601988.SH", "中国银行"),
]

# Pick first available
csv_path = None
stock_code = None
stock_name = None
for code, name in stocks:
    candidates = [f for f in os.listdir(CACHE_DIR) if f.startswith(code)]
    if candidates:
        csv_path = os.path.join(CACHE_DIR, sorted(candidates)[-1])
        stock_code = code
        stock_name = name
        break

if not csv_path:
    print("No cached stock data found")
    sys.exit(1)

print(f"📊 Loading: {stock_name} ({stock_code})")
df = pd.read_csv(csv_path)
df["Date"] = pd.to_datetime(df["Date"])
df = df.sort_values("Date")

close = df["Close"].values
print(f"   Data points: {len(close)}")
print(f"   Date range: {df['Date'].min().date()} ~ {df['Date'].max().date()}")

# --- 2. Load TimesFM ---
print("\n⏳ Loading TimesFM 2.5 (200M) ...")
t0 = time.time()
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
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
load_time = time.time() - t0
print(f"   Loaded in {load_time:.1f}s")

# --- 3. Forecast ---
# Use last 128~256 trading days as context, predict 5/10/20 days
context_lens = [128, 256]
horizons = [5, 10, 20]

print("\n🔮 Running forecasts...")
for ctx_len in context_lens:
    if len(close) < ctx_len + 10:
        continue
    ctx = close[-ctx_len:]
    for h in horizons:
        t1 = time.time()
        point, quantile = model.forecast(
            horizon=h,
            inputs=[ctx],
        )
        elapsed = time.time() - t1
        if hasattr(point, 'numpy'):
            point = point.numpy()
        point = np.array(point).flatten()
        if hasattr(quantile, 'numpy'):
            quantile = quantile.numpy()
        quantile = np.array(quantile).squeeze()
        # quantile shape: (horizon, 10) -> [mean, p10, p20, ..., p90]
        p10 = quantile[:, 1] if quantile.ndim > 1 else None
        p50 = quantile[:, 5] if quantile.ndim > 1 else None
        p90 = quantile[:, 9] if quantile.ndim > 1 else None
        
        print(f"\n   Context={ctx_len}d, Horizon={h}d ({elapsed:.1f}s)")
        print(f"   Last close: {ctx[-1]:.2f}")
        print(f"   Point forecast (day {h}): {point[-1]:.2f}")
        if p10 is not None:
            print(f"   P10 → P50 → P90 (day {h}): {p10[-1]:.2f} → {p50[-1]:.2f} → {p90[-1]:.2f}")

# --- 4. Resource summary ---
import psutil
mem = psutil.Process().memory_info()
print(f"\n📈 Resource: RSS={mem.rss/1024/1024:.0f}MB, VMS={mem.vms/1024/1024:.0f}MB")
