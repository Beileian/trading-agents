#!/usr/bin/env python3
"""
从 IMA 知识库提取金融类文章观点，按时效加权后注入交易分析报告。

数据源: IMA 个人知识库「公众号文章」文件夹
来源标签: 二小姐笔记 / EarlETF / 暮云思辨
加权策略: 当日文章权重1.0，每早一天衰减15%，7天前权重≈0.32

设计原则:
- 二小姐: 实战ETF发车/估值信号，提取可操作结论
- EarlETF: 量化+指数+图表，提取数据驱动的趋势判断
- 暮云思辨: 保留哲学性叙述，不强制提取"信号"，保持原文调性
"""

import subprocess, json, os, sys
from datetime import datetime, timezone, timedelta

SKILL_DIR = "/root/.openclaw/workspace/skills/ima-skills"
API_CJS = os.path.join(SKILL_DIR, "ima_api.cjs")
REPORT_DIR = "/root/.openclaw/workspace/projects/trading-agents/reports"

TZ_SHANGHAI = timezone(timedelta(hours=8))

# ── 搜索关键词 ──
FINANCE_KEYWORDS = [
    "A股", "股票", "ETF", "投资", "指数", "基金", "估值", "红利",
    "茅台", "五粮液", "平安银行", "同花顺", "海康威视",
    "交易", "行情", "牛市", "熊市", "价值", "成长", "微盘",
    "大盘", "沪深300", "中证", "上证", "深证"
]

# ── 权重衰减 ──
def date_weight(note_date: str) -> float:
    """当日=1.0, 每早一天衰减15%, 下限0.1"""
    today = datetime.now(TZ_SHANGHAI).date()
    try:
        d = datetime.fromisoformat(note_date).date()
        days = (today - d).days
        w = max(0.10, 1.0 - days * 0.15)
        return round(w, 2)
    except:
        return 0.30  # unknown date = low weight

def source_label(title: str, content: str) -> str:
    """根据标题和内容判断来源"""
    full_text = title + '\n' + content[:500]
    full_lower = full_text.lower()
    
    # 二小姐 signals — check first (strongest signature)
    if any(kw in full_text for kw in ['二姐', '二小姐']):
        return '二小姐笔记'
    
    # 暮云思辨 signals — check before EarlETF
    if any(kw in full_text for kw in ['原创 与慕同行', '与慕同行']):
        return '暮云思辨'
    
    # Philosophical/trading-reflective tone titles from 暮云
    if any(kw in title for kw in ['一辈子的交易', '一代人', '从教育到交易', '上桌吃菜']):
        if '与慕同行' in full_text:
            return '暮云思辨'
    
    # EarlETF signals
    if any(kw in full_lower for kw in ['earletf', '张翼轸']):
        return 'EarlETF'
    
    # Check author lines
    for line in content.split('\n')[:8]:
        if '与慕同行' in line:
            return '暮云思辨'
        if '张翼轸' in line or 'Earl' in line:
            return 'EarlETF'
        if '二姐' in line:
            return '二小姐笔记'
    
    return '其他'

def search_notes(query: str, max_results=10):
    """搜索 IMA 笔记"""
    payload = json.dumps({
        "search_type": 1,
        "query_info": {"content": query},
        "start": 0,
        "end": max_results
    })
    cmd = f'node {API_CJS} "openapi/note/v1/search_note" \'{payload}\' "{{}}"'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout.strip() or '{}')
        return data.get('data', {}).get('search_note_infos', [])
    except:
        return []

def get_note_content_md(note_id: str) -> str:
    """获取笔记 Markdown 格式内容（fallback）"""
    payload = json.dumps({"note_id": note_id, "content_format": 1})
    cmd = f'node {API_CJS} "openapi/note/v1/get_doc_content" \'{payload}\' "{{}}"'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout.strip() or '{}')
        return data.get('data', {}).get('content', '')
    except:
        return ''

