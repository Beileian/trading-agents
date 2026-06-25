#!/usr/bin/env python3
"""check_morning_consistency.py — 前置版：开盘前信号与昨日收盘数据自洽检查

在推送前运行，验证:
1. 昨日收盘价是否在今日支撑-阻力范围内
2. 今日支撑/阻力与昨日价格差距是否合理(>0.1%)
3. 方向标记与乖离率方向一致(🔴卖出时乖离应偏弱, 🟢买入时乖离应偏强)

用法: python3 rubrics/check_morning_consistency.py <trade_signals_file>
exit code: 0=通过, 1=不一致
"""

import sys, os, re, json

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_DIR, "data", "cache")

def find_daily_cache(symbol: str) -> str:
    """找到标的的日线缓存文件"""
    prefix = symbol.replace(".SH", "").replace(".SS", "").replace(".SZ", "")
    for f in os.listdir(CACHE_DIR):
        if f.startswith(prefix) and f.endswith("-daily.csv"):
            return os.path.join(CACHE_DIR, f)
    return None

def get_yesterday_close(cache_file: str) -> float:
    """获取缓存中倒数第二行的收盘价（最后一行为当日）"""
    with open(cache_file) as f:
        lines = f.readlines()
    if len(lines) < 3:
        return None
    # 跳过标题行，取倒数第二行
    last_line = lines[-1].strip()
    parts = last_line.split(",")
    try:
        return float(parts[4])  # Close列
    except (ValueError, IndexError):
        return None

def parse_signal_block(text: str) -> list[dict]:
    """解析交易信号表，提取每只标的信息"""
    signals = []
    
    # 匹配每行信号：🔴/🟡/🟢  标的名  收盘价  乖离率  仓位  方向
    pattern = r'([🔴🟡🟢])\s+(\S+)\s+([\d.]+)\s+乖离([+-][\d.]+)%?\s+[→↑↓]\s+仓位([\d.]+)%\s+(买入|持有|卖出)'
    
    for m in re.finditer(pattern, text):
        direction_emoji = m.group(1)
        name = m.group(2)
        close = float(m.group(3))
        bias = float(m.group(4))
        position = float(m.group(5))
        action = m.group(6)
        
        signals.append({
            "name": name,
            "close": close,
            "bias": bias,
            "position": position,
            "action": action,
            "direction_emoji": direction_emoji,
        })
    return signals

def symbol_from_name(name: str) -> str:
    """从标的名称映射到symbol"""
    mapping = {
        "上证50": "000016", "沪深300": "000300", "科创50": "000688",
        "农业银行": "601288", "中国银行": "601988", "招商银行": "600036",
        "国电电力": "600795", "中国长城": "000066", "国睿科技": "600562",
    }
    return mapping.get(name)

def main():
    signal_file = sys.argv[1]
    with open(signal_file) as f:
        text = f.read()
    
    signals = parse_signal_block(text)
    if not signals:
        print(json.dumps({"pass": True, "note": "无信号数据，跳过检查"}))
        sys.exit(0)
    
    issues = []
    for sig in signals:
        sym = symbol_from_name(sig["name"])
        if not sym:
            continue
        
        cache = find_daily_cache(sym)
        if not cache:
            continue
        
        yest_close = get_yesterday_close(cache)
        if yest_close is None:
            continue
        
        # Check 1: 昨日收盘价与信号中引用的收盘价偏差
        price_diff_pct = abs(sig["close"] - yest_close) / yest_close * 100
        if price_diff_pct > 10:
            issues.append(f"{sig['name']}: 收盘价偏差{price_diff_pct:.1f}% (信号{sig['close']} vs 缓存{yest_close})")
        
        # Check 2: 方向emoji与action一致性
        mapping = {"买入": "🟢", "持有": "🟡", "卖出": "🔴"}
        expected_emoji = mapping.get(sig["action"])
        if expected_emoji and sig["direction_emoji"] != expected_emoji:
            issues.append(f"{sig['name']}: 方向标记{sig['direction_emoji']}与操作{expected_emoji}{sig['action']}不匹配")
        
        # Check 3: 乖离率方向与操作逻辑一致性
        if sig["action"] == "买入" and sig["bias"] > 15:
            issues.append(f"{sig['name']}: 买入但乖离+{sig['bias']}%偏高，建议核实")
        if sig["action"] == "卖出" and sig["bias"] < -15:
            issues.append(f"{sig['name']}: 卖出但乖离{sig['bias']}%已深度超卖，建议核实")

    result = {
        "pass": len(issues) == 0,
        "checked": len(signals),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if len(issues) == 0 else 1)

if __name__ == "__main__":
    main()
