# Working on this repo

## Commands

```bash
cd track-1
make validate                       # default agent, rows 0 and 27, output-shape checks
make dev N=5 AGENT=agents.routed    # -> out/dev/
make score && make cost
make docker                         # root image, judge command, 2 cases
python3 ../eval/run_eval.py --config routed --set canary
```

`python3`, never `python`, on the dev machines.

## Invariants — never break these

- One `Dockerfile`, at the repo root. `run.py`'s command line is frozen.
- `run.py` reads only `--dataset`, writes only `--out`, reaches only `FEATHERLESS_BASE_URL`.
- Every case emits exactly as many answer objects as the instruction states. Count comes
  from the instruction parser, never from a model.
- Prediction keys in this order: datetime, component, reason. Use `format_prediction()`.
- Reason strings exactly from the 15 in `docs/data.md`; component names from telemetry.
- Timestamps are UTC+8 in answers. Metrics/logs are seconds, traces milliseconds.
- Always emit a best guess — after a model outage, a parse failure, anything.
- Evidence prose is rendered from verified facts. No model writes numbers.
- The agent never reads `scoring_points`. `run.py` drops the column before `solve()`.
- Per-case budget well under 10 min / $3; per-run under 20 min / $25 / 8 GiB / 2 CPUs.

## Ownership

| Lane | Files |
|---|---|
| A — data & detection | `rca/contracts.py`, `rca/prepare.py`, `rca/detect_*.py`, `rca/rank.py` |
| B — interface & reasoning (integrator) | `Dockerfile`, `run.py`, `agents/routed.py`, `rca/route.py`, `rca/verify.py`, `rca/evidence.py`, `eval/`, `REPORT.md`, `README.md` |

Lane B merges to `main`. Pull before you edit; don't push competing edits to the same file.
