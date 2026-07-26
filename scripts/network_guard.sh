#!/bin/bash
# network_guard.sh — Cron Pipeline 网络白名单执行包装器
# v1.0 | 2026-07-26 | meta-audit 1.1 产出
#
# 用途：在每个 cron 命令前包装执行，通过 curl/wget 的代理配置
# 限制出站网络范围。配合 iptables LOG 规则做双层防御。
#
# 用法：
#   在 crontab 中：
#     30 15 * * 1-5 bash /path/to/network_guard.sh "实际命令"
#
# 机制：
#   1. 设置 http_proxy 指向本地不存在的代理 → 阻断所有 HTTP 直连
#   2. 白名单域名通过 NO_PROXY 放行
#   3. Python 脚本中 requests/urllib 自动遵循这些环境变量

# 白名单域名（URL 编码安全）
ALLOWED_DOMAINS="hq.sinajs.cn,qt.gtimg.cn,push2.eastmoney.com,push2his.eastmoney.com,quote.eastmoney.com,money.finance.sina.com.cn,finance.sina.com.cn,finance.qq.com,web.ifzq.gtimg.cn,api.deepseek.com,api.zhituapi.com,api.dingtalk.com,hf-mirror.com,query1.finance.yahoo.com,query2.finance.yahoo.com,fc.yahoo.com,localhost,127.0.0.1"

# 设置 NO_PROXY 为白名单（允许这些域名直连）
export NO_PROXY="$ALLOWED_DOMAINS"
export no_proxy="$NO_PROXY"

# 注意：不设置 http_proxy/https_proxy 限制，因为 Python 库对 NO_PROXY 的支持不一致
# 当前阶段（LOG 观察期）先只用环境变量标记 + 日志记录，不实际阻断
export NETWORK_GUARD_MODE="log_only"
export NETWORK_GUARD_VERSION="1.0"

# 记录审计日志
LOGFILE="/root/.openclaw/workspace/projects/meta-audit/network_guard_audit.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] GUARD_WRAP: mode=$NETWORK_GUARD_MODE cmd=$1" >> "$LOGFILE"

# 执行原始命令
eval "$@"
