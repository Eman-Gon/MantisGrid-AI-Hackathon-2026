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

from dataclasses import dataclass
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
    minute_count: int
    fact: EvidenceFact


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

        if _has(name, "system_io_r_s", "system_io_rkb_s", "disk_read"):
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
        if _has(name, "system_io_w_s", "system_io_wkb_s", "disk_write"):
            return (
                _Profile(
                    "node disk write I/O consumption",
                    mode="event",
                    allow_isolated=True,
                ),
            )
        if "system_io_r_await" in name:
            return (_Profile("node disk read I/O consumption"),)
        if "system_io_w_await" in name:
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
                    minute_count=len(run),
                    fact=fact,
                )
            )
    return tuple(episodes)


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
    unique_signals = {episode.signal for episode in cluster}
    unique_sources = {episode.source_file for episode in cluster}
    magnitude = max(episode.score for episode in cluster)
    score = min(
        _MAX_STANDARD_SCORE + 2.0,
        magnitude
        + 0.35 * max(0, len(unique_signals) - 1)
        + 0.15 * max(0, len(unique_sources) - 1),
    )

    fact_ids = tuple(
        dict.fromkeys(
            episode.fact.fact_id
            for episode in sorted(
                cluster,
                key=lambda episode: (
                    -episode.score,
                    episode.signal,
                    episode.reason,
                    episode.fact.fact_id,
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
            ("cross_signal", float(len(unique_signals))),
            ("episode_count", float(len(cluster))),
            (
                "sustained_minutes",
                float(max(episode.minute_count for episode in cluster)),
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
    for keys, series in work.groupby(group_columns, sort=True, dropna=False):
        source_file, source_kind, component, signal = (str(value) for value in keys)
        records = series.sort_values("minute_epoch_s", kind="mergesort").to_dict(
            "records"
        )
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
    facts_by_id = {episode.fact.fact_id: episode.fact for episode in episodes}
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
