"""Deterministic trace anomaly detection over compact prepared summaries.

The preparation layer owns raw-unit handling and span linkage: trace timestamps
arrive here as epoch seconds, raw microsecond durations arrive as seconds, and
``trace_edges`` contains only parent/child pairs established by an exact
``span_id``/``parent_span`` join.  This module deliberately does not infer an
edge from component co-occurrence or apply another timestamp/duration scale.

Detection is episode-oriented.  Consecutive anomalous minutes for a component
form one event whose onset is the first deviating minute, while a later episode
on the same component keeps a distinct stable event id.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .contracts import (
    CandidateEvent,
    EvidenceFact,
    stable_event_id,
    stable_fact_id,
)

# Prepared trace durations are seconds.  The supplied Market traces use raw
# microseconds; prepare.py audits that fact and applies 1e-6 exactly once.
TRACE_DURATION_INPUT_UNIT = "seconds"
_EPISODE_GAP_S = 90.0
_MAX_FACTS_PER_EVENT = 4

_TRACE_COLUMNS = frozenset(
    {
        "source_file",
        "component",
        "operation",
        "minute_epoch_s",
        "count",
        "mean_duration_s",
        "max_duration_s",
        "error_rate",
        "exemplar_epoch_s",
        "baseline_mean_duration_s",
        "baseline_std_duration_s",
        "baseline_error_rate",
    }
)
_EDGE_COLUMNS = frozenset(
    (_TRACE_COLUMNS - {"component"}) | {"parent_component", "child_component"}
)


@dataclass(frozen=True, slots=True)
class _TraceSignal:
    component: str
    onset_epoch_s: float
    kind: str
    strength: float
    operation: str
    source_file: str
    fact: EvidenceFact
    edge: tuple[str, str] | None = None


def _number(value: object) -> float | None:
    """Return a finite float, or ``None`` for absent/invalid prepared data."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object, fallback: str = "<unknown>") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text or fallback


def _rounded(value: float) -> float:
    # Six decimal places retain microsecond-scale prepared durations without
    # emitting binary-float noise in deterministic evidence.
    return round(float(value), 6)


def _exemplar_ms(exemplar_epoch_s: float) -> int:
    """Recover the exact integer millisecond locator retained by preparation."""

    return round(float(exemplar_epoch_s) * 1000.0)


def _latency_shift(
    mean_duration_s: float,
    max_duration_s: float,
    baseline_mean_s: float,
    baseline_std_s: float,
    count: int,
) -> tuple[str, float, float, str] | None:
    """Return observed-field, observed value, strength, and method for a shift."""

    baseline = max(0.0, baseline_mean_s)
    std = max(0.0, baseline_std_s)
    # A proportional floor avoids treating nanosecond jitter on a constant,
    # sub-millisecond operation as a failure while remaining sensitive to the
    # large shifts caused by injected network latency.
    scale = max(std, baseline * 0.10, 1e-4)
    mean_delta = mean_duration_s - baseline
    mean_z = mean_delta / scale
    mean_ratio = mean_duration_s / max(baseline, 1e-6)
    mean_hit = mean_delta > 0 and (
        (mean_z >= 3.0 and mean_delta >= max(5e-4, baseline * 0.35))
        or (mean_ratio >= 3.0 and mean_delta >= 1e-3)
    )

    max_delta = max_duration_s - baseline
    max_z = max_delta / scale
    max_ratio = max_duration_s / max(baseline, 1e-6)
    tail_hit = (
        count >= 5
        and max_delta > 0
        and max_z >= 8.0
        and max_ratio >= 4.0
        and max_delta >= 2e-3
    )
    if not mean_hit and not tail_hit:
        return None

    mean_strength = max(mean_z / 3.0, math.log2(max(mean_ratio, 1.0)) * 1.5)
    tail_strength = max(max_z / 8.0, math.log2(max(max_ratio, 1.0)))
    if tail_hit and (not mean_hit or tail_strength > mean_strength * 1.25):
        observed_field = "max_duration_s"
        observed = max_duration_s
        strength = min(12.0, tail_strength)
        method = (
            "first minute with a trace latency-tail shift; "
            f"max-baseline={max_delta:.6g}s, standardized shift={max_z:.3g}, "
            f"ratio={max_ratio:.3g}"
        )
    else:
        observed_field = "mean_duration_s"
        observed = mean_duration_s
        strength = min(12.0, mean_strength)
        method = (
            "first minute with a trace mean-latency shift; "
            f"mean-baseline={mean_delta:.6g}s, standardized shift={mean_z:.3g}, "
            f"ratio={mean_ratio:.3g}"
        )
    method += (
        "; duration is prepared seconds (raw trace microseconds audited and "
        "multiplied by 1e-6 once at ingest)"
    )
    return observed_field, observed, strength, method


