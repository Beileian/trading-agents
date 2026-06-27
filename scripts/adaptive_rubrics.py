"""
adaptive_rubrics.py — 自适应 Rubrics 引擎（方向4：Agent 自修改 Harness）

核心理念（源自 Letta Mods / MetaHarness）：
  Rubrics 不应该是人工编写的静态 JSON，而应该像 Agent 的 skills/prompts
  一样可以基于复盘经验自动学习和调整。

工作流：
  1. 每日收盘复盘后，run_rubrics.py 产出评分日志 → rubric_log.jsonl
  2. adaptive_rubrics.py 读取最近 N 天的评分日志
  3. 检测模式：哪些 rubric 项频繁低分？哪些阈值太松/太紧？
  4. 生成调整建议 → 写入 proposals/ 目录
  5. Agent 在复盘报告中附上提案，等人确认后 apply
"""

import json, os, sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

RUBRICS_DIR = Path("/root/.openclaw/workspace/projects/trading-agents/rubrics")
PROPOSALS_DIR = RUBRICS_DIR / "proposals"
LOG_FILE = RUBRICS_DIR / "rubric_log.jsonl"


def load_rubric_log(days: int = 14):
    """加载最近 N 天的评分日志"""
    if not LOG_FILE.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                # 兼容两种时间戳格式
                ts = entry.get("ts") or entry.get("timestamp", "")
                if isinstance(ts, (int, float)):
                    if ts < cutoff.timestamp():
                        continue
                elif isinstance(ts, str):
                    try:
                        dt = datetime.fromisoformat(ts.replace('+08:00', ''))
                        if dt < cutoff:
                            continue
                    except ValueError:
                        continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries


def detect_weak_spots(entries: list, threshold: float = 6.0):
    """
    检测持续低分的 rubric 项
    threshold: 低于此分的项标记为 weak spot
    """
    item_scores = defaultdict(list)
    for e in entries:
        items = e.get("scores") or e.get("items", {})
        for item_id, item_data in (items or {}).items():
            if isinstance(item_data, dict):
                # 从 bool pass/fail 转为 0-10 分
                if "score" in item_data:
                    item_scores[item_id].append(item_data["score"])
                elif item_data.get("pass") is True:
                    item_scores[item_id].append(8)  # pass → 8
                elif item_data.get("pass") is False:
                    item_scores[item_id].append(3)  # fail → 3
                elif "exit_code" in item_data:
                    item_scores[item_id].append(10 if item_data["exit_code"] == 0 else 2)

    weak_spots = []
    for item_id, scores in item_scores.items():
        if len(scores) < 3:  # 需要足够样本
            continue
        avg = sum(scores) / len(scores)
        if avg < threshold:
            weak_spots.append({
                "item_id": item_id,
                "avg_score": round(avg, 2),
                "samples": len(scores),
                "trend": "declining" if len(scores) > 3 and scores[-3:] < scores[:3] else "stable_low",
                "recent_scores": scores[-5:]
            })
    return weak_spots


def detect_threshold_misalignment(entries: list):
    """
    检测阈值不适配：
    - 某项持续 pass 但分数在 7-8 之间 → 阈值可能太松
    - 某项频繁 reject 但分数在 6-7 之间 → 阈值可能太紧
    """
    item_stats = defaultdict(list)
    for e in entries:
        verdict = e.get("verdict", "")
        items = e.get("scores") or e.get("items", {})
        for item_id, item_data in (items or {}).items():
            if isinstance(item_data, dict):
                if "score" in item_data:
                    score = item_data["score"]
                elif item_data.get("pass") is True:
                    score = 8
                elif item_data.get("pass") is False:
                    score = 3
                elif "exit_code" in item_data:
                    score = 10 if item_data["exit_code"] == 0 else 2
                else:
                    continue
                item_stats[item_id].append({
                    "score": score,
                    "verdict": verdict
                })

    adjustments = []
    for item_id, records in item_stats.items():
        if len(records) < 5:
            continue
        scores = [r["score"] for r in records]
        passes = [r for r in records if r["verdict"] == "pass"]
        rejects = [r for r in records if r["verdict"] == "reject"]

        # 太紧：频繁reject但分数集中在6-7
        if len(rejects) > len(records) * 0.4:
            reject_scores = [r["score"] for r in rejects]
            avg_reject = sum(reject_scores) / len(reject_scores)
            if 6.0 <= avg_reject < 7.5:
                adjustments.append({
                    "item_id": item_id,
                    "issue": "threshold_too_tight",
                    "current_pass": 7,
                    "suggested_pass": max(5, round(avg_reject - 0.5)),
                    "avg_reject_score": round(avg_reject, 2),
                    "reject_rate": f"{len(rejects) / len(records) * 100:.0f}%"
                })

        # 太松：持续pass但分数在7-8（无区分度）
        if len(passes) > len(records) * 0.8:
            avg_pass = sum(scores) / len(scores)
            if 7.0 <= avg_pass < 8.5:
                adjustments.append({
                    "item_id": item_id,
                    "issue": "threshold_too_loose",
                    "current_pass": 7,
                    "suggested_pass": min(9, round(avg_pass + 0.5)),
                    "avg_score": round(avg_pass, 2),
                    "note": "持续通过但无区分度，提高阈值增加挑战性"
                })

    return adjustments


