#!/usr/bin/env python3
"""Compare the four RootRoute policies on one identical query set.

Expected layout (the Makefile creates this by default)::

    out/dev/
      rules/{predictions.csv,usage.jsonl,routing.jsonl,cache/...}
      cheap/{predictions.csv,usage.jsonl,routing.jsonl,cache/...}
      strong/{predictions.csv,usage.jsonl,routing.jsonl,cache/...}
      routed/{predictions.csv,usage.jsonl,routing.jsonl,cache/...}

The report is deliberately derived only from generated artifacts.  Missing
preparation or routing diagnostics are shown as ``n/a`` rather than silently
treated as zero time or zero failures.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

TRACK_DIR = Path(__file__).resolve().parents[1]
STARTER_DIR = TRACK_DIR / "starter"
sys.path.insert(0, str(STARTER_DIR))

from cost import dollars
from score import evaluate

MODES = ("rules", "cheap", "strong", "routed")


class ComparisonError(ValueError):
    """An output set cannot support a fair, reproducible comparison."""


@dataclass(frozen=True, slots=True)
class ModeSummary:
    mode: str
    cases: int
    strict_solved: int
    strict_accuracy: float
    partial_accuracy: float
    total_cost_usd: float
    average_cost_usd: float
    total_runtime_s: float | None
    average_runtime_s: float | None
    average_solve_runtime_s: float
    preparation_runtime_s: float | None
    timing_source: str
    diagnostic_failures: int | None
    failure_rate: float | None
    warnings: tuple[str, ...] = ()

    @property
    def mean_accuracy(self) -> float:
        """Backward-friendly name for the mean per-case partial score."""

        return self.partial_accuracy


def _finite_number(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ComparisonError(f"{label} must be numeric, got {value!r}") from exc
    if not math.isfinite(number):
        raise ComparisonError(f"{label} must be finite, got {value!r}")
    return number


def _row_id(value: Any, *, label: str) -> int:
    number = _finite_number(value, label=label)
    integer = int(number)
    if number != integer:
        raise ComparisonError(f"{label} must be an integer, got {value!r}")
    return integer


def _read_jsonl_last(path: Path) -> dict[int, Mapping[str, Any]]:
    """Read append-only run output; the last record for each row wins."""

    if not path.is_file():
        raise ComparisonError(f"missing required file: {path}")
    records: dict[int, Mapping[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ComparisonError(f"invalid JSON in {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ComparisonError(f"{path}:{line_number} must contain a JSON object")
        if "row_id" not in payload:
            raise ComparisonError(f"{path}:{line_number} has no row_id")
        row_id = _row_id(payload["row_id"], label=f"{path}:{line_number} row_id")
        records[row_id] = payload
    return records


def _read_predictions(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise ComparisonError(f"missing required file: {path}")
    frame = pd.read_csv(path)
    missing = {"row_id", "prediction"} - set(frame.columns)
    if missing:
        raise ComparisonError(f"{path} is missing column(s): {sorted(missing)}")
    if frame.empty:
        raise ComparisonError(f"{path} has no predictions")
    frame = frame.loc[:, ["row_id", "prediction"]].copy()
    frame["row_id"] = [
        _row_id(value, label=f"{path} row_id") for value in frame["row_id"]
    ]
    duplicated = sorted(frame.loc[frame.row_id.duplicated(), "row_id"].unique())
    if duplicated:
        raise ComparisonError(f"{path} has duplicate row_id(s): {duplicated}")
    return frame.sort_values("row_id").reset_index(drop=True)


def _read_queries(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise ComparisonError(f"query file does not exist: {path}")
    frame = pd.read_csv(path)
    missing = {"row_id", "scoring_points"} - set(frame.columns)
    if missing:
        raise ComparisonError(
            f"{path} is missing {sorted(missing)}; use a development query CSV "
            "that contains scoring_points"
        )
    frame = frame.copy()
    frame["row_id"] = [
        _row_id(value, label=f"{path} row_id") for value in frame["row_id"]
    ]
    duplicated = sorted(frame.loc[frame.row_id.duplicated(), "row_id"].unique())
    if duplicated:
        raise ComparisonError(f"{path} has duplicate row_id(s): {duplicated}")
    return frame.set_index("row_id", drop=False)


def _preparation_runtime(run_dir: Path) -> float | None:
    path = run_dir / "cache" / "preparation_manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComparisonError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, Mapping) or "preparation_wall_s" not in payload:
        return None
    value = _finite_number(
        payload["preparation_wall_s"], label=f"{path} preparation_wall_s"
    )
    if value < 0:
        raise ComparisonError(f"{path} preparation_wall_s may not be negative")
    return value


def _diagnostic_failed(record: Mapping[str, Any], expected_mode: str) -> bool:
    """Count operational/model failures, not normal deterministic routing."""

    if record.get("error"):
        return True
    if record.get("used_fallback") is True:
        return True
    if record.get("cheap_valid") is False or record.get("strong_valid") is False:
        return True
    if record.get("route") == "emergency-fallback":
        return True
    # A different recorded policy normally means the requested ablation did not
    # run. Emergency records deliberately use the rules policy and are failures.
    policy = record.get("policy")
    return policy is not None and str(policy) != expected_mode


def summarize_mode(
    mode: str,
    run_dir: Path,
    queries: pd.DataFrame,
    expected_row_ids: frozenset[int] | None = None,
) -> tuple[ModeSummary, frozenset[int]]:
    """Summarize one mode and return the exact evaluated row-id set."""

    predictions = _read_predictions(run_dir / "predictions.csv")
    row_ids = frozenset(int(value) for value in predictions.row_id)
    if expected_row_ids is not None and row_ids != expected_row_ids:
        missing = sorted(expected_row_ids - row_ids)
        extra = sorted(row_ids - expected_row_ids)
        raise ComparisonError(
            f"{mode} did not run the same cases as the other modes; "
            f"missing={missing}, extra={extra}"
        )
    unknown = sorted(row_ids - set(queries.index))
    if unknown:
        raise ComparisonError(f"{mode} predictions contain row_id(s) absent from queries: {unknown}")

    scores: list[float] = []
    for row in predictions.itertuples(index=False):
        scoring_points = queries.at[int(row.row_id), "scoring_points"]
        if pd.isna(scoring_points):
            raise ComparisonError(f"query row {row.row_id} has no scoring_points")
        _, _, score = evaluate(str(row.prediction), str(scoring_points))
        scores.append(float(score))

    usage_path = run_dir / "usage.jsonl"
    usage = _read_jsonl_last(usage_path)
    missing_usage = sorted(row_ids - set(usage))
    extra_usage = sorted(set(usage) - row_ids)
    if missing_usage or extra_usage:
        raise ComparisonError(
            f"{usage_path} row ids do not match predictions; "
            f"missing={missing_usage}, extra={extra_usage}"
        )

    costs: list[float] = []
    wall_times: list[float] = []
    solve_times: list[float] = []
    prepare_shares: list[float] = []
    timing_breakdowns: list[bool] = []
    for row_id in sorted(row_ids):
        record = usage[row_id]
        models = record.get("models")
        if not isinstance(models, Mapping):
            raise ComparisonError(f"{usage_path} row {row_id} has no models object")
        for model, counters in models.items():
            if not isinstance(counters, Mapping):
                raise ComparisonError(
                    f"{usage_path} row {row_id} model {model!r} has invalid counters"
                )
            for counter in ("prompt_tokens", "completion_tokens"):
                value = _finite_number(
                    counters.get(counter, 0),
                    label=f"{usage_path} row {row_id} {model} {counter}",
                )
                if value < 0:
                    raise ComparisonError(
                        f"{usage_path} row {row_id} {model} {counter} may not be negative"
                    )
        try:
            costs.append(float(dollars(dict(models))))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ComparisonError(f"cannot price {usage_path} row {row_id}: {exc}") from exc
        if "wall_s" not in record:
            raise ComparisonError(f"{usage_path} row {row_id} has no wall_s")
        wall_s = _finite_number(
            record["wall_s"], label=f"{usage_path} row {row_id} wall_s"
        )
        if wall_s < 0:
            raise ComparisonError(f"{usage_path} row {row_id} wall_s may not be negative")
        wall_times.append(wall_s)

        has_solve = "solve_wall_s" in record
        has_prepare = "prepare_share_s" in record
        if has_solve != has_prepare:
            raise ComparisonError(
                f"{usage_path} row {row_id} must have both solve_wall_s and "
                "prepare_share_s, or neither"
            )
        timing_breakdowns.append(has_solve)
        if has_solve:
            solve_s = _finite_number(
                record["solve_wall_s"],
                label=f"{usage_path} row {row_id} solve_wall_s",
            )
            prepare_share_s = _finite_number(
                record["prepare_share_s"],
                label=f"{usage_path} row {row_id} prepare_share_s",
            )
            if solve_s < 0 or prepare_share_s < 0:
                raise ComparisonError(
                    f"{usage_path} row {row_id} timing values may not be negative"
                )
            # run.py rounds all three fields to two decimals independently.
            if abs(wall_s - solve_s - prepare_share_s) > 0.031:
                raise ComparisonError(
                    f"{usage_path} row {row_id} wall_s does not equal "
                    "solve_wall_s + prepare_share_s"
                )
            solve_times.append(solve_s)
            prepare_shares.append(prepare_share_s)

    warnings: list[str] = []
    if any(timing_breakdowns) and not all(timing_breakdowns):
        raise ComparisonError(
            f"{usage_path} mixes inclusive and legacy timing records"
        )

    if all(timing_breakdowns):
        # Current run.py records wall_s as solve + an equal share of preparation.
        # Summing wall_s therefore measures the full run exactly once.
        preparation_s = sum(prepare_shares)
        solve_total_s = sum(solve_times)
        total_runtime_s = sum(wall_times)
        average_runtime_s = total_runtime_s / len(row_ids)
        timing_source = "usage wall_s (includes preparation share)"
    else:
        # Legacy run.py recorded only solve time in wall_s. Add the shared
        # preparation manifest once; if it is unavailable, do not invent it.
        preparation_s = _preparation_runtime(run_dir)
        solve_total_s = sum(wall_times)
        solve_times = list(wall_times)
        if preparation_s is None:
            total_runtime_s = None
            average_runtime_s = None
            timing_source = "legacy usage (preparation unavailable)"
            warnings.append(
                "legacy usage has no preparation timing; end-to-end runtime is unavailable"
            )
        else:
            total_runtime_s = preparation_s + solve_total_s
            average_runtime_s = total_runtime_s / len(row_ids)
            timing_source = "legacy usage + preparation manifest"

    routing_path = run_dir / "routing.jsonl"
    if routing_path.is_file():
        routing = _read_jsonl_last(routing_path)
        missing_routing = sorted(row_ids - set(routing))
        extra_routing = sorted(set(routing) - row_ids)
        if missing_routing or extra_routing:
            failures = None
            failure_rate = None
            warnings.append(
                "routing.jsonl row ids do not match predictions "
                f"(missing={missing_routing}, extra={extra_routing}); "
                "failure rate is unavailable"
            )
        else:
            failures = sum(
                _diagnostic_failed(routing[row_id], mode) for row_id in row_ids
            )
            failure_rate = failures / len(row_ids)
    else:
        failures = None
        failure_rate = None
        warnings.append("routing.jsonl is missing; failure rate is unavailable")

    strict = sum(score == 1.0 for score in scores)
    summary = ModeSummary(
        mode=mode,
        cases=len(row_ids),
        strict_solved=strict,
        strict_accuracy=strict / len(row_ids),
        partial_accuracy=sum(scores) / len(scores),
        total_cost_usd=sum(costs),
        average_cost_usd=sum(costs) / len(costs),
        total_runtime_s=total_runtime_s,
        average_runtime_s=average_runtime_s,
        average_solve_runtime_s=solve_total_s / len(solve_times),
        preparation_runtime_s=preparation_s,
        timing_source=timing_source,
        diagnostic_failures=failures,
        failure_rate=failure_rate,
        warnings=tuple(warnings),
    )
    return summary, row_ids


def compare_modes(runs_root: Path, query_csv: Path) -> tuple[ModeSummary, ...]:
    queries = _read_queries(query_csv)
    summaries: list[ModeSummary] = []
    expected_row_ids: frozenset[int] | None = None
    for mode in MODES:
        summary, row_ids = summarize_mode(
            mode,
            runs_root / mode,
            queries,
            expected_row_ids,
        )
        if expected_row_ids is None:
            expected_row_ids = row_ids
        summaries.append(summary)
    return tuple(summaries)


def _format_optional(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def print_table(summaries: tuple[ModeSummary, ...]) -> None:
    print(
        f"{'mode':<8} {'cases':>5} {'strict':>15} {'partial':>8} "
        f"{'$/case':>10} {'sec/case':>10} {'failures':>14}"
    )
    for item in summaries:
        strict = f"{item.strict_solved}/{item.cases} ({item.strict_accuracy:.1%})"
        failures = (
            "n/a"
            if item.failure_rate is None or item.diagnostic_failures is None
            else f"{item.diagnostic_failures}/{item.cases} ({item.failure_rate:.1%})"
        )
        print(
            f"{item.mode:<8} {item.cases:>5} {strict:>15} "
            f"{item.partial_accuracy:>8.3f} {item.average_cost_usd:>10.6f} "
            f"{_format_optional(item.average_runtime_s):>10} {failures:>14}"
        )
    for item in summaries:
        for warning in item.warnings:
            print(f"warning [{item.mode}]: {warning}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare rules, cheap, strong, and routed RootRoute outputs."
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        required=True,
        help="directory containing rules/, cheap/, strong/, and routed/",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        required=True,
        help="development query CSV containing scoring_points",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="optional machine-readable copy of the comparison",
    )
    args = parser.parse_args()

    try:
        summaries = compare_modes(args.runs_root.resolve(), args.queries.resolve())
    except ComparisonError as exc:
        parser.error(str(exc))
    print_table(summaries)

    if args.json_out is not None:
        destination = args.json_out.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps([asdict(summary) for summary in summaries], indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\ncomparison JSON -> {destination}")


if __name__ == "__main__":
    main()
