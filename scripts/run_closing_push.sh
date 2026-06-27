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

# 步骤2a: 虚拟盘执行交易（基于今早交易推荐信号）
echo "[2a/4] 虚拟盘交易执行..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" execute "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘交易执行失败"

# 步骤2b: 虚拟盘收盘净值更新
echo "[2b/4] 虚拟盘收盘更新..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" close "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘收盘更新失败"

# 步骤3: 推送（含降级）
REVIEW_FILE="$REPORT_DIR/closing_review_${DATE_TAG}.md"
PAPER_STATE="$REPORT_DIR/paper_state.json"

if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then
    # 步骤3.5: Rubrics 质量评估
    CLOSING_RUBRIC="$PROJECT_DIR/rubrics/closing_review.json"
    CLOSING_RUBRIC_SCRIPT="$PROJECT_DIR/rubrics/run_rubrics.py"
    if [ -f "$CLOSING_RUBRIC" ] && [ -f "$CLOSING_RUBRIC_SCRIPT" ]; then
        echo "[3.5/3] Rubrics质量评估..."
        # 临时切换rubric文件为closing标准
        cp "$PROJECT_DIR/rubrics/trade_recommendation.json" /tmp/trade_backup.json 2>/dev/null || true
        cp "$CLOSING_RUBRIC" "$PROJECT_DIR/rubrics/trade_recommendation.json"
        /usr/bin/python3 "$CLOSING_RUBRIC_SCRIPT" "$REVIEW_FILE" 2>&1 || {
            RUBRIC_EXIT=$?
            if [ $RUBRIC_EXIT -eq 2 ]; then
                echo "[RUBRIC] 复盘质量 REJECT — 标记但继续推送（复盘不阻断）"
            elif [ $RUBRIC_EXIT -eq 1 ]; then
                echo "[RUBRIC] 复盘质量 LOW_CONFIDENCE — 标记但继续推送"
            fi
        }
        # 恢复
        cp /tmp/trade_backup.json "$PROJECT_DIR/rubrics/trade_recommendation.json" 2>/dev/null || true
    fi
    
    echo "[4/4] 复盘报告推送中..."
    cat "$REVIEW_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "[4/4] 复盘文件缺失，推送降级简报..."
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

# 自动对齐 git tag：收盘复盘产出后打tag推送
# 仅在复盘报告成功生成时执行（降级简报跳过）
if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then
    echo "  同步 git tag..."
    cd "$PROJECT_DIR"
    VER=$(grep -oP '\d+\.\d+\.\d+' VERSION.md 2>/dev/null | head -1 || true)
    if [ -n "$VER" ]; then
        TAG="v${VER}"
        # 检查 tag 是否已存在、是否指向最新 commit
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
