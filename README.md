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
# Save FEATHERLESS_API_KEY=... in the repository root .env, or export it.
make validate                 # default agent on a one- and a two-failure case; checks shape
make dev N=5 && make score    # first 5 dev cases, scored with the benchmark's evaluator
make cost                     # dollars per case and per model
make docker                   # loads .env and runs two cases in Docker
```

The Make commands load the root `.env` automatically; exported variables take
precedence. Docker receives the key at runtime, and `.env` files are excluded
from the image. Direct `python3 run.py` commands still require an exported key.
Use `make docker MODE=cheap DOCKER_OUT=out/docker-cheap` to exercise model calls
even when the default routed mode can answer a case locally.

## Layout

```
Dockerfile              the only Dockerfile; builds track-1/starter into /app
track-1/starter/
  run.py                the judged entry point (CLI unchanged from the starter)
  llm.py                Featherless client: retries, fallback, circuit breaker, token counts
  agents/rootroute.py   the default agent: deterministic first, then gated GLM selection
  agents/routed.py      upstream agent retained for its existing evaluation harness
  agents/heuristic.py   no-model baseline and the always-guess fallback
  rca/                  the pipeline: prepare -> detect -> rank -> route -> verify -> evidence
eval/                   harness, frozen case set, results   (see REPORT.md)
REPORT.md               upstream agent evaluation and failure taxonomy
REPORT_ROOTROUTE.md     RootRoute implementation, smoke results, and remaining evaluation
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
`run.py` with `agents.routed` explicitly selected. Results and discussion for that
agent are in [`REPORT.md`](REPORT.md).

RootRoute uses its own four-mode comparison. From `track-1/`, run each mode on the
same cases, then compare the saved results:

```bash
make dev MODE=rules
make dev MODE=cheap
make dev MODE=strong
make dev MODE=routed
make compare
```

The RootRoute implementation and its two-case smoke test are documented in
[`REPORT_ROOTROUTE.md`](REPORT_ROOTROUTE.md). Its paid model comparison remains
pending; the upstream agent's model results do not establish RootRoute accuracy.

## AI disclosure

Models, assistants and frameworks used, and what was AI-generated versus written by
the team, are listed in [`REPORT.md`](REPORT.md#ai-disclosure).

OpenAI Codex also assisted with RootRoute's code, tests, integration, and
documentation. Its configured runtime models are `zai-org/GLM-4.7-Flash`,
`zai-org/GLM-5.3-Flash`, `zai-org/GLM-5.2`, and `zai-org/GLM-5.1` on Featherless.
RootRoute's recorded local smoke tests made no model calls. The runtime is custom
Python and has no runtime MCP or third-party agent-framework dependency.

## Team

<!-- TODO: names -->
