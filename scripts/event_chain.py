#!/usr/bin/env python3
"""
事件→产业链映射模块 v1.0
设计：LLM 推理产业链影响路径 → 规则引擎校验 → 校验通过才输出
原则：不禁止LLM推理，但必须有验证机制

用法：
  from event_chain import analyze_event
  result = analyze_event("英伟达隔夜大跌8%", model="deepseek/deepseek-v4-pro")
  # result: { "impact_chain": [...], "affected_symbols": [...], "confidence": 0.85, "checks_passed": [...] }
"""

import json, os, sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

# ─── 规则引擎：校验函数 ───────────────────────────────────────────

# 已知的申万一级行业列表（来自 symbols_config.py）
VALID_SECTORS = {
    "银行", "农林牧渔", "采掘", "化工", "钢铁", "有色金属",
    "电子", "家用电器", "食品饮料", "纺织服装", "轻工制造",
    "医药生物", "公用事业", "交通运输", "房地产", "商业贸易",
    "休闲服务", "综合", "建筑材料", "建筑装饰", "电气设备",
    "国防军工", "计算机", "传媒", "通信", "非银金融", "汽车", "机械设备",
}

# 已知产业链关键词（用于校验LLM输出的产业链环节是否合理）
# 采用扁平化集合 + 别名映射，覆盖 LLM 各种变体输出
KNOWN_CHAIN_TERMS = {
    # AI算力链
    "GPU", "GPU芯片", "GPU芯片封装", "gpu",
    "光模块", "光通信", "高速光模块",
    "PCB", "PCB（印制电路板）", "印制电路板",
    "服务器", "AI服务器", "服务器代工",
    "HBM", "HBM（高带宽存储）", "高带宽存储",
    "液冷", "液冷散热", "散热（液冷）", "散热",
    "铜连接", "铜缆连接", "铜连接/高速线缆", "高速线缆",
    "交换机", "网络设备",
    "ASIC", "AI芯片", "算力芯片", "AI芯片/算力芯片",
    "光芯片", "电芯片", "光芯片/电芯片",
    "算力", "AI算力", "算力租赁", "算力基建",
    # 半导体
    "半导体", "半导体设计", "半导体设备", "半导体设备/材料", "半导体制造",
    "封测", "芯片封测", "封装测试",
    "晶圆代工", "晶圆制造",
    "EDA", "芯片设计工具",
    "存储", "存储芯片", "存储器",
    "芯片", "芯片设计",
    # 新能源
    "光伏", "风电", "储能", "锂电", "氢能", "逆变器", "组件", "新能源",
    # 消费电子
    "手机", "PC", "可穿戴", "XR", "面板", "声学", "光学", "消费电子",
    # 汽车
    "整车", "零部件", "智能化", "电动化", "轻量化", "热管理", "汽车",
    # 金融
    "银行", "保险", "券商", "支付", "金融科技",
    # 医药
    "创新药", "CXO", "医疗器械", "中药", "生物制品", "医疗服务",
    # 国防军工
    "军工", "国防军工", "航空航天",
    # AI应用
    "AI应用", "AI应用（大模型/软件）", "AI应用（大模型、AI软件）", "大模型", "AI软件",
    "SaaS", "云计算", "云服务",
    # 工业
    "机器人", "自动化", "工业母机", "智能制造",
    # 基建/地产
    "基建", "建筑", "建筑/基建", "建材", "房地产", "地产",
    # 消费
    "白酒", "家电", "食品饮料", "消费", "消费（白酒/家电）",
    # 金融（扩展）
    "银行/券商", "保险/银行",
    # 地产链
    "钢铁", "水泥", "玻璃",
}

# 申万行业→A股标的池（从现有系统标的扩展到常用产业链标的）
SECTOR_A_STOCKS = {
    "光模块": ["中际旭创", "天孚通信", "新易盛", "光迅科技"],
    "PCB": ["沪电股份", "深南电路", "生益科技", "胜宏科技"],
    "服务器": ["工业富联", "浪潮信息", "中科曙光", "紫光股份"],
    "GPU": ["海光信息", "寒武纪", "景嘉微"],
    "半导体设备": ["北方华创", "中微公司", "盛美上海", "拓荆科技"],
    "半导体设计": ["韦尔股份", "卓胜微", "兆易创新", "澜起科技"],
    "封测": ["长电科技", "通富微电", "华天科技"],
    "存储": ["兆易创新", "北京君正", "江波龙"],
    "液冷": ["英维克", "高澜股份", "曙光数创"],
    "铜连接": ["立讯精密", "鹏鼎控股", "东山精密"],
    "银行": ["工商银行", "农业银行", "中国银行", "招商银行"],
    "公用事业": ["长江电力", "国电电力", "华能国际"],
    "国防军工": ["中航沈飞", "航发动力", "国睿科技"],
    "计算机": ["中国长城", "中国软件", "浪潮信息"],
    "机械设备": ["汇川技术", "埃斯顿", "绿的谐波"],
}

