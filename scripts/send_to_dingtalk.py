#!/usr/bin/env python3
"""
钉钉群消息推送脚本 — 使用钉钉机器人 API 发送 markdown 消息到群
大文件自动分片，切换回 sampleMarkdown 支持富文本表格

用法:
  echo "消息内容" | python3 send_to_dingtalk.py
  python3 send_to_dingtalk.py "消息内容"
  python3 send_to_dingtalk.py --file /path/to/report.md
"""

import os, sys, json, requests

DINGTALK_APP_KEY = os.getenv("DINGTALK_APP_KEY", "dingmvin6gkm96gookpo")
DINGTALK_APP_SECRET = os.getenv("DINGTALK_APP_SECRET", "5l6HvoMYkAK3AMPMDYpvnVCP7X-jCKOIweQGY0re5tSZLpQlL4UpNZUE2KxJVqzA")
GROUP_CID = os.getenv("DINGTALK_GROUP_CID", "cidY4mlx+J2kNFpTiWFgQ0gkg==")
API_BASE = "https://api.dingtalk.com"
MAX_CHARS = 18000  # 钉钉 sampleMarkdown 单条约 20KB 限制，留余量

def get_token():
    resp = requests.post(f"{API_BASE}/v1.0/oauth2/accessToken", json={
        "appKey": DINGTALK_APP_KEY,
        "appSecret": DINGTALK_APP_SECRET
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["accessToken"]

def send_markdown(text, title=None):
    token = get_token()
    if not title:
        title = text.strip().split("\n")[0][:50] if text.strip() else "消息"
    resp = requests.post(
        f"{API_BASE}/v1.0/robot/groupMessages/send",
        headers={
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json"
        },
        json={
            "robotCode": DINGTALK_APP_KEY,
            "openConversationId": GROUP_CID,
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({
                "title": title,
                "text": text
            })
        },
        timeout=15
    )
    data = resp.json()
    if resp.status_code != 200:
        print(f"❌ 发送失败: {resp.status_code} {data}", file=sys.stderr)
        return False
    print(f"✅ 已发送: {title}")
    return True

def split_at_section(text, max_chars):
    """在段落边界（## 或空行）拆分，避免截断在表格中间"""
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        
        # 找最近的 ## 或空行作为切割点
        chunk = remaining[:max_chars]
        # 回找最后一个 ##
        last_h2 = chunk.rfind("\n## ")
        # 回找最后一个连续空行
        last_gap = chunk.rfind("\n\n")
        
        cut = max(last_h2, last_gap)
        if cut <= 0:
            cut = max_chars  # 找不到合适切割点，硬切
        
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    
    return chunks

def main():
    text = ""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--file" and len(sys.argv) > 2:
            with open(sys.argv[2]) as f:
                text = f.read()
        else:
            text = sys.argv[1]
    else:
        text = sys.stdin.buffer.read().decode('utf-8', errors='replace')

    if not text.strip():
        print("❌ 无消息内容", file=sys.stderr)
        sys.exit(1)

    chunks = split_at_section(text, MAX_CHARS)
    for i, chunk in enumerate(chunks):
        title = chunk.strip().split("\n")[0][:50] if chunk.strip() else f"消息 {i+1}"
        if len(chunks) > 1:
            title = f"{title} ({i+1}/{len(chunks)})"
        if not send_markdown(chunk, title):
            print(f"❌ 第 {i+1}/{len(chunks)} 片发送失败，降到 sampleText 重试", file=sys.stderr)
            # 降级到 sampleText
            token = get_token()
            requests.post(
                f"{API_BASE}/v1.0/robot/groupMessages/send",
                headers={"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"},
                json={
                    "robotCode": DINGTALK_APP_KEY,
                    "openConversationId": GROUP_CID,
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": chunk[:5000] + "\n\n...(截断)"})
                },
                timeout=15
            )

if __name__ == "__main__":
    main()
