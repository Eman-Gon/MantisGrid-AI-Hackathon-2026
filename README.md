# MantisGrid Hackathon 2026 — Track 1: Root cause analysis

An agent that reads production telemetry from a microservice system and, for each
30-minute incident window, names when the failure started, which component caused
it and why — routing every model call across the GLM family on Featherless to the
cheapest model that can do that call's job.

The original event brief is in [`HACKATHON_BRIEF.md`](HACKATHON_BRIEF.md); the Track 1
docs are under [`track-1/docs/`](track-1/docs/).

## Run it the way the judges do

```bash
docker build -t mantis-rca .
docker run --rm \
  -e FEATHERLESS_API_KEY \
  -v /path/to/Market-cloudbed-1:/data:ro \
  -v /path/to/empty/out:/out \
  mantis-rca \
  python run.py --dataset /data --queries /data/query.csv --out /out
```

Writes `predictions.csv`, `evidence/<row_id>.md` per case and `usage.jsonl` into `/out`.
The key is read from `FEATHERLESS_API_KEY` and the endpoint from `FEATHERLESS_BASE_URL`
(default `https://api.featherless.ai/v1`). Nothing is hard-coded and nothing else is
reached.

## Run it locally

```bash
cd track-1/starter && pip install -r requirements.txt && cd ..
export FEATHERLESS_API_KEY=fw-...
make validate                 # default agent on a one- and a two-failure case; checks shape
make dev N=5 && make score    # first 5 dev cases, scored with the benchmark's evaluator
make cost                     # dollars per case and per model
```

## Layout

```
Dockerfile              the only Dockerfile; builds track-1/starter into /app
track-1/starter/
  run.py                the judged entry point (CLI unchanged from the starter)
  llm.py                Featherless client: retries, fallback, circuit breaker, token counts
  agents/routed.py      the default agent
  agents/heuristic.py   no-model baseline and the always-guess fallback
  rca/                  the pipeline: prepare -> detect -> rank -> route -> verify -> evidence
eval/                   harness, frozen case set, results   (see REPORT.md)
REPORT.md               eval, routed-vs-single-model comparison, failure taxonomy
AGENTS.md               invariants and file ownership for anyone editing this repo
```

## Eval

```bash
python3 eval/run_eval.py --config routed        # agent picks the model per call
python3 eval/run_eval.py --config single-strong # every call on GLM-5.2
python3 eval/run_eval.py --config single-flash  # every call on GLM-4.7-Flash
python3 eval/summarize.py                       # -> eval/results/summary.md
```

Every config runs the same frozen case ids (`eval/cases.json`) through the real
`run.py`. Results and discussion are in [`REPORT.md`](REPORT.md).

## AI disclosure

Models, assistants and frameworks used, and what was AI-generated versus written by
the team, are listed in [`REPORT.md`](REPORT.md#ai-disclosure).

## Team

<!-- TODO: names -->
