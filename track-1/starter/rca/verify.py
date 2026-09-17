"""Hard validation of hypotheses against the case, the candidates and the facts.

Every rule here maps to a way the benchmark evaluator scores a correct diagnosis
as zero, or to a claim the evidence file could not back:

  * wrong number of hypotheses            -> whole case zero
  * reason not one of the 15 legal strings -> exact-match miss
  * component not seen in telemetry        -> exact-match miss, or hallucinated
  * a cited fact id that does not resolve  -> evidence "not in the data"
  * onset outside the query window         -> cannot be the asked-for failure

A hypothesis that fails is not repaired in place: the slot is refilled from the
deterministic ranking, and a warning says why.  Soft causal signals (downstream
component, onset after symptom, single modality) are NOT rejected here -- they
only move confidence, and that is route.py's job.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .contracts import (
    CandidateEvent,
    CaseSpec,
    EvidenceFact,
    Hypothesis,
    LEGAL_REASON_SET,
)

# The evaluator's tolerance is 60 s and telemetry is sampled, so a deviation
# that starts a little before the window's first sample is still the failure
# asked about.  Anything further out is a different incident.
WINDOW_SLACK_S = 120.0

DEFAULT_CONFIDENCE = 0.25   # a deterministic fill-in is a narrowed guess, not a shot in the dark


def _known_components(candidates: Iterable[CandidateEvent],
                      telemetry_components: Iterable[str] | None) -> frozenset[str]:
    known = {c.component for c in candidates}
    if telemetry_components:
        known |= set(telemetry_components)
    return frozenset(known)


def check(h: Hypothesis, case: CaseSpec, known: frozenset[str],
          facts: Mapping[str, EvidenceFact]) -> list[str]:
    """Every hard-rule violation for one hypothesis; empty means it passes."""
    problems = []
    if h.reason_enum not in LEGAL_REASON_SET:
        problems.append(f"reason {h.reason_enum!r} is not a legal reason")
    if h.component not in known:
        problems.append(f"component {h.component!r} was not seen in telemetry")
    lo, hi = case.window_epoch_s
    if not (lo - WINDOW_SLACK_S <= h.onset_epoch_s <= hi + WINDOW_SLACK_S):
        problems.append(f"onset {h.onset_epoch_s:.0f} is outside the query window")
    missing = [f for f in h.fact_ids if f not in facts]
    if missing:
        problems.append(f"cited fact id(s) do not resolve: {missing[:3]}")
    return problems


def fill_from_candidate(c: CandidateEvent, model_used: str = "deterministic",
                        confidence: float = DEFAULT_CONFIDENCE) -> Hypothesis:
    """The deterministic answer for one slot: the candidate's own top reason,
    onset and facts."""
    return Hypothesis(component=c.component, reason_enum=c.reason_candidates[0],
                      onset_epoch_s=float(c.onset_epoch_s),
                      fact_ids=tuple(c.supporting_fact_ids), confidence=confidence,
                      model_used=model_used)


def placeholder(case: CaseSpec, known: frozenset[str]) -> Hypothesis:
    """When there is no candidate at all: still a well-formed guess."""
    comp = sorted(known)[0] if known else "unknown"
    reason = "node CPU load" if comp.startswith("node-") else "container CPU load"
    return Hypothesis(component=comp, reason_enum=reason,
                      onset_epoch_s=float(case.start_epoch_s), fact_ids=(),
                      confidence=0.05, model_used="placeholder")


def verify(hypotheses: Sequence[Hypothesis], case: CaseSpec,
           ranked: Sequence[CandidateEvent], facts: Mapping[str, EvidenceFact],
           telemetry_components: Iterable[str] | None = None,
           ) -> tuple[tuple[Hypothesis, ...], tuple[str, ...]]:
    """Return exactly ``case.failure_count`` hypotheses that pass every hard
    rule, plus the warnings that explain any substitution.

    ``ranked`` is the deterministic ranking (best first); it fills any slot the
    model left empty, duplicated or invalid.  Fill-ins avoid a (component,
    reason) pair already used unless nothing else is left.
    """
    known = _known_components(ranked, telemetry_components)
    warnings: list[str] = []
    kept: list[Hypothesis] = []
    used: set[tuple[str, str]] = set()

    for i, h in enumerate(hypotheses[: case.failure_count], 1):
        problems = check(h, case, known, facts)
        key = (h.component, h.reason_enum)
        if key in used:
            problems.append(f"duplicates hypothesis {key}")
        if problems:
            warnings.append(f"hypothesis {i} rejected: " + "; ".join(problems))
            continue
        kept.append(h)
        used.add(key)
    if len(hypotheses) > case.failure_count:
        warnings.append(f"model returned {len(hypotheses)} hypotheses for "
                        f"{case.failure_count} failure(s); extras dropped")

    # refill from the deterministic ranking, in order, skipping used pairs
    pool = [c for c in ranked if (c.component, c.reason_candidates[0]) not in used]
    pool += [c for c in ranked if (c.component, c.reason_candidates[0]) in used]
    while len(kept) < case.failure_count:
        if pool:
            c = pool.pop(0)
            h = fill_from_candidate(c)
            if check(h, case, known, facts):
                continue                       # a candidate that fails its own facts
            warnings.append(f"slot {len(kept) + 1} filled from deterministic ranking: "
                            f"{h.component} / {h.reason_enum}")
        else:
            h = placeholder(case, known)
            warnings.append(f"slot {len(kept) + 1} is a placeholder: no candidate available")
        kept.append(h)
        used.add((h.component, h.reason_enum))

    # the answer is written in time order
    kept.sort(key=lambda h: h.onset_epoch_s)
    return tuple(kept), tuple(warnings)
