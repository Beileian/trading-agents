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

# 日志
LOG_DIR="/var/log/closing_review"
mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/run_${DATE_TAG}.log" 2>&1

echo "=== A股收盘复盘 $DATE_STR ==="

# 失败告警 - 用PUSH_SCRIPT确保存在
if [ -f "$PUSH_SCRIPT" ]; then
    trap 'exit_code=$?; echo "# ⚠️ 收盘复盘异常\n\n脚本 exit=$exit_code\n时间: $(TZ=Asia/Shanghai date +%Y-%m-%d\ %H:%M:%S)" | python3 "$PUSH_SCRIPT" 2>/dev/null' ERR
fi

# 步骤1: 运行收盘复盘
echo "[1/3] 运行收盘复盘..."
/usr/bin/python3 "$SCRIPT_DIR/closing_review.py" 2>&1 || {
    echo "[WARN] 收盘复盘脚本失败，使用降级推送"
}

# 步骤2a: 虚拟盘交易执行
echo "[2a/4] 虚拟盘交易执行..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" execute "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘交易执行失败"

# 步骤2b: 虚拟盘收盘净值更新
echo "[2b/4] 虚拟盘收盘更新..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" close "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘收盘更新失败"

# 步骤3: 推送（含降级）
REVIEW_FILE="$REPORT_DIR/closing_review_${DATE_TAG}.md"
PAPER_STATE="$REPORT_DIR/paper_state.json"

if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then
    # 步骤3.5: Rubrics质量评估
    CLOSING_RUBRIC="$PROJECT_DIR/rubrics/closing_review.json"
    CLOSING_RUBRIC_SCRIPT="$PROJECT_DIR/rubrics/run_rubrics.py"
    RUBRIC_TAG=""
    RUBRIC_SCORE=""
    if [ -f "$CLOSING_RUBRIC" ] && [ -f "$CLOSING_RUBRIC_SCRIPT" ]; then
        echo "[3.5/3] Rubrics质量评估..."
        cp "$PROJECT_DIR/rubrics/trade_recommendation.json" /tmp/trade_backup.json 2>/dev/null || true
        cp "$CLOSING_RUBRIC" "$PROJECT_DIR/rubrics/trade_recommendation.json"
        # v3.3.0 修复: 捕获 Rubrics JSON 输出（verdict/score），注入推送标题
        # （原实现只 echo 到日志，复盘标题永远无质量标记，与盘前推送不对称）
        RUBRIC_OUTPUT=$(/usr/bin/python3 "$CLOSING_RUBRIC_SCRIPT" "$REVIEW_FILE" 2>/dev/null)
        RUBRIC_EXIT=$?
        RUBRIC_VERDICT=$(echo "$RUBRIC_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
        RUBRIC_SCORE=$(echo "$RUBRIC_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score') or '')" 2>/dev/null || echo "")
        if [ "$RUBRIC_VERDICT" = "reject" ]; then
            RUBRIC_TAG="⚠️ 低质量 "
            echo "[RUBRIC] 复盘质量 REJECT — 标题已标记，继续推送"
        elif [ "$RUBRIC_VERDICT" = "low_confidence" ]; then
            RUBRIC_TAG="⚠️ 低置信度 "
            echo "[RUBRIC] 复盘质量 LOW_CONFIDENCE — 标题已标记，继续推送"
        else
            echo "[RUBRIC] 复盘质量 PASS"
        fi
        cp /tmp/trade_backup.json "$PROJECT_DIR/rubrics/trade_recommendation.json" 2>/dev/null || true
    fi
    
    # v3.3.0: 质量标记注入推送内容头部（标题行前插入，与盘前推送 RUBRIC_TAG 对称）
    if [ -n "$RUBRIC_TAG" ]; then
        TMP_REVIEW="${REVIEW_FILE}.tagged"
        if [ -n "$RUBRIC_SCORE" ]; then
            echo "${RUBRIC_TAG}· Rubrics ${RUBRIC_SCORE}" > "$TMP_REVIEW"
        else
            echo "${RUBRIC_TAG}" > "$TMP_REVIEW"
        fi
        echo "" >> "$TMP_REVIEW"
        cat "$REVIEW_FILE" >> "$TMP_REVIEW"
        REVIEW_FILE="$TMP_REVIEW"
    fi
    
    echo "[4/4] 复盘报告推送中..."
    cat "$REVIEW_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "[4/4] 复盘文件缺失，推送降级简报..."
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

# 自动对齐 git tag
if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then
    echo "  同步 git tag..."
    cd "$PROJECT_DIR"
    VER=$(grep -oP '\d+\.\d+\.\d+' VERSION.md 2>/dev/null | head -1 || true)
    if [ -n "$VER" ]; then
        TAG="v${VER}"
        EXISTING_COMMIT=$(git rev-list -n 1 "$TAG" 2>/dev/null || true)
        CURRENT_COMMIT=$(git rev-parse HEAD)
        if [ "$EXISTING_COMMIT" != "$CURRENT_COMMIT" ]; then
            git tag -f "$TAG" && git push origin "$TAG" --force 2>/dev/null && echo "  ✅ tag $TAG 已对齐到 $(git rev-parse --short HEAD)" || echo "  [WARN] tag推送失败"
        else
            echo "  ✅ tag $TAG 已是最新，跳过"
        fi
    else
        echo "  [WARN] VERSION.md 中未找到版本号，跳过自动tag"
    fi
fi

echo "=== 完成 ==="