def _error_shift(
    error_rate: float,
    baseline_error_rate: float,
    count: int,
) -> tuple[float, str] | None:
    baseline = min(1.0, max(0.0, baseline_error_rate))
    observed = min(1.0, max(0.0, error_rate))
    delta = observed - baseline
    # A small variance floor keeps a zero-error baseline useful without making
    # one failed span in a tiny group overwhelmingly significant.
    standard_error = math.sqrt(max(baseline * (1.0 - baseline), 0.0025) / count)
    z_score = delta / max(standard_error, 0.01)
    if not (observed >= 0.03 and delta >= 0.02 and (z_score >= 2.5 or observed >= 0.25)):
        return None
    strength = min(12.0, max(delta / 0.05, z_score / 2.5, observed * 5.0))
    method = (
        "first minute with a trace error-rate shift; "
        f"observed-baseline={delta:.6g}, binomial-style standardized shift={z_score:.3g}"
    )
    return strength, method


def _fact_locator(
    *,
    exemplar_epoch_s: float,
    minute_epoch_s: float,
    component: str,
    operation: str,
    edge: tuple[str, str] | None,
) -> str:
    fields = [
        f"timestamp_ms={_exemplar_ms(exemplar_epoch_s)}",
        f"minute_epoch_s={minute_epoch_s:.3f}",
    ]
    if edge is None:
        fields.append(f"component={component}")
    else:
        fields.extend(
            (f"parent_component={edge[0]}", f"child_component={edge[1]}")
        )
    fields.append(f"operation={operation}")
    return ";".join(fields)


def _signals_from_frame(
    frame: pd.DataFrame,
    *,
    row_id: int | str,
    window: tuple[float, float],
    edge: bool,
) -> list[_TraceSignal]:
    if frame.empty:
        return []
    required = _EDGE_COLUMNS if edge else _TRACE_COLUMNS
    missing = required - set(frame.columns)
    if missing:
        label = "trace_edges" if edge else "trace_minutes"
        raise ValueError(f"{label} missing prepared columns: {sorted(missing)}")

    signals: list[_TraceSignal] = []
    lo, hi = window
    for _, row in frame.iterrows():
        minute = _number(row["minute_epoch_s"])
        exemplar = _number(row["exemplar_epoch_s"])
        count_number = _number(row["count"])
        if minute is None or exemplar is None or count_number is None:
            continue
        if not lo <= minute < hi:
            continue
        count = max(1, int(count_number))

        if edge:
            parent = _text(row["parent_component"], "")
            child = _text(row["child_component"], "")
            if not parent or not child:
                continue
            component = child
            edge_pair: tuple[str, str] | None = (parent, child)
        else:
            component = _text(row["component"], "")
            if not component:
                continue
            edge_pair = None
        source_file = _text(row["source_file"])
        operation = _text(row["operation"])
        locator = _fact_locator(
            exemplar_epoch_s=exemplar,
            minute_epoch_s=minute,
            component=component,
            operation=operation,
            edge=edge_pair,
        )

        mean_duration = _number(row["mean_duration_s"])
        max_duration = _number(row["max_duration_s"])
        baseline_mean = _number(row["baseline_mean_duration_s"])
        baseline_std = _number(row["baseline_std_duration_s"])
        if None not in (mean_duration, max_duration, baseline_mean, baseline_std):
            assert mean_duration is not None
            assert max_duration is not None
            assert baseline_mean is not None
            assert baseline_std is not None
            latency = _latency_shift(
                mean_duration,
                max_duration,
                baseline_mean,
                baseline_std,
                count,
            )
            if latency is not None:
                observed_field, observed, strength, method = latency
                prefix = "trace.edge" if edge else "trace"
                if edge:
                    method += (
                        "; topology edge comes from an exact span_id/parent_span "
                        "join during preparation"
                    )
                fact_id = stable_fact_id(
                    row_id,
                    prefix,
                    "latency",
                    source_file,
                    *(edge_pair or (component,)),
                    operation,
                    f"{minute:.6f}",
                    _exemplar_ms(exemplar),
                    observed_field,
                )
                fact = EvidenceFact(
                    fact_id=fact_id,
                    source_file=source_file,
                    locator=locator,
                    component=component,
                    signal=f"{prefix}.{observed_field}:{operation}",
                    onset_epoch_s=minute,
                    observed=_rounded(observed),
                    baseline=_rounded(baseline_mean),
                    method=method,
                )
                signals.append(
                    _TraceSignal(
                        component=component,
                        onset_epoch_s=minute,
                        kind="latency",
                        strength=strength,
                        operation=operation,
                        source_file=source_file,
                        fact=fact,
                        edge=edge_pair,
                    )
                )

        error_rate = _number(row["error_rate"])
        baseline_error = _number(row["baseline_error_rate"])
        if error_rate is not None and baseline_error is not None:
            error = _error_shift(error_rate, baseline_error, count)
            if error is not None:
                strength, method = error
                prefix = "trace.edge" if edge else "trace"
                if edge:
                    method += (
                        "; topology edge comes from an exact span_id/parent_span "
                        "join during preparation"
                    )
                fact_id = stable_fact_id(
                    row_id,
                    prefix,
                    "error",
                    source_file,
                    *(edge_pair or (component,)),
                    operation,
                    f"{minute:.6f}",
                    _exemplar_ms(exemplar),
                )
                fact = EvidenceFact(
                    fact_id=fact_id,
                    source_file=source_file,
                    locator=locator,
                    component=component,
                    signal=f"{prefix}.error_rate:{operation}",
                    onset_epoch_s=minute,
                    observed=_rounded(error_rate),
                    baseline=_rounded(baseline_error),
                    method=method,
                )
                signals.append(
                    _TraceSignal(
                        component=component,
                        onset_epoch_s=minute,
                        kind="error",
                        strength=strength,
                        operation=operation,
                        source_file=source_file,
                        fact=fact,
                        edge=edge_pair,
                    )
                )
    return signals


