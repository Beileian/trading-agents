#!/usr/bin/env python3
"""check_data_timeliness.py — 检查 TimesFM 校准数据时效性

检查 logs/timesfm_calibration/*.json 中所有标的的最新窗口 ctx_end，
若任一标的 ctx_end 距今超过14天，则时效性不合格。

用法:
  python3 rubrics/check_data_timeliness.py <report_file>
输出: JSON {pass, score, max_age_days, stale_stocks}
exit code: 0=通过, 1=不通过
"""

import sys, os, json, glob
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
CAL_DIR = os.path.join(PROJECT_DIR, "logs", "timesfm_calibration")
MAX_AGE_DAYS = 14

if not os.path.exists(CAL_DIR):
    # TimesFM 校准目录不存在 → 通过（未启用该功能）
    print(json.dumps({"pass": True, "score": 10, "note": "TimesFM校准未启用，跳过"}), ensure_ascii=False)
    sys.exit(0)

cal_files = glob.glob(os.path.join(CAL_DIR, "*.json"))
if not cal_files:
    print(json.dumps({"pass": True, "score": 10, "note": "无校准文件"}))
    sys.exit(0)

today = datetime.now(TZ).date()
stale_stocks = []
max_age = 0

for cf in cal_files:
    try:
        with open(cf) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        continue
    
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
    
    if age > MAX_AGE_DAYS:
        name = d.get("name", os.path.basename(cf).replace(".json", ""))
        stale_stocks.append({"name": name, "ctx_end": ctx_end_str, "age_days": age})

if stale_stocks:
    score = max(0, 10 - min(max_age // 7, 10))  # 每超过7天扣1分，最多扣10分
    result = {
        "pass": False,
        "score": score,
        "max_age_days": max_age,
        "stale_stocks": stale_stocks,
        "note": f"TimesFM校准数据过期（最久{max_age}天），合成判断依赖过期数据",
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)
else:
    result = {
        "pass": True,
        "score": 10,
        "max_age_days": max_age,
        "note": "TimesFM校准数据在14天有效期内",
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)
