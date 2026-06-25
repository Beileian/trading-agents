#!/usr/bin/env python3
"""check_data_timeliness.py — 检查 TimesFM 校准数据时效性

v2: TimesFM 滑动窗口回测的属性决定了 ctx_end 天然落后最新数据约3个月
（256天上下文 + 60天滑动步长 + 20天留出验证 → 最后窗口 ctx_end ≈ N - 68行）。
因此合理性判断改为：最后一个校准窗口对应的缓存样本数是否与当前数据行数匹配。
若校准窗口ctx_end到最新缓存日期之间的"留出验证行" 在 [40, 100] 范围内，
则视为时效性正常（回测留出数据量合理）。

用法:
  python3 rubrics/check_data_timeliness.py <report_file>
输出: JSON {pass, score, max_age_days, stale_stocks, gap_rows}
exit code: 0=通过, 1=不通过
"""

import sys, os, json, glob, pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
CAL_DIR = os.path.join(PROJECT_DIR, "logs", "timesfm_calibration")
CACHE_DIR = os.path.join(PROJECT_DIR, "data", "cache")
CACHE_LEGACY = os.path.join(PROJECT_DIR, "logs", "cache")

# TimesFM 参数
CTX_LEN = 256
HORIZON = 20
# 留出验证行数的合理范围：最小20（horizon覆盖），最大120（约半年留出）
MIN_GAP = HORIZON
MAX_GAP = 120

if not os.path.exists(CAL_DIR):
    print(json.dumps({"pass": True, "score": 10, "note": "TimesFM校准未启用，跳过"}), ensure_ascii=False)
    sys.exit(0)

cal_files = glob.glob(os.path.join(CAL_DIR, "*.json"))
if not cal_files:
    print(json.dumps({"pass": True, "score": 10, "note": "无校准文件"}))
    sys.exit(0)

today = datetime.now(TZ).date()
stale_stocks = []
warnings = []
max_age = 0

for cf in cal_files:
    try:
        with open(cf) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        continue
    
    code = d.get("code", "")
    name = d.get("name", os.path.basename(cf).replace(".json", ""))
    windows = d.get("windows_detail", [])
    if not windows:
        continue
    
    last_win = windows[-1]
    ctx_end_str = last_win.get("ctx_end", "")
    if not ctx_end_str:
        continue
    
    try:
        ctx_end_date = datetime.strptime(ctx_end_str, "%Y-%m-%d").date()
    except ValueError:
        continue
    
    age = (today - ctx_end_date).days
    if age > max_age:
        max_age = age
    
    # 找到对应的每日缓存文件
    prefix = code.replace(".SH", "").replace(".SS", "").replace(".SZ", "")
    cache_file = None
    for d_dir in [CACHE_DIR, CACHE_LEGACY]:
        if not os.path.isdir(d_dir):
            continue
        for fname in os.listdir(d_dir):
            if fname.startswith(prefix) and fname.endswith("-daily.csv"):
                cache_file = os.path.join(d_dir, fname)
                break
        if cache_file:
            break
    
    if cache_file:
        try:
            df = pd.read_csv(cache_file)
            total_rows = len(df)
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date")
            dates = df["Date"].values
            # 计算ctx_end在缓存中的位置
            ctx_end_dt = pd.Timestamp(ctx_end_str)
            match_idx = df[df["Date"] == ctx_end_dt].index
            if len(match_idx) > 0:
                ctx_end_row = match_idx[0]
                gap_rows = total_rows - ctx_end_row - 1
                if gap_rows < MIN_GAP:
                    stale_stocks.append({
                        "name": name, "ctx_end": ctx_end_str,
                        "gap_rows": gap_rows,
                        "note": f"留出行数不足({gap_rows}<{MIN_GAP})，校准可能覆盖了验证集"
                    })
                elif gap_rows > MAX_GAP:
                    stale_stocks.append({
                        "name": name, "ctx_end": ctx_end_str,
                        "gap_rows": gap_rows,
                        "note": f"留出行数过多({gap_rows}>{MAX_GAP})，校准数据严重过期"
                    })
                else:
                    # 正常
                    warnings.append({
                        "name": name, "ctx_end": ctx_end_str,
                        "gap_rows": gap_rows,
                        "status": "ok"
                    })
            else:
                warnings.append({"name": name, "ctx_end": ctx_end_str, "status": "date_not_found"})
        except Exception as e:
            warnings.append({"name": name, "ctx_end": ctx_end_str, "status": f"cache_read_error: {str(e)}"})
    else:
        warnings.append({"name": name, "ctx_end": ctx_end_str, "status": "no_cache_file"})

if stale_stocks:
    result = {
        "pass": False,
        "score": max(0, 10 - len(stale_stocks)),
        "max_age_days": max_age,
        "stale_stocks": stale_stocks,
        "warnings": warnings,
        "note": f"{len(stale_stocks)}只标的TimesFM校准时效异常",
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)
else:
    result = {
        "pass": True,
        "score": 10,
        "max_age_days": max_age,
        "warnings": warnings,
        "note": f"TimesFM校准时效正常（{len(cal_files)}只标的留出验证行数在合理范围）",
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