def get_note_content(note_id: str) -> str:
    """获取笔记纯文本内容"""
    payload = json.dumps({"note_id": note_id, "content_format": 0})
    cmd = f'node {API_CJS} "openapi/note/v1/get_doc_content" \'{payload}\' "{{}}"'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        data = json.loads(r.stdout.strip() or '{}')
        return data.get('data', {}).get('content', '')
    except:
        return ''

def extract_opinion(title: str, content: str, source: str) -> str:
    """根据来源类型提取观点摘要"""
    # Use DeepSeek to summarize, or extract first meaningful paragraph
    # For now: heuristic extraction based on source type
    para = content.strip().split('\n\n')
    
    if source == '暮云思辨':
        # 保持哲学调性 — 保留叙事和隐喻，不破坏原文节奏
        # 只跳过纯元数据行，保留所有叙事段落
        meaningful = []
        for p in para:
            p = p.strip()
            if len(p) < 15:
                continue
            # Only skip pure metadata/header lines
            if p.startswith('原创') and ('2026' in p or '2025' in p):
                continue
            if p.startswith('▲点击') or p.startswith('大家好') or p.startswith('关注我'):
                continue
            if '▲点击上方卡片' in p:
                continue
            meaningful.append(p)
        if not meaningful:
            return content[:500]
        return '\n\n'.join(meaningful[:4])
    
    elif source == 'EarlETF':
        # 提取数据驱动的结论
        conclusions = []
        for p in para:
            p = p.strip()
            if len(p) < 30:
                continue
            if '原创' in p and ('2026' in p or '2025' in p):
                continue
            # EarlETF 风格: 找"毫无疑问""结论""所以"等标志词
            if any(kw in p for kw in ['毫无疑问', '结论', '这意味着', '本质上', '所以']):
                conclusions.append(p[:200])
                if len(conclusions) >= 2:
                    break
        if not conclusions:
            # Fallback: take first substantial paragraph
            for p in para:
                if len(p) > 80 and '原创' not in p:
                    conclusions.append(p[:200])
                    break
        return '\n'.join(conclusions) if conclusions else content[:300]
    
    elif source == '二小姐笔记':
        # 提取可操作信号: 发车/估值/策略
        signals = []
        for p in para:
            p = p.strip()
            if len(p) < 30:
                continue
            if '原创' in p and '2026' in p:
                continue
            if '▲点击' in p or '大家好' in p:
                continue
            # 找操作信号词
            if any(kw in p for kw in ['发车', '定投', '估值', '买入', '卖出', '仓位', '策略', '趋势']):
                signals.append(p[:250])
                if len(signals) >= 2:
                    break
        if not signals:
            for p in para:
                if len(p) > 80 and '原创' not in p:
                    signals.append(p[:250])
                    break
        return '\n'.join(signals) if signals else content[:300]
    
    else:
        return content[:300]

