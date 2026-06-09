#!/bin/bash
# TradingAgents 每日开盘前自动分析 + 推送
# cron: 55 8 * * 1-5
set -euo pipefail

SCRIPTS_DIR="/root/.openclaw/workspace/projects/trading-agents/scripts"
REPORT_DIR="/root/.openclaw/workspace/projects/trading-agents/reports"
LOG_DIR="/root/.openclaw/workspace/projects/trading-agents/logs"
DATA_DIR="/root/.openclaw/workspace/projects/trading-agents/data"
TARGET_CHAT="cidY4mlx+J2kNFpTiWFgQ0gkg=="
DATE_TAG=$(TZ=Asia/Shanghai date +%Y%m%d)

echo "============================================================"
echo "TradingAgents 自动分析 — $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S') CST"
echo "============================================================"

# ── 1. 缓存更新 ──
echo ""
echo "=== 更新数据缓存 ==="
mkdir -p "$DATA_DIR/cache" "$LOG_DIR" "$REPORT_DIR"

# ── 2. LLM 分析 ──
echo ""
echo "=== 生成技术分析报告 ==="
LOG_FILE="$LOG_DIR/auto_analysis_${DATE_TAG}.log"

# ── 3. 提取 IMA 观点 ──
echo ""
echo "=== 提取 IMA 知识库观点 ==="
/usr/bin/python3 "$SCRIPTS_DIR/extract_ima_opinions.py" 2>&1 || echo "⚠ IMA 观点提取未完成"

# ── 4. 提取外盘晨间研判信号 ──
echo ""
echo "=== 提取外盘晨间研判信号 ==="

OVERSEAS_DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
OVERSEAS_SIGNAL_FILE="/root/.openclaw/workspace/projects/overseas-morning-brief/reports/overseas_signal_${OVERSEAS_DATE}.md"

/usr/bin/python3 /root/.openclaw/workspace/projects/overseas-morning-brief/scripts/extract_signal.py 2>&1 || echo "⚠ 外盘信号提取失败"

# ── 5. 合并报告并推送 ──
echo ""
echo "=== 合并报告 ==="

ANALYSIS_FILE=$(find "$REPORT_DIR" -name "trading_analysis_${DATE_TAG}*" -type f 2>/dev/null | head -1)
OPINIONS_FILE="$REPORT_DIR/opinions_${DATE_TAG}.md"
FINAL_FILE="$REPORT_DIR/daily_report_${DATE_TAG}.md"

# ── 5a. 生成交易推荐表格（乖离率+支撑/阻力+外盘/知识库落地）──
SIGNALS_FILE="$REPORT_DIR/trade_signals_${DATE_TAG}.md"
echo ""
echo "=== 生成交易推荐表格 ==="
/usr/bin/python3 "$SCRIPTS_DIR/generate_trade_signals.py" "$DATE_TAG" 2>&1 || echo "⚠ 交易推荐表格生成失败"

{
    echo "# 📊 A股开盘前分析 · $(TZ=Asia/Shanghai date +%Y%m%d)"
    echo ""
    echo "> 自动生成 | 数据: 新浪财经 + IMA知识库 + 外盘研判 | 分析: DeepSeek"
    echo "> 免责声明: AI模拟分析，不构成投资建议"
    echo ""

    # Section 0: 外盘信号修正因子（如果存在）
    if [ -f "$OVERSEAS_SIGNAL_FILE" ]; then
        cat "$OVERSEAS_SIGNAL_FILE"
        echo ""
        echo "---"
        echo ""
    fi

    # Section 1: 交易推荐表格（优先展示）
    if [ -f "$SIGNALS_FILE" ]; then
        cat "$SIGNALS_FILE"
        echo ""
        echo "---"
        echo ""
    fi

    # Section 2: IMA 外部观点
    if [ -f "$OPINIONS_FILE" ]; then
        cat "$OPINIONS_FILE" | head -c 3000
    fi

    echo ""
    echo "---"
    echo ""
    echo "*⚠️ AI 模拟交易分析，不构成投资建议。实盘操作请自行判断。*"
} > "$FINAL_FILE"

echo "报告生成: $FINAL_FILE ($(wc -c < "$FINAL_FILE") bytes)"

# ── 推送 ──
echo ""
echo "=== 推送到群 ==="

# crontab 环境可能没有 nvm 路径，使用绝对路径
OPENCLAW_BIN="/root/.nvm/versions/node/v22.22.0/bin/openclaw"

# 推送到群，作为消息文本发送（手机端可直接阅读，非文件附件）
# 限制长度 4000 字节避免超时
MSG_CONTENT=$(head -c 4000 "$FINAL_FILE")
$OPENCLAW_BIN message send \
    --channel dingtalk-connector \
    --target "cidY4mlx+J2kNFpTiWFgQ0gkg==" \
    --message "$MSG_CONTENT" 2>&1 || {
    echo "✗ 推送失败"
}

echo ""
echo "============================================================"
echo "完成 — $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S') CST"
echo "============================================================"
