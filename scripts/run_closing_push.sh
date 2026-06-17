#!/bin/bash
# ============================================================================
# A股收盘复盘 — 一站式执行+推送脚本（含降级兜底）
# 用法: ./run_closing_push.sh
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_DIR="$PROJECT_DIR/reports"
PUSH_SCRIPT="$SCRIPT_DIR/send_to_dingtalk.py"

# 失败告警
trap 'exit_code=$?; echo "# ⚠️ 收盘复盘异常\n\n脚本 exit=$exit_code\n时间: $(TZ=Asia/Shanghai date +%Y-%m-%d\\ %H:%M:%S)" | python3 "$PUSH_SCRIPT" 2>/dev/null' ERR

echo "=== A股收盘复盘 $DATE_STR ==="

# 步骤1: 运行收盘复盘（先跑→获取准确收盘价→生成快照JSON）
echo "[1/3] 运行收盘复盘..."
/usr/bin/python3 "$SCRIPT_DIR/closing_review.py" 2>&1 || {
    echo "[WARN] 收盘复盘脚本失败，使用降级推送"
}

# 步骤2: 更新虚拟盘收盘净值（从复盘快照JSON读取收盘价）
echo "[2/3] 虚拟盘收盘更新..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" close "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘收盘更新失败"

# 步骤3: 推送（含降级）
REVIEW_FILE="$REPORT_DIR/closing_review_${DATE_TAG}.md"
PAPER_STATE="$REPORT_DIR/paper_state.json"

if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then
    echo "[3/3] 复盘报告推送中..."
    cat "$REVIEW_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "[3/3] 复盘文件缺失，推送降级简报..."
    # 降级方案：推送简单收盘提醒
    cat << EOF | python3 "$PUSH_SCRIPT"
# 📉 A股收盘复盘 · ${DATE_STR}

⚠️ 复盘脚本未能生成报告。

请手动检查：
- closing_review.py 执行日志
- API key 是否有效
- 网络连接是否正常

*降级推送 | 认知闭环 v1*
EOF
fi

echo "=== 完成 ==="
