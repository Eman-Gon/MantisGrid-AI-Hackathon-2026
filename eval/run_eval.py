#!/usr/bin/env python3
"""Run one configuration over a fixed case set, through the real run.py, and
score, price and time it.

    python3 eval/run_eval.py --config routed        --cases-file eval/cases.json
    python3 eval/run_eval.py --config single-strong --cases-file eval/cases.json
    python3 eval/run_eval.py --config single-flash  --cases-file eval/cases.json --repeat 2

Configs:
    routed          the agent's own per-call model choice
    single-strong   every call pinned to STRONG_MODEL  (RCA_MODEL env)
    single-flash    every call pinned to FLASH_MODEL

Results land in eval/results/<config>/run<k>/ with the raw run.py output plus
summary.json. `summarize.py` tabulates them.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STARTER = ROOT / "track-1" / "starter"
sys.path.insert(0, str(STARTER))
from score import evaluate          # noqa: E402  the benchmark's evaluator, unchanged
from cost import by_model           # noqa: E402

STRONG_MODEL = os.environ.get("STRONG_MODEL", "zai-org/GLM-5.2")
FLASH_MODEL = os.environ.get("FLASH_MODEL", "zai-org/GLM-4.7-Flash")
PIN = {"routed": None, "single-strong": STRONG_MODEL, "single-flash": FLASH_MODEL}
DIFFICULTY = {"task_1": "easy", "task_2": "easy", "task_3": "easy", "task_4": "middle",
              "task_5": "middle", "task_6": "middle", "task_7": "hard"}


def run_once(config: str, ids: list[int], dev: Path, dataset: Path, agent: str,
             out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    q = pd.read_csv(dev)
    sub = q[q.row_id.isin(ids)].set_index("row_id").loc[ids].reset_index()
    sub.drop(columns=["scoring_points"], errors="ignore").to_csv(out / "queries.csv", index=False)

    env = dict(os.environ)
    env.pop("RCA_MODEL", None)
    if PIN[config]:
        env["RCA_MODEL"] = PIN[config]
    cmd = [sys.executable, "run.py", "--dataset", str(dataset),
           "--queries", str(out / "queries.csv"), "--out", str(out), "--agent", agent]
    t0 = time.time()
    with (out / "run.log").open("w") as log:
        rc = subprocess.run(cmd, cwd=STARTER, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    wall = time.time() - t0

    pred = pd.read_csv(out / "predictions.csv")
    usage = {}
    for line in (out / "usage.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            usage[r["row_id"]] = r
    per_case = []
    for r in sub.itertuples(index=False):
        p = pred[pred.row_id == r.row_id]
        text = str(p.prediction.iloc[0]) if len(p) else ""
        passed, failed, sc = evaluate(text, r.scoring_points)
        u = usage.get(r.row_id, {})
        per_case.append({
            "row_id": int(r.row_id), "task": r.task_index, "difficulty": DIFFICULTY[r.task_index],
            "score": sc, "solved": bool(sc >= 1.0), "passed": passed, "failed": failed,
            "wall_s": u.get("wall_s"), "dollars": sum(by_model(u.get("models", {})).values()),
            "models": u.get("models", {}),
        })
    scores = [c["score"] for c in per_case]
    walls = [c["wall_s"] for c in per_case if c["wall_s"] is not None]
    dollars = [c["dollars"] for c in per_case]
    solved = sum(c["solved"] for c in per_case)
    by_task = {}
    for c in per_case:
        by_task.setdefault(c["task"], []).append(c["score"])
    summary = {
        "config": config, "pinned_model": PIN[config], "agent": agent, "rc": rc,
        "case_ids": ids, "n": len(ids),
        "mean_score": statistics.mean(scores) if scores else 0.0,
        "solved": solved, "strict_rate": solved / len(ids) if ids else 0.0,
        "by_task": {k: statistics.mean(v) for k, v in sorted(by_task.items())},
        "dollars_total": sum(dollars), "dollars_per_case": statistics.mean(dollars) if dollars else 0.0,
        "dollars_per_solved": (sum(dollars) / solved) if solved else None,
        "wall_total_s": sum(walls), "wall_mean_s": statistics.mean(walls) if walls else None,
        "wall_p50_s": statistics.median(walls) if walls else None, "wall_max_s": max(walls) if walls else None,
        "harness_wall_s": wall,
        "cases": per_case,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, choices=list(PIN))
    ap.add_argument("--cases-file", default=str(HERE / "cases.json"))
    ap.add_argument("--set", default="holdout")
    ap.add_argument("--row-ids", default="", help="override the case set, e.g. 27,38,56")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--agent", default="agents.routed")
    ap.add_argument("--dataset", default=str(ROOT / "track-1" / "data" / "Market-cloudbed-1"))
    ap.add_argument("--dev", default="", help="query_dev.csv (default: <dataset>/dev/query_dev.csv)")
    ap.add_argument("--results", default=str(HERE / "results"))
    a = ap.parse_args()

    dataset = Path(a.dataset).resolve()
    dev = Path(a.dev).resolve() if a.dev else dataset / "dev" / "query_dev.csv"
    ids = ([int(x) for x in a.row_ids.split(",")] if a.row_ids
           else json.loads(Path(a.cases_file).read_text())[a.set])
    if PIN[a.config] is None and os.environ.get("RCA_MODEL"):
        raise SystemExit("RCA_MODEL is set in your shell; unset it for the routed config")

    base = Path(a.results) / a.config
    existing = sorted(p for p in base.glob("run*") if p.is_dir()) if base.exists() else []
    start = len(existing) + 1
    for k in range(start, start + a.repeat):
        out = base / f"run{k}"
        print(f"\n== {a.config} run{k}: {len(ids)} case(s) {ids} -> {out}")
        s = run_once(a.config, ids, dev, dataset, a.agent, out)
        print(f"   mean {s['mean_score']:.3f}  solved {s['solved']}/{s['n']}  "
              f"${s['dollars_per_case']:.4f}/case  {s['wall_mean_s'] or 0:.1f}s/case  rc={s['rc']}")


if __name__ == "__main__":
    main()
