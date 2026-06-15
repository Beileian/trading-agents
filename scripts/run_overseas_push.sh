#!/bin/bash
# ============================================================================
# 外盘信号独立推送 — 8:05 BJT 执行
# 从 morning_brief 提取精简信号推送到「谈股论金奔富」群
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIGNAL_SCRIPT="/root/.openclaw/workspace/projects/overseas-morning-brief/scripts/extract_signal.py"
PUSH_SCRIPT="/root/.openclaw/workspace/projects/overseas-morning-brief/scripts/send_to_dingtalk.py"
SIGNAL_FILE="/root/.openclaw/workspace/projects/overseas-morning-brief/reports/overseas_signal_${DATE_STR}.md"

echo "=== 外盘信号推送 $DATE_STR ==="

# 步骤1: 提取外盘信号
echo "[1/2] 提取外盘信号..."
/usr/bin/python3 "$SIGNAL_SCRIPT" 2>&1 || {
    echo "[WARN] 外盘信号提取失败，跳过推送"
    exit 0
}

# 步骤2: 检查信号文件是否存在
if [ ! -f "$SIGNAL_FILE" ]; then
    echo "[WARN] 信号文件不存在，跳过推送"
    exit 0
fi

CONTENT=$(cat "$SIGNAL_FILE")
if [ -z "$CONTENT" ] || [ "$CONTENT" = "" ]; then
    echo "[WARN] 信号文件为空，跳过推送"
    exit 0
fi

echo "[2/2] 推送到钉钉群..."

# 构建推送内容
echo "# 隔夜外盘信号 · $DATE_STR" | python3 "$PUSH_SCRIPT"

# 推送正文（清理装饰图标）
sed '/^## /d' "$SIGNAL_FILE" \
  | sed 's/🌐//g; s/📊//g; s/📰//g; s/💡//g; s/🔥//g; s/⭐//g; s/⚠️//g; s/🎯//g; s/🧘//g' \
  | python3 "$PUSH_SCRIPT"

# 附加风险提示
echo "> *交易推荐+技术分析将于8:55推送。AI辅助分析，不构成投资建议*" | python3 "$PUSH_SCRIPT"

echo "=== 完成 ==="
