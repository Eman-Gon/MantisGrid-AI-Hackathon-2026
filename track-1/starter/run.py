#!/usr/bin/env python3
"""The submission requirements. We run exactly this.

    python run.py --dataset <dir> --queries <query.csv> --out <dir>

Produces, in --out:

    predictions.csv     row_id, prediction   (plus columns we ignore)
    evidence/<row_id>.md    one per case, your reasoning and the data behind it
    usage.jsonl         per case: wall-clock and tokens per model, appended as you go
                        (`python cost.py usage.jsonl` turns it into dollars)

`predictions.csv` is scored by OpenRCA's own evaluator, unchanged. `evidence/`
is read by humans and is worth more of your grade than accuracy.

Replace the agent, not this file. `--agent` takes any module exposing
`solve(instruction, dataset_dir, ctx) -> Solution`.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class Solution:
    """What an agent returns for one case.

    prediction: the JSON-like answer string. The evaluator's regex requires the
                keys IN THIS ORDER -- datetime, component, reason -- and silently
                scores zero if they are reordered. Use `format_prediction()`.
    evidence:   markdown. What you looked at, what it showed, why you concluded
                what you did. Say so plainly when you are guessing.
    usage:      tokens per model -- {model: {"prompt_tokens", "completion_tokens",
                "calls"}}. `llm.LLM().usage` is already in this shape.
    """
    prediction: str
    evidence: str = ""
    usage: dict = field(default_factory=dict)


COUNTS = ("prompt_tokens", "completion_tokens", "calls")


def per_model(usage: dict) -> dict:
    """Solution.usage as {model: counts}. A flat {"prompt_tokens": ...} with no
    model named is kept under "unknown"."""
    if usage and all(isinstance(v, dict) for v in usage.values()):
        return usage
    return {"unknown": usage} if any(usage.get(k) for k in COUNTS) else {}


def format_prediction(answers: list[dict]) -> str:
    """Build a prediction string the evaluator can actually parse.

    Each answer is a dict with any of: datetime, component, reason.
    Omit a key the question did not ask for. KEY ORDER IS LOAD-BEARING.
    """
    out = {}
    for i, a in enumerate(answers, 1):
        item = {}
        if a.get("datetime"):
            item["root cause occurrence datetime"] = a["datetime"]
        if a.get("component"):
            item["root cause component"] = a["component"]
        if a.get("reason"):
            item["root cause reason"] = a["reason"]
        out[str(i)] = item
    return "```json\n" + json.dumps(out, indent=4) + "\n```"


def last_resort(instruction: str, dataset: Path, ctx: dict, tb: str) -> Solution:
    """The answer when the agent raised: the free baseline if it runs, otherwise
    exactly as many placeholder guesses as the instruction says there are
    failures. Never an empty prediction."""
    try:
        from agents import heuristic
        sol = heuristic.solve(instruction, dataset, ctx)
        sol.evidence = ("AGENT RAISED -- this is the no-model baseline's answer\n\n"
                        "```\n" + tb + "```\n\n" + sol.evidence)
        return sol
    except Exception:
        tb += "\nBASELINE ALSO RAISED\n" + traceback.format_exc()
    n, when = 1, ""
    try:
        from agents.heuristic import failure_count, parse_window
        n = failure_count(instruction)
        win = parse_window(instruction)
        when = win[0].strftime("%Y-%m-%d %H:%M:%S") if win else ""
    except Exception:
        pass
    guess = {"datetime": when, "component": "unknown", "reason": "container CPU load"}
    return Solution(prediction=format_prediction([guess] * n),
                    evidence="AGENT RAISED -- placeholder guess, no analysis\n\n```\n"
                             + tb + "```")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True, help="bundle dir, containing telemetry/")
    p.add_argument("--queries", required=True, help="query.csv")
    p.add_argument("--out", required=True)
    p.add_argument("--agent", default="agents.rootroute",
                   help="module exposing solve(instruction, dataset_dir, ctx)")
    p.add_argument("--limit", type=int, default=0, help="first N cases only")
    p.add_argument("--resume", action="store_true",
                   help="skip row_ids already in predictions.csv")
    args = p.parse_args()

    dataset = Path(args.dataset).resolve()
    out = Path(args.out).resolve()
    (out / "evidence").mkdir(parents=True, exist_ok=True)

    queries = pd.read_csv(args.queries)
    # A dev split carries the answers. The agent must never see them.
    queries = queries.drop(columns=["scoring_points"], errors="ignore")
    if args.limit:
        queries = queries.head(args.limit)

    pred_path = out / "predictions.csv"
    done: set[int] = set()
    rows: list[dict] = []
    if args.resume and pred_path.exists():
        prev = pd.read_csv(pred_path)
        rows = prev.to_dict("records")
        done = set(prev.row_id.astype(int))
        print(f"resuming: {len(done)} case(s) already done")

    agent = importlib.import_module(args.agent)
    query_columns = [
        column
        for column in ("row_id", "task_index", "instruction")
        if column in queries.columns
    ]
    answer_free_queries = queries.loc[:, query_columns].copy()
    ctx = {
        "dataset_dir": dataset,
        "out_dir": out,
        "queries": answer_free_queries,
    }

    # Agents that need to scan large telemetry files may prepare every requested
    # case in one pass.  Never expose development-only columns such as
    # ``scoring_points`` to that hook: the judged query file does not contain
    # them, and production analysis must remain answer-free.
    prepare = getattr(agent, "prepare", None)
    prepare_wall_s = 0.0
    prepare_case_count = 0
    if callable(prepare):
        pending_queries = answer_free_queries[
            ~answer_free_queries["row_id"].astype(int).isin(done)
        ] if done else answer_free_queries
        prepare_case_count = len(pending_queries)
        if len(pending_queries):
            prepare_started = time.time()
            try:
                prepared = prepare(dataset, pending_queries.copy(), ctx)
                # RootRoute returns its prepared run; the upstream agent writes
                # its own cache into ctx and returns None. Support both hooks.
                if prepared is not None:
                    ctx["prepared_run"] = prepared
            except Exception:
                print("prepare() raised; continuing per case\n" + traceback.format_exc())
            prepare_wall_s = time.time() - prepare_started
            print(f"prepare: {prepare_wall_s:.1f}s")
    prepare_share_s = (
        prepare_wall_s / prepare_case_count if prepare_case_count else 0.0
    )
    ctx["prepare_wall_s"] = prepare_wall_s
    ctx["prepare_share_s"] = prepare_share_s

    for r in queries.itertuples(index=False):
        rid = int(r.row_id)
        if rid in done:
            continue
        ctx["row_id"] = rid
        ctx["task_index"] = getattr(r, "task_index", "")
        t0 = time.time()
        try:
            sol = agent.solve(r.instruction, dataset, ctx)
        except Exception:
            # A crashed case must not lose the run -- and a blank answer scores
            # the same zero as a wrong one, so guess anyway. The traceback lands
            # in the evidence so the failure is visible.
            sol = last_resort(r.instruction, dataset, ctx, traceback.format_exc())
        solve_wall = time.time() - t0
        # Preparation is shared across the pending cases.  Allocate an equal
        # share so summing/averaging usage.jsonl reflects real end-to-end time
        # instead of hiding the telemetry scan before the per-case loop.
        wall = solve_wall + prepare_share_s

        (out / "evidence" / f"{rid}.md").write_text(sol.evidence or "_no evidence_\n")
        models = per_model(sol.usage or {})
        rec = {"row_id": rid, "prediction": sol.prediction,
               "task_index": getattr(r, "task_index", ""), "wall_s": round(wall, 2),
               "solve_wall_s": round(solve_wall, 2),
               "prepare_share_s": round(prepare_share_s, 2),
               **{k: sum(m.get(k, 0) for m in models.values()) for k in COUNTS}}
        rows.append(rec)
        with (out / "usage.jsonl").open("a") as fh:
            fh.write(json.dumps({k: v for k, v in rec.items() if k != "prediction"}
                                | {"models": models}) + "\n")
        # Written after every case: a run that stops at case 60 keeps the first 59.
        # Via a temp file and a rename, because the judged run is stopped at a hard
        # time limit and a half-written predictions.csv would lose the lot.
        tmp = pred_path.with_suffix(".csv.tmp")
        pd.DataFrame(rows).sort_values("row_id").to_csv(tmp, index=False)
        os.replace(tmp, pred_path)
        print(f"  row {rid:>3}  {wall:6.1f}s  {rec['prompt_tokens']:>8,} tok  "
              f"{(sol.prediction or '')[:60].replace(chr(10),' ')}")

    df = pd.DataFrame(rows)
    print(f"\n{len(df)} case(s) -> {pred_path}")
    if "wall_s" in df and len(df):
        print(f"wall-clock: mean {df.wall_s.mean():.1f}s  max {df.wall_s.max():.1f}s"
              f"  total {df.wall_s.sum()/60:.1f}min")
        if df.prompt_tokens.sum():
            print(f"tokens: mean {df.prompt_tokens.mean():,.0f} in / "
                  f"{df.completion_tokens.mean():,.0f} out per case")


if __name__ == "__main__":
    main()
