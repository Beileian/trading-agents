#!/usr/bin/env python3
"""
IMA 观点管线 v2.0 — 从知识库提取金融文章 + 时间衰减 + DeepSeek摘要
项目: 金桥量化 v2.5.0

合并 extract_ima_opinions.py (v1.1) + summarize_ima_opinions.py (v1) 为单一步骤。
一次运行完成: 提取→去重→衰减→排序→摘要→写入 opinions_{date}.md

用法: python3 ima_pipeline.py
输出: reports/opinions_{YYYYMMDD}.md
"""

import os, re, json, sys, subprocess
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"

# IMA API 配置
IMA_API_CJS = "/root/.openclaw/workspace/skills/ima-skills/ima_api.cjs"
KB_ID = "p2U2Du3TS2OyfEHx0JpUGTKQsZnE-eLmiUVedwnywEI="

# 知名文件夹（用于来源映射）
FOLDER_MAP = {
    'folder_7464136493508841': 'EarlETF',
    'folder_7464142336175585': '暮云思辨',
    'folder_7468274916795772': '二小姐笔记',
}

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"

# 衰减参数
DECAY_BASE = 0.85
MAX_ARTICLES = 30
MIN_WEIGHT = 0.10

AUTHOR_MAP = {
    'ETF发车/实战信号': '二小姐笔记',
    '量化/指数/数据': 'EarlETF',
    '哲学思辨/宏观叙事': '暮云思辨',
}


def _get_deepseek_key():
    """三级查找链：环境变量 → .env文件 → 硬编码fallback"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key
    return ""


def _date_weight(note_date):
    """等比衰减: w = 0.85^days, 下限0.1"""
    today = datetime.now(TZ).date()
    try:
        d = datetime.fromisoformat(str(note_date)[:10]).date()
        days = (today - d).days
        w = max(MIN_WEIGHT, DECAY_BASE ** days)
        return round(w, 2)
    except:
        return 0.30


def _call_ima_api(api_path, body):
    """调用 IMA API，失败时返回 None"""
    try:
        result = subprocess.run(
            ["node", IMA_API_CJS, api_path, json.dumps(body)],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(IMA_API_CJS),
        )
        if result.returncode != 0:
            print(f"[ima_api error] {result.stderr.strip()}", file=sys.stderr)
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[ima_api exception] {e}", file=sys.stderr)
        return None


def _ima_search_batch(queries, kb_id=KB_ID, per_query=15):
    """批量搜索 IMA 知识库，去重后返回文章列表"""
    seen_ids = set()
    articles = []
    for q in queries:
        data = _call_ima_api("openapi/note/v1/search_note", {
            "search_type": 1,
            "query_info": {"content": q},
            "start": 0,
            "end": per_query,
            "knowledge_base_id": kb_id,
        })
        if not data:
            continue
        for info in data.get("data", {}).get("search_note_infos", []):
            nb = info.get("note_book_info", info)
            nid = nb.get("note_id", "")
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            ts = nb.get("create_time", "0")
            try:
                note_date = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
            except:
                note_date = datetime.now(TZ).strftime("%Y-%m-%d")
            articles.append({
                "note_id": nid,
                "title": nb.get("title", "无标题"),
                "summary": nb.get("summary", ""),
                "date": note_date,
            })
    return articles


def _detect_section(title, summary):
    """根据标题和摘要检测文章来源"""
    full = title + summary[:300]
    if any(kw in full for kw in ['二小姐', '二姐']):
        return '二小姐笔记'
    if '与慕同行' in full:
        return '暮云思辨'
    if any(kw in title for kw in ['一辈子的交易', '一代人', '从教育到交易', '上桌吃菜', '不可能三角']):
        return '暮云思辨'
    if any(kw in full for kw in ['EarlETF', '张翼轸']):
        return 'EarlETF'
    # Heuristic: 数据复盘 is EarlETF signature
    if '数据复盘' in title:
        return 'EarlETF'
    return None  # 无法识别的来源


def extract_articles():
    """从IMA知识库（云端API）读取金融相关文章，返回带衰减权重的文章列表"""
    # 多轮搜索关键词覆盖三个来源
    search_queries = [
        # EarlETF 主题
        "数据复盘", "EarlETF", "红利", "微盘", "估值",
        "动量", "指数 图表", "价值投资",
        # 暮云思辨 主题
        "与慕同行", "一辈子的交易", "不可能三角",
        "从教育到交易", "一代人",
        # 二小姐笔记 主题
        "二小姐", "ETF发车", "定投", "发车",
        # 金融通用
        "A股 ETF", "牛市 熊市", "量化 超额", "科创板",
        "基金 投资", "央行 利率", "中证 沪深",
    ]

    raw_articles = _ima_search_batch(search_queries)
    if not raw_articles:
        print("[ima_pipeline] API 搜索无结果", file=sys.stderr)
        return []

    print(f"[ima_pipeline] API 搜索到 {len(raw_articles)} 篇候选文章")

    # 金融关键词匹配（在 summary 中统计命中）
    keywords = ['股票', 'A股', '上证', '创业板', '科创', 'ETF', '量化',
               '银行', '保险', '券商', '基金', '指数', '牛市', '熊市',
               '价值投资', '技术分析', '宏观', '美联储', '央行', '利率',
               'CPI', 'GDP', '人民币', '汇率', '黄金', '原油', '港股',
               '美股', '中概', '外资', '北向', '融资', '杠杆',
               '红利', '股息', 'ROE', 'PE', 'PB', '估值', '投资', '行情']

    articles = []
    for a in raw_articles:
        # 在标题+摘要中做关键词匹配（摘要已含文章开头内容）
        text = a['title'] + a['summary'][:3000]
        hit_count = sum(1 for kw in keywords if kw in text)
        if hit_count < 2:
            continue

        section = _detect_section(a['title'], a['summary'])
        if section is None:
            continue  # 无法识别的来源跳过

        weight = _date_weight(a['date'])
        articles.append({
            'title': a['title'],
            'section': section,
            'date': a['date'],
            'weight': weight,
        })

    # 按权重排序，取前N
    articles.sort(key=lambda x: -x['weight'])
    return articles[:MAX_ARTICLES]


def summarize_section(articles, author_name, deepseek_key):
    """对一篇文章群调用DeepSeek生成一句话概括"""
    titles = [a['title'] for a in articles[:5]]
    if not titles:
        return f"{author_name}：近期无相关文章。"

    prompt = f"""用一句话（不超过100字）概括投资公众号作者"{author_name}"最近文章的主要观点。只输出概括本身，不要前缀说明，不要标点包裹。

