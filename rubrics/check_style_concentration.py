#!/usr/bin/env python3
"""check_style_concentration.py — 风格依赖检查

检测交易推荐输出是否存在风格过度集中：
1. 方向集中度：所有推荐指向同一方向（全买/全卖）→ 可能押注单一市场风格
2. 板块集中度：标的集中在同一行业 → 缺乏分散性
3. 乖离率集中度：所有信号乖离率同向且绝对值接近 → 可能同质化选股

原理依据：百亿私募衍复投资顾王琴访谈指出，"短期冠军多是押中市场风格"，
风格集中可能导致风控放宽、跟风追涨，需要醒目风险提示。

用法: python3 rubrics/check_style_concentration.py <trade_signals_file>
输出: JSON {pass, issues, concentration_score}
exit code: 0=通过, 1=风格集中告警(level>=2)
"""

import sys, os, re, json

# 板块映射表（标的 → 行业标签）
SECTOR_MAP = {
    "贵州茅台": "消费/白酒", "五粮液": "消费/白酒",
    "招商银行": "金融/银行", "农业银行": "金融/银行", "中国银行": "金融/银行",
    "工商银行": "金融/银行", "建设银行": "金融/银行",
    "中国平安": "金融/保险",
    "国电电力": "能源/电力", "长江电力": "能源/电力",
    "中国长城": "科技/信创", "国睿科技": "科技/军工",
    "中兴通讯": "科技/通信", "海康威视": "科技/安防",
    "药明康德": "医药/CXO", "恒瑞医药": "医药/创新药",
    "宁德时代": "新能源/电池", "比亚迪": "新能源/整车",
    "中国移动": "通信/运营商", "中国联通": "通信/运营商",
    "上证50": "指数", "沪深300": "指数", "科创50": "指数",
}

# 行业大类映射（收拢到大类）
SECTOR_BROAD = {
    "消费/白酒": "消费",
    "金融/银行": "金融", "金融/保险": "金融",
    "能源/电力": "能源",
    "科技/信创": "科技", "科技/军工": "科技", "科技/通信": "科技", "科技/安防": "科技",
    "医药/CXO": "医药", "医药/创新药": "医药",
    "新能源/电池": "新能源", "新能源/整车": "新能源",
    "通信/运营商": "通信",
    "指数": "指数",
}


def parse_signal_block(text: str) -> list[dict]:
    """解析交易信号表，提取每只标的信息"""
    signals = []
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


def check_direction_concentration(signals: list[dict]) -> dict:
    """方向集中度检查：全买或全卖为高风险"""
    actions = [s["action"] for s in signals]
    unique_actions = set(actions)
    
    if len(unique_actions) <= 1 and len(signals) >= 3:
        action = list(unique_actions)[0]
        return {
            "level": 2,  # 高风险
            "issue": f"方向高度集中：全部{len(signals)}只标的均为'{action}'信号，可能押注单一市场风格",
        }
    elif len(unique_actions) <= 1 and len(signals) == 2:
        return {
            "level": 1,  # 中风险
            "issue": f"方向较集中：{len(signals)}只标的均为'{list(unique_actions)[0]}'信号",
        }
    return {"level": 0}


def check_sector_concentration(signals: list[dict]) -> dict:
    """板块集中度检查：同一大类行业占比过高"""
    sectors = []
    for s in signals:
        sec = SECTOR_MAP.get(s["name"])
        if sec:
            sectors.append(SECTOR_BROAD.get(sec, sec))
    
    if len(sectors) < 2:
        return {"level": 0}
    
    # 统计大类分布
    from collections import Counter
    counts = Counter(sectors)
    
    # 判断：最大行业占比是否超过 50% 且 ≥2 只标的
    max_sector, max_count = counts.most_common(1)[0]
    ratio = max_count / len(sectors)
    
    if ratio >= 0.75 and max_count >= 2:
        return {
            "level": 2,
            "issue": f"板块高度集中：{max_sector}类占{max_count}/{len(sectors)}({ratio:.0%})",
        }
    elif ratio >= 0.5 and max_count >= 2:
        return {
            "level": 1,
            "issue": f"板块较集中：{max_sector}类占{max_count}/{len(sectors)}({ratio:.0%})",
        }
    return {"level": 0}


def check_bias_concentration(signals: list[dict]) -> dict:
    """乖离率集中度：全部同向且跨度<10%表示同质化选股"""
    biases = [s["bias"] for s in signals]
    if len(biases) < 2:
        return {"level": 0}
    
    all_positive = all(b > 0 for b in biases)
    all_negative = all(b < 0 for b in biases)
    
    if all_positive or all_negative:
        bias_range = max(biases) - min(biases)
        if bias_range < 5:
            return {
                "level": 2,
                "issue": f"乖离率高度同质：全部{'+' if all_positive else ''}方向，跨度仅{bias_range:.1f}%",
            }
        elif bias_range < 10:
            return {
                "level": 1,
                "issue": f"乖离率较同质：全部{'+' if all_positive else ''}方向，跨度{bias_range:.1f}%",
            }
    return {"level": 0}


def main():
    signal_file = sys.argv[1]
    with open(signal_file) as f:
        text = f.read()
    
    signals = parse_signal_block(text)
    if len(signals) < 2:
        result = {
            "pass": True,
            "concentration_score": 0,
            "checked": len(signals),
            "issues": [],
            "note": "标的数不足2只，跳過风格集中检查",
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    
    checks = [
        check_direction_concentration(signals),
        check_sector_concentration(signals),
        check_bias_concentration(signals),
    ]
    
    issues = []
    total_level = 0
    
    for check in checks:
        if check.get("level", 0) > 0:
            issues.append(check["issue"])
            total_level += check["level"]
    
    # concentration_score: 0-6
    concentration_score = min(total_level, 6)
    passed = concentration_score < 2  # level 0-1 通过，≥2 告警
    
    result = {
        "pass": passed,
        "concentration_score": concentration_score,
        "checked": len(signals),
        "issues": issues,
        "details": {
            "signals_checked": len(signals),
            "actions": [s["action"] for s in signals],
            "names": [s["name"] for s in signals],
            "biases": [s["bias"] for s in signals],
        },
    }
    
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
