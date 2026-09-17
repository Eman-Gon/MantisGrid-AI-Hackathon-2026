#!/usr/bin/env python3
"""Measure one-pass preparation wall time and peak RSS on answer-free queries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import sys
import time

import pandas as pd


STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.prepare import prepare_run  # noqa: E402


def _peak_rss_mib() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    return raw / (1024.0 * 1024.0) if sys.platform == "darwin" else raw / 1024.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--row-ids", default="38")
    parser.add_argument("--chunk-rows", type=int, default=200_000)
    parser.add_argument("--no-trace", action="store_true")
    args = parser.parse_args()

    wanted = {int(value) for value in args.row_ids.split(",") if value.strip()}
    queries = pd.read_csv(args.queries)
    queries = queries[queries.row_id.astype(int).isin(wanted)][
        ["row_id", "task_index", "instruction"]
    ]
    if len(queries) != len(wanted):
        raise SystemExit("one or more requested row ids are absent")

    started = time.monotonic()
    prepared = prepare_run(
        args.dataset,
        queries,
        args.out,
        chunk_rows=args.chunk_rows,
        include_trace=not args.no_trace,
    )
    case_sizes = {
        str(row_id): {
            "metric_minutes": len(case.metric_minutes),
            "trace_minutes": len(case.trace_minutes),
            "trace_edges": len(case.trace_edges),
        }
        for row_id, case in prepared.cases.items()
    }
    print(
        json.dumps(
            {
                "row_ids": sorted(wanted),
                "wall_s": round(time.monotonic() - started, 3),
                "peak_rss_mib": round(_peak_rss_mib(), 1),
                "scan_counts": prepared.scan_counts,
                "source_rows": prepared.source_rows,
                "duration_unit": (
                    prepared.duration_audit.raw_unit if prepared.duration_audit else None
                ),
                "case_sizes": case_sizes,
                "warnings": prepared.warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
