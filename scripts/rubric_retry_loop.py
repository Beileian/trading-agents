#!/usr/bin/env python3
"""
rubric_retry_loop.py — Rubric Reject 自动修复闭环（调测版）
当 run_rubrics.py 判定 reject 后，基于失败项定向修复再评估。

用法（独立测试）:
  python3 scripts/rubric_retry_loop.py trade_signals_20260623.md [--max-rounds 3]

流程:
  1. 运行 rubrics → 判定
  2. 若 reject:
     a. 提取失败项及 LLM 评判输出
     b. 构建定向修复 prompt → 调用 generate_trade_signals 的 LLM 重生成推荐
     c. 重跑 rubrics
  3. 最多 N 轮，记录每轮得分变化

exit code:
  0 = 最终通过
  1 = 最终 low_confidence
  2 = 多轮后仍 reject（降级发送时标记为低质量）

调测状态：待与 rubrics 共同确认后合入生产。
"""

import sys, os, json, subprocess, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
PROJECT_DIR = Path("/root/.openclaw/workspace/projects/trading-agents")
RUBRICS_SCRIPT = PROJECT_DIR / "rubrics" / "run_rubrics.py"
GEN_SCRIPT = PROJECT_DIR / "scripts" / "generate_trade_signals.py"
REPORT_DIR = PROJECT_DIR / "reports"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"
MAX_ROUNDS = 3


def _get_deepseek_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key
    return "sk-284…03e9"


