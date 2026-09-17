"""RootRoute: deterministic RCA with evidence-gated GLM escalation.

``run.py`` calls :func:`prepare` once for the answer-free query table, then
calls :func:`solve` per row.  Preparation scans each telemetry source once;
the per-case path only runs compact detectors, ranking, optional selection,
strict validation, and deterministic evidence rendering.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import LLM
from rca.contracts import (
    REQUESTED_FIELDS,
    CaseSpec,
    format_utc8,
    parse_case,
)
from rca.detect_metrics import detect_metrics
from rca.detect_traces import detect_traces
from rca.evidence import render_evidence
from rca.prepare import PreparedRun, prepare_run
from rca.rank import rank_events
from rca.routing import POLICIES, route_case
from run import Solution, format_prediction


def _usage_snapshot(llm: LLM | None) -> dict[str, dict[str, int]]:
    if llm is None:
        return {}
    return {
        model: {
            "prompt_tokens": int(counts.get("prompt_tokens", 0)),
            "completion_tokens": int(counts.get("completion_tokens", 0)),
            "calls": int(counts.get("calls", 0)),
        }
        for model, counts in llm.usage.items()
    }


def _usage_delta(
    before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for model in sorted(set(before) | set(after)):
        delta = {
            field: max(
                0,
                int(after.get(model, {}).get(field, 0))
                - int(before.get(model, {}).get(field, 0)),
            )
            for field in ("prompt_tokens", "completion_tokens", "calls")
        }
        if any(delta.values()):
            result[model] = delta
    return result


def _shared_llm(ctx: dict[str, Any]) -> LLM | None:
    if "rootroute_llm" in ctx:
        return ctx["rootroute_llm"]
    if not os.environ.get("FEATHERLESS_API_KEY"):
        ctx["rootroute_llm"] = None
        return None
    try:
        client = LLM()
    except Exception as exc:  # noqa: BLE001 - model initialization must degrade
        ctx["rootroute_llm_error"] = f"{type(exc).__name__}: {exc}"
        client = None
    ctx["rootroute_llm"] = client
    return client


def prepare(dataset_dir: Path, queries: Any, ctx: dict[str, Any]) -> PreparedRun | None:
    """Prepare the complete answer-free run and initialize one run-wide client."""

    _shared_llm(ctx)
    try:
        return prepare_run(
            dataset_dir,
            queries,
            ctx["out_dir"],
            include_trace=True,
        )
    except Exception as exc:  # noqa: BLE001 - preparation must degrade per case
        # Trace audit/schema damage must not erase otherwise useful metric RCA.
        ctx["rootroute_prepare_warning"] = (
            f"trace-inclusive preparation failed ({type(exc).__name__}: {exc}); "
            "retried with metrics only"
        )
        try:
            return prepare_run(
                dataset_dir,
                queries,
                ctx["out_dir"],
                include_trace=False,
            )
        except Exception as metric_exc:  # noqa: BLE001 - degrade cases, not the run
            ctx["rootroute_prepare_error"] = (
                f"trace preparation: {type(exc).__name__}: {exc}; "
                f"metric preparation: {type(metric_exc).__name__}: {metric_exc}"
            )
            return None


def _infer_task_type(instruction: str) -> str:
    """Compatibility fallback for old runners that omit ``ctx['task_index']``."""

    lowered = instruction.lower()
    asks_time = "occurrence datetime" in lowered or "exact time" in lowered
    asks_component = (
        "root cause component" in lowered
        or "identify the root cause component" in lowered
    )
    asks_reason = "root cause reason" in lowered or "underlying reason" in lowered
    wanted = tuple(
        field
        for field, present in (
            ("datetime", asks_time),
            ("component", asks_component),
            ("reason", asks_reason),
        )
        if present
    )
    for task_type, fields in REQUESTED_FIELDS.items():
        if fields == wanted:
            return task_type
    # The current runner will always provide task_index.  This only keeps an
    # older runner from crashing; task_7 is safer than silently omitting fields.
    return "task_7"


def _compat_case(
    instruction: str, dataset_dir: Path, ctx: dict[str, Any]
) -> tuple[PreparedRun, CaseSpec]:
    task_type = str(ctx.get("task_index") or _infer_task_type(instruction))
    row_id: int | str = ctx.get("row_id")
    if row_id is None:
        row_id = "compat-" + hashlib.sha1(instruction.encode("utf-8")).hexdigest()[:10]
    spec = parse_case(row_id, task_type, instruction)
    prepared = ctx.get("prepared_run")
    if isinstance(prepared, PreparedRun) and row_id in prepared.cases:
        return prepared, spec
    if ctx.get("rootroute_prepare_error"):
        raise RuntimeError(f"run preparation failed: {ctx['rootroute_prepare_error']}")

    # This path exists only for the unhooked starter runner.  It is correct but
    # scans one case at a time, so the integrated run.py prepare hook is strongly
    # preferred for judged runs.
    out_dir = Path(ctx.get("out_dir", Path("/tmp") / "rootroute-output"))
    prepared = prepare_run(
        dataset_dir,
        (
            {
                "row_id": row_id,
                "task_index": task_type,
                "instruction": instruction,
            },
        ),
        out_dir,
        include_trace=True,
    )
    ctx["prepared_run"] = prepared
    return prepared, spec


def _answers(case: CaseSpec, hypotheses: tuple) -> list[dict[str, str]]:
    answers: list[dict[str, str]] = []
    requested = frozenset(case.requested_fields)
    for hypothesis in hypotheses:
        answer: dict[str, str] = {}
        if "datetime" in requested:
            answer["datetime"] = format_utc8(hypothesis.onset_epoch_s)
        if "component" in requested:
            answer["component"] = hypothesis.component
        if "reason" in requested:
            answer["reason"] = hypothesis.reason_enum
        answers.append(answer)
    return answers


def _append_diagnostic(ctx: dict[str, Any], record: dict[str, Any]) -> None:
    out_dir = Path(ctx.get("out_dir", "."))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "routing.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        # Diagnostics must never cost a scored answer. Evidence still contains
        # the human-readable routing record.
        return


def _solve(instruction: str, dataset_dir: Path, ctx: dict[str, Any]) -> Solution:
    prepared_run, case = _compat_case(instruction, Path(dataset_dir), ctx)
    prepared_case = prepared_run.for_case(case.row_id)

    metric_candidates, metric_facts = detect_metrics(prepared_case)
    trace_candidates, trace_facts = detect_traces(prepared_case)
    candidates = tuple(metric_candidates) + tuple(trace_candidates)
    facts_by_id = {fact.fact_id: fact for fact in (*metric_facts, *trace_facts)}
    ranked = rank_events(candidates, failure_count=max(1, len(candidates)))
    preparation_warnings = tuple(prepared_run.warnings) + tuple(prepared_case.warnings)
    if ctx.get("rootroute_prepare_warning"):
        preparation_warnings += (str(ctx["rootroute_prepare_warning"]),)

    policy = (
        os.environ.get("RCA_ROUTING_MODE", os.environ.get("ROOTROUTE_POLICY", "routed"))
        .strip()
        .lower()
    )
    if policy not in POLICIES:
        policy = "routed"
    llm = _shared_llm(ctx)
    before = _usage_snapshot(llm)
    outcome = route_case(
        case=case,
        candidates=ranked,
        facts=facts_by_id,
        telemetry_components=prepared_run.telemetry_components,
        llm=llm,
        policy=policy,
        pipeline_warnings=preparation_warnings,
    )
    usage = _usage_delta(before, _usage_snapshot(llm))

    if ctx.get("rootroute_llm_error"):
        preparation_warnings += (
            f"model client unavailable: {ctx['rootroute_llm_error']}",
        )
    evidence = render_evidence(
        case=case,
        outcome=outcome,
        candidates=ranked,
        facts=facts_by_id,
        preparation_warnings=preparation_warnings,
    )
    diagnostic = {
        "row_id": case.row_id,
        "task_index": case.task_type,
        **outcome.diagnostic(),
        "candidate_count": len(ranked),
        "fact_count": len(facts_by_id),
        "model_calls": sum(item.get("calls", 0) for item in usage.values()),
    }
    _append_diagnostic(ctx, diagnostic)

    return Solution(
        prediction=format_prediction(_answers(case, outcome.hypotheses)),
        evidence=evidence,
        usage=usage,
    )


def _emergency_solution(
    instruction: str,
    ctx: dict[str, Any],
    error: Exception,
) -> Solution:
    """Return an exact-count legal guess when any internal detector step fails."""

    task_type = str(ctx.get("task_index") or _infer_task_type(instruction))
    row_id: int | str = ctx.get("row_id")
    if row_id is None:
        row_id = (
            "emergency-" + hashlib.sha1(instruction.encode("utf-8")).hexdigest()[:10]
        )
    case = parse_case(row_id, task_type, instruction)
    prepared = ctx.get("prepared_run")
    components = (
        prepared.telemetry_components
        if isinstance(prepared, PreparedRun)
        else ("unknown-component",)
    )
    outcome = route_case(
        case=case,
        candidates=(),
        facts=(),
        telemetry_components=components,
        llm=None,
        policy="rules",
        pipeline_warnings=("internal pipeline failure",),
    )
    answers = _answers(case, outcome.hypotheses)
    safe_error = " ".join(f"{type(error).__name__}: {error}".split())[:500]
    evidence = "\n".join(
        [
            "# RootRoute emergency fallback",
            "",
            "## Answer",
            "",
            *(
                f"{index}. `{hypothesis.component}` — {hypothesis.reason_enum} — "
                f"{format_utc8(hypothesis.onset_epoch_s)} UTC+8"
                for index, hypothesis in enumerate(outcome.hypotheses, 1)
            ),
            "",
            "## Confidence",
            "",
            "Very low. This is a mandatory deterministic guess.",
            "",
            "## Evidence",
            "",
            "The telemetry pipeline did not complete, so no anomaly fact supports this guess.",
            f"Internal failure: `{safe_error}`",
            "",
            "## Ruled out",
            "",
            "Nothing could be ruled out because analysis did not complete.",
            "",
        ]
    )
    diagnostic = {
        "row_id": case.row_id,
        "task_index": case.task_type,
        "policy": "rules",
        "route": "emergency-fallback",
        "escalated": False,
        "used_fallback": True,
        "error": safe_error,
        "candidate_count": 0,
        "fact_count": 0,
        "model_calls": 0,
    }
    _append_diagnostic(ctx, diagnostic)
    return Solution(prediction=format_prediction(answers), evidence=evidence, usage={})


def solve(instruction: str, dataset_dir: Path, ctx: dict[str, Any]) -> Solution:
    try:
        return _solve(instruction, dataset_dir, ctx)
    except Exception as exc:  # noqa: BLE001 - the agent boundary must always answer
        try:
            return _emergency_solution(instruction, ctx, exc)
        except Exception:  # noqa: BLE001 - final no-crash guard at agent boundary
            # Official queries always satisfy the CaseSpec parser.  This final
            # guard prevents an unexpected malformed query from escaping the
            # agent boundary, even though its shape cannot be guaranteed.
            return Solution(
                prediction=format_prediction([{}]),
                evidence=(
                    "## Answer\n\nMandatory fallback could not parse the query.\n\n"
                    "## Confidence\n\nVery low.\n\n## Evidence\n\nNone.\n\n"
                    "## Ruled out\n\nNothing.\n"
                ),
                usage={},
            )


__all__ = ["prepare", "solve"]
