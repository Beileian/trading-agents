#!/bin/bash
# ============================================================================
# A股收盘复盘 — 一站式执行+推送脚本（v3.7.0 双通道：群聊摘要 + IMA全文入库）
# 用法: ./run_closing_push.sh
#
# v3.7.0 (2026-09-10, 评审整合结论 P0-1/2/3 + P1 落地, 托董确认 IMA路径=我的知识库/投资理财/盘后复盘)
#   - 群聊改推「摘要」(≤500字, 契约见 generate_closing_summary.py); 全文不再直推群
#   - 全文入库 IMA「王海明的知识库/投资理财/盘后复盘」(folder_ID=7503...) 带读回校验
#   - P0-2 失败回指: 群摘要末行只在 IMA 回读成功才写「已存盘后复盘」; 否则写「暂存本地, 次日补传」
#   - P1 幂等: 当日已推标记文件推送前检查, 重跑不重复发; 摘要生成失败时才降级简报
#   - 下游(paper_trading/style_rotation/update_daily_cache)消费的 close_snapshot/json 与原md路径时序不动
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_DIR="$PROJECT_DIR/reports"
PUSH_SCRIPT="$SCRIPT_DIR/send_to_dingtalk.py"
IMA_PUSH="$SCRIPT_DIR/ima_push_closing.sh"

# 日志
LOG_DIR="/var/log/closing_review"
mkdir -p "$LOG_DIR"
# P1 幂等标记: 当日已推送/已入库
GROUP_MARK="$LOG_DIR/.pushed_group_${DATE_TAG}"
IMA_MARK="$LOG_DIR/.ima_${DATE_TAG}"
exec >> "$LOG_DIR/run_${DATE_TAG}.log" 2>&1

echo "=== A股收盘复盘 $DATE_STR (v3.7.0 双通道) ==="

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

REVIEW_FILE="$REPORT_DIR/closing_review_${DATE_TAG}.md"
SUMMARY_FILE="$REPORT_DIR/closing_summary_${DATE_TAG}.md"
PAPER_STATE="$REPORT_DIR/paper_state.json"

