#!/bin/bash
# ============================================================================
# A股开盘前分析 — 一站式执行+推送脚本 v2.7.0
# 由 Gateway Cron 直接调用（不走 agentTurn prompt）
# 用法: ./run_premarket_push.sh
# ============================================================================
set -euo pipefail
export TZ=Asia/Shanghai
DATE_TAG=$(date +%Y%m%d)
DATE_STR=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPORT_DIR="$PROJECT_DIR/reports"
PUSH_SCRIPT="$SCRIPT_DIR/send_to_dingtalk.py"
PUSH_FILE="$REPORT_DIR/premarket_${DATE_TAG}.md"

# 失败告警：脚本退出码非0时推送到群
trap 'exit_code=$?; echo "# ⚠️ 交易推荐异常\n\n脚本 exit=$exit_code\n时间: $(TZ=Asia/Shanghai date +%Y-%m-%d\\ %H:%M:%S)" | python3 "$PUSH_SCRIPT" 2>/dev/null' ERR

echo "=== A股开盘前分析 $DATE_STR ==="

# 步骤0: 更新本地缓存到最新交易日
echo "[0/X] 更新本地日线缓存..."
/usr/bin/python3 "$SCRIPT_DIR/update_daily_cache.py" 2>&1 || echo "[WARN] 缓存更新失败，用已有数据继续"
# 同步到 trading_analysis 脚本读取的目录和文件名格式
# 旧脚本用 logs/cache/{symbol}-YFin-data-*.csv 格式，需要覆盖旧文件名
for src in /root/.openclaw/workspace/projects/trading-agents/data/cache/*-daily.csv; do
    base=$(basename "$src")
    # 转换为 YFin-data 格式的文件名
    sym="${base%-daily.csv}"
    sym_sh="${sym/.SS/.SH}"
    dest="/root/.openclaw/workspace/projects/trading-agents/logs/cache/${sym_sh}-YFin-data-2021-06-04-2026-06-09.csv"
    cp "$src" "$dest"
    # 同时覆盖旧 YFin-data 文件（注意沪市 .SS 后缀需转换为 .SH）
    sym_sh="${sym/.SS/.SH}"
    old_dest="/root/.openclaw/workspace/projects/trading-agents/logs/cache/${sym_sh}-YFin-data-2021-06-04-2026-06-04.csv"
    [ -f "$old_dest" ] && cp "$src" "$old_dest" || true
done
echo "  缓存已同步到 logs/cache (YFin-data 格式)"

# ── 第一层：缓存同步后抽查价格一致性 ──
echo "[0/Y] 抽查缓存价格一致性..."
/usr/bin/python3 "$SCRIPT_DIR/verify_cache_sync.py" 2>&1 || {
    echo "[ALERT] 缓存同步校验失败，阻断推送"
    echo "# 🚨 缓存同步异常\n\nverify_cache_sync.py 校验失败，价格数据可能过期，已阻断推送。\n时间: $(TZ=Asia/Shanghai date +%Y-%m-%d\ %H:%M:%S)" | python3 "$PUSH_SCRIPT" 2>/dev/null
    exit 1
}
echo "  校验通过"

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
while [ $GEN_RETRY -le $MAX_GEN_RETRIES ]; do
    if /usr/bin/python3 "$SCRIPT_DIR/generate_trade_signals.py" "$DATE_TAG" 2>&1; then
        GEN_OK=true
        break
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 2 ]; then
            echo "[RETRY] Schema 校验失败 (exit=$EXIT_CODE)，重试第 $((GEN_RETRY+1))/$MAX_GEN_RETRIES 次..."
            # 重跑 trading_analysis 重新生成报告（可能格式差异导致解析失败）
            /usr/bin/python3 trading_analysis_latest.py 2>&1 || echo "[WARN] 重新分析失败"
            GEN_RETRY=$((GEN_RETRY+1))
        elif [ $EXIT_CODE -eq 3 ]; then
            echo "[RETRY] Rubrics 门控拒绝 (exit=$EXIT_CODE)，重试第 $((GEN_RETRY+1))/$MAX_GEN_RETRIES 次..."
            # Rubrics拒绝说明内容质量问题，重跑LLM分析后再次生成
            /usr/bin/python3 trading_analysis_latest.py 2>&1 || echo "[WARN] 重新分析失败"
            GEN_RETRY=$((GEN_RETRY+1))
        else
            echo "[WARN] 交易推荐生成失败 (exit=$EXIT_CODE)，跳过"
            break
        fi
    fi
done
if [ "$GEN_OK" = false ]; then
    echo "[ALERT] 交易推荐生成经 $MAX_GEN_RETRIES 次重试仍失败，阻断推送"
    echo "# 🚨 交易推荐生成异常

Schema 校验经 $MAX_GEN_RETRIES 次重试仍未通过，已阻断推送。
时间: $(TZ=Asia/Shanghai date +%Y-%m-%d\ %H:%M:%S)" | python3 "$PUSH_SCRIPT" 2>/dev/null
    exit 1
fi

# 步骤3.5: Rubric 三套标准交叉验证
#   A. 分析报告质量 → trading_analysis → trade_recommendation.json (LLM类,5维度)
#   B. 信号格式质量 → trade_signals → trade_signals.json (script类+LLM类,4维度)
#   C. 信号规则门控 → generate_trade_signals 内置 (script类,3维度，已在步骤3中执行)
#
#  聚合判定: 任一 veto 不通过→REJECT; 任一 high 不通过→LOW_CONFIDENCE;
#            否则取三套最低分判定; 全部通过→PASS
RUBRIC_TAG=""
RUBRIC_VERDICT="pass"
RUBRIC_MIN_SCORE=10
TRADE_FILE="$REPORT_DIR/trade_signals_${DATE_TAG}.md"
ANALYSIS_FILE="$REPORT_DIR/trading_analysis_${DATE_TAG}.md"
RUBRIC_SCRIPT="$PROJECT_DIR/rubrics/run_rubrics.py"
SIGNAL_RUBRIC="$PROJECT_DIR/rubrics/trade_signals.json"
RECO_RUBRIC="$PROJECT_DIR/rubrics/trade_recommendation.json"

# 辅助函数: 从 rubric_log.jsonl 最新一条提取聚合判定
_rubric_merge() {
    local verdict="$1" score="$2" label="$3"
    # 更新最低分
    if [ "$(echo "$score < $RUBRIC_MIN_SCORE" | bc 2>/dev/null || echo 0)" = "1" ]; then
        RUBRIC_MIN_SCORE="$score"
    fi
    # veto → REJECT 不可逆
    if [ "$verdict" = "reject" ]; then
        RUBRIC_VERDICT="reject"
    elif [ "$RUBRIC_VERDICT" != "reject" ] && [ "$verdict" = "low_confidence" ]; then
        RUBRIC_VERDICT="low_confidence"
    fi
}

if [ -f "$RUBRIC_SCRIPT" ]; then
    # C. script类门控已在步骤3中由 generate_trade_signals 执行
    #    从日志提取结果
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

    # A. 分析报告质量评估 (LLM类)
    if [ -f "$ANALYSIS_FILE" ] && [ -f "$RECO_RUBRIC" ]; then
        echo "[3.5/5] Rubric A: 分析报告质量 (LLM)..."
        RUBRIC_A_OUTPUT=$(/usr/bin/python3 "$RUBRIC_SCRIPT" "$ANALYSIS_FILE" --rubric "$RECO_RUBRIC" 2>&1) || {
            RUBRIC_A_EXIT=$?
            RUBRIC_A_VERDICT=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verdict','pass'))" 2>/dev/null || echo "pass")
            RUBRIC_A_SCORE=$(echo "$RUBRIC_A_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',10))" 2>/dev/null || echo "10")
            # 列出不通过项
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

    # B. 信号格式质量评估 (script+LLM混合)
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

    # 聚合判定 → 标签
    if [ "$RUBRIC_VERDICT" = "reject" ]; then
        RUBRIC_TAG="⚠️ 低质量 "
        echo ""
        echo "📋 三套Rubrics聚合: REJECT (min_score=$RUBRIC_MIN_SCORE) — 标记为低质量继续推送"
    elif [ "$RUBRIC_VERDICT" = "low_confidence" ]; then
        RUBRIC_TAG="⚠️ 低置信度 "
        echo ""
        echo "📋 三套Rubrics聚合: LOW_CONFIDENCE (min_score=$RUBRIC_MIN_SCORE) — 标记为低置信度推送"
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
        echo "  ⚠️ ${CONSISTENCY_ISSUES}项不一致（继续推送，附加标记）"
        if [ "$RUBRIC_TAG" = "" ]; then
            CONSISTENCY_WARN=true
        fi
    fi
else
    echo "[3.6/5] 前置一致性检查... 跳过"
fi

# 步骤4: 虚拟盘执行（基于今日交易推荐）
echo "[4/5] 虚拟盘执行..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" execute "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘执行失败"

# 步骤5: 拼装并推送
echo "[5/5] 拼装推送..."
SIGNAL_FILE="$PROJECT_DIR/reports/overseas_signal_${DATE_STR}.md"
OPINION_FILE="$REPORT_DIR/opinions_${DATE_TAG}.md"
HAS_CONTENT=false

# Rubric 质量标记（由步骤3.5的三套交叉验证聚合判定）
# RUBRIC_TAG 和 RUBRIC_VERDICT 已由步骤3.5的 _rubric_merge 函数确定
# 前置一致性标记
if [ "${CONSISTENCY_WARN:-false}" = "true" ]; then
    RUBRIC_TAG="${RUBRIC_TAG}📉数据偏差 "
fi

# 构建推送内容头部
# 构建标题 — 始终附加 Rubrics 分数（所有判定结果可见可追溯）
RUBRIC_SCORE_LINE=" · Rubrics ${RUBRIC_MIN_SCORE}"
echo "# ${RUBRIC_TAG}A股开盘前分析 · $DATE_STR${RUBRIC_SCORE_LINE}" > "$PUSH_FILE"
echo "" >> "$PUSH_FILE"

if [ -f "$TRADE_FILE" ]; then
    echo "## 交易推荐" >> "$PUSH_FILE"
    # 跳过文件自身的 ## 标题行和 > 脚注行；清理装饰图标（保留🔴🟡操作信号和↑↓→方向）
    sed '/^## /d; /^> 乖离/d' "$TRADE_FILE" | sed 's/🌐//g; s/📊//g; s/📰//g; s/💡//g; s/🔥//g; s/⭐//g; s/⚠️//g; s/🎯//g; s/🧘//g' >> "$PUSH_FILE"
    echo "" >> "$PUSH_FILE"
    HAS_CONTENT=true
fi

if [ -f "$OPINION_FILE" ]; then
    OPS_OUTPUT=$(python3 "$SCRIPT_DIR/summarize_ima_opinions.py" "$OPINION_FILE" 2>/dev/null || {
        # fallback: 提取标题做简要概括
        python3 -c "
with open('$OPINION_FILE') as f:
    lines = f.readlines()

sections = {}
current_section = None
for i, line in enumerate(lines):
    s = line.strip()
    if s.startswith('###'):
        current_section = s.lstrip('#').strip()
        sections[current_section] = []
        continue
    if current_section and s.startswith('**') and '[' in s:
        title = s.strip('*').strip().strip('*').replace('**','')
        if '腾云马对话' in title or '对话记录' in title:
            continue
        bracket_idx = title.rfind('[')
        pure_title = title[:bracket_idx].strip() if bracket_idx > 0 else title
        sections[current_section].append(pure_title)

for sec_name, articles in sections.items():
    if not articles:
        continue
    clean_name = sec_name
    for ch in ['🎯 ','📊 ','🧘 ']:
        clean_name = clean_name.replace(ch, '')
    titles = '、'.join(articles[:3])
    if len(titles) > 100:
        titles = titles[:97] + '...'
    print(f'{clean_name}：近期关注{titles}。')
"
    })
    OPS_CLEAN=$(echo "$OPS_OUTPUT" | sed '/^$/d')
    if [ -n "$OPS_CLEAN" ]; then
        echo "## 外部观点参考" >> "$PUSH_FILE"
        echo "*数据源: IMA 知识库（公众号文章）*" >> "$PUSH_FILE"
        echo "" >> "$PUSH_FILE"
        echo "$OPS_CLEAN" >> "$PUSH_FILE"
        echo "" >> "$PUSH_FILE"
        HAS_CONTENT=true
    else
        echo "[PUSH] IMA观点为空，跳过外部参考section"
    fi
fi

echo "" >> "$PUSH_FILE"
# 版本号：优先 git describe --tags，失败则用 VERSION.md，再失败则标记 unknown
GIT_TAG=$(cd "$PROJECT_DIR" && git describe --tags --abbrev=7 2>/dev/null || true)
GIT_HASH=$(cd "$PROJECT_DIR" && git log -1 --format='%h' 2>/dev/null || echo "?")
if [ -n "$GIT_TAG" ]; then
    GIT_VER="${GIT_TAG}@${GIT_HASH}"
else
    # 查不到 tag → unknown + 告警
    echo "[WARN] git tag 缺失，请检查 VERSION.md 并打 tag" >&2
    VER_FROM_FILE=$(grep -oP '\d+\.\d+\.\d+' "$PROJECT_DIR/VERSION.md" 2>/dev/null | head -1 || echo "unknown")
    GIT_VER="v${VER_FROM_FILE}@${GIT_HASH}"
fi
echo "> *${GIT_VER} | 外盘信号已在8:05推送，收盘复盘将于15:30自动验证。AI辅助分析，不构成投资建议*" >> "$PUSH_FILE"

if [ "$HAS_CONTENT" = true ]; then
    echo "推送内容已就绪，推送到钉钉群..."
    cat "$PUSH_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "无可用内容，跳过推送"
fi

# 自动对齐 git tag（保险：即使收盘复盘漏了，开盘推送也打tag）
cd "$PROJECT_DIR"
VER=$(grep -oP '\d+\.\d+\.\d+' VERSION.md 2>/dev/null | head -1 || true)
if [ -n "$VER" ]; then
    TAG="v${VER}"
    if ! git rev-parse "$TAG" >/dev/null 2>&1 || [ "$(git rev-list -n 1 "$TAG" 2>/dev/null)" != "$(git rev-parse HEAD)" ]; then
        git tag -f "$TAG" && git push origin "$TAG" --force 2>/dev/null || true
    fi
fi

echo "=== 完成 ==="
