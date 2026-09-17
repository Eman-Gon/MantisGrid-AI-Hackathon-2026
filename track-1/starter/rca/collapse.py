"""Service-level collapse: when every replica of a service deviates together,
the fault is on the service, not on one pod.

The benchmark names a root cause at the level the fault was injected: a single
pod (`cartservice-1`) or the whole deployment (`cartservice`).  Detectors work
per pod, so without this step the pipeline can only ever answer a pod -- and
on the dev split the deployment is the more common answer for container faults.

The rule is an SRE's, derived from telemetry naming only: pods are `<service>-<n>`
(metric_container), services are in metric_service.  If two or more replicas of
the same service show the same reason family with onsets within a short window
and scores within a small margin of each other, a service-level candidate is
added ahead of them.  The pods stay in the ranking, so the model can still
prefer one pod when one clearly moved first.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .contracts import CandidateEvent, stable_event_id
from .rank import _reason_family

_POD = re.compile(r"^(.+)-(\d+)$")
ONSET_WINDOW_S = 120.0     # replicas' onsets must fall inside this
SCORE_MARGIN = 0.25        # and score within this fraction of the group's best
MIN_REPLICAS = 2           # at least this many distinct replicas


def service_of(component: str) -> str | None:
    m = _POD.match(component)
    return m.group(1) if m and not component.startswith("node-") else None


def replicas(telemetry_components: Iterable[str]) -> dict[str, set[str]]:
    """service -> its pods, from telemetry names alone."""
    out: dict[str, set[str]] = {}
    for c in telemetry_components:
        s = service_of(c)
        if s:
            out.setdefault(s, set()).add(c)
    return out


def service_names(telemetry_components: Iterable[str]) -> frozenset[str]:
    """Services with more than one replica: the only ones a collapse can name."""
    return frozenset(s for s, pods in replicas(telemetry_components).items()
                     if len(pods) >= MIN_REPLICAS)


def collapse(ranked: Sequence[CandidateEvent], telemetry_components: Iterable[str],
             row_id) -> tuple[tuple[CandidateEvent, ...], tuple[str, ...]]:
    """Return the ranking with service-level candidates inserted, plus notes."""
    fleet = replicas(telemetry_components)
    groups: dict[tuple[str, str], list[CandidateEvent]] = {}
    for c in ranked:
        s = service_of(c.component)
        if not s or len(fleet.get(s, ())) < MIN_REPLICAS:
            continue
        groups.setdefault((s, _reason_family(c.reason_candidates[0])), []).append(c)

    added: list[CandidateEvent] = []
    notes: list[str] = []
    for (s, family), cs in groups.items():
        cs.sort(key=lambda c: -c.score)
        best = cs[0]
        # cluster around the best: same onset window, similar score, distinct pods
        members = {}
        for c in cs:
            if abs(c.onset_epoch_s - best.onset_epoch_s) <= ONSET_WINDOW_S \
                    and best.score > 0 and c.score >= best.score * (1 - SCORE_MARGIN) \
                    and c.component not in members:
                members[c.component] = c
        if len(members) < MIN_REPLICAS:
            continue
        pods = sorted(members)
        reasons = []
        for c in members.values():
            for r in c.reason_candidates:
                if r not in reasons:
                    reasons.append(r)
        # the best pod's own reason first
        reasons.sort(key=lambda r: 0 if r == best.reason_candidates[0] else 1)
        facts = []
        for c in members.values():
            facts += [f for f in c.supporting_fact_ids if f not in facts]
        added.append(CandidateEvent(
            component=s,
            onset_epoch_s=min(c.onset_epoch_s for c in members.values()),
            score=best.score + 0.01,          # ahead of its own replicas, nothing else
            reason_candidates=tuple(reasons),
            supporting_fact_ids=tuple(facts),
            alternatives=tuple(pods),
            modality=best.modality,
            event_id=stable_event_id(row_id, "service", s, family, int(best.onset_epoch_s // ONSET_WINDOW_S)),
            feature_scores=(("replicas_affected", float(len(pods))),
                            ("replicas_total", float(len(fleet[s])))),
        ))
        notes.append(f"service-level candidate `{s}`: {len(pods)}/{len(fleet[s])} replicas "
                     f"({', '.join(pods)}) share {family} within {ONSET_WINDOW_S:.0f}s")
    if not added:
        return tuple(ranked), ()
    merged = sorted((*ranked, *added), key=lambda c: -c.score)
    return tuple(merged), tuple(notes)