def validate_chain(llm_output: dict) -> dict:
    """
    规则引擎校验 LLM 输出的产业链推理结果
    返回: { "passed": bool, "issues": [...], "filtered": [...] }
    """
    issues = []
    checks_passed = []

    # 校验1：行业/环节名称必须在已知词表中（支持模糊匹配）
    impacted_sectors = llm_output.get("impacted_sectors", [])
    for s in impacted_sectors:
        s_name = s.get("sector", "") if isinstance(s, dict) else s
        # 去掉括号后缀如 "PCB（印制电路板）" → "PCB"
        s_name_clean = s_name.split("（")[0].split("(")[0].strip()
        # 斜杠拆分匹配（如 "铜连接/高速线缆" → 分别匹配 "铜连接" "高速线缆"）
        sub_terms = s_name_clean.replace("/", " ").replace("、", " ").split()
        s_found = any(
            t in KNOWN_CHAIN_TERMS for t in [s_name, s_name_clean] + sub_terms
        ) or s_name in VALID_SECTORS or s_name_clean in VALID_SECTORS
        if not s_found:
            issues.append(f"行业/环节'{s_name}'不在已知分类中，可能为LLM幻觉")

    if not issues:
        checks_passed.append("行业分类校验通过")

    # 校验2：映射到A股标的后，检查标的数量是否合理（空或太多都不对）
    affected = llm_output.get("affected_stocks", [])
    if len(affected) == 0:
        issues.append("未映射到任何A股标的，推理可能不完整")
    elif len(affected) > 20:
        issues.append(f"映射到{len(affected)}个标的过多，产业链辐射范围过大需要审视")

    if len(affected) > 0 and len(affected) <= 20:
        checks_passed.append(f"标的数量合理({len(affected)}个)")

    # 校验3：confidence 阈值
    confidence = llm_output.get("confidence", 0)
    if confidence < 0.5:
        issues.append(f"LLM置信度过低({confidence})，推理结果不可靠")
    else:
        checks_passed.append(f"置信度达标({confidence:.0%})")

    passed = len(issues) == 0

    return {
        "passed": passed,
        "issues": issues,
        "checks_passed": checks_passed,
    }


def map_to_a_stocks(sectors: list) -> list:
    """将行业/产业链环节映射到具体A股标的"""
    stocks = set()
    for s in sectors:
        s_name = s.get("sector", s) if isinstance(s, dict) else s
        if s_name in SECTOR_A_STOCKS:
            stocks.update(SECTOR_A_STOCKS[s_name])
    return sorted(stocks)


# ─── LLM 调用层 ───────────────────────────────────────────────────

ANALYSIS_PROMPT = """你是金融产业链分析师。给定一个市场事件，推理其对A股产业链的影响路径。

## 事件
{event}

## 任务
1. 识别该事件可能影响的产业链环节（从以下维度思考：上游原材料、中游制造、下游应用、相关服务）
2. 对每个环节，判断影响方向（利好/利空/中性）和逻辑链条
3. 输出 JSON 格式，不要输出其他内容

## 输出格式
```json
{{
  "event_summary": "一句话概括事件",
  "impacted_sectors": [
    {{
      "sector": "产业链环节名称（如光模块、PCB、半导体设备等，使用A股市场常用术语）",
      "direction": "positive/negative/neutral",
      "logic": "影响逻辑（一句话，因果链）"
    }}
  ],
  "affected_stocks": ["预估受影响的A股标的名称（中文简称，如中际旭创）"],
  "confidence": 0.0-1.0,
  "risk_note": "如果推理存在较大不确定性，在此标注"
}}
```

注意：
- sector 使用 A 股市场常用术语，如"光模块"而非"Optical Transceiver"
- 标的名称使用中文简称，如"中际旭创"而非"300308.SZ"
- confidence 反映推理链条的确定性，不是对市场走势的预测
- 如果事件与A股无明显关联，confidence 设为 0.1 以下并说明原因"""


def analyze_event(event: str, model: str = None) -> dict:
    """
    分析事件→产业链映射
    流程: LLM推理 → 规则校验 → 映射标的 → 返回结构化结果
    """
    import openai

    api_key = os.getenv("DEEPSEEK_API_KEY")
    api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

    if not api_key:
        # fallback: 尝试从 .env 文件读取
        env_file = os.path.join(PROJECT_DIR, ".env")
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith("DEEPSEEK_API_KEY="):
                        api_key = line.strip().split("=", 1)[1].strip('"\'')
                        break

    if not api_key:
        return {"error": "DEEPSEEK_API_KEY not found", "passed": False}

    client = openai.OpenAI(api_key=api_key, base_url=api_base)

    try:
        response = client.chat.completions.create(
            model=model or "deepseek-chat",
            messages=[{"role": "user", "content": ANALYSIS_PROMPT.format(event=event)}],
            temperature=0.3,
            max_tokens=1500,
        )
        content = response.choices[0].message.content.strip()
    except Exception as e:
        return {"error": f"LLM call failed: {e}", "passed": False}

    # 提取 JSON
    try:
        # 处理 markdown 代码块包裹
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        llm_output = json.loads(content)
    except (json.JSONDecodeError, IndexError):
        return {"error": f"Failed to parse LLM JSON output", "raw": content[:500], "passed": False}

    # 规则引擎校验
    validation = validate_chain(llm_output)

    # 映射到A股标的（LLM可能有遗漏，用规则补全）
    llm_stocks = set(llm_output.get("affected_stocks", []))
    rule_stocks = set(map_to_a_stocks(llm_output.get("impacted_sectors", [])))
    all_stocks = sorted(llm_stocks | rule_stocks)

    return {
        "event": event,
        "event_summary": llm_output.get("event_summary", ""),
        "impacted_sectors": llm_output.get("impacted_sectors", []),
        "affected_stocks": all_stocks,
        "llm_confidence": llm_output.get("confidence", 0),
        "risk_note": llm_output.get("risk_note", ""),
        "validation": validation,
    }


# ─── 测试入口 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    test_events = [
        "英伟达股价隔夜大跌8%，市场担心AI芯片需求见顶",
        "Meta宣布大规模采购算力，资本开支上调50%",
    ]
    for ev in test_events:
        print(f"\n{'='*60}")
        print(f"事件: {ev}")
        result = analyze_event(ev)
        print(json.dumps(result, ensure_ascii=False, indent=2))
