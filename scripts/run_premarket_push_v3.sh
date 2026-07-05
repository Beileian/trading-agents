#!/bin/bash
# ============================================================================
# A股盘前推送 — 纯推送脚本 v3.0.0（拆分自 run_premarket_push.sh）
# 由 Gateway Cron "金桥盘前推送" 08:00 调用
# 职责: 步骤4-5（虚拟盘执行→拼装→推送）
# 依赖: 06:35 金桥盘前分析已产出 trade_signals_*.md 等报告文件
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
TRADE_FILE="$REPORT_DIR/trade_signals_${DATE_TAG}.md"
ANALYSIS_FILE="$REPORT_DIR/trading_analysis_${DATE_TAG}.md"
STATE_FILE="$REPORT_DIR/analysis_state_${DATE_TAG}.json"
OVERSEAS_BRIEF="/root/.openclaw/workspace/projects/overseas-morning-brief/reports/morning_brief_${DATE_STR}.md"

echo "=== A股盘前推送 $DATE_STR (v3.0.0) ==="

# 检查上游分析是否完成
if [ ! -f "$TRADE_FILE" ]; then
    echo "[ALERT] 交易推荐文件不存在: $TRADE_FILE"
    echo "上游 06:35 金桥盘前分析可能失败，请检查 cron 运行日志"
    exit 1
fi

if [ ! -f "$ANALYSIS_FILE" ]; then
    echo "[WARN] 技术分析报告不存在: $ANALYSIS_FILE，继续但内容可能不全"
fi

# 读取状态文件获取 Rubrics 结果
RUBRIC_TAG=""
RUBRIC_VERDICT="pass"
RUBRIC_MIN_SCORE="10"
if [ -f "$STATE_FILE" ]; then
    RUBRIC_TAG=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('rubric_tag',''))" 2>/dev/null || echo "")
    RUBRIC_VERDICT=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('rubric_verdict','pass'))" 2>/dev/null || echo "pass")
    RUBRIC_MIN_SCORE=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('rubric_min_score',10))" 2>/dev/null || echo "10")
fi

# 步骤4: 虚拟盘执行（基于今日交易推荐）
echo "[4/5] 虚拟盘执行..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" execute "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘执行失败"

# 步骤5: 拼装并推送
echo "[5/5] 拼装推送..."
OPINION_FILE="$REPORT_DIR/opinions_${DATE_TAG}.md"
HAS_CONTENT=false

# 构建推送内容头部
RUBRIC_SCORE_LINE=" · Rubrics ${RUBRIC_MIN_SCORE}"
echo "# ${RUBRIC_TAG}A股开盘前分析 · $DATE_STR${RUBRIC_SCORE_LINE}" > "$PUSH_FILE"
echo "" >> "$PUSH_FILE"

# ── 外盘方向信号摘要（引用 morning_brief，不重复研判全文）──
if [ -f "$OVERSEAS_BRIEF" ]; then
    DIR_LINE=$(grep -m1 '方向[：:]' "$OVERSEAS_BRIEF" 2>/dev/null || echo "")
    if [ -n "$DIR_LINE" ]; then
        echo "## 🌍 隔夜外盘信号" >> "$PUSH_FILE"
        echo "" >> "$PUSH_FILE"
        echo "$DIR_LINE" | sed 's/^[#[:space:]]*//' >> "$PUSH_FILE"
        echo "" >> "$PUSH_FILE"
        HAS_CONTENT=true
    fi
fi

if [ -f "$TRADE_FILE" ]; then
    echo "## 交易推荐" >> "$PUSH_FILE"
    sed '/^## /d; /^> 乖离/d' "$TRADE_FILE" | sed 's/🌐//g; s/📊//g; s/📰//g; s/💡//g; s/🔥//g; s/⭐//g; s/⚠️//g; s/🎯//g; s/🧘//g' >> "$PUSH_FILE"
    echo "" >> "$PUSH_FILE"
    HAS_CONTENT=true
fi

if [ -f "$OPINION_FILE" ]; then
    OPS_OUTPUT=$(python3 "$SCRIPT_DIR/summarize_ima_opinions.py" "$OPINION_FILE" 2>/dev/null || {
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
# 版本号
GIT_TAG=$(cd "$PROJECT_DIR" && git describe --tags --abbrev=7 2>/dev/null || true)
GIT_HASH=$(cd "$PROJECT_DIR" && git log -1 --format='%h' 2>/dev/null || echo "?")
if [ -n "$GIT_TAG" ]; then
    GIT_VER="${GIT_TAG}@${GIT_HASH}"
else
    echo "[WARN] git tag 缺失，请检查 VERSION.md 并打 tag" >&2
    VER_FROM_FILE=$(grep -oP '\d+\.\d+\.\d+' "$PROJECT_DIR/VERSION.md" 2>/dev/null | head -1 || echo "unknown")
    GIT_VER="v${VER_FROM_FILE}@${GIT_HASH}"
fi
echo "> *${GIT_VER} | 外盘研判06:30推送，收盘复盘15:30自动验证。AI辅助分析，不构成投资建议*" >> "$PUSH_FILE"

if [ "$HAS_CONTENT" = true ]; then
    echo "推送内容已就绪，推送到钉钉群..."
    cat "$PUSH_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "无可用内容，跳过推送"
    exit 1
fi

# 自动对齐 git tag
cd "$PROJECT_DIR"
VER=$(grep -oP '\d+\.\d+\.\d+' VERSION.md 2>/dev/null | head -1 || true)
if [ -n "$VER" ]; then
    TAG="v${VER}"
    if ! git rev-parse "$TAG" >/dev/null 2>&1 || [ "$(git rev-list -n 1 "$TAG" 2>/dev/null)" != "$(git rev-parse HEAD)" ]; then
        git tag -f "$TAG" && git push origin "$TAG" --force 2>/dev/null || true
    fi
fi

echo "=== 推送完成 ==="
