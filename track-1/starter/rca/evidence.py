"""Render the evidence file from verified structures.  No model writes a number.

The four sections are the ones docs/submission.md asks for and judges check
against the raw telemetry.  Every value printed comes from an EvidenceFact or
is derived mechanically from one (a difference, a ratio, a UTC+8 timestamp).
A negative claim ("ruled out") is only made for a candidate that was actually
scored and compared; anything else is "could not be ruled out".
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import (
    CandidateEvent,
    CaseSpec,
    EvidenceFact,
    Hypothesis,
    format_utc8,
)
from .evidence_rootroute import render_evidence as render_evidence

MAX_FACTS_PER_HYPOTHESIS = 6
MAX_RULED_OUT = 6
_POD = re.compile(r"^(.+)-(\d+)$")


def _num(x) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, (int, float)):
        a = abs(x)
        if a >= 1e6:
            return f"{x:,.0f}"
        if a >= 100:
            return f"{x:,.1f}"
        if a >= 1:
            return f"{x:.2f}"
        return f"{x:.4g}"
    return str(x)


def _ratio(f: EvidenceFact) -> str:
    try:
        o, b = float(f.observed), float(f.baseline)
    except (TypeError, ValueError):
        return ""
    if b == 0:
        return " (baseline zero)"
    r = o / b
    return f" ({r:.1f}x baseline)" if r >= 1 else f" ({1 / r:.1f}x below baseline)"


def _fact_line(f: EvidenceFact) -> str:
    return (f"- `{f.source_file}` — {f.locator}: **{f.signal}** on `{f.component}` "
            f"observed {_num(f.observed)} vs baseline {_num(f.baseline)}{_ratio(f)}; "
            f"deviation from {format_utc8(f.onset_epoch_s)} UTC+8. Method: {f.method}.")


def _confidence_word(c: float) -> str:
    return "High" if c >= 0.7 else "Medium" if c >= 0.4 else "Low"


def _answer_line(h: Hypothesis, case: CaseSpec) -> str:
    parts = []
    if "datetime" in case.requested_fields:
        parts.append(format_utc8(h.onset_epoch_s))
    if "component" in case.requested_fields:
        parts.append(f"`{h.component}`")
    if "reason" in case.requested_fields:
        parts.append(h.reason_enum)
    asked = " / ".join(parts)
    full = f"{h.component} / {h.reason_enum} / {format_utc8(h.onset_epoch_s)}"
    return f"{asked}  (full: {full})" if asked != full else full


def render(case: CaseSpec, hypotheses: Sequence[Hypothesis],
           ranked: Sequence[CandidateEvent], facts: Mapping[str, EvidenceFact],
           reasoning: str = "", warnings: Sequence[str] = (),
           usage: Mapping[str, Mapping[str, int]] | None = None) -> str:
    """The evidence markdown for one case."""
    lo, hi = case.window_utc8
    out = [f"# Case {case.row_id} — {case.task_type}", "",
           f"Window {lo:%Y-%m-%d %H:%M}–{hi:%H:%M} UTC+8; the instruction states "
           f"{case.failure_count} failure(s); asked for {', '.join(case.requested_fields)}.", ""]

    # ## Answer
    out += ["## Answer", ""]
    for i, h in enumerate(hypotheses, 1):
        out.append(f"{i}. {_answer_line(h, case)}")
    out.append("")

    # ## Confidence
    out += ["## Confidence", ""]
    for i, h in enumerate(hypotheses, 1):
        n = len([f for f in h.fact_ids if f in facts])
        src = {facts[f].source_file.split("/")[-1] for f in h.fact_ids if f in facts}
        how = ("deterministic ranking, no model" if h.model_used in ("deterministic", "placeholder")
               else f"chosen by `{h.model_used}`")
        line = (f"{i}. **{_confidence_word(h.confidence)}** ({h.confidence:.2f}) — {how}; "
                f"{n} supporting fact(s) from {', '.join(sorted(src)) or 'no source'}.")
        # a service-level answer (no replica suffix) carries its replicas as alternatives
        cand = next((c for c in ranked if c.component == h.component and c.alternatives
                     and not _POD.match(h.component)), None)
        if cand:
            line += (f" Service-level: replicas {', '.join(f'`{p}`' for p in cand.alternatives)} "
                     f"deviated together, so the fault is placed on the service.")
        if n == 0:
            line += " No fact supports this slot: it is a guess, not a diagnosis."
        elif len(src) == 1:
            line += " Only one telemetry modality supports it."
        out.append(line)
    # runner-up margin, when it is small, is the honest thing to say
    if len(ranked) > case.failure_count:
        top, nxt = ranked[0], ranked[case.failure_count]
        if top.score > 0 and nxt.score / top.score >= 0.7:
            out.append(f"\nThe next candidate, `{nxt.component}` "
                       f"({nxt.reason_candidates[0]}), scored {nxt.score:.2f} against the "
                       f"leader's {top.score:.2f} — close enough that the ordering could be "
                       f"sampling noise.")
    if reasoning:
        out += ["", f"Model reasoning: {reasoning.strip()}"]
    out.append("")

    # ## Evidence
    out += ["## Evidence", ""]
    cited_any = False
    for i, h in enumerate(hypotheses, 1):
        hf = [facts[f] for f in h.fact_ids if f in facts]
        hf.sort(key=lambda f: f.onset_epoch_s)
        out.append(f"**{i}. `{h.component}` / {h.reason_enum}**")
        if not hf:
            out.append("- no fact is cited for this slot")
        for f in hf[:MAX_FACTS_PER_HYPOTHESIS]:
            out.append(_fact_line(f))
            cited_any = True
        if len(hf) > MAX_FACTS_PER_HYPOTHESIS:
            out.append(f"- …and {len(hf) - MAX_FACTS_PER_HYPOTHESIS} more fact(s) on the same component")
        out.append("")
    if not cited_any:
        out.append("No telemetry fact could be tied to the answer; the answer is a guess.\n")

    # ## Ruled out
    out += ["## Ruled out", ""]
    chosen = {(h.component, h.reason_enum) for h in hypotheses}
    chosen_comp = {h.component for h in hypotheses}
    shown = 0
    for c in ranked:
        if (c.component, c.reason_candidates[0]) in chosen:
            continue
        if shown >= MAX_RULED_OUT:
            break
        cf = [facts[f] for f in c.supporting_fact_ids if f in facts]
        lead = ranked[0].score if ranked and ranked[0].score else None
        why = []
        if lead:
            why.append(f"scored {c.score:.2f} vs the leader's {lead:.2f}")
        first_chosen = min((h.onset_epoch_s for h in hypotheses), default=None)
        if first_chosen is not None and c.onset_epoch_s > first_chosen + 30:
            why.append(f"its deviation starts {c.onset_epoch_s - first_chosen:.0f}s after the chosen onset")
        if c.component in chosen_comp:
            why.append("same component as a chosen answer with a different reason candidate")
        if cf:
            f = cf[0]
            why.append(f"strongest signal {f.signal} {_num(f.observed)} vs {_num(f.baseline)}{_ratio(f)}")
        verdict = "ruled out" if why and lead and c.score / lead < 0.7 else "could not be ruled out"
        out.append(f"- `{c.component}` ({c.reason_candidates[0]}) — {verdict}: "
                   + ("; ".join(why) if why else "no comparison was computed") + ".")
        shown += 1
    if shown == 0:
        out.append("No alternative candidate was scored; nothing can honestly be ruled out.")
    out.append("")

    # provenance
    out += ["## How this was produced", ""]
    if usage:
        for m, u in usage.items():
            out.append(f"- `{m}`: {u.get('calls', 0)} call(s), {u.get('prompt_tokens', 0):,} in / "
                       f"{u.get('completion_tokens', 0):,} out")
    else:
        out.append("- no model call was made")
    for w in warnings:
        out.append(f"- {w}")
    return "\n".join(out) + "\n"