def generate_proposals(entries: list):
    """主入口：生成所有调整建议"""
    weak_spots = detect_weak_spots(entries)
    threshold_adj = detect_threshold_misalignment(entries)

    proposals = []

    # 类型1：弱项增强
    for ws in weak_spots:
        proposals.append({
            "type": "weight_adjustment",
            "item_id": ws["item_id"],
            "reason": f"{ws['item_id']} 近{ws['samples']}次平均分 {ws['avg_score']}，趋势 {ws['trend']}",
            "suggestion": "increase_weight",
            "details": ws
        })

    # 类型2：阈值调优
    for adj in threshold_adj:
        proposals.append({
            "type": "threshold_tuning",
            "item_id": adj["item_id"],
            "reason": f"{adj['issue']}：当前 pass≥{adj['current_pass']}，建议调整为 ≥{adj['suggested_pass']}",
            "suggestion": adj
        })

    return proposals


def save_proposals(proposals: list, date_str: str = None):
    """将调整建议保存到 proposals/ 目录"""
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    filepath = PROPOSALS_DIR / f"adaptive_{date_str}.json"
    with open(filepath, "w") as f:
        json.dump({
            "date": date_str,
            "generated_at": datetime.now().isoformat(),
            "proposals": proposals,
            "total_entries_analyzed": len(load_rubric_log())
        }, f, indent=2, ensure_ascii=False)

    return filepath


def format_for_agent(proposals: list) -> str:
    """将建议格式化为 Agent 可读的文本"""
    if not proposals:
        return "✅ 近期 Rubrics 无异常，所有项表现稳定。"

    lines = ["📋 Rubrics 自适应调整建议：\n"]
    for i, p in enumerate(proposals[:3], 1):  # 最多显示3条
        if p["type"] == "weight_adjustment":
            lines.append(
                f"{i}. {p['item_id']}: 权重调高\n"
                f"   近{p['details']['samples']}次平均 {p['details']['avg_score']}/10\n"
                f"   建议：此事对判断质量影响大，考虑提升权重"
            )
        elif p["type"] == "threshold_tuning":
            s = p["suggestion"]
            lines.append(
                f"{i}. {p['item_id']}: 阈值调整\n"
                f"   当前: pass≥{s['current_pass']} → 建议: pass≥{s['suggested_pass']}\n"
                f"   原因: {s.get('issue', '')}，近期评分 {s.get('avg_reject_score', s.get('avg_score', 0))}，拒绝率 {s.get('reject_rate', 'N/A')}"
            )

    lines.append(f"\n共 {len(proposals)} 项建议，待确认后执行。")
    return "\n".join(lines)


# ═══ CLI 入口 ═══
if __name__ == "__main__":
    entries = load_rubric_log()
    if not entries:
        print("⚠️ rubric_log.jsonl 为空或不存在，无法生成建议")
        sys.exit(0)

    proposals = generate_proposals(entries)
    filepath = save_proposals(proposals)
    print(format_for_agent(proposals))
    print(f"\n建议已保存: {filepath}")
