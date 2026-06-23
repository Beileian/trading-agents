#!/usr/bin/env python3
"""
用 DeepSeek 对 IMA 知识库三个来源的文章做一句话概括（每人≤100字）。
用法: python3 summarize_ima_opinions.py <opinions_file.md>
"""
import sys, json, subprocess, os

def _load_deepseek_key():
    import os as _os
    key = _os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = "/root/.openclaw/workspace/projects/trading-agents/.env"
    if _os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key
    return ""

DEEPSEEK_KEY = _load_deepseek_key()
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"

import re

AUTHOR_MAP = {
    'ETF发车/实战信号': '二小姐笔记',
    '量化/指数/数据': 'EarlETF',
    '哲学思辨/宏观叙事': '暮云思辨',
}

def main():
    if len(sys.argv) < 2:
        print("用法: summarize_ima_opinions.py <opinions_file>")
        sys.exit(1)
    
    opinion_file = sys.argv[1]
    with open(opinion_file) as f:
        lines = f.readlines()
    
    # 按 section 收集文章标题
    sections = {}
    current_section = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('###'):
            current_section = s.lstrip('#').strip()
            sections[current_section] = []
            continue
        if current_section and s.startswith('**') and '[' in s:
            title = s.strip('*').strip().strip('*').replace('**', '')
            # 过滤聊天记录等无关内容
            if '腾云马对话' in title or '对话记录' in title:
                continue
            bracket_idx = title.rfind('[')
            pure_title = title[:bracket_idx].strip() if bracket_idx > 0 else title
            sections[current_section].append(pure_title)
    
    if not sections:
        print("*无可用观点*")
        return
    
    # 为每个 section 调用 DeepSeek 生成一句话概括
    for sec_name, articles in sections.items():
        if not articles:
            continue
        
        author = AUTHOR_MAP.get(sec_name, sec_name)
        # 清理图标
        clean_author = re.sub(r'[\U0001F300-\U0001FFFF]', '', author).strip()
        articles_str = '、'.join(articles[:5])
        
        prompt = f"""用一句话（不超过100字）概括投资公众号作者"{author}"最近文章的主要观点。只输出概括本身，不要前缀说明，不要标点包裹。

最近文章：{articles_str}

一句话概括："""
        
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.3
            })
            resp = subprocess.run(
                ['curl', '-s', DEEPSEEK_URL,
                 '-H', 'Content-Type: application/json',
                 '-H', f'Authorization: Bearer {DEEPSEEK_KEY}',
                 '-d', payload],
                capture_output=True, text=True, timeout=15
            )
            data = json.loads(resp.stdout)
            summary = data['choices'][0]['message']['content'].strip()
            # 清理可能的引号包裹
            summary = summary.strip('"\'').strip('"').strip("'")
        except Exception as e:
            # fallback: 直接用文章标题
            summary = f'近期关注{articles_str[:80]}。'
        
        print(f"{clean_author}：{summary}")
        print()

if __name__ == '__main__':
    main()
