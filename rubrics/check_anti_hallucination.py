#!/usr/bin/env python3
"""
check_anti_hallucination.py — 交易推荐过程审计脚本
对应 rubrics/trade_recommendation.json v3.4.0 的 anti_hallucination_audit 项

基于 Qwen/Fudan "The Verification Horizon" 论文启发：
不只看输出结果，也审计生成过程是否诚实——检测虚构数据、
无来源裸数字、模糊技术位等反幻觉铁律违规。

输入：stdin 或 --file 指定交易推荐文本
输出：exit 0 = 无违规 / exit 1 = 有违规（veto）
"""

import sys
import re
import json
import argparse

# ── 审计规则 ──

def check_bare_number(text):
    """检测裸数字：价格/涨跌幅/PE/市值等数字必须绑定来源"""
    patterns = [
        (r'PE[约]?\s*[:：]?\s*(\d+\.?\d*)', 'PE值无来源'),
        (r'市值[约]?\s*[:：]?\s*(\d+\.?\d*)亿', '市值数字无来源'),
        (r'涨跌幅?[约]?\s*[:：]?\s*([+-]?\d+\.?\d*%?)', '涨跌幅无来源'),
        (r'价格[约]?\s*[:：]?\s*(\d+\.?\d*)', '价格数字无来源'),
    ]
    violations = []
    for pattern, desc in patterns:
        for m in re.finditer(pattern, text):
            context = text[max(0, m.start()-20):m.end()+20]
            # 检查附近是否有来源关键词
            nearby = text[max(0, m.start()-80):m.end()]
            has_source = any(kw in nearby for kw in [
                'Sina', 'akshare', '快照', '日线', '收盘', '数据来源',
                'wind', '东方财富', '同花顺', '交易所', '财报'
            ])
            if not has_source:
                violations.append(f"{desc}: {context.strip()}")
    return violations


def check_vague_level(text):
    """检测模糊技术位：支撑/阻力位必须有日期+价格"""
    vague_terms = ['附近', '一带', '平台', '区域', '左右', '差不多', '大概', '约在']
    violations = []

    for term in vague_terms:
        for m in re.finditer(term, text):
            context = text[max(0, m.start()-40):m.end()+40]
            # 如果附近没有日期格式或具体价格，判定违规
            nearby = text[max(0, m.start()-120):m.end()+120]
            has_date = bool(re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', nearby)) or \
                       bool(re.search(r'\d{1,2}月\d{1,2}日', nearby)) or \
                       bool(re.search(r'\d{1,2}\.\d{1,2}', nearby))
            has_price = bool(re.search(r'\d{2,4}\.\d{2}', nearby))

            if not (has_date and has_price):
                violations.append(f"模糊技术位: ...{context.strip()}...")
    return violations


def check_trend_evidence(text):
    """检测趋势判断是否有具体指标数值"""
    # 查找"趋势"相关的断言，检查是否有数值支撑
    trend_sentences = re.finditer(
        r'[^。\n]*?(?:走势|趋势|方向|多头|空头|看多|看空|看涨|看跌|反弹|回调|突破)[^。\n]*?[。\n]',
        text
    )

    violations = []
    for m in trend_sentences:
        sentence = m.group()
        # 纯定性判断无数据支撑
        has_number = bool(re.search(r'\d+\.?\d*', sentence))
        has_indicator = any(kw in sentence for kw in [
            '均线', 'MA', 'RSI', 'MACD', 'KDJ', 'BOLL', '布林',
            '成交量', '换手率', 'PE', 'PB', 'ROE'
        ])
        vague_only = any(kw in sentence for kw in [
            '多头排列', '空头排列', '金叉', '死叉', '走强', '走弱', '向好', '向淡'
        ])

        if vague_only and not has_number and not has_indicator:
            violations.append(f"趋势判断缺乏指标数据: {sentence.strip()[:60]}...")

    return violations


def check_contradiction(text):
    """检测方向与数据矛盾"""
    violations = []

    # 简单模式：说多头排列但同时说均线空头/下跌
    if '多头排列' in text and re.search(r'空头排列|下跌趋势|均线死叉', text):
        violations.append("方向矛盾: 同时出现多头排列和空头/下跌信号")

    if '看多' in text and re.search(r'看空|不建议', text):
        # 可能是不同标的，检查是否同一段落
        pass

    return violations


def check_prior_memory(text):
    """检测是否凭记忆引用指数点位"""
    # 查找"沪指"/"上证"相关的点位引用
    index_refs = re.finditer(
        r'(?:沪指|上证|深指|创业板|科创板)[^。\n]*?(\d{3,5}\.?\d*)点?[^。\n]*?[。\n]',
        text
    )
    violations = []
    for m in index_refs:
        context = m.group()
        # 检查是否有数据来源标记
        nearby = text[max(0, m.start()-100):m.end()+100]
        has_source = any(kw in nearby for kw in [
            'AKShare', 'Sina', '实时', '最新', '收盘', '数据来源', '拉取',
            'sh000001', '399001', '399006'
        ])
        if not has_source:
            violations.append(f"指数点位未标注来源: ...{context.strip()[:60]}...")

    return violations


def audit(text):
    """执行所有审计规则，返回违规列表"""
    all_violations = []
    all_violations.extend(check_bare_number(text))
    all_violations.extend(check_vague_level(text))
    all_violations.extend(check_trend_evidence(text))
    all_violations.extend(check_contradiction(text))
    all_violations.extend(check_prior_memory(text))
    return all_violations


def main():
    parser = argparse.ArgumentParser(description='交易推荐反幻觉审计')
    parser.add_argument('--file', help='从文件读取推荐文本')
    parser.add_argument('--json', action='store_true', help='JSON 格式输出')
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    violations = audit(text)

    if args.json:
        result = {
            "passed": len(violations) == 0,
            "violation_count": len(violations),
            "violations": violations,
            "rules_checked": [
                "bare_number", "vague_level", "trend_evidence",
                "contradiction", "prior_memory"
            ]
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif violations:
        print(f"❌ 过程审计未通过 — {len(violations)} 项违规:")
        for i, v in enumerate(violations, 1):
            print(f"  {i}. {v}")
    else:
        print("✅ 过程审计通过")

    sys.exit(0 if len(violations) == 0 else 1)


if __name__ == '__main__':
    main()
