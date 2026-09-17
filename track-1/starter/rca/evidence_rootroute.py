"""Deterministic, fact-addressable evidence reports for RootRoute."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contracts import CandidateEvent, CaseSpec, EvidenceFact, format_utc8
from .routing import RoutingOutcome


def _cell(value: object, limit: int = 260) -> str:
    text = " ".join(str(value).replace("|", "\\|").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _confidence_label(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    if value >= 0.15:
        return "low"
    return "very low"


def render_evidence(
    *,
    case: CaseSpec,
    outcome: RoutingOutcome,
    candidates: Sequence[CandidateEvent],
    facts: Mapping[str, EvidenceFact] | Sequence[EvidenceFact],
    preparation_warnings: Sequence[str] = (),
) -> str:
    """Render a report solely from contracts, detector facts, and route state."""

    fact_by_id = (
        dict(facts)
        if isinstance(facts, Mapping)
        else {fact.fact_id: fact for fact in facts}
    )
    lines = [
        "# RootRoute evidence",
        "",
        "## Answer",
        "",
    ]
    for index, hypothesis in enumerate(outcome.hypotheses, 1):
        confidence = _confidence_label(hypothesis.confidence)
        lines.append(
            f"{index}. `{_cell(hypothesis.component)}` — "
            f"{_cell(hypothesis.reason_enum)} — "
            f"{format_utc8(hypothesis.onset_epoch_s)} UTC+8 "
            f"(**{confidence}**, {hypothesis.confidence:.2f})"
        )

    lines.extend(
        [
            "",
            "## Confidence",
            "",
        ]
    )
    for index, hypothesis in enumerate(outcome.hypotheses, 1):
        lines.append(
            f"- Failure {index}: **{_confidence_label(hypothesis.confidence)}** "
            f"(`{hypothesis.confidence:.2f}`)."
        )
    if outcome.used_fallback:
        lines.append(
            "- The final answer used deterministic fallback; confidence is capped by "
            "the available detector evidence."
        )

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            (
                "Every row below is a detector-produced fact. The language model was not "
                "allowed to add facts or write this report."
            ),
            "",
        ]
    )
    cited: set[str] = set()
    for index, hypothesis in enumerate(outcome.hypotheses, 1):
        lines.append(f"### Failure {index}")
        lines.append("")
        available = [
            fact_by_id[fact_id]
            for fact_id in hypothesis.fact_ids
            if fact_id in fact_by_id
        ]
        if not available:
            lines.append(
                "No verified anomaly fact supports this mandatory best guess. Treat it "
                "as very low confidence."
            )
            lines.append("")
            continue
        lines.extend(
            [
                "| fact | signal | onset (UTC+8) | observed | baseline |",
                "|---|---|---|---:|---:|",
            ]
        )
        for fact in available:
            cited.add(fact.fact_id)
            lines.append(
                f"| `{_cell(fact.fact_id)}` | `{_cell(fact.signal)}` | "
                f"{format_utc8(fact.onset_epoch_s)} | {_cell(fact.observed)} | "
                f"{_cell(fact.baseline)} |"
            )
        lines.append("")
        for fact in available:
            lines.append(
                f"- `{_cell(fact.fact_id)}`: {_cell(fact.method)} "
                f"Source: `{_cell(fact.source_file)}`; locator: `{_cell(fact.locator)}`."
            )
        lines.append("")

    selected_ids = {
        candidate.event_id
        for hypothesis in outcome.hypotheses
        for candidate in candidates
        if abs(candidate.onset_epoch_s - hypothesis.onset_epoch_s) <= 60.0
        and bool(set(hypothesis.fact_ids) & set(candidate.supporting_fact_ids))
    }
    alternatives = [
        candidate for candidate in candidates if candidate.event_id not in selected_ids
    ][:5]
    lines.extend(["## Ruled out", ""])
    if alternatives:
        lines.append(
            "These lower-ranked candidates were not selected. They are alternatives, "
            "not claims of absolute exclusion:"
        )
        lines.append("")
        lines.extend(
            [
                "| component | onset (UTC+8) | detector score | possible reasons |",
                "|---|---|---:|---|",
            ]
        )
        for candidate in alternatives:
            lines.append(
                f"| `{_cell(candidate.component)}` | {format_utc8(candidate.onset_epoch_s)} | "
                f"{candidate.score:.3f} | {_cell(', '.join(candidate.reason_candidates))} |"
            )
    else:
        lines.append("No other detector candidate was available.")

    ambiguity = outcome.ambiguity
    lines.extend(
        [
            "",
            "## Routing decision",
            "",
            f"- Policy: `{_cell(outcome.policy)}`",
            f"- Path used: `{_cell(outcome.route)}`",
            f"- Strong-tier escalation: `{'yes' if outcome.escalated else 'no'}`",
            f"- Candidate support floor: `{ambiguity.support_floor:.3f}`",
            f"- Candidate margin: `{ambiguity.score_margin:.3f}`",
            f"- Evidence coverage: `{ambiguity.evidence_coverage:.1%}`",
            f"- Metrics/traces disagreement: `{'yes' if ambiguity.metric_trace_disagreement else 'no'}`",
            f"- Ambiguous network endpoint: `{'yes' if ambiguity.network_endpoint_ambiguity else 'no'}`",
            f"- Trace topology incomplete for selected evidence: `{'yes' if ambiguity.topology_incomplete else 'no'}`",
        ]
    )
    if ambiguity.deterministic_reasons:
        lines.append(
            "- Why deterministic evidence was not conclusive: "
            + "; ".join(_cell(reason) for reason in ambiguity.deterministic_reasons)
        )
    if ambiguity.strong_reasons:
        lines.append(
            "- Objective strong-tier triggers: "
            + "; ".join(_cell(reason) for reason in ambiguity.strong_reasons)
        )

    all_warnings = tuple(preparation_warnings) + tuple(outcome.warnings)
    lines.extend(["", "## Limitations", ""])
    if all_warnings:
        lines.extend(f"- {_cell(warning, 400)}" for warning in all_warnings)
    else:
        lines.append("- No pipeline warning was recorded for this case.")
    if any(not hypothesis.fact_ids for hypothesis in outcome.hypotheses):
        lines.append("- At least one required answer is an unsupported fallback guess.")
    if outcome.used_fallback:
        lines.append("- Model selection was not used as the final answer.")

    lines.extend(
        [
            "",
            "## Method boundary",
            "",
            (
                "Raw telemetry was processed locally. Models, when used, received only a "
                "bounded list of candidate event IDs and compact verified facts. Final "
                "component names, reasons, times, failure count, and fact IDs were checked "
                "by deterministic code."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_evidence"]
