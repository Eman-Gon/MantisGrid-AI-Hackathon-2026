"""Deterministic, event-aware ranking for metric and trace candidates.

Ranking is deliberately count-aware and never enforces one result per component:
two distinct onset episodes on the same pod remain eligible for a two-failure
case.  Closely aligned metric/trace observations of the *same* reason family are
fused before selection so the requested count is spent on independent events,
not detector duplicates.

Topology is a bounded positive feature.  A candidate without topology evidence
is never rejected, which matters for node faults and incomplete parent caches.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from .contracts import CandidateEvent, CaseSpec, stable_event_id

_FUSION_WINDOW_S = 90.0


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _features(event: CandidateEvent) -> dict[str, float]:
    values: dict[str, float] = {}
    for name, value in event.feature_scores:
        number = _finite(value, -math.inf)
        if math.isfinite(number):
            # A malformed duplicate feature cannot silently lower an earlier
            # detector value; taking the maximum is stable under input order.
            values[str(name)] = max(values.get(str(name), -math.inf), number)
    return values


def _modalities(event: CandidateEvent) -> frozenset[str]:
    return frozenset(
        part.strip()
        for part in str(event.modality).replace(",", "+").split("+")
        if part.strip()
    )


def _reason_family(reason: str) -> str:
    value = reason.lower()
    if any(term in value for term in ("network", "packet", "retrans", "corrupt")):
        return "network"
    if "cpu" in value:
        return "cpu"
    if "memory" in value:
        return "memory"
    if "read" in value and "i/o" in value:
        return "read-io"
    if "write" in value and "i/o" in value:
        return "write-io"
    if "disk space" in value:
        return "disk-space"
    if "termination" in value:
        return "termination"
    return value


def _reason_families(event: CandidateEvent) -> frozenset[str]:
    return frozenset(_reason_family(reason) for reason in event.reason_candidates)


def _canonical_key(event: CandidateEvent) -> tuple[object, ...]:
    onset = _finite(event.onset_epoch_s, math.inf)
    return (
        event.component,
        onset,
        event.event_id,
        event.modality,
        event.reason_candidates,
        event.supporting_fact_ids,
        -_finite(event.score),
    )


def _intrinsic_score(event: CandidateEvent) -> float:
    """Put detector-specific scores on a bounded, comparable ranking scale."""

    feature = _features(event)
    detector_score = max(0.0, _finite(event.score))
    magnitude = max(0.0, feature.get("magnitude", detector_score))
    cross_signal = max(0.0, feature.get("cross_signal", 0.0))
    sustained = max(0.0, feature.get("sustained_minutes", 0.0))
    episode_count = max(0.0, feature.get("episode_count", 0.0))
    topology = max(0.0, feature.get("topology_support", 0.0))
    component_level = max(0.0, feature.get("component_level", 0.0))
    modality_support = max(
        float(len(_modalities(event))), feature.get("modality_support", 0.0)
    )

    return (
        3.0 * math.log1p(detector_score)
        + 0.80 * math.log1p(magnitude)
        + 0.45 * min(cross_signal, 5.0)
        + 0.35 * math.log1p(sustained)
        + 0.15 * math.log1p(episode_count)
        + 0.40 * min(topology, 4.0)
        + 0.10 * min(component_level, 1.0)
        + 0.70 * max(0.0, min(modality_support, 3.0) - 1.0)
        + 0.08 * math.log1p(len(event.supporting_fact_ids))
    )


def _can_fuse(group: Sequence[CandidateEvent], event: CandidateEvent) -> bool:
    representative = group[0]
    if representative.component != event.component:
        return False
    event_onset = _finite(event.onset_epoch_s, math.inf)
    group_onset = min(_finite(member.onset_epoch_s, math.inf) for member in group)
    if not math.isfinite(event_onset) or abs(event_onset - group_onset) > _FUSION_WINDOW_S:
        return False
    if not (_reason_families(representative) & _reason_families(event)):
        return False

    event_ids = {member.event_id for member in group if member.event_id}
    if event.event_id and event.event_id in event_ids:
        return True

    # Two episodes emitted by one detector remain two explicit event identities.
    # Cross-detector candidates can fuse when they independently support the
    # same component/time/reason family.
    group_modalities = frozenset().union(*(_modalities(member) for member in group))
    event_modalities = _modalities(event)
    return bool(
        group_modalities
        and event_modalities
        and group_modalities.isdisjoint(event_modalities)
    )


def _merge_features(events: Sequence[CandidateEvent]) -> tuple[tuple[str, float], ...]:
    combined: dict[str, float] = {}
    for event in events:
        for name, value in _features(event).items():
            combined[name] = max(combined.get(name, -math.inf), value)
    modalities = frozenset().union(*(_modalities(event) for event in events))
    combined["cross_signal"] = max(0.0, combined.get("cross_signal", 0.0)) + max(
        0.0, float(len(modalities) - 1)
    )
    combined["modality_support"] = float(len(modalities))
    combined["candidate_support"] = float(len(events))
    return tuple((name, round(value, 6)) for name, value in sorted(combined.items()))


def _unique_in_order(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return tuple(ordered)


def _fuse_group(events: Sequence[CandidateEvent]) -> CandidateEvent:
    if len(events) == 1:
        return events[0]
    ordered = sorted(
        events,
        key=lambda event: (-_intrinsic_score(event), _canonical_key(event)),
    )
    leader = ordered[0]
    onset = min(_finite(event.onset_epoch_s, math.inf) for event in ordered)
    modalities = sorted(frozenset().union(*(_modalities(event) for event in ordered)))
    reasons = _unique_in_order(
        reason for event in ordered for reason in event.reason_candidates
    )
    fact_ids = _unique_in_order(
        fact_id for event in ordered for fact_id in event.supporting_fact_ids
    )
    alternatives = tuple(
        sorted({alternative for event in ordered for alternative in event.alternatives})
    )
    event_id = leader.event_id or stable_event_id(
        "rank",
        "fused",
        leader.component,
        f"{onset:.6f}",
        *(event.event_id for event in sorted(ordered, key=_canonical_key)),
    )
    score = max(_finite(event.score) for event in ordered) + 0.75 * (len(ordered) - 1)
    return CandidateEvent(
        component=leader.component,
        onset_epoch_s=onset,
        score=round(score, 6),
        reason_candidates=reasons,
        supporting_fact_ids=fact_ids,
        alternatives=alternatives,
        modality="+".join(modalities),
        event_id=event_id,
        feature_scores=_merge_features(ordered),
    )


def _fuse_cross_modality(candidates: Sequence[CandidateEvent]) -> list[CandidateEvent]:
    groups: list[list[CandidateEvent]] = []
    for event in sorted(candidates, key=_canonical_key):
        destination = next((group for group in groups if _can_fuse(group, event)), None)
        if destination is None:
            groups.append([event])
        else:
            destination.append(event)
    return [_fuse_group(group) for group in groups]


def rank_events(
    candidates: Iterable[CandidateEvent],
    failure_count: int | None = None,
    *,
    case: CaseSpec | None = None,
) -> tuple[CandidateEvent, ...]:
    """Return the deterministic top independent events for a case.

    ``failure_count`` is normally parsed from the query contract.  ``case`` is
    accepted as a convenience but never inferred from model output.  If fewer
    candidates exist than requested, all available candidates are returned so
    the integration layer can fill the remaining mandatory guesses explicitly.
    """

    if failure_count is None:
        if case is None:
            raise TypeError("failure_count or case is required")
        failure_count = case.failure_count
    if isinstance(failure_count, bool) or not isinstance(failure_count, int):
        raise TypeError("failure_count must be an integer")
    if failure_count < 1:
        raise ValueError("failure_count must be positive")

    materialized = tuple(candidates)
    if any(not isinstance(event, CandidateEvent) for event in materialized):
        raise TypeError("candidates must contain CandidateEvent objects")
    fused = _fuse_cross_modality(materialized)
    if not fused:
        return ()

    onset_order = {
        identity: index
        for index, identity in enumerate(
            sorted(
                range(len(fused)),
                key=lambda index: (
                    _finite(fused[index].onset_epoch_s, math.inf),
                    _canonical_key(fused[index]),
                ),
            )
        )
    }
    denominator = max(1, len(fused) - 1)

    def ranked_key(item: tuple[int, CandidateEvent]) -> tuple[object, ...]:
        index, event = item
        # Earlier deviations receive a deliberately small causal prior.  It can
        # break a close tie but cannot overcome materially stronger evidence.
        onset_bonus = 0.35 * (1.0 - onset_order[index] / denominator)
        total = _intrinsic_score(event) + onset_bonus
        return (
            -round(total, 12),
            -round(_intrinsic_score(event), 12),
            _finite(event.onset_epoch_s, math.inf),
            event.component,
            event.event_id,
            event.reason_candidates,
            event.supporting_fact_ids,
        )

    ranked = [event for _, event in sorted(enumerate(fused), key=ranked_key)]
    return tuple(ranked[:failure_count])


# Small aliases make the integration intent obvious without maintaining another
# ranking implementation.
rank_candidates = rank_events
top_events = rank_events


__all__ = ["rank_candidates", "rank_events", "top_events"]
