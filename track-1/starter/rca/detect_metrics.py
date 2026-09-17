"""Deterministic, reason-aware detection over prepared one-minute metrics.

The preparation stage deliberately presents a small, answer-free table: one row
per component, signal, and minute in the query window, with baseline statistics
computed outside that window.  This module turns that table into candidate
failure *events* and immutable evidence facts.  It never reads raw telemetry,
gold answers, or the network.

Two design choices are load-bearing:

* onset is the first minute in a sustained deviation, not the later peak; and
* candidates are clustered by component *and onset*, so two failures on the
  same component remain two distinct events.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import math
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from .contracts import (
    CandidateEvent,
    EvidenceFact,
    LEGAL_REASONS,
    stable_event_id,
    stable_fact_id,
)


REQUIRED_COLUMNS = frozenset(
    {
        "source_file",
        "source_kind",
        "component",
        "signal",
        "minute_epoch_s",
        "count",
        "mean",
        "min",
        "max",
        "delta",
        "baseline_mean",
        "baseline_std",
        "baseline_delta_mean",
        "baseline_delta_std",
    }
)

_NUMERIC_COLUMNS = (
    "minute_epoch_s",
    "count",
    "mean",
    "min",
    "max",
    "delta",
    "baseline_mean",
    "baseline_std",
    "baseline_delta_mean",
    "baseline_delta_std",
)

# A minute becomes a possible onset at the lower threshold.  An episode then
# needs two adjacent minutes and at least one clear deviation.  Discrete event
# counters and CPU spikes may qualify as a single, very strong minute.
_ONSET_SCORE = 2.5
_SUSTAINED_SCORE = 3.5
_ISOLATED_EVENT_SCORE = 6.0
_ADJACENT_MINUTE_S = 90.0
_CLUSTER_ONSET_S = 90.0
_MAX_STANDARD_SCORE = 25.0
_IO_CONTEXT_WINDOW_S = 120.0
_IO_PERSISTENCE_RATIO = 0.20
_IO_RELATED_LEVEL_SCORE = 1.50
_UNCONFIRMED_IO_IMPULSE_CAP = 6.0

_REASON_ORDER = {reason: index for index, reason in enumerate(LEGAL_REASONS)}


@dataclass(frozen=True, slots=True)
class _Profile:
    reason: str
    direction: int = 1
    # gauge: sustained level; counter: rate-of-change; spike: abrupt/extreme;
    # event: either a level transition or a counter increment.
    mode: str = "gauge"
    allow_isolated: bool = False


@dataclass(frozen=True, slots=True)
class _MinuteScore:
    score: float
    observed: float
    baseline: float
    basis: str


@dataclass(frozen=True, slots=True)
class _Episode:
    source_file: str
    source_kind: str
    component: str
    signal: str
    reason: str
    onset_epoch_s: float
    end_epoch_s: float
    score: float
    raw_score: float
    minute_count: int
    fact: EvidenceFact
    context_facts: tuple[EvidenceFact, ...] = ()
    post_onset_persistence: float = 0.0
    related_signal_support: float = 0.0
    isolated_impulse: bool = False
    effective_minute_count: int = 1


def _normalise_signal(signal: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", signal.lower()).strip("_")


def _has(name: str, *fragments: str) -> bool:
    return any(fragment in name for fragment in fragments)


def _is_counter_signal(name: str) -> bool:
    """Recognise cumulative container counters whose raw level is misleading."""

    return _has(
        name,
        "_seconds",
        "_periods",
        "_packets",
        "_errors",
        "_failcnt",
        "_failures",
        "_reads_mb",
        "_writes_mb",
        "_fs_reads",
        "_fs_writes",
        "_sector_reads",
        "_sector_writes",
        "_retransmit",
        "_retrans_",
    )


def _direct_node_io_direction(signal: str) -> str | None:
    """Return the direction for a direct throughput signal, not an await gauge."""

    name = _normalise_signal(signal)
    if "await" in name:
        return None
    if _has(name, "system_io_r_s", "system_io_rkb_s", "disk_read"):
        return "read"
    if _has(name, "system_io_w_s", "system_io_wkb_s", "disk_write"):
        return "write"
    return None


def _node_io_await_direction(signal: str) -> str | None:
    name = _normalise_signal(signal)
    if "system_io_r_await" in name or ("disk_read" in name and "await" in name):
        return "read"
    if "system_io_w_await" in name or ("disk_write" in name and "await" in name):
        return "write"
    return None


@lru_cache(maxsize=1024)
def _profiles_for(source_kind: str, signal: str) -> tuple[_Profile, ...]:
    """Map a source/signal pair to legal reasons and the right feature shape.

    The mapping is intentionally semantic rather than tied to the development
    deployment's component names.  Unknown signals produce no root candidate.
    """

    kind = source_kind.strip().lower()
    name = _normalise_signal(signal)

    if kind == "container":
        # CPU specification values are configuration, not load.
        if name.startswith("container_cpu_") and not _has(
            name,
            "spec_cpu",
            "cpu_quota",
            "cpu_shares",
            "cpu_period",
        ):
            mode = "counter" if _is_counter_signal(name) else "gauge"
            return (_Profile("container CPU load", mode=mode),)

        if name.startswith("container_memory_") and not _has(
            name,
            "limit_mb",
            "reservation_limit",
        ):
            mode = "counter" if _has(name, "failcnt", "failures") else "gauge"
            return (_Profile("container memory load", mode=mode),)

        if name.startswith("container_fs_"):
            if _has(name, "read") and not _has(name, "inodes", "readonly"):
                return (_Profile("container read I/O load", mode="counter"),)
            if _has(name, "write"):
                return (_Profile("container write I/O load", mode="counter"),)

        # Keep the network labels disjoint where the signal is specific.  The
        # broad "errors" form is treated as corruption, while explicit drops
        # are packet loss.
        if _has(name, "retrans"):
            mode = "counter" if _is_counter_signal(name) else "gauge"
            return (
                _Profile(
                    "container network packet retransmission",
                    mode=mode,
                    allow_isolated=True,
                ),
            )
        if _has(name, "latency", "round_trip", "rtt", "network_delay"):
            return (_Profile("container network latency"),)
        if _has(name, "corrupt", "checksum", "crc"):
            mode = "counter" if _is_counter_signal(name) else "gauge"
            return (
                _Profile(
                    "container network packet corruption",
                    mode=mode,
                    allow_isolated=True,
                ),
            )
        if _has(name, "packets_dropped", "packet_drop", "packet_loss", "lost_packet"):
            mode = "counter" if _is_counter_signal(name) else "gauge"
            return (
                _Profile(
                    "container packet loss",
                    mode=mode,
                    allow_isolated=True,
                ),
            )
        if "network" in name and _has(name, "_errors", "_error"):
            mode = "counter" if _is_counter_signal(name) else "gauge"
            return (
                _Profile(
                    "container network packet corruption",
                    mode=mode,
                    allow_isolated=True,
                ),
            )

        if _has(name, "restart", "terminated", "termination", "killed", "stopped", "dead"):
            return (
                _Profile(
                    "container process termination",
                    mode="event",
                    allow_isolated=True,
                ),
            )
        if _has(name, "last_seen", "tasks_state_running", "process_alive", "container_up"):
            return (
                _Profile(
                    "container process termination",
                    direction=-1,
                    mode="event",
                    allow_isolated=True,
                ),
            )
        if _has(name, "start_time"):
            return (
                _Profile(
                    "container process termination",
                    mode="counter",
                    allow_isolated=True,
                ),
            )

    if kind == "node":
        if _has(name, "system_cpu_", "system_load_"):
            profiles = [_Profile("node CPU load")]
            # Load averages already smooth time; an instantaneous spike label
            # is useful only for direct CPU usage signals.
            if "system_cpu_" in name:
                profiles.append(
                    _Profile("node CPU spike", mode="spike", allow_isolated=True)
                )
            return tuple(profiles)

        if _has(name, "system_mem_", "system_memory_", "system_swap_") and not _has(
            name, "_total"
        ):
            direction = -1 if _has(name, "_free", "_usable", "_available") else 1
            return (_Profile("node memory consumption", direction=direction),)

        if _has(name, "system_disk_", "system_fs_inodes_") and not _has(
            name, "_total", "readonly"
        ):
            if _has(name, "_free", "_available"):
                direction = -1
            elif _has(name, "_used", "pct_usage", "in_use"):
                direction = 1
            else:
                return ()
            return (_Profile("node disk space consumption", direction=direction),)

        io_direction = _direct_node_io_direction(name)
        if io_direction == "read":
            # Direct throughput rates can represent a real one-minute burst.
            # Accept a strong level/delta event without weakening thresholds
            # for every gauge in the table.
            return (
                _Profile(
                    "node disk read I/O consumption",
                    mode="event",
                    allow_isolated=True,
                ),
            )
        if io_direction == "write":
            return (
                _Profile(
                    "node disk write I/O consumption",
                    mode="event",
                    allow_isolated=True,
                ),
            )
        await_direction = _node_io_await_direction(name)
        if await_direction == "read":
            return (_Profile("node disk read I/O consumption"),)
        if await_direction == "write":
            return (_Profile("node disk write I/O consumption"),)

    # Service aggregates can corroborate another detector, but they are not
    # valid root components and therefore intentionally originate no event.
    return ()


def _float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _standard_score(observed: Any, baseline: Any, std: Any) -> float:
    """Return a bounded signed score, including a zero-variance fallback."""

    obs = _float(observed)
    base = _float(baseline)
    if obs is None or base is None:
        return 0.0
    sigma = _float(std)
    if sigma is not None and sigma > max(1e-12, abs(base) * 1e-12):
        score = (obs - base) / sigma
    else:
        difference = obs - base
        tolerance = max(1e-12, max(abs(obs), abs(base)) * 1e-9)
        if abs(difference) <= tolerance:
            return 0.0
        # A constant baseline still carries information.  A roughly 10%
        # change scores about four, while zero -> non-zero is a strong event.
        scale = max(max(abs(obs), abs(base)) * 0.025, 1e-12)
        score = difference / scale
    return max(-_MAX_STANDARD_SCORE, min(_MAX_STANDARD_SCORE, score))


def _score_minute(row: Mapping[str, Any], profile: _Profile) -> _MinuteScore:
    direction = float(profile.direction)
    mean_z = direction * _standard_score(
        row["mean"], row["baseline_mean"], row["baseline_std"]
    )
    extreme_key = "max" if profile.direction > 0 else "min"
    extreme_z = direction * _standard_score(
        row[extreme_key], row["baseline_mean"], row["baseline_std"]
    )
    delta_z = direction * _standard_score(
        row["delta"], row["baseline_delta_mean"], row["baseline_delta_std"]
    )
    mean_z = max(0.0, mean_z)
    extreme_z = max(0.0, extreme_z)
    delta_z = max(0.0, delta_z)

    if profile.mode == "counter":
        return _MinuteScore(
            score=delta_z,
            observed=_float(row["delta"]) or 0.0,
            baseline=_float(row["baseline_delta_mean"]) or 0.0,
            basis="minute delta versus baseline delta",
        )
    if profile.mode == "spike":
        within_minute_spike = max(0.0, extreme_z - mean_z)
        if delta_z >= within_minute_spike:
            observed = _float(row["delta"])
            baseline = _float(row["baseline_delta_mean"])
            basis = "abrupt minute delta versus baseline delta"
        else:
            observed = _float(row[extreme_key])
            baseline = _float(row["baseline_mean"])
            basis = f"minute {extreme_key} versus baseline mean"
        return _MinuteScore(
            score=max(delta_z, within_minute_spike),
            observed=observed or 0.0,
            baseline=baseline or 0.0,
            basis=basis,
        )
    if profile.mode == "event":
        possibilities = (
            (mean_z, _float(row["mean"]), _float(row["baseline_mean"]), "minute mean versus baseline mean"),
            (extreme_z, _float(row[extreme_key]), _float(row["baseline_mean"]), f"minute {extreme_key} versus baseline mean"),
            (delta_z, _float(row["delta"]), _float(row["baseline_delta_mean"]), "minute delta versus baseline delta"),
        )
        score, observed, baseline, basis = max(possibilities, key=lambda item: item[0])
        return _MinuteScore(
            score=score,
            observed=observed or 0.0,
            baseline=baseline or 0.0,
            basis=basis,
        )

    # Gauges should remain anomalous while the level remains elevated.  Delta
    # can strengthen the transition, but cannot erase a sustained level shift.
    score = max(mean_z, 0.75 * mean_z + 0.25 * delta_z)
    observed = _float(row["mean"])
    if observed is None:
        observed = _float(row[extreme_key]) or 0.0
    return _MinuteScore(
        score=score,
        observed=observed,
        baseline=_float(row["baseline_mean"]) or 0.0,
        basis="minute mean versus baseline mean",
    )


def _qualifying_runs(
    rows_and_scores: list[tuple[Mapping[str, Any], _MinuteScore]],
    *,
    allow_isolated: bool,
) -> Iterable[list[tuple[Mapping[str, Any], _MinuteScore]]]:
    active = [
        item
        for item in rows_and_scores
        if item[1].score >= _ONSET_SCORE
        and _float(item[0]["minute_epoch_s"]) is not None
    ]
    if not active:
        return ()

    runs: list[list[tuple[Mapping[str, Any], _MinuteScore]]] = []
    current: list[tuple[Mapping[str, Any], _MinuteScore]] = []
    for item in active:
        minute = float(item[0]["minute_epoch_s"])
        previous = float(current[-1][0]["minute_epoch_s"]) if current else None
        if current and previous is not None and minute - previous > _ADJACENT_MINUTE_S:
            runs.append(current)
            current = []
        current.append(item)
    if current:
        runs.append(current)

    qualified = []
    for run in runs:
        peak = max(item[1].score for item in run)
        sustained = len(run) >= 2 and peak >= _SUSTAINED_SCORE
        isolated_event = allow_isolated and peak >= _ISOLATED_EVENT_SCORE
        if sustained or isolated_event:
            qualified.append(run)
    return tuple(qualified)


def _fact_for_run(
    *,
    row_id: int | str,
    source_file: str,
    component: str,
    signal: str,
    run: list[tuple[Mapping[str, Any], _MinuteScore]],
) -> EvidenceFact:
    onset_row, onset_score = run[0]
    onset = float(onset_row["minute_epoch_s"])
    onset_token = int(onset) if onset.is_integer() else onset
    fact_id = stable_fact_id(
        row_id,
        "metric",
        source_file,
        component,
        signal,
        onset_token,
        onset_score.basis,
    )
    locator = (
        f"minute_epoch_s={onset_token}; component={component}; signal={signal}"
    )
    if len(run) >= 2:
        method = (
            f"first of {len(run)} adjacent anomalous one-minute aggregates; "
            f"{onset_score.basis}"
        )
    else:
        method = f"first strong discrete-event minute; {onset_score.basis}"
    return EvidenceFact(
        fact_id=fact_id,
        source_file=source_file,
        locator=locator,
        component=component,
        signal=signal,
        onset_epoch_s=onset,
        observed=onset_score.observed,
        baseline=onset_score.baseline,
        method=method,
    )


def _episodes_for_series(
    *,
    row_id: int | str,
    source_file: str,
    source_kind: str,
    component: str,
    signal: str,
    records: list[Mapping[str, Any]],
) -> tuple[_Episode, ...]:
    episodes: list[_Episode] = []
    for profile in _profiles_for(source_kind, signal):
        rows_and_scores = [(row, _score_minute(row, profile)) for row in records]
        for run in _qualifying_runs(
            rows_and_scores, allow_isolated=profile.allow_isolated
        ):
            fact = _fact_for_run(
                row_id=row_id,
                source_file=source_file,
                component=component,
                signal=signal,
                run=run,
            )
            episodes.append(
                _Episode(
                    source_file=source_file,
                    source_kind=source_kind,
                    component=component,
                    signal=signal,
                    reason=profile.reason,
                    onset_epoch_s=fact.onset_epoch_s,
                    end_epoch_s=float(run[-1][0]["minute_epoch_s"]),
                    score=max(item[1].score for item in run),
                    raw_score=max(item[1].score for item in run),
                    minute_count=len(run),
                    fact=fact,
                    effective_minute_count=len(run),
                )
            )
    return tuple(episodes)


def _context_fact(
    *,
    row_id: int | str,
    source_file: str,
    component: str,
    signal: str,
    row: Mapping[str, Any],
    context_kind: str,
    method: str,
) -> EvidenceFact:
    minute = float(row["minute_epoch_s"])
    minute_token = int(minute) if minute.is_integer() else minute
    return EvidenceFact(
        fact_id=stable_fact_id(
            row_id,
            "metric-context",
            context_kind,
            source_file,
            component,
            signal,
            minute_token,
        ),
        source_file=source_file,
        locator=(
            f"minute_epoch_s={minute_token}; component={component}; signal={signal}"
        ),
        component=component,
        signal=signal,
        onset_epoch_s=minute,
        observed=_float(row["mean"]) or 0.0,
        baseline=_float(row["baseline_mean"]) or 0.0,
        method=method,
    )


def _enrich_direct_io_episodes(
    *,
    row_id: int | str,
    episodes: Iterable[_Episode],
    series_records: Mapping[
        tuple[str, str, str, str], list[Mapping[str, Any]]
    ],
) -> tuple[_Episode, ...]:
    """Distinguish persistent node I/O pressure from an isolated normal impulse.

    A direct throughput spike remains a candidate even without context.  Its
    effective magnitude merely saturates, because a very large single sample is
    not proportionally stronger causal evidence.  Immediate level persistence
    or a post-onset same-direction await signal removes that saturation and is
    retained as independently verifiable evidence.
    """

    by_component: dict[
        tuple[str, str, str], list[tuple[str, list[Mapping[str, Any]]]]
    ] = {}
    for (source_file, source_kind, component, signal), records in series_records.items():
        by_component.setdefault((source_file, source_kind, component), []).append(
            (signal, records)
        )
    for values in by_component.values():
        values.sort(key=lambda item: item[0])

    enriched: list[_Episode] = []
    for episode in episodes:
        direction = (
            _direct_node_io_direction(episode.signal)
            if episode.source_kind == "node" and episode.minute_count == 1
            else None
        )
        if direction is None:
            enriched.append(episode)
            continue

        series_key = (
            episode.source_file,
            episode.source_kind,
            episode.component,
            episode.signal,
        )
        own_records = series_records.get(series_key, [])
        onset_row = next(
            (
                row
                for row in own_records
                if abs(float(row["minute_epoch_s"]) - episode.onset_epoch_s) < 1e-6
            ),
            None,
        )
        next_row = next(
            (
                row
                for row in own_records
                if 0.0
                < float(row["minute_epoch_s"]) - episode.onset_epoch_s
                <= _ADJACENT_MINUTE_S
            ),
            None,
        )

        persistence = 0.0
        context_facts: list[EvidenceFact] = []
        if onset_row is not None and next_row is not None:
            onset_mean = _float(onset_row["mean"])
            onset_base = _float(onset_row["baseline_mean"])
            next_mean = _float(next_row["mean"])
            next_base = _float(next_row["baseline_mean"])
            if None not in (onset_mean, onset_base, next_mean, next_base):
                onset_excess = max(0.0, float(onset_mean) - float(onset_base))
                next_excess = max(0.0, float(next_mean) - float(next_base))
                if onset_excess > 0.0:
                    persistence = min(1.0, next_excess / onset_excess)
            if persistence >= _IO_PERSISTENCE_RATIO:
                context_facts.append(
                    _context_fact(
                        row_id=row_id,
                        source_file=episode.source_file,
                        component=episode.component,
                        signal=episode.signal,
                        row=next_row,
                        context_kind="post-onset-persistence",
                        method=(
                            "post-onset persistence check; the next-minute mean "
                            "retained at least 20% of the onset excess above baseline"
                        ),
                    )
                )

        related_support = 0.0
        related_row: Mapping[str, Any] | None = None
        related_signal = ""
        component_key = (
            episode.source_file,
            episode.source_kind,
            episode.component,
        )
        for signal, records in by_component.get(component_key, []):
            if _node_io_await_direction(signal) != direction:
                continue
            for row in records:
                offset = float(row["minute_epoch_s"]) - episode.onset_epoch_s
                if not 0.0 <= offset <= _IO_CONTEXT_WINDOW_S:
                    continue
                score = max(
                    0.0,
                    _standard_score(
                        row["mean"], row["baseline_mean"], row["baseline_std"]
                    ),
                )
                if (
                    score > related_support
                    or (
                        score == related_support
                        and related_row is not None
                        and float(row["minute_epoch_s"])
                        < float(related_row["minute_epoch_s"])
                    )
                ):
                    related_support = score
                    related_row = row
                    related_signal = signal
        if related_row is not None and related_support >= _IO_RELATED_LEVEL_SCORE:
            context_facts.append(
                _context_fact(
                    row_id=row_id,
                    source_file=episode.source_file,
                    component=episode.component,
                    signal=related_signal,
                    row=related_row,
                    context_kind="post-onset-directional-await",
                    method=(
                        "post-onset directional corroboration; the related await "
                        "mean was at least 1.5 baseline standard deviations high"
                    ),
                )
            )

        has_persistence = persistence >= _IO_PERSISTENCE_RATIO
        has_related_signal = related_support >= _IO_RELATED_LEVEL_SCORE
        corroborated = has_persistence or has_related_signal
        adjusted_score = (
            episode.score
            if corroborated
            else min(episode.score, _UNCONFIRMED_IO_IMPULSE_CAP)
        )
        enriched.append(
            replace(
                episode,
                score=adjusted_score,
                context_facts=tuple(context_facts),
                post_onset_persistence=persistence,
                related_signal_support=related_support,
                isolated_impulse=not corroborated,
                effective_minute_count=2 if has_persistence else 1,
            )
        )
    return tuple(enriched)


def _cluster_episodes(episodes: Iterable[_Episode]) -> tuple[tuple[_Episode, ...], ...]:
    ordered = sorted(
        episodes,
        key=lambda episode: (
            episode.component,
            episode.onset_epoch_s,
            episode.reason,
            episode.signal,
            episode.fact.fact_id,
        ),
    )
    clusters: list[list[_Episode]] = []
    for episode in ordered:
        if (
            not clusters
            or clusters[-1][0].component != episode.component
            or episode.onset_epoch_s - clusters[-1][0].onset_epoch_s
            > _CLUSTER_ONSET_S
        ):
            clusters.append([episode])
        else:
            clusters[-1].append(episode)
    return tuple(tuple(cluster) for cluster in clusters)


def _candidate_for_cluster(
    row_id: int | str, cluster: tuple[_Episode, ...]
) -> CandidateEvent:
    component = cluster[0].component
    onset = min(episode.onset_epoch_s for episode in cluster)
    reason_scores: dict[str, list[float]] = {}
    for episode in cluster:
        reason_scores.setdefault(episode.reason, []).append(episode.score)

    # Multiple independent signals supporting one reason get a modest bonus,
    # while the underlying magnitude remains the dominant feature.
    aggregated_reason_scores = {
        reason: max(scores) + 0.2 * min(3, len(scores) - 1)
        for reason, scores in reason_scores.items()
    }
    reasons = tuple(
        sorted(
            aggregated_reason_scores,
            key=lambda reason: (
                -aggregated_reason_scores[reason],
                _REASON_ORDER[reason],
            ),
        )
    )
    cluster_facts = tuple(
        fact
        for episode in cluster
        for fact in (episode.fact, *episode.context_facts)
    )
    unique_signals = {fact.signal for fact in cluster_facts}
    unique_sources = {fact.source_file for fact in cluster_facts}
    magnitude = max(episode.score for episode in cluster)
    raw_magnitude = max(episode.raw_score for episode in cluster)
    score = min(
        _MAX_STANDARD_SCORE + 2.0,
        magnitude
        + 0.35 * max(0, len(unique_signals) - 1)
        + 0.15 * max(0, len(unique_sources) - 1),
    )

    ordered_episodes = sorted(
        cluster,
        key=lambda episode: (
            -episode.score,
            episode.signal,
            episode.reason,
            episode.fact.fact_id,
        ),
    )
    fact_ids = tuple(
        dict.fromkeys(
            fact.fact_id
            for episode in ordered_episodes
            for fact in (
                episode.fact,
                *sorted(
                    episode.context_facts,
                    key=lambda fact: (
                        fact.onset_epoch_s,
                        fact.signal,
                        fact.fact_id,
                    ),
                ),
            )
        )
    )
    onset_token = int(onset) if onset.is_integer() else onset
    event_id = stable_event_id(row_id, "metric", component, onset_token)
    is_node = any(episode.source_kind == "node" for episode in cluster)
    return CandidateEvent(
        component=component,
        onset_epoch_s=onset,
        score=round(score, 6),
        reason_candidates=reasons,
        supporting_fact_ids=fact_ids,
        alternatives=reasons[1:],
        modality="metric",
        event_id=event_id,
        feature_scores=(
            ("magnitude", round(magnitude, 6)),
            ("raw_magnitude", round(raw_magnitude, 6)),
            ("cross_signal", float(len(unique_signals))),
            ("episode_count", float(len(cluster))),
            (
                "sustained_minutes",
                float(max(episode.effective_minute_count for episode in cluster)),
            ),
            (
                "post_onset_persistence",
                round(
                    max(episode.post_onset_persistence for episode in cluster),
                    6,
                ),
            ),
            (
                "related_signal_support",
                round(max(episode.related_signal_support for episode in cluster), 6),
            ),
            (
                "isolated_impulse",
                float(any(episode.isolated_impulse for episode in cluster)),
            ),
            ("component_level", 1.0 if is_node else 0.0),
        ),
    )


def detect_metrics(
    prepared_case: Any,
) -> tuple[tuple[CandidateEvent, ...], tuple[EvidenceFact, ...]]:
    """Detect metric-backed candidate events for one prepared case.

    ``prepared_case`` must expose ``.spec`` (a :class:`CaseSpec`) and
    ``.metric_minutes`` (the preparation table).  Empty input is valid and
    deterministically produces two empty tuples.  A non-empty malformed table
    is an integration error and raises ``ValueError`` with the missing columns.
    """

    frame = prepared_case.metric_minutes
    if frame.empty:
        return (), ()
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"metric_minutes missing required columns: {missing}")

    work = frame.loc[:, sorted(REQUIRED_COLUMNS)].copy()
    for column in _NUMERIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    for column in ("source_file", "source_kind", "component", "signal"):
        work[column] = work[column].fillna("").astype(str).str.strip()

    spec = prepared_case.spec
    work = work[
        work["source_kind"].str.lower().isin(("container", "node"))
        & work["source_file"].ne("")
        & work["component"].ne("")
        & work["signal"].ne("")
        & work["minute_epoch_s"].notna()
        & work["minute_epoch_s"].ge(float(spec.start_epoch_s))
        & work["minute_epoch_s"].lt(float(spec.end_epoch_s))
    ]
    if work.empty:
        return (), ()

    group_columns = ["source_file", "source_kind", "component", "signal"]
    work = work.sort_values(group_columns + ["minute_epoch_s"], kind="mergesort")
    episodes: list[_Episode] = []
    series_records: dict[
        tuple[str, str, str, str], list[Mapping[str, Any]]
    ] = {}
    for keys, series in work.groupby(group_columns, sort=True, dropna=False):
        source_file, source_kind, component, signal = (str(value) for value in keys)
        records = series.sort_values("minute_epoch_s", kind="mergesort").to_dict(
            "records"
        )
        series_records[(source_file, source_kind.lower(), component, signal)] = records
        episodes.extend(
            _episodes_for_series(
                row_id=spec.row_id,
                source_file=source_file,
                source_kind=source_kind.lower(),
                component=component,
                signal=signal,
                records=records,
            )
        )

    episodes = list(
        _enrich_direct_io_episodes(
            row_id=spec.row_id,
            episodes=episodes,
            series_records=series_records,
        )
    )

    if not episodes:
        return (), ()

    clusters = _cluster_episodes(episodes)
    candidates = tuple(
        sorted(
            (_candidate_for_cluster(spec.row_id, cluster) for cluster in clusters),
            key=lambda candidate: (
                -candidate.score,
                candidate.onset_epoch_s,
                candidate.component,
                candidate.event_id,
            ),
        )
    )

    # Facts are returned once even when (for example) one CPU row supports both
    # load and spike alternatives.  All candidate references resolve locally.
    facts_by_id = {
        fact.fact_id: fact
        for episode in episodes
        for fact in (episode.fact, *episode.context_facts)
    }
    facts = tuple(
        sorted(
            facts_by_id.values(),
            key=lambda fact: (
                fact.onset_epoch_s,
                fact.component,
                fact.signal,
                fact.fact_id,
            ),
        )
    )
    return candidates, facts


# Descriptive alias for callers that prefer noun-first detector naming.
detect_metric_events = detect_metrics


__all__ = ["detect_metric_events", "detect_metrics"]
