"""
price_data.py — 价格数据源通道文件（方向3：多后端声明式路由）

每个通道 = 一组有序后端列表，按优先级探测，第一个健康即当选。
与 Agent Reach channels/ 架构同源。

通道列表:
  1. 腾讯财经 (qt.gtimg.cn)     — 实时，免费，GBK 编码
  2. 东方财富 (push2.eastmoney)  — 实时，逐标拉取，index 优先
  3. 新浪财经 (hq.sinajs.cn)    — 个股免费无限额，指数不可靠
  4. VPS 东方财富 (硅谷跳板机)   — 当 direct 被限流时走代理
  5. AKShare (akshare)          — 备用，Tushare 同类但无 token 依赖
"""

# ── 通道注册表 ──
# 格式: backend_id → {priority, probe_cmd, roles, notes}
PRICE_BACKENDS = {
    "tencent": {
        "priority": 1,
        "desc": "腾讯财经",
        "probe": "curl -s --max-time 5 'https://qt.gtimg.cn/q=sh000001' | grep -q 'v_sh000001'",
        "roles": ["index", "stock"],
        "url_template": "https://qt.gtimg.cn/q={codes}",
        "encoding": "gbk",
        "best_for": "实时行情，10个标的批量拉取",
        "failure_mode": "网络超时 / 返回空",
        "degrade_after": 5,    # 连续失败次数触发降级
        "degrade_seconds": 1800 # 降级30分钟
    },
    "eastmoney": {
        "priority": 2,
        "desc": "东方财富",
        "probe": "curl -s --max-time 5 'https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43' | python3 -c \"import json,sys; d=json.load(sys.stdin); exit(0 if d.get('data') else 1)\"",
        "roles": ["index", "stock"],
        "url_template": "https://push2.eastmoney.com/api/qt/stock/get?secid={code}&fields=f43,f44,f45,f46,f47,f57,f58,f60,f170",
        "best_for": "指数实时价，字段明确 f43=最新价",
        "failure_mode": "限流 / 返回空 data",
        "degrade_after": 5,
        "degrade_seconds": 1800
    },
    "eastmoney_vps": {
        "priority": 3,
        "desc": "东方财富(VPS跳板)",
        "probe": "ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no root@49.51.33.96 'curl -s --max-time 5 \"https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43\"' | python3 -c \"import json,sys; d=json.load(sys.stdin); exit(0 if d.get('data') else 1)\"",
        "roles": ["index", "stock"],
        "best_for": "东方财富限流时通过硅谷IP绕过",
        "failure_mode": "SSH超时 / VPS不可达",
        "degrade_after": 3,
        "degrade_seconds": 900,
        "requires": "ssh root@49.51.33.96"
    },
    "sina": {
        "priority": 4,
        "desc": "新浪财经",
        "probe": "curl -s --max-time 5 'http://hq.sinajs.cn/list=sh601288' | grep -q 'hq_str_sh601288'",
        "roles": ["stock"],  # 仅个股，指数不可靠
        "url_template": "http://hq.sinajs.cn/list={codes}",
        "encoding": "gbk",
        "best_for": "个股 free 无限额",
        "failure_mode": "网络超时 / 编码问题",
        "degrade_after": 5,
        "degrade_seconds": 1800
    },
    "akshare": {
        "priority": 5,
        "desc": "AKShare(东方财富包装)",
        "probe": "python3 -c 'import akshare; akshare.stock_zh_index_daily(symbol=\"sh000001\")' 2>/dev/null",
        "roles": ["index", "stock"],
        "best_for": "离线备用，不需 token",
        "failure_mode": "包未安装 / API 变更",
        "degrade_after": 2,
        "degrade_seconds": 3600,
        "note": "非实时，T+1 数据。勿用于盘中价格"
    }
}

# ── 通道路由决策表 ──
# 不同场景的首选+备选链
ROUTING_TABLE = {
    "live_price": {
        "desc": "实时价位（盘中推送/交叉验证）",
        "chain": ["tencent", "eastmoney", "eastmoney_vps"]
    },
    "index_price": {
        "desc": "指数价格（上证/沪深/科创）",
        "chain": ["eastmoney", "tencent", "akshare"]
    },
    "stock_price": {
        "desc": "个股价格",
        "chain": ["tencent", "eastmoney", "sina", "eastmoney_vps"]
    },
    "historical_daily": {
        "desc": "历史日线",
        "chain": ["akshare", "tencent"]  # akshare 历史数据更好
    },
    "fallback_only": {
        "desc": "兜底（任一可用）",
        "chain": ["tencent", "eastmoney", "sina", "eastmoney_vps", "akshare"]
    }
}
