"""The default agent: prepare once -> detect -> rank -> one routed decision ->
verify -> deterministic evidence.

    python run.py ... --agent agents.routed                      # routed
    RCA_MODEL=zai-org/GLM-5.2 python run.py ... --agent agents.routed   # one model

run.py calls `prepare()` once with every query, so each telemetry file is
scanned once per run (rca/prepare.py).  `solve()` then works on that case's
compact summaries.  If preparation failed, or a case was not prepared, solve()
prepares that one case on its own; if that fails too, the no-model baseline in
agents/heuristic.py answers, so there is always a prediction.

Model calls happen only in rca/route.py.  Evidence is rendered from verified
facts in rca/evidence.py -- no model writes a number.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from llm import LLM                                          # noqa: E402
from run import Solution, format_prediction                  # noqa: E402
from rca import evidence, route                              # noqa: E402
from rca.contracts import CaseSpec, format_utc8, parse_case  # noqa: E402
from rca.detect_metrics import detect_metrics                # noqa: E402
from rca.detect_traces import detect_traces                  # noqa: E402
from rca.prepare import prepare_run                          # noqa: E402
from rca.rank import rank_events                             # noqa: E402

RANKED = 8          # candidates kept after ranking (the model sees these)
_PREP_KEY = "rca_prepared"


def prepare(dataset: Path, queries: pd.DataFrame, ctx: dict) -> None:
    """Called once by run.py before the case loop."""
    run = prepare_run(dataset, queries, ctx["out_dir"])
    ctx[_PREP_KEY] = run
    print(f"prepared {len(run.cases)} case(s): {run.preparation_wall_s:.0f}s, "
          f"{len(run.telemetry_components)} components, "
          f"{sum(run.scan_counts.values())} file scan(s)")
    for w in run.warnings:
        print(f"  prepare warning: {w}")


def _spec_for(instruction: str, ctx: dict) -> CaseSpec:
    q = ctx.get("queries")
    if q is not None:
        hit = q[q.instruction == instruction]
        if len(hit):
            r = hit.iloc[0]
            return parse_case(int(r.row_id), r.task_index, instruction)
    # no frame (a direct call): infer the task type from what the prose asks for
    t = instruction.lower()
    asks = ("datetime" if "datetime" in t or "occurrence time" in t else "",
            "component" if "component" in t else "",
            "reason" if "reason" in t else "")
    task = {("datetime", "", ""): "task_1", ("", "", "reason"): "task_2",
            ("", "component", ""): "task_3", ("datetime", "", "reason"): "task_4",
            ("datetime", "component", ""): "task_5", ("", "component", "reason"): "task_6"
            }.get(asks, "task_7")
    return parse_case(-1, task, instruction)


def _prepared_case(spec: CaseSpec, dataset: Path, ctx: dict):
    run = ctx.get(_PREP_KEY)
    if run is not None and spec.row_id in run.cases:
        return run, run.cases[spec.row_id]
    # not prepared (prepare() failed or a direct call): prepare this one case
    run = prepare_run(dataset, (spec,), ctx.get("out_dir", Path(".")))
    return run, run.cases[spec.row_id]


def solve(instruction: str, dataset_dir: Path, ctx: dict) -> Solution:
    t0 = time.time()
    try:
        spec = _spec_for(instruction, ctx)
    except Exception:
        from agents.heuristic import solve as baseline
        sol = baseline(instruction, dataset_dir, ctx)
        sol.evidence = "Could not parse the case; this is the baseline's answer.\n\n" + sol.evidence
        return sol

    notes: list[str] = []
    try:
        run, pc = _prepared_case(spec, Path(dataset_dir), ctx)
        mc, mf = detect_metrics(pc)
        tc, tf = detect_traces(pc)
        facts = {f.fact_id: f for f in (*mf, *tf)}
        ranked = rank_events((*mc, *tc), failure_count=max(RANKED, spec.failure_count))
        notes.append(f"detectors: {len(mc)} metric + {len(tc)} trace candidate(s), "
                     f"{len(facts)} fact(s); {len(ranked)} ranked")
        notes += [f"prepare: {w}" for w in pc.warnings]
        components = run.telemetry_components
    except Exception:
        from agents.heuristic import solve as baseline
        sol = baseline(instruction, dataset_dir, ctx)
        sol.evidence = ("The RCA pipeline raised; this is the baseline's answer.\n\n```\n"
                        + traceback.format_exc() + "```\n\n" + sol.evidence)
        return sol

    try:
        llm = LLM()
    except Exception as e:
        llm = None
        notes.append(f"no model reachable ({type(e).__name__}: {e})")

    hyps, w, reasoning = route.decide(spec, ranked, facts, llm, components)
    notes += list(w)
    usage = llm.usage if llm else {}

    answers = [{"datetime": format_utc8(h.onset_epoch_s), "component": h.component,
                "reason": h.reason_enum} for h in hyps]
    notes.append(f"solve wall: {time.time() - t0:.1f}s")
    md = evidence.render(spec, hyps, ranked, facts, reasoning=reasoning,
                         warnings=notes, usage=usage)
    return Solution(prediction=format_prediction(answers), evidence=md, usage=usage)
