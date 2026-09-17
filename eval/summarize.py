#!/usr/bin/env python3
"""Tabulate eval/results/*/run*/summary.json into eval/results/summary.md.

    python3 eval/summarize.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def fmt(x, spec=".3f"):
    return "—" if x is None else format(x, spec)


def main() -> None:
    runs = {}
    for f in sorted(RESULTS.glob("*/run*/summary.json")):
        s = json.loads(f.read_text())
        runs.setdefault(s["config"], []).append(s)
    if not runs:
        print("no results under", RESULTS)
        return

    lines = ["# Eval results", "",
             "Same case ids, same prompts, same detector output for every config; only the "
             "model choice differs. Scored with the benchmark's own evaluator (`score.py`), "
             "priced at the table in `docs/models.md` (`cost.py`).", ""]

    lines += ["## Per run", "",
              "| config | run | cases | mean score | solved | $/case | $/solved | s/case mean | s/case p50 | s/case max |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for cfg, rs in runs.items():
        for i, s in enumerate(rs, 1):
            lines.append(f"| {cfg} | {i} | {s['n']} | {s['mean_score']:.3f} | {s['solved']}/{s['n']} | "
                         f"{s['dollars_per_case']:.4f} | {fmt(s['dollars_per_solved'], '.4f')} | "
                         f"{fmt(s['wall_mean_s'], '.1f')} | {fmt(s['wall_p50_s'], '.1f')} | {fmt(s['wall_max_s'], '.1f')} |")

    lines += ["", "## Across repeats (mean ± sd where n>1)", "",
              "| config | runs | mean score | $/case | s/case |", "|---|---|---|---|---|"]
    for cfg, rs in runs.items():
        def agg(key):
            v = [r[key] for r in rs if r.get(key) is not None]
            if not v:
                return "—"
            m = statistics.mean(v)
            return f"{m:.3f}" if len(v) == 1 else f"{m:.3f} ± {statistics.stdev(v):.3f}"
        lines.append(f"| {cfg} | {len(rs)} | {agg('mean_score')} | {agg('dollars_per_case')} | {agg('wall_mean_s')} |")

    lines += ["", "## By task type (mean score, first run of each config)", ""]
    tasks = sorted({t for rs in runs.values() for t in rs[0]["by_task"]})
    lines.append("| config | " + " | ".join(tasks) + " |")
    lines.append("|---|" + "---|" * len(tasks))
    for cfg, rs in runs.items():
        lines.append(f"| {cfg} | " + " | ".join(fmt(rs[0]["by_task"].get(t)) for t in tasks) + " |")

    lines += ["", "## Model usage (first run of each config)", ""]
    for cfg, rs in runs.items():
        calls = {}
        for c in rs[0]["cases"]:
            for m, u in c["models"].items():
                x = calls.setdefault(m, {"calls": 0, "in": 0, "out": 0})
                x["calls"] += u.get("calls", 0); x["in"] += u.get("prompt_tokens", 0); x["out"] += u.get("completion_tokens", 0)
        lines.append(f"- **{cfg}**: " + (", ".join(f"`{m}` {x['calls']} calls, {x['in']:,} in / {x['out']:,} out"
                                            for m, x in calls.items()) or "no model calls"))

    md = "\n".join(lines) + "\n"
    (RESULTS / "summary.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
