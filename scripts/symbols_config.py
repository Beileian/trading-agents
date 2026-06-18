#!/usr/bin/env python3
"""
金桥标的列表 — 全局唯一配置
所有脚本通过 import symbols_config 获取，一处修改全局同步

格式说明:
  SYMBOLS            — trading_analysis 使用的 (ticker, 名称, 类型)
  TICKER_SINA_MAP    — update_daily_cache 新浪接口所需的 ticker → sina_code 映射
"""

# trading_analysis 格式: (ticker, 中文名, 类型: index|stock)
SYMBOLS = [
    ("000016.SH", "上证50指数", "index"),
    ("000300.SH", "沪深300指数", "index"),
    ("000688.SH", "科创50指数", "index"),
    ("601288.SH", "农业银行", "stock"),
    ("601988.SH", "中国银行", "stock"),
    ("600036.SH", "招商银行", "stock"),
    ("600795.SH", "国电电力", "stock"),
    ("000066.SZ", "中国长城", "stock"),
    ("600562.SH", "国睿科技", "stock"),
    ("562500.SH", "中证机器人", "stock"),
]

# update_daily_cache 格式: ticker → sina_api_code
TICKER_SINA_MAP = {
    "000016.SH": "sh000016",
    "000300.SH": "sh000300",
    "000688.SH": "sh000688",
    "601288.SH": "sh601288",
    "601988.SH": "sh601988",
    "600036.SH": "sh600036",
    "600795.SH": "sh600795",
    "000066.SZ": "sz000066",
    "600562.SH": "sh600562",
    "562500.SH": "sh562500",
}

# 从 SYMBOLS 动态生成 TICKER_SINA_MAP（自动同步，但保留显式定义以便审计）
def derive_sina_map() -> dict:
    """从 SYMBOLS 自动推导 TICKER_SINA_MAP，用于验证一致性"""
    result = {}
    for ticker, name, stype in SYMBOLS:
        code = ticker.split(".")[0]
        market = ticker.split(".")[1].lower()
        result[ticker] = f"{market}{code}"
    return result

# 启动时校验一致性
_derived = derive_sina_map()
for ticker, expected in TICKER_SINA_MAP.items():
    if ticker in _derived and _derived[ticker] != expected:
        import sys
        print(f"[symbols_config] WARN: TICKER_SINA_MAP[{ticker}]={expected} vs derived={_derived[ticker]}",
              file=sys.stderr)
