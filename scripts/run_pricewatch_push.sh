#!/bin/bash
# ============================================================================
# 盘中价格穿越预警 — 条件推送脚本
# 用法: ./run_pricewatch_push.sh
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
PUSH_SCRIPT="/root/.openclaw/workspace/projects/overseas-morning-brief/scripts/send_to_dingtalk.py"

# 运行检测脚本
OUTPUT=$(/usr/bin/python3 /root/.openclaw/workspace/projects/trading-agents/scripts/price_watch_stdout.py 2>&1) || true

# 检查是否有告警
if echo "$OUTPUT" | grep -q "NO_ALERT"; then
    echo "无告警，不推送"
    exit 0
fi

if echo "$OUTPUT" | grep -q "## ⚡ 盘中价格预警"; then
    echo "发现预警，推送中..."
    echo "$OUTPUT" | python3 "$PUSH_SCRIPT"
else
    echo "无预警信号"
fi
