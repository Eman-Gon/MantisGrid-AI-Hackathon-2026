#!/usr/bin/env python3
"""Write an answer-free subset of a query file.

    python3 eval/select_cases.py --queries <dev/query_dev.csv> --row-ids 27,38,56 --out out/q.csv
    python3 eval/select_cases.py --queries <dev/query_dev.csv> --set holdout --out out/q.csv

`scoring_points` is always dropped: the agent must never see answers. Only
`run_eval.py`'s scoring step joins back to the dev split.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def ids_for(row_ids: str, set_name: str) -> list[int]:
    if row_ids:
        return [int(x) for x in row_ids.split(",")]
    return json.loads((HERE / "cases.json").read_text())[set_name]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", required=True)
    ap.add_argument("--row-ids", default="")
    ap.add_argument("--set", default="holdout", help="key in eval/cases.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ids = ids_for(a.row_ids, a.set)
    q = pd.read_csv(a.queries)
    q = q[q.row_id.isin(ids)].drop(columns=["scoring_points"], errors="ignore")
    q = q.set_index("row_id").loc[ids].reset_index()      # keep the requested order
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    q.to_csv(a.out, index=False)
    print(f"{len(q)} case(s) -> {a.out}: {ids}")


if __name__ == "__main__":
    main()
