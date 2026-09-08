#!/usr/bin/env python3
"""ab_test_daily.py — 交易推荐双模型 A/B 试点 v3（持续运行版，2026-08-08）
对比 deepseek-v4-flash vs MiniMax-M3 生成的 trading_analysis 质量（rubrics 打分）。
完全独立：只生成两版分析到 ab_test/ 目录，各跑 run_rubrics(analysis, trade_recommendation.json)。
不调用 generate_trade_signals.py、不写生产 reports/、零生产接触。
结果追加到 ab_test/results.jsonl，供多天汇总。
用法: python3 scripts/ab_test_daily.py [YYYY-MM-DD]   # 默认最近交易日
"""
import sys, os, json, time, subprocess

DATE = sys.argv[1] if len(sys.argv) > 1 else ""
DATE_TAG = DATE.replace("-", "")
PROJ = "/root/.openclaw/workspace/projects/trading-agents"
OUT = f"{PROJ}/ab_test"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, f"{PROJ}/scripts")
sys.path.insert(0, PROJ)

import requests
import symbols_config
import trading_analysis_concurrent as tac

# ── keys ──
DEEPSEEK_KEY = ""
for line in open(f"{PROJ}/.env"):
    if line.startswith("DEEPSEEK_API_KEY="):
        DEEPSEEK_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
KEYS = json.load(open("/root/.openclaw/secrets/provider-keys.json"))
MINIMAX_KEY = KEYS["providers"]["minimax"]["apiKey"]


def call_model(model: str, sys_prompt: str, user_prompt: str) -> dict | None:
    is_mm = model.lower().startswith("minimax")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    if is_mm:
        url, key = "https://api.minimax.chat/v1/chat/completions", MINIMAX_KEY
    else:
        url, key = "https://api.deepseek.com/chat/completions", DEEPSEEK_KEY
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    end = next((i for i in range(len(lines)-1, 0, -1) if lines[i].strip() == "```"), len(lines))
                    content = "\n".join(lines[1:end])
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    b = content.find("{")
                    if b >= 0:
                        depth = 0
                        for i in range(b, len(content)):
                            if content[i] == "{": depth += 1
                            elif content[i] == "}":
                                depth -= 1
                                if depth == 0:
                                    return json.loads(content[b:i+1])
                    return None
            else:
                print(f"  [{model}] HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as e:
            print(f"  [{model}] attempt {attempt+1} err: {e}")
            time.sleep(2 ** attempt)
    return None


def gen_analysis(model: str, label: str, date_str: str) -> str:
    sections = []
    header = f"""# 📊 A股技术分析与交易决策报告（A/B 试点）

**生成日期**: {date_str}
**数据范围**: 2021-06-04 至 {date_str}（约5年日线）
**分析标的**: {len(symbols_config.SYMBOLS)}只
**分析模型**: {label}

---

## 📈 标的技术分析

"""
    ok = 0
    for symbol, name, stype in symbols_config.SYMBOLS:
        try:
            rows = tac.load_csv(symbol)
            metrics, extra = tac.compute_metrics(rows, symbol, stype)
            sys_p, user_p = tac.build_prompt(symbol, name, stype, metrics, extra)
            decision = call_model(model, sys_p, user_p)
            if decision is None:
                decision = tac._generate_concurrent_fallback(symbol, name, metrics)
            else:
                ok += 1
            sections.append(f"""### {symbol} — {name}

#### 技术指标概览

{tac.format_metrics_table(metrics)}

#### 交易决策（{label}）

| 维度 | 判断 |
|------|------|
| **趋势判断** | {decision.get('趋势', 'N/A')} |
| **支撑位** | {decision.get('支撑位', 'N/A')} |
| **阻力位** | {decision.get('阻力位', 'N/A')} |
| **交易建议** | **{decision.get('建议', 'N/A')}** |
| **建议仓位** | {decision.get('仓位', 'N/A')}% |
| **行业背景** | {decision.get('行业背景', 'N/A')} |
| **具体风险** | {decision.get('具体风险', 'N/A')} |
| **简要理由** | {decision.get('理由', 'N/A')} |

---
""")
        except Exception as e:
            sections.append(f"### {symbol} — {name}\n\n*⚠️ 失败: {e}*\n\n---\n")
    report = header + "".join(sections) + "\n*报告由自动化分析系统生成，仅供参考，不构成投资建议。*\n"
    tag = model.replace("/", "_")
    path = os.path.join(OUT, f"analysis_{tag}_{date_str}.md")
    with open(path, "w") as f:
        f.write(report)
    print(f"  [{label}] 成功 {ok}/{len(symbols_config.SYMBOLS)} → {path}")
    return path


def run_rubrics(analysis_path: str, label: str) -> dict:
    proc = subprocess.run(
        [sys.executable, f"{PROJ}/rubrics/run_rubrics.py", analysis_path, "--rubric", f"{PROJ}/rubrics/trade_recommendation.json"],
        capture_output=True, text=True, timeout=300, cwd=PROJ
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": proc.stdout[-300:], "err": proc.stderr[-200:]}


if __name__ == "__main__":
    # 默认最近交易日：取缓存最新 CSV 的修改日期
    if not DATE:
        import glob
        csvs = glob.glob(f"{PROJ}/data/cache/*-daily.csv")
        if csvs:
            latest = max(csvs, key=os.path.getmtime)
            DATE = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(latest)))
            DATE_TAG = DATE.replace("-", "")
    print(f"=== 双模型 A/B 试点 v3 | {DATE} ===")
    results = {"date": DATE, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    for model, label in [("deepseek-v4-flash", "Flash"), ("MiniMax-M3", "M3")]:
        print(f"\n--- {label} ---")
        a = gen_analysis(model, label, DATE)
        r = run_rubrics(a, label)
        results[label] = {"verdict": r.get("verdict"), "score": r.get("score"),
                          "failed": [k for k, v in r.get("items", {}).items() if not v.get("pass", True)]}
        print(f"  {label}: verdict={r.get('verdict')} score={r.get('score')} failed={results[label]['failed']}")
    with open(f"{OUT}/results.jsonl", "a") as f:
        f.write(json.dumps(results, ensure_ascii=False) + "\n")
    print(f"\n结果已追加: {OUT}/results.jsonl")