def _episodes(signals: Iterable[_TraceSignal]) -> list[list[_TraceSignal]]:
    ordered = sorted(
        signals,
        key=lambda item: (
            item.component,
            item.onset_epoch_s,
            item.kind,
            item.operation,
            item.source_file,
            item.fact.fact_id,
        ),
    )
    episodes: list[list[_TraceSignal]] = []
    current: list[_TraceSignal] = []
    last_component = ""
    last_minute = -math.inf
    for signal in ordered:
        if (
            current
            and (
                signal.component != last_component
                or signal.onset_epoch_s - last_minute > _EPISODE_GAP_S
            )
        ):
            episodes.append(current)
            current = []
        current.append(signal)
        last_component = signal.component
        last_minute = signal.onset_epoch_s
    if current:
        episodes.append(current)
    return episodes


def _selected_signals(episode: list[_TraceSignal]) -> list[_TraceSignal]:
    """Keep compact evidence while retaining onset, signal, and topology facts."""

    chosen: list[_TraceSignal] = []
    seen: set[str] = set()

    def add(signal: _TraceSignal) -> None:
        if signal.fact.fact_id not in seen and len(chosen) < _MAX_FACTS_PER_EVENT:
            chosen.append(signal)
            seen.add(signal.fact.fact_id)

    earliest = min(signal.onset_epoch_s for signal in episode)
    onset_signals = [signal for signal in episode if signal.onset_epoch_s == earliest]
    add(max(onset_signals, key=lambda item: (item.strength, item.fact.fact_id)))
    for kind in ("latency", "error"):
        matching = [signal for signal in episode if signal.kind == kind]
        if matching:
            add(max(matching, key=lambda item: (item.strength, item.fact.fact_id)))
    edge_signals = [signal for signal in episode if signal.edge is not None]
    if edge_signals:
        add(max(edge_signals, key=lambda item: (item.strength, item.fact.fact_id)))
    for signal in sorted(
        episode,
        key=lambda item: (-item.strength, item.onset_epoch_s, item.fact.fact_id),
    ):
        add(signal)
    return chosen


def _reason_candidates(episode: list[_TraceSignal]) -> tuple[str, ...]:
    # A generic span status code cannot distinguish packet loss, corruption, or
    # retransmission.  It may corroborate measured latency, but those specific
    # legal reasons must originate from their corresponding metric signals.
    if any(signal.kind == "latency" for signal in episode):
        return ("container network latency",)
    return ()