def main():
    print("=" * 60)
    print("IMA 金融观点提取 —", datetime.now(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)
    
    # 1. 搜索最近7天的金融文章
    seen_ids = set()
    articles = []
    
    for kw in FINANCE_KEYWORDS:
        results = search_notes(kw, max_results=10)
        for hit in results:
            info = hit.get('note_book_info', hit)
            nid = info.get('note_id', '')
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            
            title = info.get('title', '无标题')
            ctime_ms = info.get('create_time', '0')
            if ctime_ms and ctime_ms != '?':
                ts = int(ctime_ms) / 1000
                # Only last 7 days
                note_dt = datetime.fromtimestamp(ts, tz=TZ_SHANGHAI)
                note_date = note_dt.strftime('%Y-%m-%d')
                days_ago = (datetime.now(TZ_SHANGHAI) - note_dt).days
                if days_ago > 7:
                    continue
            else:
                note_date = 'unknown'
                continue
            
            articles.append({
                'note_id': nid,
                'title': title,
                'date': note_date,
                'days_ago': (datetime.now(TZ_SHANGHAI) - note_dt).days if ctime_ms else 7,
            })
    
    # 2. 去重，按日期排序
    articles.sort(key=lambda x: x['date'], reverse=True)
    unique = []
    seen_titles = set()
    for a in articles:
        key = a['title'][:40]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(a)
    
    print(f"\n找到 {len(unique)} 篇金融相关文章（最近7天）\n")
    
    # 3. 读取内容并提取观点
    sources = {'二小姐笔记': [], 'EarlETF': [], '暮云思辨': [], '其他': []}
    
    for i, art in enumerate(unique[:30]):
        content = get_note_content(art['note_id'])
        if not content or len(content) < 30:
            content = get_note_content_md(art['note_id'])
        if not content or len(content) < 30:
            continue
        
        src = source_label(art['title'], content)
        weight = date_weight(art['date'])
        opinion = extract_opinion(art['title'], content, src)
        
        entry = {
            'date': art['date'],
            'title': art['title'],
            'source': src,
            'weight': weight,
            'opinion': opinion,
            'days_ago': art['days_ago'],
        }
        sources[src].append(entry)
        
        w_bar = '█' * int(weight * 10)
        print(f"[{art['date']}] [{src}] w={weight:.2f} {w_bar}")
        print(f"  {art['title']}")
        print(f"  {opinion[:120]}...")
        print()
    
    # 4. 生成报告章节
    report = generate_opinion_section(sources)
    
    # 5. 写入缓存文件
    os.makedirs(REPORT_DIR, exist_ok=True)
    cache_path = os.path.join(REPORT_DIR, f"opinions_{datetime.now(TZ_SHANGHAI).strftime('%Y%m%d')}.md")
    with open(cache_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✓ 观点缓存已写入: {cache_path}")
    print(f"  总计: {sum(len(v) for v in sources.values())} 篇文章")
    for src, items in sources.items():
        if items:
            avg_w = sum(i['weight'] for i in items) / len(items)
            print(f"    {src}: {len(items)} 篇, 平均权重 {avg_w:.2f}")
    
    return cache_path


def generate_opinion_section(sources: dict) -> str:
    """生成报告中的「外部观点参考」章节"""
    lines = []
    lines.append("## 外部观点参考（IMA 知识库近7日）\n")
    lines.append(f"*提取时间: {datetime.now(TZ_SHANGHAI).strftime('%Y-%m-%d %H:%M')} | 数据源: IMA 个人知识库「公众号文章」*\n")
    lines.append("> 权重衰减: 当日=1.0, 每早一天-15%, 7日前≈0.32\n")
    
    source_config = [
        ('二小姐笔记', '🎯 ETF发车/实战信号', '二小姐笔记'),
        ('EarlETF', '📊 量化/指数/数据', 'EarlETF'),
        ('暮云思辨', '🧘 哲学思辨/宏观叙事', '暮云思辨'),
    ]
    
    for src_key, icon_desc, display_name in source_config:
        items = sources.get(src_key, [])
        if not items:
            continue
        
        # Sort by weight descending
        items.sort(key=lambda x: x['weight'], reverse=True)
        
        lines.append(f"\n### {icon_desc}\n")
        
        for item in items[:5]:  # Top 5 per source
            date = item['date']
            w = item['weight']
            title = item['title']
            opinion = item['opinion']
            
            w_tag = ''
            if w >= 0.9:
                w_tag = '🔥'
            elif w >= 0.7:
                w_tag = '⭐'
            
            lines.append(f"**{title}** [{date}] w={w:.2f} {w_tag}\n")
            
            if src_key == '暮云思辨':
                # 暮云文章保留完整段落，不做缩减
                lines.append(f"> {opinion.replace(chr(10), chr(10)+'> ')}\n")
            else:
                # 二小姐/EarlETF 提取核心观点
                lines.append(f"> {opinion[:300]}\n")
    
    lines.append("\n---\n")
    lines.append("*以上观点来自 IMA 知识库已保存的公众号文章，不代表投资建议。*\n")
    
    return '\n'.join(lines)

if __name__ == '__main__':
    main()
