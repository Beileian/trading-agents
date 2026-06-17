#!/bin/bash
# ============================================================================
# A股开盘前分析 — 一站式执行+推送脚本
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

# 步骤3: 交易推荐表格（Schema 校验 + 重试）
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

# 步骤4: 虚拟盘执行（基于今日交易推荐）
echo "[4/5] 虚拟盘执行..."
/usr/bin/python3 "$SCRIPT_DIR/paper_trading.py" execute "$DATE_STR" 2>&1 || echo "[WARN] 虚拟盘执行失败"

# 步骤5: 拼装并推送
echo "[5/5] 拼装推送..."
SIGNAL_FILE="$PROJECT_DIR/reports/overseas_signal_${DATE_STR}.md"
TRADE_FILE="$REPORT_DIR/trade_signals_${DATE_TAG}.md"
OPINION_FILE="$REPORT_DIR/opinions_${DATE_TAG}.md"

HAS_CONTENT=false

# 构建推送内容头部
echo "# A股开盘前分析 · $DATE_STR" > "$PUSH_FILE"
echo "" >> "$PUSH_FILE"

if [ -f "$TRADE_FILE" ]; then
    echo "## 交易推荐" >> "$PUSH_FILE"
    # 跳过文件自身的 ## 标题行和 > 脚注行；清理装饰图标（保留🔴🟡操作信号和↑↓→方向）
    sed '/^## /d; /^> 乖离/d' "$TRADE_FILE" | sed 's/🌐//g; s/📊//g; s/📰//g; s/💡//g; s/🔥//g; s/⭐//g; s/⚠️//g; s/🎯//g; s/🧘//g' >> "$PUSH_FILE"
    echo "" >> "$PUSH_FILE"
    HAS_CONTENT=true
fi

if [ -f "$OPINION_FILE" ]; then
    echo "## 外部观点参考" >> "$PUSH_FILE"
    echo "*数据源: IMA 知识库（公众号文章）*" >> "$PUSH_FILE"
    echo "" >> "$PUSH_FILE"
    # 用 DeepSeek 对每位作者的文章做一句话概括（每人≤100字）
    python3 "$SCRIPT_DIR/summarize_ima_opinions.py" "$OPINION_FILE" >> "$PUSH_FILE" 2>/dev/null || {
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
    }
    echo "" >> "$PUSH_FILE"
    HAS_CONTENT=true
fi

echo "" >> "$PUSH_FILE"
echo "> *外盘信号已在8:05推送，收盘复盘将于15:30自动验证今日判断。AI辅助分析，不构成投资建议*" >> "$PUSH_FILE"

if [ "$HAS_CONTENT" = true ]; then
    echo "推送内容已就绪，推送到钉钉群..."
    cat "$PUSH_FILE" | python3 "$PUSH_SCRIPT"
else
    echo "无可用内容，跳过推送"
fi

echo "=== 完成 ==="
