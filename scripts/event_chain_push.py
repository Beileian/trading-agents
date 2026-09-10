#!/usr/bin/env python3
"""
事件驱动产业链分析 v1.2
独立脚本，可被 cron 调用或手动触发。
输入：事件文本（从海外新闻/外盘波动中提取）
输出：产业链影响分析报告 → 推送到群聊

用法：
  python3 event_chain_push.py "英伟达股价隔夜大跌8%"
  python3 event_chain_push.py --auto  # 自动从海外简报提取事件

版本史:
  v1.2 (2026-09-10, 托董指令): 输出注明信息来源出处——
    ①事件事实来源(auto=外盘晨间研判文件+其commit落款+行情数据源; manual=手动输入未核验)
    ②推演来源(产业链映射为 LLM 推理, 非新闻原文引用)
    不附新闻URL: 上游简报未保存原文链接, 强行补链接=幻觉(反幻觉铁律)。
  v1.1 (2026-09-06): push_to_group 接线(此前静默无效运行45天)
"""

import sys, os, re, json
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = "/root/.openclaw/workspace/projects/trading-agents"
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

from event_chain import analyze_event, ANALYSIS_PROMPT

def format_push(result: dict, source_meta: dict = None) -> str:
    """格式化推送内容
    source_meta (v1.2): {"mode": "auto"|"manual", "brief_file", "brief_gen", "brief_tag", "model"}
    """
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
    # ── 来源出处（v1.2 托董 9/10 指令）──
    if source_meta and source_meta.get("mode") == "auto":
        src = (f"**来源：** 外盘晨间研判 `{source_meta.get('brief_file','?')}`"
               f"（{source_meta.get('brief_gen','生成时间未知')}生成"
               + (f"，落款 {source_meta.get('brief_tag')}" if source_meta.get('brief_tag') else "")
               + "）")
        lines.append(src)
        lines.append(f"　　└ 行情事实：AKShare新浪/Yahoo/CBOE/腾讯交叉；产业链推演：{source_meta.get('model','deepseek-chat')} 关联推理，非新闻原文引用")
    elif source_meta and source_meta.get("mode") == "manual":
        lines.append("**来源：** 手动输入事件文本（未经采集链路核验）；产业链推演：LLM 关联推理，非新闻原文引用")
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


def auto_extract_event():
    """从最新海外简报中提取关键事件
    数据源: morning_brief_YYYY-MM-DD.md（盘前 07:55 生成，当前活跃）；
    兼容旧 overseas_signal_*.md（6月前旧格式）。
    返回 (event_text, source_meta) 或 (None, None)。v1.2: source_meta 记录简报文件/生成时间/落款供出处标注。"""
    overseas_dir = os.path.join(PROJECT_DIR, "..", "overseas-morning-brief")
    if not os.path.exists(overseas_dir):
        overseas_dir = "/root/.openclaw/workspace/projects/overseas-morning-brief"

    # 主数据源：最新 morning_brief（活跃格式）
    brief_dir = os.path.join(overseas_dir, "reports")
    brief_pattern = os.path.join(brief_dir, "morning_brief_*.md")
    import glob
    files = sorted(glob.glob(brief_pattern), reverse=True)
    content = None
    used_file = None
    if files:
        with open(files[0]) as f:
            content = f.read()
        used_file = files[0]
    else:
        # fallback: 旧 overseas_signal 格式
        signal_dir = brief_dir if os.path.exists(brief_dir) else overseas_dir
        pattern = os.path.join(signal_dir, "overseas_signal_*.md")
        files = sorted(glob.glob(pattern), reverse=True)
        if not files:
            pattern2 = os.path.join(PROJECT_DIR, "reports", "overseas_signal_*.md")
            files = sorted(glob.glob(pattern2), reverse=True)
        if files:
            with open(files[0]) as f:
                content = f.read()
            used_file = files[0] if files else None
    if not content:
        return None, None

    # v1.2: 采集来源元信息——文件mtime=生成时间, 简报尾部落款(如 [外盘研判@da7b126])
    import time
    source_meta = {
        "mode": "auto",
        "brief_file": os.path.basename(used_file) if used_file else "?",
        "brief_gen": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(used_file))) if used_file and os.path.exists(used_file) else "未知",
        "brief_tag": "",
    }
    mt = re.search(r"\[外盘研判@([0-9a-f]{7})\]", content)
    if mt:
        source_meta["brief_tag"] = f"@{mt.group(1)}"

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
        return None, None

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
            return None, None
        # 取第一条
        first_line = event_text.split("\n")[0].strip()
        return (first_line, source_meta) if first_line else (None, None)
    except Exception:
        return None, None


def push_to_group(text: str) -> bool:
    """推送分析结果到钉钉群聊，返回是否成功（v1.1 2026-09-06 接线：此前定义未调用，静默无效运行 45 天）"""
    from send_to_dingtalk import send_markdown
    return bool(send_markdown(text))


def main():
    event = None
    source_meta = None
    if len(sys.argv) > 1 and sys.argv[1] != "--auto":
        event = " ".join(sys.argv[1:])
        source_meta = {"mode": "manual"}
    elif "--auto" in sys.argv:
        event, source_meta = auto_extract_event()
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

    push_text = format_push(result, source_meta)
    print(push_text)

    # 推送落款: 版本号+commit（AGENTS.md 代码推送铁律, 2026-09-10 补——此前该 cron 推送无落款）
    import subprocess
    try:
        ver = subprocess.run(["git", "describe", "--tags", "--abbrev=7"],
                             cwd=PROJECT_DIR, capture_output=True, text=True, timeout=5).stdout.strip()
        h = subprocess.run(["git", "log", "-1", "--format=%h"], cwd=PROJECT_DIR,
                           capture_output=True, text=True, timeout=5).stdout.strip() or "?"
        if not ver:
            v = re.search(r"\d+\.\d+\.\d+", open(f"{PROJECT_DIR}/VERSION.md").read())
            ver = f"v{v.group(0) if v else 'unknown'}"
        ver = f"{ver}@{h}"  # 与盘前推送 GIT_TAG@GIT_HASH 同构
    except Exception:
        ver = "unknown@?"
    push_text += f"\n\n[{ver}]"

    # 输出到文件供后续使用
    date_str = datetime.now(TZ).strftime("%Y%m%d")
    output_file = f"{PROJECT_DIR}/reports/event_chain_{date_str}.md"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(push_text)
    print(f"\n✓ {output_file}")

    # 推送到群（--dry-run 跳过；失败时非零退出让 cron 日志可见）
    if "--dry-run" in sys.argv:
        print("[dry-run] 跳过推送")
        return
    if push_to_group(push_text):
        print("✅ 已推送钉钉群")
    else:
        print("❌ 事件链推送失败", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
