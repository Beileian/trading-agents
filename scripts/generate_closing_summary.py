#!/usr/bin/env python3
"""
generate_closing_summary.py — A股收盘复盘「群聊摘要」生成器（P0-1 摘要契约）

输入: reports/closing_review_YYYYMMDD.md (全文 151 行)
输出: reports/closing_summary_YYYYMMDD.md (≤500字, 钉钉群推送用)

摘要契约(2026-09-10 评审整合结论 P0-1 冻结):
  1. 一句话总判(取「今日一句话」, 无则合成指数表现)
  2. 指数小表格(①今日指数)
  3. 判定错位行: 正确项只报 "N/M 全对"; 错误项(判定≠✅)全列出
  4. Rubrics 标记(从主脚本注入的文件首行解析, 或读全文)
  5. 次日关注 ≤3 条(来自全文; 无则写占位)
  6. 空值规则: 无错位时写「今日判定无偏差」; 无关注事项写「无明确新增关注」
生成后可在文件头留 <IMA_STATUS> 占位符供推送脚本回填 IMA 入库结论。

版本: v3.6.1 | 纯文本解析, 零AI依赖(原则八)
"""
import re, os, sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJ = "/root/.openclaw/workspace/projects/trading-agents"

def now_tag():
    return datetime.now(TZ).strftime("%Y%m%d")

def main():
    date_tag = sys.argv[1] if len(sys.argv) > 1 else now_tag()
    src = f"{PROJ}/reports/closing_review_{date_tag}.md"
    out = f"{PROJ}/reports/closing_summary_{date_tag}.md"
    if not os.path.exists(src):
        print(f"ERR: 复盘全文不存在 {src}"); sys.exit(1)
    txt = open(src, encoding="utf-8").read()

    L = []
    # --- 1. 一句话总判 ---
    onel = ""
    m = re.search(r"【今日一句话】(.+)", txt)
    if m: onel = m.group(1).strip()
    if not onel:
        # fallback: 从 ①今日指数 合成
        m2 = re.search(r"\*\*① 今日指数\*\*\s*\n(.+)", txt)
        onel = m2.group(1).strip() if m2 else "指数数据缺失"
    date_str = f"{date_tag[:4]}-{date_tag[4:6]}-{date_tag[6:]}"
    L.append(f"# A股收盘复盘摘要 · {date_str}")

    # --- 2. Rubrics 标记(读全文; 主脚本注入的 tagged 文件头由推送脚本预合并) ---
    rubric = ""
    rm = re.search(r"Rubrics\s*([0-9.]+)", txt)
    if rm: rubric = rm.group(1)
    low = ""
    if "低质量" in txt: low = "低质量"
    elif "低置信" in txt: low = "低置信度"
    if low: L.append(f"> ⚠️ {low} · Rubrics {rubric}")
    elif rubric: L.append(f"> Rubrics {rubric}")

    # --- 3. 一句话 ---
    L.append("")
    L.append(f"**总判** {onel}")

    # --- 4. 指数表(①) ---
    mi = re.search(r"\*\*① 今日指数\*\*\s*\n(.+)", txt)
    idx_line = mi.group(1).strip() if mi else ""
    idx_parts = [p.strip() for p in idx_line.split("|") if p.strip()]
    L.append("")
    L.append("**① 指数**")
    for p in idx_parts:
        L.append(f"- {p}")

    # --- 5. 判定错位(④): 只列非✅/非"无"行 ---
    L.append("")
    L.append("**② 推荐判定**")
    # 取④表格区
    dec = txt.split("**④ 推荐方向 vs 实际收盘方向**")[-1]
    dec = dec.split("**④b")[0]
    rows = [l for l in dec.split("\n") if l.startswith("|") and "标的" not in l and "---" not in l and l.strip(" |")]
    ok_count = sum(1 for r in rows if "✅" in r or "无" in r)
    wrong = [r for r in rows if "✅" not in r and "无" not in r]
    total = len(rows)
    if total == 0:
        L.append("今日无推荐标的判定")
    elif wrong:
        L.append(f"{total} 标的中 {len(wrong)} 个偏离：")
        for r in wrong:
            cells = [c.strip().strip('*') for c in r.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 4:
                L.append(f"- {cells[0]} 荐{cells[1]} 收{cells[2]} 判{cells[3]}")
    else:
        L.append(f"{total} 标的判定全对（{ok_count}/{total}）✅")

    # --- 6. 次日关注/风险(摘要 ≤3 条来源) ---
    L.append("")
    L.append("**③ 关注/风险**")
    notes = []
    # 缠论简评里的「动作:watch/observe signal」异常, 价格穿越, 纪律偏离
    # 简化: 用⑦本周认知尾行 + 信号矛盾; 此处从 价格穿越 & 合成判断 提炼
    pv = txt.split("**⑤ 价格穿越**")
    if len(pv) > 1:
        seg = pv[1].split("**⑥")[0]
        body = " ".join(l.strip() for l in seg.split("\n") if l.strip() and not l.startswith("*"))
        if body and body != "无":
            notes.append("价格穿越: " + body[:80])
    # 信号矛盾(⑥内) → 合成一句关注
    if notes.__len__() < 3:
        m_amb = re.search(r"⚡ 信号矛盾:\s*\n\s*•\s*([^\n]+)", txt)
        m_dom = re.search(r"⚡ 板块联动[^\n]*", txt)
        if m_amb:
            if "不矛盾" in m_amb.group(1):
                pass  # v3.8.0: 「→不矛盾：…」是矛盾消解结论, 非风险, 不进「关注/风险」节
            else:
                notes.append("信号矛盾: " + m_amb.group(1).replace("\u2192", "｜").strip()[:100])
        elif m_dom:
            notes.append("板块联动: " + m_dom.group(0).split(":")[-1].strip()[:100])
    if not notes:
        notes.append("无明确新增关注")
    for n in notes[:3]:
        L.append(f"- {n}")

    L.append("")
    L.append("> 摘要由 run_closing_push.sh 生成 · 全文详见知识库「盘后复盘」")
    L.append("<IMA_STATUS_PENDING>")

    content = "\n".join(L) + "\n"
    open(out, "w", encoding="utf-8").write(content)
    # 字数统计(不含标题/占位)
    body = re.sub(r"<[A-Z_]+>", "", content)
    chars = len(body.replace("\n", "").replace(" ", ""))
    print(f"OK {out} ({chars}字符)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