def _event_from_episode(
    episode: list[_TraceSignal], row_id: int | str
) -> tuple[CandidateEvent, tuple[EvidenceFact, ...]]:
    component = episode[0].component
    onset = min(signal.onset_epoch_s for signal in episode)
    minutes = {signal.onset_epoch_s for signal in episode}
    operations = {signal.operation for signal in episode}
    kinds = {signal.kind for signal in episode}
    edges = {signal.edge for signal in episode if signal.edge is not None}
    strongest = sorted((signal.strength for signal in episode), reverse=True)
    magnitude = strongest[0] + (sum(strongest[1:3]) * 0.15 if len(strongest) > 1 else 0.0)
    cross_signal = max(0.0, float(len(kinds) - 1)) + min(
        2.0, math.log2(max(1, len(operations)))
    )
    topology_support = min(3.0, math.log2(1.0 + len(edges))) if edges else 0.0
    sustained_minutes = float(len(minutes))
    score = (
        magnitude
        + 0.55 * math.log2(1.0 + sustained_minutes)
        + 0.35 * cross_signal
        + 0.50 * topology_support
        + 0.10 * math.log2(1.0 + sum(max(1.0, signal.strength) for signal in episode))
    )
    selected = _selected_signals(episode)
    facts = tuple(signal.fact for signal in selected)
    fact_ids = tuple(fact.fact_id for fact in facts)
    alternatives = tuple(
        sorted(
            {
                signal.edge[0]
                for signal in episode
                if signal.edge is not None and signal.edge[0] != component
            }
        )
    )
    reasons = _reason_candidates(episode)
    event_id = stable_event_id(
        row_id,
        "trace",
        component,
        f"{onset:.6f}",
        reasons[0],
    )
    feature_scores = (
        ("magnitude", round(magnitude, 6)),
        ("cross_signal", round(cross_signal, 6)),
        ("episode_count", 1.0),
        ("sustained_minutes", sustained_minutes),
        ("component_level", 0.0),
        ("topology_support", round(topology_support, 6)),
        ("edge_support", float(len(edges))),
        (
            "latency_shift",
            round(
                max(
                    (signal.strength for signal in episode if signal.kind == "latency"),
                    default=0.0,
                ),
                6,
            ),
        ),
        (
            "error_shift",
            round(
                max(
                    (signal.strength for signal in episode if signal.kind == "error"),
                    default=0.0,
                ),
                6,
            ),
        ),
    )
    return (
        CandidateEvent(
            component=component,
            onset_epoch_s=onset,
            score=round(score, 6),
            reason_candidates=reasons,
            supporting_fact_ids=fact_ids,
            alternatives=alternatives,
            modality="trace",
            event_id=event_id,
            feature_scores=feature_scores,
        ),
        facts,
    )


def detect_traces(
    prepared_case: Any,
) -> tuple[tuple[CandidateEvent, ...], tuple[EvidenceFact, ...]]:
    """Detect trace failure episodes in one :class:`~rca.prepare.PreparedCase`.

    The input is intentionally duck-typed so focused tests and downstream
    integrations can provide a compact PreparedCase-like fixture.  Non-empty
    frames must follow the frozen preparation schema.
    """

    try:
        spec = prepared_case.spec
        trace_minutes = prepared_case.trace_minutes
        trace_edges = prepared_case.trace_edges
        row_id = spec.row_id
        window = (float(spec.start_epoch_s), float(spec.end_epoch_s))
    except AttributeError as exc:
        raise TypeError(
            "detect_traces expects PreparedCase-like spec, trace_minutes, and trace_edges"
        ) from exc
    if not isinstance(trace_minutes, pd.DataFrame) or not isinstance(
        trace_edges, pd.DataFrame
    ):
        raise TypeError("trace_minutes and trace_edges must be pandas DataFrames")

    signals = _signals_from_frame(
        trace_minutes,
        row_id=row_id,
        window=window,
        edge=False,
    )
    signals.extend(
        _signals_from_frame(
            trace_edges,
            row_id=row_id,
            window=window,
            edge=True,
        )
    )

    events: list[CandidateEvent] = []
    facts_by_id: dict[str, EvidenceFact] = {}
    for episode in _episodes(signals):
        if not any(signal.kind == "latency" for signal in episode):
            continue
        event, facts = _event_from_episode(episode, row_id)
        events.append(event)
        for fact in facts:
            facts_by_id[fact.fact_id] = fact
    events.sort(
        key=lambda event: (
            -event.score,
            event.onset_epoch_s,
            event.component,
            event.event_id,
        )
    )
    facts = tuple(facts_by_id[fact_id] for fact_id in sorted(facts_by_id))
    return tuple(events), facts


# Descriptive alias retained for callers that prefer an event-oriented name.
detect_trace_events = detect_traces


__all__ = [
    "TRACE_DURATION_INPUT_UNIT",
    "detect_trace_events",
    "detect_traces",
]
