"""One-pass, bounded-memory telemetry preparation for all cases in a run.

Physical CSV files are opened at most once.  They are read in chunks even when
their timestamps are unsorted, reduced to per-minute aggregates, and only then
sliced into cases.  The resulting objects contain no development answers and write
runtime metadata only below the caller-provided output directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .contracts import CaseSpec, parse_cases


METRIC_MINUTE_COLUMNS = (
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
)

TRACE_MINUTE_COLUMNS = (
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
)

TRACE_EDGE_COLUMNS = (
    "source_file",
    "parent_component",
    "child_component",
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
)


@dataclass(frozen=True, slots=True)
class TraceDurationAudit:
    raw_unit: str
    seconds_scale: float
    sample_spans: int
    linked_pairs: int
    enclosure_fraction: float
    median_raw_duration: float
    method: str


@dataclass(slots=True)
class PreparedCase:
    spec: CaseSpec
    metric_minutes: pd.DataFrame
    trace_minutes: pd.DataFrame
    trace_edges: pd.DataFrame
    metric_baselines: pd.DataFrame
    trace_baselines: pd.DataFrame
    trace_edge_baselines: pd.DataFrame
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class PreparedRun:
    cases: dict[int | str, PreparedCase]
    telemetry_components: frozenset[str]
    scan_counts: dict[str, int]
    source_rows: dict[str, int]
    duration_audit: TraceDurationAudit | None
    preparation_wall_s: float
    cache_dir: Path
    warnings: tuple[str, ...] = ()

    def for_case(self, row_id: int | str) -> PreparedCase:
        return self.cases[row_id]


_METRIC_SOURCES = (
    ("metric/metric_container.csv", "container", "cmdb_id"),
    ("metric/metric_node.csv", "node", "cmdb_id"),
    ("metric/metric_service.csv", "service", "service"),
)

# Retain only signals capable of distinguishing the legal failure reasons.  The
# raw files contain many invariant spec/JVM/exporter gauges that add rows but no
# diagnostic information.
_CONTAINER_SIGNAL_TERMS = (
    "cpu_cfs_throttled",
    "cpu_load_average",
    "cpu_system",
    "cpu_usage",
    "cpu_user",
    "memory_cache",
    "memory_fail",
    "memory_max_usage",
    "memory_rss",
    "memory_swap",
    "memory_usage",
    "memory_working_set",
    "fs_io_current",
    "fs_io_time",
    "fs_limit",
    "fs_read",
    "fs_sector_read",
    "fs_sector_write",
    "fs_usage",
    "fs_write",
    "network_receive_errors",
    "network_receive_packets_dropped",
    "network_transmit_errors",
    "network_transmit_packets_dropped",
    "container_last_seen",
    "container_start_time",
    "tasks_state.running",
    "tasks_state.stopped",
    "threads",
)
_NODE_SIGNAL_TERMS = (
    "system.cpu",
    "system.load",
    "system.disk",
    "system.fs.inodes",
    "system.io",
    "system.mem",
    "system.swap",
    "system.net.packets_in.error",
    "system.net.packets_out.error",
    "system.net.tcp.retrans",
    "system.process.zombie",
    "system.tcp.retrans",
)


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def _dates_for_cases(cases: Sequence[CaseSpec]) -> tuple[str, ...]:
    dates: set[str] = set()
    epsilon = timedelta(microseconds=1)
    for case in cases:
        current = case.window_utc8[0].date()
        last = (case.window_utc8[1] - epsilon).date()
        while current <= last:
            dates.add(current.strftime("%Y_%m_%d"))
            current += timedelta(days=1)
    return tuple(sorted(dates))


def normalize_component(source_kind: str, raw_component: object) -> str:
    """Normalize only schema-defined wrappers; never learn aliases from answers."""

    value = str(raw_component).strip()
    if not value or value.lower() in {"nan", "none"}:
        return ""
    if source_kind == "container":
        # metric_container is '<node>.<pod>'; the pod itself may contain dashes.
        return value.split(".", 1)[1] if "." in value else value
    if source_kind == "runtime":
        # '<service>.ts:<port>' is an aggregate service runtime, not a pod id.
        return value.split(".ts:", 1)[0]
    if source_kind == "service":
        for suffix in ("-grpc", "-http", "-https"):
            if value.endswith(suffix):
                return value[: -len(suffix)]
    return value


def service_component(component: object) -> str | None:
    """Derive a service name from a pod replica while retaining the exact pod."""

    value = str(component).strip()
    if not value or re.fullmatch(r"node-\d+", value):
        return None
    match = re.fullmatch(r"(.+)-\d+", value)
    return match.group(1) if match and match.group(1) else None


def parse_mesh_components(cmdb_id: object) -> tuple[str, str] | None:
    """Parse the documented mesh source/destination pair without splitting CSV."""

    parts = str(cmdb_id).strip().split(".")
    if len(parts) < 4:
        return None
    pod, direction = parts[0], parts[1]
    if direction == "source":
        return pod, parts[-1]
    if direction == "destination":
        return parts[-2], pod
    return None


def _metric_signal_mask(signals: pd.Series, source_kind: str) -> pd.Series:
    values = signals.astype("string").str.lower()
    if source_kind == "service":
        return pd.Series(True, index=signals.index)
    terms = _CONTAINER_SIGNAL_TERMS if source_kind == "container" else _NODE_SIGNAL_TERMS
    mask = pd.Series(False, index=signals.index)
    for term in terms:
        mask |= values.str.contains(term, regex=False, na=False)
    return mask


def infer_trace_duration_scale(frame: pd.DataFrame) -> TraceDurationAudit:
    """Infer duration units from parent/child timing rather than the column name.

    Trace timestamps are epoch milliseconds.  For each candidate duration scale,
    parent starts should enclose child starts, while the typical parent slack should
    not be three orders of magnitude larger than the observed child offset.  The
    provided Market data selects microseconds (1e-6 seconds per raw unit).
    """

    required = {"timestamp", "span_id", "parent_span", "duration"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trace duration audit missing columns: {sorted(missing)}")
    sample = frame.loc[:, sorted(required)].copy()
    sample["timestamp"] = pd.to_numeric(sample["timestamp"], errors="coerce")
    sample["duration"] = pd.to_numeric(sample["duration"], errors="coerce")
    sample = sample.dropna(subset=["timestamp", "duration", "span_id"])
    sample = sample[sample.duration >= 0]
    if sample.empty:
        raise ValueError("cannot audit trace duration units from an empty sample")

    parents = sample[["span_id", "timestamp", "duration"]].rename(
        columns={
            "span_id": "parent_span",
            "timestamp": "parent_timestamp",
            "duration": "parent_duration",
        }
    )
    linked = sample[["parent_span", "timestamp"]].merge(
        parents, on="parent_span", how="inner"
    )
    linked["offset_s"] = (
        linked["timestamp"] - linked["parent_timestamp"]
    ) / 1000.0
    linked = linked[(linked.offset_s >= -0.001) & linked.parent_duration.gt(0)]
    if len(linked) < 20:
        raise ValueError("too few linked spans to verify trace duration units")

    scored: list[tuple[float, float, float]] = []
    positive_offsets = linked[linked.offset_s > 0.001]
    for scale in (1e-3, 1e-6, 1e-9):  # raw ms, us, ns -> seconds
        parent_seconds = linked.parent_duration * scale
        enclosed = float((linked.offset_s <= parent_seconds + 0.001).mean())
        if positive_offsets.empty:
            slack_penalty = 10.0
        else:
            ratios = (
                positive_offsets.parent_duration * scale
                / positive_offsets.offset_s.clip(lower=0.001)
            )
            ratios = ratios[ratios > 0]
            slack_penalty = abs(math.log10(float(ratios.median()))) if len(ratios) else 10.0
        # Enclosure is load-bearing; among enclosing scales, prefer realistic slack.
        loss = (1.0 - enclosed) * 100.0 + slack_penalty
        scored.append((loss, scale, enclosed))
    _, scale, enclosure = min(scored, key=lambda item: (item[0], item[1]))
    unit = {1e-3: "milliseconds", 1e-6: "microseconds", 1e-9: "nanoseconds"}[scale]
    return TraceDurationAudit(
        raw_unit=unit,
        seconds_scale=scale,
        sample_spans=len(sample),
        linked_pairs=len(linked),
        enclosure_fraction=enclosure,
        median_raw_duration=float(sample.duration.median()),
        method=(
            "selected the scale that encloses parent/child start offsets with "
            "minimum median slack; trace timestamps were compared in milliseconds"
        ),
    )


def _aggregate_metric_chunks(chunks: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not chunks:
        return _empty_frame(METRIC_MINUTE_COLUMNS), pd.DataFrame()
    raw = pd.concat(chunks, ignore_index=True)
    keys = ["source_file", "source_kind", "component", "signal", "minute_epoch_s"]
    minute = raw.groupby(keys, as_index=False, observed=True).agg(
        count=("count", "sum"),
        sum=("sum", "sum"),
        square_sum=("square_sum", "sum"),
        min=("min", "min"),
        max=("max", "max"),
    )
    minute["mean"] = minute["sum"] / minute["count"].clip(lower=1)
    series_keys = ["source_file", "source_kind", "component", "signal"]
    minute.sort_values(series_keys + ["minute_epoch_s"], inplace=True, kind="stable")
    minute["delta"] = minute.groupby(series_keys, observed=True)["mean"].diff()

    baseline = minute.groupby(series_keys, as_index=False, observed=True).agg(
        baseline_count=("count", "sum"),
        # A median location resists incident contamination, while ordinary
        # day-level dispersion avoids MAD=0 on intermittent I/O counters.
        baseline_mean=("mean", "median"),
        baseline_std=("mean", "std"),
        baseline_delta_mean=("delta", "median"),
        baseline_delta_std=("delta", "std"),
        baseline_min=("min", "min"),
        baseline_max=("max", "max"),
    )
    for column in ("baseline_std", "baseline_delta_mean", "baseline_delta_std"):
        baseline[column] = baseline[column].fillna(0.0)
    minute = minute.merge(baseline, on=series_keys, how="left", validate="many_to_one")
    minute = minute.loc[:, METRIC_MINUTE_COLUMNS]
    minute.sort_values("minute_epoch_s", inplace=True, kind="stable")
    minute.reset_index(drop=True, inplace=True)
    return minute, baseline


def _finalize_trace(
    chunks: list[pd.DataFrame],
    *,
    edge: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = TRACE_EDGE_COLUMNS if edge else TRACE_MINUTE_COLUMNS
    if not chunks:
        return _empty_frame(columns), pd.DataFrame()
    raw = pd.concat(chunks, ignore_index=True)
    identity = (
        ["source_file", "parent_component", "child_component", "operation"]
        if edge
        else ["source_file", "component", "operation"]
    )
    keys = identity + ["minute_epoch_s"]
    grouped = raw.groupby(keys, as_index=False, observed=True).agg(
        count=("count", "sum"),
        duration_sum=("duration_sum", "sum"),
        duration_square_sum=("duration_square_sum", "sum"),
        max_duration_s=("max_duration_s", "max"),
        error_count=("error_count", "sum"),
    )
    exemplar_indexes = raw.groupby(keys, observed=True)["max_duration_s"].idxmax()
    exemplars = raw.loc[exemplar_indexes, [*keys, "exemplar_epoch_s"]]
    grouped = grouped.merge(
        exemplars, on=keys, how="left", validate="one_to_one"
    )
    grouped["mean_duration_s"] = grouped.duration_sum / grouped["count"].clip(lower=1)
    grouped["error_rate"] = grouped.error_count / grouped["count"].clip(lower=1)
    baseline = grouped.groupby(identity, as_index=False, observed=True).agg(
        baseline_count=("count", "sum"),
        total_errors=("error_count", "sum"),
        baseline_mean_duration_s=("mean_duration_s", "median"),
        baseline_std_duration_s=("mean_duration_s", "std"),
    )
    baseline["baseline_std_duration_s"] = baseline[
        "baseline_std_duration_s"
    ].fillna(0.0)
    baseline_error = (
        grouped.groupby(identity, as_index=False, observed=True)["error_rate"]
        .median()
        .rename(columns={"error_rate": "baseline_error_rate"})
    )
    baseline = baseline.merge(
        baseline_error, on=identity, how="left", validate="one_to_one"
    )
    grouped = grouped.merge(baseline, on=identity, how="left", validate="many_to_one")
    grouped = grouped.loc[:, columns]
    grouped.sort_values("minute_epoch_s", inplace=True, kind="stable")
    grouped.reset_index(drop=True, inplace=True)
    return grouped, baseline


def _slice_minutes(frame: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    minutes = frame.minute_epoch_s.to_numpy(dtype="float64", copy=False)
    start = int(np.searchsorted(minutes, lo, side="left"))
    stop = int(np.searchsorted(minutes, hi, side="left"))
    return frame.iloc[start:stop].copy()


def _slice_baselines(
    baseline: pd.DataFrame,
    case_minutes: pd.DataFrame,
    identity: Sequence[str],
) -> pd.DataFrame:
    """Keep only series present in a case; never duplicate a day table per case."""

    if baseline.empty or case_minutes.empty:
        return baseline.iloc[0:0].copy()
    present = case_minutes.loc[:, identity].drop_duplicates()
    return baseline.merge(present, on=list(identity), how="inner", validate="one_to_one")


def _read_metrics(
    telemetry: Path,
    dates: Sequence[str],
    *,
    chunk_rows: int,
    scan_counts: dict[str, int],
    source_rows: dict[str, int],
    opened: set[Path],
    warnings: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    chunks: list[pd.DataFrame] = []
    for date in dates:
        for relative, source_kind, component_column in _METRIC_SOURCES:
            path = telemetry / date / relative
            source_file = str(path.relative_to(telemetry.parent))
            if not path.exists():
                warnings.append(f"missing telemetry source: {source_file}")
                continue
            resolved = path.resolve()
            if resolved in opened:
                raise RuntimeError(f"physical telemetry file would be rescanned: {path}")
            opened.add(resolved)
            scan_counts[source_file] = scan_counts.get(source_file, 0) + 1
            row_count = 0

            if source_kind == "service":
                reader = pd.read_csv(
                    path,
                    usecols=["service", "timestamp", "rr", "sr", "mrt", "count"],
                    chunksize=chunk_rows,
                    on_bad_lines="skip",
                )
            else:
                reader = pd.read_csv(
                    path,
                    usecols=["timestamp", "cmdb_id", "kpi_name", "value"],
                    chunksize=chunk_rows,
                    on_bad_lines="skip",
                )
            for frame in reader:
                row_count += len(frame)
                if source_kind == "service":
                    frame = frame.melt(
                        id_vars=["service", "timestamp"],
                        value_vars=["rr", "sr", "mrt", "count"],
                        var_name="signal",
                        value_name="value",
                    ).rename(columns={"service": "raw_component"})
                    frame["signal"] = "service_" + frame.signal.astype(str)
                else:
                    frame = frame.rename(
                        columns={"cmdb_id": "raw_component", "kpi_name": "signal"}
                    )
                    frame = frame[_metric_signal_mask(frame.signal, source_kind)]
                if frame.empty:
                    continue
                frame["timestamp"] = pd.to_numeric(frame.timestamp, errors="coerce")
                frame["value"] = pd.to_numeric(frame.value, errors="coerce")
                frame = frame.dropna(subset=["timestamp", "value", "raw_component", "signal"])
                if frame.empty:
                    continue
                frame["component"] = frame.raw_component.map(
                    lambda value, kind=source_kind: normalize_component(kind, value)
                )
                frame = frame[frame.component.ne("")]
                frame["minute_epoch_s"] = (
                    np.floor(frame.timestamp.astype("float64") / 60.0) * 60.0
                )
                frame["source_file"] = source_file
                frame["source_kind"] = source_kind
                frame["square"] = frame.value.astype("float64") ** 2
                keys = [
                    "source_file",
                    "source_kind",
                    "component",
                    "signal",
                    "minute_epoch_s",
                ]
                reduced = frame.groupby(keys, as_index=False, observed=True).agg(
                    count=("value", "size"),
                    sum=("value", "sum"),
                    square_sum=("square", "sum"),
                    min=("value", "min"),
                    max=("value", "max"),
                )
                chunks.append(reduced)
            source_rows[source_file] = row_count
    return _aggregate_metric_chunks(chunks)


def _read_traces(
    telemetry: Path,
    dates: Sequence[str],
    *,
    chunk_rows: int,
    scan_counts: dict[str, int],
    source_rows: dict[str, int],
    opened: set[Path],
    warnings: list[str],
    parent_cache_rows: int,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    TraceDurationAudit | None,
]:
    trace_chunks: list[pd.DataFrame] = []
    edge_chunks: list[pd.DataFrame] = []
    duration_audit: TraceDurationAudit | None = None
    total_children = 0
    resolved_children = 0

    for date in dates:
        path = telemetry / date / "trace" / "trace_span.csv"
        source_file = str(path.relative_to(telemetry.parent))
        if not path.exists():
            warnings.append(f"missing telemetry source: {source_file}")
            continue
        resolved = path.resolve()
        if resolved in opened:
            raise RuntimeError(f"physical telemetry file would be rescanned: {path}")
        opened.add(resolved)
        scan_counts[source_file] = scan_counts.get(source_file, 0) + 1
        row_count = 0
        parent_cache = pd.DataFrame(columns=["parent_span", "parent_component"])
        pending = pd.DataFrame(
            columns=[
                "parent_span",
                "child_component",
                "operation",
                "minute_epoch_s",
                "duration_s",
                "duration_square",
                "is_error",
                "timestamp_s",
            ]
        )
        reader = pd.read_csv(
            path,
            usecols=[
                "timestamp",
                "cmdb_id",
                "span_id",
                "duration",
                "status_code",
                "operation_name",
                "parent_span",
            ],
            chunksize=chunk_rows,
            dtype={
                "cmdb_id": "string",
                "span_id": "string",
                "status_code": "string",
                "operation_name": "string",
                "parent_span": "string",
            },
            on_bad_lines="skip",
        )
        for frame in reader:
            row_count += len(frame)
            frame["timestamp"] = pd.to_numeric(frame.timestamp, errors="coerce")
            frame["duration"] = pd.to_numeric(frame.duration, errors="coerce")
            frame = frame.dropna(subset=["timestamp", "duration", "cmdb_id", "span_id"])
            if frame.empty:
                continue
            if duration_audit is None:
                duration_audit = infer_trace_duration_scale(frame)
                if duration_audit.raw_unit != "microseconds":
                    warnings.append(
                        "trace duration audit selected " + duration_audit.raw_unit
                    )
            assert duration_audit is not None
            frame["component"] = frame.cmdb_id.map(
                lambda value: normalize_component("trace", value)
            )
            frame = frame[frame.component.ne("")]
            frame["operation"] = frame.operation_name.fillna("<unknown>").astype(str)
            frame["timestamp_s"] = frame.timestamp.astype("float64") / 1000.0
            frame["minute_epoch_s"] = (
                np.floor(frame.timestamp_s / 60.0) * 60.0
            )
            frame["duration_s"] = (
                frame.duration.astype("float64") * duration_audit.seconds_scale
            )
            frame["duration_square"] = frame.duration_s ** 2
            status = frame.status_code.fillna("").astype(str).str.strip().str.lower()
            frame["is_error"] = ~status.isin({"", "0", "0.0", "ok", "none", "nan"})
            frame["source_file"] = source_file

            trace_keys = ["source_file", "component", "operation", "minute_epoch_s"]
            trace_exemplar_indexes = frame.groupby(
                trace_keys, observed=True
            )["duration_s"].idxmax()
            trace_exemplars = frame.loc[
                trace_exemplar_indexes, [*trace_keys, "timestamp_s"]
            ].rename(columns={"timestamp_s": "exemplar_epoch_s"})
            reduced = frame.groupby(trace_keys, as_index=False, observed=True).agg(
                count=("duration_s", "size"),
                duration_sum=("duration_s", "sum"),
                duration_square_sum=("duration_square", "sum"),
                max_duration_s=("duration_s", "max"),
                error_count=("is_error", "sum"),
            )
            reduced = reduced.merge(
                trace_exemplars,
                on=trace_keys,
                how="left",
                validate="one_to_one",
            )
            trace_chunks.append(reduced)

            current_parents = frame[["span_id", "component"]].rename(
                columns={"span_id": "parent_span", "component": "parent_component"}
            )
            lookup = (
                current_parents.copy()
                if parent_cache.empty
                else pd.concat([parent_cache, current_parents], ignore_index=True)
            )
            lookup.drop_duplicates("parent_span", keep="last", inplace=True)
            if len(lookup) > parent_cache_rows:
                lookup = lookup.tail(parent_cache_rows).copy()
            parent_cache = lookup

            current_children = frame[
                frame.parent_span.notna()
                & frame.parent_span.astype(str).str.len().gt(0)
            ][
                [
                    "parent_span",
                    "component",
                    "operation",
                    "minute_epoch_s",
                    "duration_s",
                    "duration_square",
                    "is_error",
                    "timestamp_s",
                ]
            ].rename(columns={"component": "child_component"})
            total_children += len(current_children)
            edge_input = (
                current_children.copy()
                if pending.empty
                else pd.concat([pending, current_children], ignore_index=True)
            )
            if not edge_input.empty:
                joined = edge_input.merge(
                    lookup, on="parent_span", how="left", indicator=True
                )
                matched = joined[joined._merge.eq("both")].copy()
                resolved_children += len(matched)
                if not matched.empty:
                    matched["source_file"] = source_file
                    edge_keys = [
                        "source_file",
                        "parent_component",
                        "child_component",
                        "operation",
                        "minute_epoch_s",
                    ]
                    edge_exemplar_indexes = matched.groupby(
                        edge_keys, observed=True
                    )["duration_s"].idxmax()
                    edge_exemplars = matched.loc[
                        edge_exemplar_indexes, [*edge_keys, "timestamp_s"]
                    ].rename(columns={"timestamp_s": "exemplar_epoch_s"})
                    edge_reduced = matched.groupby(
                        edge_keys, as_index=False, observed=True
                    ).agg(
                            count=("duration_s", "size"),
                            duration_sum=("duration_s", "sum"),
                            duration_square_sum=("duration_square", "sum"),
                            max_duration_s=("duration_s", "max"),
                            error_count=("is_error", "sum"),
                    )
                    edge_chunks.append(
                        edge_reduced.merge(
                            edge_exemplars,
                            on=edge_keys,
                            how="left",
                            validate="one_to_one",
                        )
                    )
                pending = joined[joined._merge.eq("left_only")][pending.columns].copy()
                if len(pending) > parent_cache_rows:
                    pending = pending.tail(parent_cache_rows).copy()
        source_rows[source_file] = row_count
    if total_children:
        resolution = resolved_children / total_children
        if resolution < 0.9:
            warnings.append(
                f"trace parent/child edge resolution was {resolution:.1%}; "
                "parents absent from the source or bounded lookup were omitted"
            )
    trace, trace_baseline = _finalize_trace(trace_chunks, edge=False)
    edges, edge_baseline = _finalize_trace(edge_chunks, edge=True)
    return trace, trace_baseline, edges, edge_baseline, duration_audit


def _manifest(run: PreparedRun) -> dict[str, Any]:
    audit = run.duration_audit
    return {
        "cases": len(run.cases),
        "scan_counts": run.scan_counts,
        "source_rows": run.source_rows,
        "telemetry_components": len(run.telemetry_components),
        "preparation_wall_s": round(run.preparation_wall_s, 3),
        "warnings": list(run.warnings),
        "trace_duration": (
            {
                "raw_unit": audit.raw_unit,
                "seconds_scale": audit.seconds_scale,
                "sample_spans": audit.sample_spans,
                "linked_pairs": audit.linked_pairs,
                "enclosure_fraction": audit.enclosure_fraction,
                "median_raw_duration": audit.median_raw_duration,
                "method": audit.method,
            }
            if audit
            else None
        ),
    }


def prepare_run(
    dataset_dir: str | Path,
    queries: Any,
    out_dir: str | Path,
    *,
    chunk_rows: int = 200_000,
    include_trace: bool = True,
    parent_cache_rows: int = 300_000,
) -> PreparedRun:
    """Prepare every case while scanning each selected physical CSV once."""

    started = time.monotonic()
    dataset = Path(dataset_dir).resolve()
    telemetry = dataset / "telemetry"
    if not telemetry.is_dir():
        raise FileNotFoundError(f"telemetry directory not found: {telemetry}")
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    specs = queries if isinstance(queries, tuple) and all(
        isinstance(case, CaseSpec) for case in queries
    ) else parse_cases(queries)
    specs = tuple(specs)
    dates = _dates_for_cases(specs)
    cache_dir = Path(out_dir).resolve() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    scan_counts: dict[str, int] = {}
    source_rows: dict[str, int] = {}
    opened: set[Path] = set()
    warnings: list[str] = []
    metric, metric_baseline = _read_metrics(
        telemetry,
        dates,
        chunk_rows=chunk_rows,
        scan_counts=scan_counts,
        source_rows=source_rows,
        opened=opened,
        warnings=warnings,
    )
    if include_trace:
        (
            trace,
            trace_baseline,
            edges,
            edge_baseline,
            duration_audit,
        ) = _read_traces(
            telemetry,
            dates,
            chunk_rows=chunk_rows,
            scan_counts=scan_counts,
            source_rows=source_rows,
            opened=opened,
            warnings=warnings,
            parent_cache_rows=parent_cache_rows,
        )
    else:
        trace = _empty_frame(TRACE_MINUTE_COLUMNS)
        edges = _empty_frame(TRACE_EDGE_COLUMNS)
        trace_baseline = pd.DataFrame()
        edge_baseline = pd.DataFrame()
        duration_audit = None

    components: set[str] = set()
    if not metric.empty:
        metric_components = set(
            metric.loc[
                metric.source_kind.isin(["container", "node", "service"]),
                "component",
            ]
            .dropna()
            .astype(str)
        )
        components.update(metric_components)
        components.update(
            derived
            for component in metric_components
            if (derived := service_component(component)) is not None
        )
    if not trace.empty:
        trace_components = set(trace.component.dropna().astype(str))
        components.update(trace_components)
        components.update(
            derived
            for component in trace_components
            if (derived := service_component(component)) is not None
        )

    prepared_cases: dict[int | str, PreparedCase] = {}
    for spec in specs:
        lo, hi = spec.window_epoch_s
        case_metric = _slice_minutes(metric, lo, hi)
        case_trace = _slice_minutes(trace, lo, hi)
        case_edges = _slice_minutes(edges, lo, hi)
        prepared_cases[spec.row_id] = PreparedCase(
            spec=spec,
            metric_minutes=case_metric,
            trace_minutes=case_trace,
            trace_edges=case_edges,
            metric_baselines=_slice_baselines(
                metric_baseline,
                case_metric,
                ("source_file", "source_kind", "component", "signal"),
            ),
            trace_baselines=_slice_baselines(
                trace_baseline,
                case_trace,
                ("source_file", "component", "operation"),
            ),
            trace_edge_baselines=_slice_baselines(
                edge_baseline,
                case_edges,
                (
                    "source_file",
                    "parent_component",
                    "child_component",
                    "operation",
                ),
            ),
        )

    run = PreparedRun(
        cases=prepared_cases,
        telemetry_components=frozenset(sorted(components)),
        scan_counts=scan_counts,
        source_rows=source_rows,
        duration_audit=duration_audit,
        preparation_wall_s=time.monotonic() - started,
        cache_dir=cache_dir,
        warnings=tuple(warnings),
    )
    (cache_dir / "preparation_manifest.json").write_text(
        json.dumps(_manifest(run), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run


# Friendly hook name for run.py integration.
prepare = prepare_run
