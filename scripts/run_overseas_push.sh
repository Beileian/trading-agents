#!/bin/bash
# ============================================================================
# 外盘信号独立推送 — 8:05 BJT 执行（v2.0 自包含版）
# 不依赖 overseas-morning-brief 项目的脚本路径。
# overseas-morning-brief 只负责生成 morning_brief_YYYY-MM-DD.md。
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PUSH_SCRIPT="$SCRIPT_DIR/send_to_dingtalk.py"
SIGNAL_SCRIPT="$SCRIPT_DIR/extract_signal.py"
OVERSEAS_BRIEF="/root/.openclaw/workspace/projects/overseas-morning-brief/reports/morning_brief_${DATE_STR}.md"
SIGNAL_FILE="$PROJECT_DIR/reports/overseas_signal_${DATE_STR}.md"

echo "=== 外盘信号推送 $DATE_STR ==="

# 步骤1: 等待 morning_brief（外盘 cron 08:00 开始生成，最多等 120s）
MAX_WAIT=120
WAITED=0
while [ ! -f "$OVERSEAS_BRIEF" ] && [ $WAITED -lt $MAX_WAIT ]; do
    echo "  等待 morning_brief ... ${WAITED}s / ${MAX_WAIT}s"
    sleep 5
    WAITED=$((WAITED + 5))
done

if [ ! -f "$OVERSEAS_BRIEF" ]; then
    echo "[WARN] morning_brief 未生成 ($MAX_WAIT s超时)，使用降级模式"
fi

# 步骤2: 提取外盘信号（含降级逻辑）
echo "[1/2] 提取外盘信号..."
/usr/bin/python3 "$SIGNAL_SCRIPT" 2>&1 || {
    echo "[WARN] 外盘信号提取失败，跳过推送"
    exit 0
}

# 步骤3: 推送
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
echo "# 隔夜外盘信号 · $DATE_STR" | python3 "$PUSH_SCRIPT"
sed '/^## /d' "$SIGNAL_FILE" \
  | sed 's/🌐//g; s/📊//g; s/📰//g; s/💡//g; s/🔥//g; s/⭐//g; s/⚠️//g; s/🎯//g; s/🧘//g' \
  | python3 "$PUSH_SCRIPT"
# 版本号脚注
GIT_TAG=$(cd "$PROJECT_DIR" && git describe --tags --abbrev=7 2>/dev/null || true)
GIT_HASH=$(cd "$PROJECT_DIR" && git log -1 --format='%h' 2>/dev/null || echo "?")
if [ -n "$GIT_TAG" ]; then
    GIT_VER="${GIT_TAG}@${GIT_HASH}"
else
    echo "[WARN] git tag 缺失" >&2
    VER_FROM_FILE=$(grep -oP '\d+\.\d+\.\d+' "$PROJECT_DIR/VERSION.md" 2>/dev/null | head -1 || echo "unknown")
    GIT_VER="v${VER_FROM_FILE}@${GIT_HASH}"
fi
echo "> *${GIT_VER} | 交易推荐+技术分析将于8:55推送。AI辅助分析，不构成投资建议*" | python3 "$PUSH_SCRIPT"

echo "=== 完成 ==="