def run_rubrics(report_file: str, analysis_file: str = "") -> dict:
    """运行 rubrics 评估，返回解析后的 JSON"""
    args = [str(report_file)]
    if analysis_file and os.path.exists(analysis_file):
        args.extend(["--analysis", analysis_file])
    proc = subprocess.run(
        [sys.executable, str(RUBRICS_SCRIPT)] + args,
        capture_output=True, text=True, timeout=120,
        cwd=str(PROJECT_DIR)
    )
    try:
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        return {"verdict": "error", "raw": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def extract_failures(result: dict) -> str:
    """从 rubrics 结果中提取失败项的具体评判输出，用于定向修复"""
    failures = []
    items = result.get("items", {})
    for item_id, detail in items.items():
        if not detail.get("pass"):
            failures.append(f"- {item_id}: {json.dumps(detail, ensure_ascii=False)}")

    if not failures:
        return ""

    # 从 rubrics log 中取最新一条的 LLM 输出
    log_file = PROJECT_DIR / "rubrics" / "rubric_log.jsonl"
    llm_details = []
    if log_file.exists():
        with open(log_file) as f:
            lines = f.readlines()
        if lines:
            last = json.loads(lines[-1])
            for item_id, detail in last.get("items", {}).items():
                if not detail.get("pass") and detail.get("llm_output"):
                    llm_details.append(f"### {item_id}: {detail['weight']}\n评判输出: {detail['llm_output']}")

    result = "\n".join(failures) if failures else "(无详细信息)"
    if llm_details:
        result += "\n\n--- LLM评判详情 ---\n" + "\n\n".join(llm_details)
    return result


def build_fix_prompt(original_recommendations: str, failures: str) -> str:
    """构建定向修复 prompt——喂回给交易推荐的 LLM 做质量修正"""
    prompt = f"""你之前的交易推荐未通过质量评估。以下是评审意见，请根据这些意见重写推荐。

## 评审意见（未通过项）
{failures if failures else "(评审意见缺失，请重新审视推荐质量)"}

## 强制要求
1. 每个推荐必须覆盖三个技术面维度：趋势判断依据（含具体指标数值）、支撑/阻力位引用、仓位建议与方向匹配
2. 每个推荐必须包含至少一个基于技术指标形态的风险推演（如均线死叉、关键阻力/支撑突破、RSI极限区域），不能是笼统表述
3. 买入/卖出/持有方向必须与推荐理由一致，不得自相矛盾
4. 引用的价格、PE等数据必须与下方数据源一致

## 原始推荐
{original_recommendations}

请输出修正后的完整交易推荐表格，格式与原格式一致。"""

    return prompt[:8000]  # 截断防止超 token


def retry_generation_with_fix(prompt: str) -> str:
    """用定向修复 prompt 调用 LLM 重新生成推荐内容"""
    import requests
    key = _get_deepseek_key()
    resp = requests.post(DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.3,
        },
        timeout=120
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'].strip()


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/rubric_retry_loop.py <report_file.md> [--max-rounds 3]", file=sys.stderr)
        sys.exit(1)

    report_file = sys.argv[1]
    max_rounds = MAX_ROUNDS
    for i, a in enumerate(sys.argv):
        if a == "--max-rounds" and i + 1 < len(sys.argv):
            max_rounds = int(sys.argv[i + 1])

    if not os.path.exists(report_file):
        print(f"文件不存在: {report_file}", file=sys.stderr)
        sys.exit(1)

    # 找对应的 analysis 文件
    date_tag = None
    m = re.search(r'(\d{8})', os.path.basename(report_file))
    if m:
        date_tag = m.group(1)
    analysis_file = str(REPORT_DIR / f"trading_analysis_{date_tag}.md") if date_tag else ""

    # 初始评估
    print(f"📋 Rubric 自动修复闭环 · 最多 {max_rounds} 轮", file=sys.stderr)
    print(f"   报告: {os.path.basename(report_file)}", file=sys.stderr)
    print(file=sys.stderr)

    result = run_rubrics(report_file, analysis_file)
    print(f"[轮次0] 初始评估: {result['verdict']} (score={result.get('score', 'N/A')})", file=sys.stderr)

    if result["verdict"] != "reject":
        verdict = result["verdict"]
        if verdict == "pass":
            print("✅ 初始评估已通过，无需修复", file=sys.stderr)
            sys.exit(0)
        else:
            print(f"⚠️ 初始评估: {verdict} (非reject，无需重试)", file=sys.stderr)
            sys.exit(1)

    # 修复循环
    with open(report_file) as f:
        original_content = f.read()

    for round_num in range(1, max_rounds + 1):
        failures = extract_failures(result)
        if not failures:
            print(f"[轮次{round_num}] 无失败详情可提取，停止", file=sys.stderr)
            break

        fix_prompt = build_fix_prompt(original_content, failures)
        print(f"[轮次{round_num}] 向LLM提交定向修复... (prompt {len(fix_prompt)} 字符)", file=sys.stderr)

        try:
            fixed = retry_generation_with_fix(fix_prompt)
        except Exception as e:
            print(f"[轮次{round_num}] LLM调用失败: {e}", file=sys.stderr)
            continue

        # 写入临时文件
        tmp_file = report_file.replace(".md", f"_retry_r{round_num}.md")
        with open(tmp_file, "w") as f:
            f.write(fixed)
        print(f"[轮次{round_num}] 修复输出 → {os.path.basename(tmp_file)}", file=sys.stderr)

        # 重新评估
        result = run_rubrics(tmp_file, analysis_file)
        print(f"[轮次{round_num}] 重评: {result['verdict']} (score={result.get('score', 'N/A')})", file=sys.stderr)

        if result["verdict"] == "pass":
            # 用修复版本覆盖原文件
            with open(tmp_file) as f:
                fixed_content = f.read()
            with open(report_file, "w") as f:
                f.write(fixed_content)
            print(f"✅ 第{round_num}轮修复通过，已覆盖 {os.path.basename(report_file)}", file=sys.stderr)
            sys.exit(0)

        if result["verdict"] == "low_confidence":
            # low_confidence 已可降级发送，用修复版本覆盖
            with open(tmp_file) as f:
                fixed_content = f.read()
            with open(report_file, "w") as f:
                f.write(fixed_content)
            print(f"⚠️ 第{round_num}轮达到 low_confidence，已覆盖（降级发送）", file=sys.stderr)
            sys.exit(1)

        # reject 继续下一轮
        original_content = fixed

    print(f"❌ {max_rounds} 轮后仍 reject，降级发送", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
