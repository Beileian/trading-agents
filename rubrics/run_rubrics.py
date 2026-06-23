#!/usr/bin/env python3
"""
run_rubrics.py — 交易推荐质量评估主 Harness
加载 rubrics/trade_recommendation.json 标准，逐项运行评估，输出结果并写入日志。

用法:
  python3 rubrics/run_rubrics.py <report_file> [--date YYYYMMDD]

评估项:
  1. schema_validity    (veto)  — 调用 validate_schema.py
  2. factual_accuracy   (high)  — 调用 rubrics/check_factual.py
  3. logic_completeness (medium)— LLM 评估四维度覆盖
  4. risk_specificity   (medium)— LLM 评估风险具体性
  5. action_consistency (high)  — LLM 评估方向一致性

聚合规则: weighted_sum_with_veto
  - veto 项不通过 → 整个报告 reject
  - action_consistency 不通过 → reject
  - 任一 high 项不通过 → low_confidence
  - 加权总分 < 0.6 → low_confidence

输出: JSON + rubric_log.jsonl 追加
exit code: 0=通过, 2=reject, 1=low_confidence
"""

import sys, os, re, json, subprocess
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
RUBRICS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(RUBRICS_DIR)
RUBRIC_FILE = os.path.join(RUBRICS_DIR, "trade_recommendation.json")
LOG_FILE = os.path.join(RUBRICS_DIR, "rubric_log.jsonl")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"


def _get_deepseek_key():
    """三级查找链：环境变量 → .env 文件 → hardcoded fallback"""
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


def call_llm(prompt: str, max_tokens: int = 200) -> str:
    """调用 DeepSeek 做 LLM 评判"""
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
            "max_tokens": max_tokens,
            "temperature": 0.1,
        },
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'].strip()


def run_script(script_path: str, args: list[str]) -> dict:
    """运行评判脚本，返回 {pass, result_json, exit_code, stderr}"""
    script_abs = os.path.join(PROJECT_DIR, script_path) if not os.path.isabs(script_path) else script_path
    try:
        proc = subprocess.run(
            [sys.executable, script_abs] + args,
            capture_output=True, text=True, timeout=60,
            cwd=PROJECT_DIR
        )
        try:
            result = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            result = {"raw": proc.stdout.strip()}
        return {
            "pass": proc.returncode == 0,
            "result": result,
            "exit_code": proc.returncode,
            "stderr": proc.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"pass": False, "result": {"error": "timeout"}, "exit_code": -1, "stderr": "timeout"}
    except Exception as e:
        return {"pass": False, "result": {"error": str(e)}, "exit_code": -1, "stderr": str(e)}


def load_rubric() -> dict:
    with open(RUBRIC_FILE) as f:
        return json.load(f)


def evaluate_llm_item(item: dict, report_text: str, analysis_text: str = "") -> dict:
    """对 LLM 评判项进行评估"""
    prompt = item["prompt"] + "\n\n--- 以下是待评估的交易推荐信号表 ---\n\n" + report_text
    if analysis_text:
        # 取每只标的的推理部分（DeepSeek 交易决策 表格后的简要理由），不超过 3000 字
        import re as _re
        reasoning_parts = []
        for block in _re.split(r'### \d{6}\.', analysis_text):
            m = _re.search(r'\*\*简要理由\*\*\s*\|\s*(.+?)(?:\n\n|---)', block, _re.DOTALL)
            if m:
                reasoning_parts.append(m.group(1).strip()[:300])
            else:
                # fallback: match markdown table row
                m2 = _re.search(r'\*\*简要理由\*\*\s*\|\s*(.+?)(?:\n)', block)
                if m2:
                    reasoning_parts.append(m2.group(1).strip()[:300])
        if reasoning_parts:
            prompt += "\n\n--- 以下是每只标的的详细分析推理（来自技术分析报告） ---\n\n"
            for i, rp in enumerate(reasoning_parts):
                prompt += f"标的{i+1}推理: {rp}\n"
            prompt = prompt[:6000]  # 超长截断
    # 截断以防止超 token
    if len(prompt) > 8000:
        prompt = prompt[:8000] + "\n\n[...报告过长已截断...]"

    try:
        llm_output = call_llm(prompt, max_tokens=300)
    except Exception as e:
        return {"pass": False, "llm_output": None, "error": str(e)}

    # 判断通过条件
    cond = item["pass_condition"]
    if ">= 2.5" in cond or ">=" in cond:
        try:
            score = float(re.search(r'[\d.]+', llm_output).group())
            passed = score >= 2.5
        except (ValueError, AttributeError):
            passed = False
    elif "是" in cond:
        passed = "是" in llm_output and "否" not in llm_output[:50]
    else:
        passed = cond in llm_output

    return {"pass": passed, "llm_output": llm_output}


