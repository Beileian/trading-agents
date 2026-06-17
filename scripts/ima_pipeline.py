#!/usr/bin/env python3
"""
IMA 观点管线 v2.0 — 从知识库提取金融文章 + 时间衰减 + DeepSeek摘要
项目: 金桥量化 v2.5.0

合并 extract_ima_opinions.py (v1.1) + summarize_ima_opinions.py (v1) 为单一步骤。
一次运行完成: 提取→去重→衰减→排序→摘要→写入 opinions_{date}.md

用法: python3 ima_pipeline.py
输出: reports/opinions_{YYYYMMDD}.md
"""

import os, re, json, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
IMA_DIR = os.path.expanduser("~/.ima/knowledge_base/公众号文章")
if not os.path.exists(IMA_DIR):
    IMA_DIR = os.path.expanduser("~/.ima/知识库/公众号文章")  # fallback

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

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
    return "sk-2fe07fda653b47c6997a51ea0fe842a0"  # 最后fallback


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


def extract_articles():
    """从IMA知识库读取金融相关文章，返回带衰减权重的文章列表"""
    if not os.path.exists(IMA_DIR):
        return []

    articles = []
    for root, dirs, files in os.walk(IMA_DIR):
        for fn in files:
            if not fn.endswith('.md'):
                continue
            fpath = os.path.join(root, fn)
            try:
                mtime = os.path.getmtime(fpath)
                note_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            except:
                note_date = datetime.now(TZ).strftime("%Y-%m-%d")

            with open(fpath) as f:
                content = f.read(3000)  # 只读前3KB做标题/关键词匹配

            # 金融关键词匹配
            keywords = ['股票', 'A股', '上证', '创业板', '科创', 'ETF', '量化',
                       '银行', '保险', '券商', '基金', '指数', '牛市', '熊市',
                       '价值投资', '技术分析', '宏观', '美联储', '央行', '利率',
                       'CPI', 'GDP', '人民币', '汇率', '黄金', '原油', '港股',
                       '美股', '中概', '外资', '北向', '融资', '杠杆',
                       '红利', '股息', 'ROE', 'PE', 'PB', '估值']
            hit_count = sum(1 for kw in keywords if kw in content)
            if hit_count < 2:
                continue

            # 提取标题（第一行或YAML frontmatter）
            title = fn.replace('.md', '')
            lines = content.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('# '):
                    title = line[2:].strip()
                    break

            section = os.path.basename(root)
            weight = _date_weight(note_date)
            articles.append({
                'title': title,
                'section': section,
                'date': note_date,
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