最近文章：{'、'.join(titles)}

一句话概括："""

    try:
        import requests
        resp = requests.post(DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {deepseek_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.3
            },
            timeout=20
        )
        data = resp.json()
        summary = data['choices'][0]['message']['content'].strip()
        summary = summary.strip('"').strip("'")
        return f"{author_name}：{summary}"
    except Exception as e:
        return f"{author_name}：近期关注{'、'.join(titles[:3])}。"


def main():
    today_tag = datetime.now(TZ).strftime("%Y%m%d")

    # 步骤1: 提取
    articles = extract_articles()
    if not articles:
        print(f"[ima_pipeline] 无匹配文章，生成空文件")
        out_file = os.path.join(PROJECT_DIR, "reports", f"opinions_{today_tag}.md")
        with open(out_file, "w") as f:
            f.write("*今日无金融相关文章*\n")
        return

    print(f"[ima_pipeline] 提取 {len(articles)} 篇文章")

    # 步骤2: 按 section 分组 + 按权重排序
    sections = defaultdict(list)
    for a in articles:
        sections[a['section']].append(a)

    # 步骤3: 写入 opinions 文件
    out_file = os.path.join(PROJECT_DIR, "reports", f"opinions_{today_tag}.md")
    lines = []
    deepseek_key = _get_deepseek_key()

    # 摘要模式（调用DeepSeek概括每个section）
    for sec_name in sorted(sections.keys()):
        sec_articles = sections[sec_name]
        author = AUTHOR_MAP.get(sec_name, sec_name)
        clean_author = re.sub(r'[^\u4e00-\u9fff\w\s]', '', author).strip()

        lines.append(f"### {sec_name}")
        lines.append("")

        # 标题列表
        for a in sorted(sec_articles, key=lambda x: -x['weight'])[:8]:
            lines.append(f"- **{a['title']}** (权重{a['weight']:.2f})")
        lines.append("")

        # DeepSeek 一句话概括
        summary = summarize_section(sec_articles, clean_author, deepseek_key)
        lines.append(summary)
        lines.append("")

    with open(out_file, "w") as f:
        f.write("\n".join(lines))

    print(f"[ima_pipeline] 写入 {out_file} ({len(lines)} 行)")


if __name__ == '__main__':
    main()
