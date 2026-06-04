#!/bin/bash
# TradingAgents 每日收盘自动分析 + 推送到「谈股论金奔富」群
# 触发: 每个交易日 15:30 CST
# 数据: 新浪财经(OHLCV) + IMA 知识库(观点) + DeepSeek(分析)
# 目标: cidY4mlx+J2kNFpTiWFgQ0gkg==

set -euo pipefail

PROJECT_DIR="/root/.openclaw/workspace/projects/trading-agents"
REPORT_DIR="$PROJECT_DIR/reports"
SCRIPTS_DIR="$PROJECT_DIR/scripts"
CACHE_DIR="$PROJECT_DIR/data/cache"
DATE_TAG=$(TZ=Asia/Shanghai date +%Y%m%d)
LOG_FILE="$PROJECT_DIR/logs/auto_analysis_${DATE_TAG}.log"
TARGET_CHAT="cidY4mlx+J2kNFpTiWFgQ0gkg=="

# ── 初始化 ──
mkdir -p "$PROJECT_DIR/logs" "$REPORT_DIR" "$CACHE_DIR"
exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "TradingAgents 自动分析 — $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S') CST"
echo "============================================================"

# ── 1. 更新行情缓存 ──
echo ""
echo "[1/3] 更新行情缓存..."

python3 << 'PYEOF' 2>&1
import json, os, time, requests, pandas as pd
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
today = datetime.now(TZ)
tickers = [
    ("sh000016", "000016.SH", "上证50"),
    ("sh000300", "000300.SH", "沪深300"),
    ("sh000688", "000688.SH", "科创50"),
    ("sh601288", "601288.SS", "农业银行"),
    ("sh601988", "601988.SS", "中国银行"),
    ("sh600036", "600036.SS", "招商银行"),
    ("sh600886", "600886.SS", "国投电力"),
]

CACHE_DIR = "/root/.openclaw/workspace/projects/trading-agents/data/cache"

# 新浪财经 K 线 API（每日增量更新）
for code, symbol, name in tickers:
    try:
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen=1500"
        resp = requests.get(url, timeout=15)
        data = json.loads(resp.text)
        if not data:
            print(f"  ✗ {symbol} ({name}): 无数据")
            continue

        df = pd.DataFrame(data)
        df["Date"] = pd.to_datetime(df["day"])
        df["Open"] = pd.to_numeric(df["open"], errors="coerce")
        df["High"] = pd.to_numeric(df["high"], errors="coerce")
        df["Low"] = pd.to_numeric(df["low"], errors="coerce")
        df["Close"] = pd.to_numeric(df["close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

        start = today - pd.DateOffset(years=5)
        mask = (df["Date"] >= start.strftime("%Y-%m-%d")) & (df["Date"] <= today.strftime("%Y-%m-%d"))
        df = df[mask][["Date","Open","High","Low","Close","Volume"]]

        cache_path = os.path.join(CACHE_DIR, f"{symbol}-daily.csv")
        df.to_csv(cache_path, index=False, encoding="utf-8")
        print(f"  ✓ {symbol} ({name}): {len(df)} rows")
        time.sleep(0.5)
    except Exception as e:
        print(f"  ✗ {symbol} ({name}): {e}")

print(f"\n  快照时间: {today.strftime('%Y-%m-%d %H:%M')} CST")
PYEOF

# ── 2. 运行技术分析 ──
echo ""
echo "[2/3] 运行技术分析..."

/usr/bin/python3 "$SCRIPTS_DIR/trading_analysis_20260604.py" 2>&1 || {
    echo "⚠ 分析脚本失败，使用备用简化分析..."
    /usr/bin/python3 -c "
import subprocess, json, os, sys
# Fallback: run the minimal analysis
sys.exit(1)  # TODO: implement fallback
" && true
}

# ── 3. 提取 IMA 观点 ──
echo ""
echo "[3/3] 提取 IMA 知识库观点..."

cd /root/.openclaw/workspace
/usr/bin/python3 "$SCRIPTS_DIR/extract_ima_opinions.py" 2>&1 || echo "⚠ IMA 观点提取未完成"

# ── 4. 合并报告并推送 ──
echo ""
echo "=== 合并报告 ==="

ANALYSIS_FILE=$(find "$REPORT_DIR" -name "trading_analysis_${DATE_TAG}*" -type f 2>/dev/null | head -1)
OPINIONS_FILE="$REPORT_DIR/opinions_${DATE_TAG}.md"
FINAL_FILE="$REPORT_DIR/daily_report_${DATE_TAG}.md"

{
    echo "# 📊 A股收盘分析 · $(TZ=Asia/Shanghai date +%Y%m%d)"
    echo ""
    echo "> 自动生成 | 数据: 新浪财经 + IMA知识库 | 分析: DeepSeek"
    echo "> 免责声明: AI模拟分析，不构成投资建议"
    echo ""

    # Section 1: 技术分析
    if [ -f "$ANALYSIS_FILE" ]; then
        tail -n +2 "$ANALYSIS_FILE" | head -c 4000
        FILE_SIZE=$(wc -c < "$ANALYSIS_FILE")
        if [ "$FILE_SIZE" -gt 4500 ]; then
            echo ""
            echo "*（完整报告过长，仅展示摘要）*"
        fi
    else
        echo "⚠ 技术分析报告生成失败，请检查日志"
        echo "   Log: $LOG_FILE"
    fi

    echo ""
    echo "---"
    echo ""

    # Section 2: 外部观点
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

# 使用 openclaw message send 推送到群
openclaw message send \
    --target "channel:dingtalk-connector:chat:${TARGET_CHAT}" \
    --media "$FINAL_FILE" 2>&1 || {

    # 备用方案：直接使用 sessions_send
    echo "直接推送失败，尝试备用路径..."
    openclaw message send \
        --channel dingtalk-connector \
        --target "user:${TARGET_CHAT}" \
        --media "$FINAL_FILE" 2>&1 || {
        echo "✗ 所有推送路径均失败"
    }
}

echo ""
echo "============================================================"
echo "完成 — $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S') CST"
echo "============================================================"
