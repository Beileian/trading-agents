#!/usr/bin/env python3
"""
事件驱动产业链分析 v1.0
独立脚本，可被 cron 调用或手动触发。
输入：事件文本（从海外新闻/外盘波动中提取）
输出：产业链影响分析报告 → 推送到群聊

用法：
  python3 event_chain_push.py "英伟达股价隔夜大跌8%"
  python3 event_chain_push.py --auto  # 自动从海外简报提取事件
"""

import sys, os, json
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

from event_chain import analyze_event, ANALYSIS_PROMPT

def format_push(result: dict) -> str:
    """格式化推送内容"""
    lines = []
    summary = result.get("event_summary", "")
    confidence = result.get("llm_confidence", 0)
    risk = result.get("risk_note", "")
    validation = result.get("validation", {})
    sectors = result.get("impacted_sectors", [])
    stocks = result.get("affected_stocks", [])
    passed = validation.get("passed", False)

    lines.append(f"## 🔗 事件产业链分析")
    lines.append(f"**事件：** {result.get('event', '')}")
    if summary:
        lines.append(f"**摘要：** {summary}")
    lines.append("")

    # 影响方向统计
    pos = [s for s in sectors if s.get("direction") == "positive"]
    neg = [s for s in sectors if s.get("direction") == "negative"]
    neutral = [s for s in sectors if s.get("direction") == "neutral"]

    if neg:
        lines.append(f"### 利空方向 ({len(neg)}个)")
        for s in neg:
            lines.append(f"- **{s['sector']}**：{s.get('logic', '')}")
        lines.append("")
    if pos:
        lines.append(f"### 利好方向 ({len(pos)}个)")
        for s in pos:
            lines.append(f"- **{s['sector']}**：{s.get('logic', '')}")
        lines.append("")
    if neutral:
        lines.append(f"### 中性/间接影响")
        for s in neutral:
            lines.append(f"- **{s['sector']}**：{s.get('logic', '')}")
        lines.append("")

    if stocks:
        lines.append(f"### 相关标的 ({len(stocks)}只)")
        stock_list = "、".join(stocks[:15])
        if len(stocks) > 15:
            stock_list += f" 等{len(stocks)}只"
        lines.append(stock_list)
        lines.append("")

    if not passed:
        issues = validation.get("issues", [])
        lines.append(f"⚠️ 验证未通过（{len(issues)}项）：")
        for i in issues:
            lines.append(f"  - {i}")
        lines.append("")

    if risk:
        lines.append(f"💡 风险提示：{risk}")
        lines.append("")

    lines.append(f"*置信度：{confidence:.0%} | AI辅助分析，不构成投资建议*")
    return "\n".join(lines)


def auto_extract_event() -> str:
    """从最新海外简报中提取关键事件"""
    overseas_dir = os.path.join(PROJECT_DIR, "..", "overseas-morning-brief")
    if not os.path.exists(overseas_dir):
        overseas_dir = "/root/.openclaw/workspace/projects/overseas-morning-brief"

    # 找最新的 overseas_signal 文件
    signal_dir = os.path.join(overseas_dir, "reports") if os.path.exists(os.path.join(overseas_dir, "reports")) else overseas_dir
    pattern = os.path.join(signal_dir, "overseas_signal_*.md")
    import glob
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        # fallback: 看一下 trading-agents 的目录
        pattern2 = os.path.join(PROJECT_DIR, "reports", "overseas_signal_*.md")
        files = sorted(glob.glob(pattern2), reverse=True)
    if not files:
        return None

    with open(files[0]) as f:
        content = f.read()

    # 从内容中提取第一条关键事件（用 LLM 提炼）
    import openai
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        env_file = os.path.join(PROJECT_DIR, ".env")
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith("DEEPSEEK_API_KEY="):
                        api_key = line.strip().split("=", 1)[1].strip('"\'')
                        break
    if not api_key:
        return None

    client = openai.OpenAI(api_key=api_key, base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"))
    extract_prompt = f"""从以下海外市场简报中，提取1-2条对A股可能产生显著影响的关键事件。
输出格式：每条事件一行，用中文概括事件+量化数据。
如果内容中没有明确的关键事件，输出 "NONE"。

简报内容：
{content[:2000]}"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        event_text = response.choices[0].message.content.strip()
        if event_text == "NONE":
            return None
        # 取第一条
        first_line = event_text.split("\n")[0].strip()
        return first_line if first_line else None
    except Exception:
        return None


def push_to_group(text: str):
    """推送分析结果到钉钉群聊"""
    from send_to_dingtalk import send_markdown_msg
    send_markdown_msg(text)


def main():
    event = None
    if len(sys.argv) > 1 and sys.argv[1] != "--auto":
        event = " ".join(sys.argv[1:])
    elif "--auto" in sys.argv:
        event = auto_extract_event()
        if not event:
            print("No significant event found in overseas brief")
            return

    if not event:
        print("Usage: python3 event_chain_push.py <event_text>")
        print("       python3 event_chain_push.py --auto")
        return

    print(f"Analyzing: {event}")
    result = analyze_event(event)

    if result.get("error"):
        print(f"Error: {result['error']}")
        return

    push_text = format_push(result)
    print(push_text)

    # 输出到文件供后续使用
    date_str = datetime.now(TZ).strftime("%Y%m%d")
    output_file = f"{PROJECT_DIR}/reports/event_chain_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(push_text)
    print(f"\n✓ {output_file}")


if __name__ == "__main__":
    main()