# ══════════ 主流程: 全文已生成才走双通道 ══════════
if [ -f "$REVIEW_FILE" ] && [ -s "$REVIEW_FILE" ]; then

    # ── Rubrics 质量评估(不注入全文, 只作标记传给摘要/IMA) ──
    CLOSING_RUBRIC="$PROJECT_DIR/rubrics/closing_review.json"
    CLOSING_RUBRIC_SCRIPT="$PROJECT_DIR/rubrics/run_rubrics.py"
    RUBRIC_SCORE=""; VERDICT="pass"
    if [ -f "$CLOSING_RUBRIC" ] && [ -f "$CLOSING_RUBRIC_SCRIPT" ]; then
        echo "[3/3] Rubrics质量评估..."
        cp "$PROJECT_DIR/rubrics/trade_recommendation.json" /tmp/trade_backup.json 2>/dev/null || true
        cp "$CLOSING_RUBRIC" "$PROJECT_DIR/rubrics/trade_recommendation.json"
        RUBRIC_OUTPUT=$(/usr/bin/python3 "$CLOSING_RUBRIC_SCRIPT" "$REVIEW_FILE" 2>/dev/null)
        VERDICT=$(echo "$RUBRIC_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
        RUBRIC_SCORE=$(echo "$RUBRIC_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score') or '')" 2>/dev/null || echo "")
        echo "[RUBRIC] verdict=$VERDICT score=$RUBRIC_SCORE"
        cp /tmp/trade_backup.json "$PROJECT_DIR/rubrics/trade_recommendation.json" 2>/dev/null || true
    fi

    # ── 生成摘要 ──
    echo "[4/4] 生成群聊摘要..."
    /usr/bin/python3 "$SCRIPT_DIR/generate_closing_summary.py" "$DATE_TAG" 2>/dev/null || echo "[WARN] 摘要生成失败"
    if [ -f "$SUMMARY_FILE" ]; then
        SUMMARY_READY="yes"
    else
        SUMMARY_READY="no"
        echo "[WARN] 摘要文件缺失, 走全文/降级退路"
    fi

    # 质量标记注入摘要首行(与盘前对称)
    if [ "$SUMMARY_READY" = "yes" ] && { [ "$VERDICT" = "reject" ] || [ "$VERDICT" = "low_confidence" ]; }; then
        TAG_LINE=""
        if [ "$VERDICT" = "reject" ]; then TAG_LINE="⚠️ 低质量"; else TAG_LINE="⚠️ 低置信度"; fi
        [ -n "$RUBRIC_SCORE" ] && TAG_LINE="${TAG_LINE} · Rubrics ${RUBRIC_SCORE}"
        TMP_SUM="${SUMMARY_FILE}.tagged"
        echo "**${TAG_LINE}**" > "$TMP_SUM"
        tail -n +1 "$SUMMARY_FILE" >> "$TMP_SUM"
        mv "$TMP_SUM" "$SUMMARY_FILE"
    fi

    # ── IMA 全文入库(带读回校验) ──
    IMA_STATUS="IMA_FAIL"
    if [ -f "$IMA_PUSH" ]; then
        echo "[5/5] 全文入库 IMA 盘后复盘..."
        IMA_STATUS=$(bash "$IMA_PUSH" "$REVIEW_FILE" 2>/dev/null | tail -1)
        # 兜底: 若脚本未回echo状态(0), 用是否已有 mark 判定
        case "$IMA_STATUS" in
            IMA_OK)   touch "$IMA_MARK"; echo "  ✅ IMA 已入库+回读可见";;
            IMA_PENDING) echo "  ⚠️ IMA add成功但回读未确认, 需人工核验";;
            *) IMA_STATUS="IMA_FAIL"; echo "  ⚠️ IMA 入库失败/未执行";;
        esac
    else
        echo "  [WARN] ima_push_closing.sh 缺失, 跳过 IMA 入库"
    fi

    # ── 群聊: 推摘要, 末行 = IMA 回指(P0-2) ──
    if [ ! -f "$GROUP_MARK" ]; then
        if [ "$SUMMARY_READY" = "yes" ]; then
            echo "[6/6] 群聊推送摘要..."
            # 替换摘要末尾 IMA_STATUS 占位符为实际状态
            if [ "$IMA_STATUS" = "IMA_OK" ]; then
                sed -i 's#<IMA_STATUS_PENDING>#✅ 全文已存「我的知识库→投资理财→盘后复盘」#' "$SUMMARY_FILE"
            elif [ "$IMA_STATUS" = "IMA_PENDING" ]; then
                sed -i 's#<IMA_STATUS_PENDING>#⚠️ 全文待人工核验位置, 次日可见#g' "$SUMMARY_FILE"
            else
                sed -i 's#<IMA_STATUS_PENDING>#⚠️ 全文暂存本地, 次日 X 点前补传 IMA#g' "$SUMMARY_FILE"
            fi
            cat "$SUMMARY_FILE" | python3 "$PUSH_SCRIPT" && touch "$GROUP_MARK"
        else
            echo "[6/6] 摘要不可用, 降级推送全文(异常一次)..."
            cat "$REVIEW_FILE" | python3 "$PUSH_SCRIPT" && touch "$GROUP_MARK"
        fi
    else
        echo "[6/6] 当日群聊已推送($GROUP_MARK 存在), 跳过幂等"
    fi

else
    echo "[推送] 复盘文件缺失，推送降级简报..."
    cat << EOF | python3 "$PUSH_SCRIPT"
# 📉 A股收盘复盘 · ${DATE_STR}

⚠️ 复盘脚本未能生成报告。

请手动检查：
- closing_review.py 执行日志
- API key 是否有效
- 网络连接是否正常

*降级推送 | 认知闭环 v3.7.0*
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
