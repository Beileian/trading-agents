#!/bin/bash
# ============================================================================
# A股盘前分析 — 纯分析脚本 v3.0.0（拆分自 run_premarket_push.sh）
# 由 Gateway Cron "金桥盘前分析" 06:35 调用
# 职责: 步骤0-3（缓存→技术分析→IMA→交易推荐→Rubrics→一致性检查+外盘信号注入）
# 产出: reports/trade_signals_*.md, reports/trading_analysis_*.md 等
# 不推送！
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_DIR="$PROJECT_DIR/reports"

echo "=== A股盘前分析 $DATE_STR (v3.0.0) ==="

# 步骤0: 更新本地缓存到最新交易日
echo "[0/X] 更新本地日线缓存..."
/usr/bin/python3 "$SCRIPT_DIR/update_daily_cache.py" 2>&1 || echo "[WARN] 缓存更新失败，用已有数据继续"
# 同步到 trading_analysis 脚本读取的目录和文件名格式
for src in /root/.openclaw/workspace/projects/trading-agents/data/cache/*-daily.csv; do
    base=$(basename "$src")
    sym="${base%-daily.csv}"
    sym_sh="${sym/.SS/.SH}"
    dest="/root/.openclaw/workspace/projects/trading-agents/logs/cache/${sym_sh}-YFin-data-2021-06-04-2026-06-09.csv"
    cp "$src" "$dest"
    sym_sh="${sym/.SS/.SH}"
    old_dest="/root/.openclaw/workspace/projects/trading-agents/logs/cache/${sym_sh}-YFin-data-2021-06-04-2026-06-04.csv"
    [ -f "$old_dest" ] && cp "$src" "$old_dest" || true
done
echo "  缓存已同步到 logs/cache (YFin-data 格式)"

# ── 第一层：缓存同步后抽查价格一致性 ──
echo "[0/Y] 抽查缓存价格一致性..."
/usr/bin/python3 "$SCRIPT_DIR/verify_cache_sync.py" 2>&1 || {
    echo "[ALERT] 缓存同步校验失败，阻断分析"
    exit 1
}
echo "  校验通过"

# ── 读取外盘晨间研判（06:30 已产出），注入到交易推荐生成 ──
OVERSEAS_BRIEF="/root/.openclaw/workspace/projects/overseas-morning-brief/reports/morning_brief_${DATE_STR}.md"
OVERSEAS_DIRECTION=""
OVERSEAS_CONFIDENCE=""

if [ -f "$OVERSEAS_BRIEF" ]; then
    echo "[0/Z] 读取外盘晨间研判..."
    # 提取方向判断和置信度
    DIR_LINE=$(grep -m1 '方向[：:]' "$OVERSEAS_BRIEF" 2>/dev/null || echo "")
    if [ -n "$DIR_LINE" ]; then
        # 提取方向关键词
        if echo "$DIR_LINE" | grep -q '偏多'; then
            OVERSEAS_DIRECTION="偏多"
        elif echo "$DIR_LINE" | grep -q '偏空'; then
            OVERSEAS_DIRECTION="偏空"
        else
            OVERSEAS_DIRECTION="中性"
        fi
        # 提取置信度
        if echo "$DIR_LINE" | grep -q '置信度[：: ]*高'; then
            OVERSEAS_CONFIDENCE="高"
        elif echo "$DIR_LINE" | grep -q '置信度[：: ]*中'; then
            OVERSEAS_CONFIDENCE="中"
        elif echo "$DIR_LINE" | grep -q '置信度[：: ]*低'; then
            OVERSEAS_CONFIDENCE="低"
        else
            OVERSEAS_CONFIDENCE="中"
        fi
        echo "  外盘方向: $OVERSEAS_DIRECTION | 置信度: $OVERSEAS_CONFIDENCE"
    else
        echo "  ⚠️ 未能从外盘研判提取方向信号，将不使用外盘上下文"
    fi
else
    echo "[0/Z] 外盘晨间研判文件不存在 ($OVERSEAS_BRIEF)，跳过外盘信号注入"
fi

# 步骤1: 技术分析报告
echo "[1/5] 生成技术分析报告..."
cd "$SCRIPT_DIR"
cp trading_analysis_20260604.py trading_analysis_latest.py
sed -i "s/TODAY_STR = \"2026-06-04\"/TODAY_STR = \"$DATE_STR\"/" trading_analysis_latest.py
sed -i "s/trading_analysis_20260604\.md/trading_analysis_${DATE_TAG}.md/" trading_analysis_latest.py
sed -i "s/opinions_20260604\.md/opinions_${DATE_TAG}.md/" trading_analysis_latest.py
sed -i "s/2021-06-04 至 2026-06-04/2021-06-04 至 $DATE_STR/" trading_analysis_latest.py
/usr/bin/python3 trading_analysis_latest.py 2>&1 || echo "[WARN] 技术分析部分失败，继续"

# 步骤2: IMA知识库观点（一步完成：提取+衰减+摘要）
echo "[2/5] IMA观点管线..."
/usr/bin/python3 "$SCRIPT_DIR/ima_pipeline.py" 2>&1 || echo "[WARN] IMA管线失败，继续"

# 步骤3: 交易推荐表格（Schema 校验 + Rubrics 规则门控 + 重试）
echo "[3/5] 生成交易推荐..."
MAX_GEN_RETRIES=2
GEN_RETRY=0
GEN_OK=false

# 如果有外盘方向信号，设置环境变量供 generate_trade_signals.py 读取
if [ -n "$OVERSEAS_DIRECTION" ]; then
    export OVERSEAS_DIRECTION
    export OVERSEAS_CONFIDENCE
fi

while [ $GEN_RETRY -le $MAX_GEN_RETRIES ]; do
    if /usr/bin/python3 "$SCRIPT_DIR/generate_trade_signals.py" "$DATE_TAG" 2>&1; then
        GEN_OK=true
        break
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 2 ]; then
            echo "[RETRY] Schema 校验失败 (exit=$EXIT_CODE)，重试第 $((GEN_RETRY+1))/$MAX_GEN_RETRIES 次..."
            /usr/bin/python3 trading_analysis_latest.py 2>&1 || echo "[WARN] 重新分析失败"
            GEN_RETRY=$((GEN_RETRY+1))
        elif [ $EXIT_CODE -eq 3 ]; then
            echo "[RETRY] Rubrics 门控拒绝 (exit=$EXIT_CODE)，重试第 $((GEN_RETRY+1))/$MAX_GEN_RETRIES 次..."
            /usr/bin/python3 trading_analysis_latest.py 2>&1 || echo "[WARN] 重新分析失败"
            GEN_RETRY=$((GEN_RETRY+1))
        else
            echo "[WARN] 交易推荐生成失败 (exit=$EXIT_CODE)，跳过"
            break
        fi
    fi
done
if [ "$GEN_OK" = false ]; then
    echo "[ALERT] 交易推荐生成经 $MAX_GEN_RETRIES 次重试仍失败"
    exit 1
fi

# 步骤3.5: Rubric 三套标准交叉验证
RUBRIC_TAG=""
RUBRIC_VERDICT="pass"
RUBRIC_MIN_SCORE=10
TRADE_FILE="$REPORT_DIR/trade_signals_${DATE_TAG}.md"
ANALYSIS_FILE="$REPORT_DIR/trading_analysis_${DATE_TAG}.md"
RUBRIC_SCRIPT="$PROJECT_DIR/rubrics/run_rubrics.py"
SIGNAL_RUBRIC="$PROJECT_DIR/rubrics/trade_signals.json"
RECO_RUBRIC="$PROJECT_DIR/rubrics/trade_recommendation.json"

_rubric_merge() {
    local verdict="$1" score="$2" label="$3"
    if [ "$(echo "$score < $RUBRIC_MIN_SCORE" | bc 2>/dev/null || echo 0)" = "1" ]; then
        RUBRIC_MIN_SCORE="$score"
    fi
    if [ "$verdict" = "reject" ]; then
        RUBRIC_VERDICT="reject"
    elif [ "$RUBRIC_VERDICT" != "reject" ] && [ "$verdict" = "low_confidence" ]; then
        RUBRIC_VERDICT="low_confidence"
    fi
}

if [ -f "$RUBRIC_SCRIPT" ]; then
    if [ -f "$TRADE_FILE" ]; then
        echo "[3.5/5] Rubric C: 信号规则门控 (script)..."
        SIG_OUTPUT=$(/usr/bin/python3 "$SCRIPT_DIR/generate_trade_signals.py" "$DATE_TAG" 2>&1 || true)
        if echo "$SIG_OUTPUT" | grep -q "Rubrics门控: pass"; then
            _rubric_merge "pass" "10.0" "C"
            echo "  ✅ Rubric C: pass (score=10.0)"
        elif echo "$SIG_OUTPUT" | grep -q "Rubrics门控: low_confidence"; then
            _rubric_merge "low_confidence" "6.0" "C"
            echo "  ⚠️ Rubric C: low_confidence"
        else
            echo "  ⚠️ Rubric C: 未检测到门控输出，跳过"
        fi
    fi

    if [ -f "$ANALYSIS_FILE" ] && [ -f "$RECO_RUBRIC" ]; then
        echo "[3.5/5] Rubric A: 分析报告质量 (LLM)..."
        RUBRIC_A_OUTPUT=$(/usr/bin/python3 "$RUBRIC_SCRIPT" "$ANALYSIS_FILE" --rubric "$RECO_RUBRIC" 2>&1) || {
            RUBRIC_A_EXIT=$?
            RUBRIC_A_VERDICT=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
            RUBRIC_A_SCORE=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',10))" 2>/dev/null || echo "10")
            RUBRIC_A_FAILED=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
items=d.get('items',{})
failed=[k for k,v in items.items() if not v.get('pass',True)]
print(','.join(failed) if failed else '')
" 2>/dev/null || echo "")
            _rubric_merge "$RUBRIC_A_VERDICT" "$RUBRIC_A_SCORE" "A"
            if [ -n "$RUBRIC_A_FAILED" ]; then
                echo "  ⚠️ Rubric A: $RUBRIC_A_VERDICT (score=$RUBRIC_A_SCORE, failed=$RUBRIC_A_FAILED)"
            else
                echo "  ✅ Rubric A: $RUBRIC_A_VERDICT (score=$RUBRIC_A_SCORE)"
            fi
            true
        }
        if [ "${RUBRIC_A_EXIT:-0}" -eq 0 ]; then
            RUBRIC_A_VERDICT=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
            RUBRIC_A_SCORE=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',10))" 2>/dev/null || echo "10")
            _rubric_merge "$RUBRIC_A_VERDICT" "$RUBRIC_A_SCORE" "A"
            echo "  ✅ Rubric A: $RUBRIC_A_VERDICT (score=$RUBRIC_A_SCORE)"
        fi
    else
        echo "[3.5/5] Rubric A: 跳过（分析报告或rubric缺失）"
    fi

    if [ -f "$TRADE_FILE" ] && [ -f "$SIGNAL_RUBRIC" ]; then
        echo "[3.5/5] Rubric B: 信号格式质量 (混合)..."
        RUBRIC_B_OUTPUT=$(/usr/bin/python3 "$RUBRIC_SCRIPT" "$TRADE_FILE" --rubric "$SIGNAL_RUBRIC" 2>&1) || {
            RUBRIC_B_EXIT=$?
            RUBRIC_B_VERDICT=$(echo "$RUBRIC_B_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
            RUBRIC_B_SCORE=$(echo "$RUBRIC_B_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',10))" 2>/dev/null || echo "10")
            RUBRIC_B_FAILED=$(echo "$RUBRIC_B_OUTPUT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
items=d.get('items',{})
failed=[k for k,v in items.items() if not v.get('pass',True)]
print(','.join(failed) if failed else '')
" 2>/dev/null || echo "")
            _rubric_merge "$RUBRIC_B_VERDICT" "$RUBRIC_B_SCORE" "B"
            if [ -n "$RUBRIC_B_FAILED" ]; then
                echo "  ⚠️ Rubric B: $RUBRIC_B_VERDICT (score=$RUBRIC_B_SCORE, failed=$RUBRIC_B_FAILED)"
            elif [ "$RUBRIC_B_EXIT" -eq 0 ]; then
                echo "  ✅ Rubric B: pass (score=$RUBRIC_B_SCORE)"
            else
                echo "  ⚠️ Rubric B: $RUBRIC_B_VERDICT (score=$RUBRIC_B_SCORE)"
            fi
            true
        }
        if [ "${RUBRIC_B_EXIT:-0}" -eq 0 ]; then
            RUBRIC_B_VERDICT=$(echo "$RUBRIC_B_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
            RUBRIC_B_SCORE=$(echo "$RUBRIC_B_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',10))" 2>/dev/null || echo "10")
            _rubric_merge "$RUBRIC_B_VERDICT" "$RUBRIC_B_SCORE" "B"
            echo "  ✅ Rubric B: $RUBRIC_B_VERDICT (score=$RUBRIC_B_SCORE)"
        fi
    else
        echo "[3.5/5] Rubric B: 跳过（信号表或rubric缺失）"
    fi

    if [ "$RUBRIC_VERDICT" = "reject" ]; then
        RUBRIC_TAG="⚠️ 低质量 "
        echo ""
        echo "📋 三套Rubrics聚合: REJECT (min_score=$RUBRIC_MIN_SCORE) — 标记为低质量"
    elif [ "$RUBRIC_VERDICT" = "low_confidence" ]; then
        RUBRIC_TAG="⚠️ 低置信度 "
        echo ""
        echo "📋 三套Rubrics聚合: LOW_CONFIDENCE (min_score=$RUBRIC_MIN_SCORE) — 标记为低置信度"
    else
        echo ""
        echo "📋 三套Rubrics聚合: PASS (min_score=$RUBRIC_MIN_SCORE)"
    fi
else
    echo "[3.5/5] Rubric质量评估... 跳过（脚本缺失）"
fi

# 步骤3.6: 前置一致性检查（昨日收盘 ↔ 今日信号）
CONSISTENCY_SCRIPT="$PROJECT_DIR/rubrics/check_morning_consistency.py"
if [ -f "$CONSISTENCY_SCRIPT" ] && [ -f "$TRADE_FILE" ]; then
    echo "[3.6/5] 前置一致性检查..."
    if python3 "$CONSISTENCY_SCRIPT" "$TRADE_FILE" 2>&1; then
        echo "  ✅ 信号与昨日数据自洽"
    else
        CONSISTENCY_ISSUES=$(python3 "$CONSISTENCY_SCRIPT" "$TRADE_FILE" 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('issues',[])))" 2>/dev/null || echo "?")
        echo "  ⚠️ ${CONSISTENCY_ISSUES}项不一致（继续，推送时附加标记）"
    fi
else
    echo "[3.6/5] 前置一致性检查... 跳过"
fi

# 写入状态文件供推送脚本读取
STATE_FILE="$REPORT_DIR/analysis_state_${DATE_TAG}.json"
python3 -c "
import json
json.dump({
    'date': '$DATE_STR',
    'trade_file_exists': $( [ -f "$TRADE_FILE" ] && echo "true" || echo "false" ),
    'analysis_file_exists': $( [ -f "$ANALYSIS_FILE" ] && echo "true" || echo "false" ),
    'rubric_verdict': '$RUBRIC_VERDICT',
    'rubric_min_score': $RUBRIC_MIN_SCORE,
    'rubric_tag': '$RUBRIC_TAG',
    'overseas_direction': '${OVERSEAS_DIRECTION:-}',
    'overseas_confidence': '${OVERSEAS_CONFIDENCE:-}',
}, open('$STATE_FILE', 'w'))
" 2>/dev/null || echo "[WARN] 状态文件写入失败"

echo "=== 盘前分析完成 ==="
echo "产出: $TRADE_FILE, $ANALYSIS_FILE"
if [ -n "$OVERSEAS_DIRECTION" ]; then
    echo "外盘信号已注入: $OVERSEAS_DIRECTION / $OVERSEAS_CONFIDENCE"
fi