def aggregate(rubric: dict, results: list[dict]) -> dict:
    """加权求和 + veto"""
    thresholds = rubric["thresholds"]
    items = rubric["items"]

    # 解析每个item的结果
    item_results = {}
    for item, res in zip(items, results):
        item_results[item["id"]] = {
            "weight": item["weight"],
            "pass": res.get("pass", False),
            "fail_action": item["fail_action"],
            "detail": res,
        }

    # Veto check
    veto_triggered = []
    for iid, ir in item_results.items():
        if ir["weight"] == "veto" and not ir["pass"]:
            veto_triggered.append(iid)

    # action_consistency 不通过 → reject
    if not item_results.get("action_consistency", {}).get("pass", True):
        veto_triggered.append("action_consistency")

    if veto_triggered:
        return {"verdict": "reject", "reason": f"veto触发: {veto_triggered}", "item_results": item_results}

    # 加权分数
    weight_map = {"high": 0.3, "medium": 0.2}
    total_weight = sum(weight_map.get(ir["weight"], 0) for ir in item_results.values())
    total_score = sum(
        weight_map.get(ir["weight"], 0) * (1.0 if ir["pass"] else 0.0)
        for ir in item_results.values()
    )
    final_score = total_score / total_weight if total_weight > 0 else 0

    # 判断 low_confidence
    high_failed = any(
        ir["weight"] == "high" and not ir["pass"]
        for ir in item_results.values()
    )

    if high_failed or final_score < 0.6:
        return {
            "verdict": "low_confidence",
            "score": round(final_score, 3),
            "high_failed": high_failed,
            "item_results": item_results,
        }

    return {
        "verdict": "pass",
        "score": round(final_score, 3),
        "item_results": item_results,
    }


def write_log(rubric: dict, report_file: str, verdict: dict, results: list[dict]):
    """追加评估日志"""
    entry = {
        "timestamp": datetime.now(TZ).isoformat(),
        "rubric_version": rubric["version"],
        "report_file": os.path.basename(report_file),
        "verdict": verdict["verdict"],
        "score": verdict.get("score"),
        "items": {},
    }
    for item, res in zip(rubric["items"], results):
        entry["items"][item["id"]] = {
            "pass": res.get("pass"),
            "weight": item["weight"],
        }
        # 脚本项附 exit code
        if "exit_code" in res:
            entry["items"][item["id"]]["exit_code"] = res["exit_code"]
        # LLM 项附输出
        if "llm_output" in res:
            llm_out = res["llm_output"]
            entry["items"][item["id"]]["llm_output"] = llm_out[:200] if llm_out else None

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 rubrics/run_rubrics.py <report_file> [--analysis <analysis_file>]", file=sys.stderr)
        sys.exit(1)

    report_file = sys.argv[1]
    analysis_file = None
    if "--analysis" in sys.argv:
        idx = sys.argv.index("--analysis")
        if idx + 1 < len(sys.argv):
            analysis_file = sys.argv[idx + 1]

    if not os.path.exists(report_file):
        print(f"文件不存在: {report_file}", file=sys.stderr)
        sys.exit(1)

    with open(report_file) as f:
        report_text = f.read()

    analysis_text = ""
    if analysis_file and os.path.exists(analysis_file):
        with open(analysis_file) as f:
            analysis_text = f.read()

    rubric = load_rubric()
    items = rubric["items"]

    results = []
    print(f"📋 交易推荐质量评估 · {rubric['version']}", file=sys.stderr)
    print(f"   报告: {os.path.basename(report_file)}", file=sys.stderr)
    print(file=sys.stderr)

    for i, item in enumerate(items):
        item_id = item["id"]
        judge = item["judge"]
        weight = item["weight"]
        label = f"[{i+1}/{len(items)}] {item_id} ({weight})"
        print(f"  {label}...", end=" ", file=sys.stderr)

        if judge == "script":
            script = item["script"]
            res = run_script(script, [report_file])
        elif judge == "llm":
            res = evaluate_llm_item(item, report_text, analysis_text)
        else:
            res = {"pass": False, "error": f"未知评判方式: {judge}"}

        results.append(res)
        status = "✅" if res.get("pass") else "❌"
        print(status, file=sys.stderr)

        if not res.get("pass"):
            if "exit_code" in res:
                detail = res.get("result", {}).get("errors", ["unknown"])[0]
                print(f"    ↳ {detail}", file=sys.stderr)
            elif "llm_output" in res:
                out = res["llm_output"]
                print(f"    ↳ LLM: {out[:80]}", file=sys.stderr)

    print(file=sys.stderr)

    verdict = aggregate(rubric, results)
    write_log(rubric, report_file, verdict, results)

    # 输出
    print(f"判定: {verdict['verdict']}", file=sys.stderr)
    if verdict.get("score") is not None:
        print(f"得分: {verdict['score']:.3f}", file=sys.stderr)
    if verdict.get("reason"):
        print(f"理由: {verdict['reason']}", file=sys.stderr)

    # JSON 输出到 stdout
    output = {
        "verdict": verdict["verdict"],
        "score": verdict.get("score"),
        "reason": verdict.get("reason"),
        "items": {},
    }
    for item, res in zip(items, results):
        output["items"][item["id"]] = {
            "pass": res.get("pass"),
            "weight": item["weight"],
        }
    print(json.dumps(output, ensure_ascii=False))

    if verdict["verdict"] == "reject":
        sys.exit(2)
    elif verdict["verdict"] == "low_confidence":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
