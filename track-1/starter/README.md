# Track 1 starter

The submission requirements, a deterministic telemetry pipeline, cost-aware GLM
routing, a free baseline, and a scorer. Read `run.py` — it is the entry point the
judge executes.

```
run.py          the submission requirements. Replace the agent, not this file
score.py        score yourself against the dev split
llm.py          a Featherless client that counts tokens per model
cost.py         turns those token counts into dollars
../../Dockerfile  the single root submission image (nested examples use a suffix)
agents/
  heuristic.py  a baseline with no model in it. Beat this on day one
  routed.py     an example that routes calls across the GLM family
  rootroute.py  the default: local RCA first, then evidence-gated model routing
rca/            RootRoute preparation, detectors, ranking, validation, and evidence
```

## RootRoute quick start

Run these commands from `track-1/`. `routed` is the default mode and writes to
`out/dev/routed/`:

```bash
# Put FEATHERLESS_API_KEY=... in the repository root .env, or export it.
make dev
make score
make cost
```

Make loads the root `.env` without overriding exported variables. `make docker`
passes the key into the container at runtime; `.env` is excluded from the image.
Direct Python commands require the key to be exported in your shell.

To run a smaller smoke test, add `N=2`. To compare every policy on the same query
file, keep a separate output directory for each mode:

```bash
make dev MODE=rules
make dev MODE=cheap
make dev MODE=strong
make dev MODE=routed

make score MODE=rules
make score MODE=cheap
make score MODE=strong
make score MODE=routed

make cost MODE=rules
make cost MODE=cheap
make cost MODE=strong
make cost MODE=routed

make compare   # strict/partial accuracy, cost, end-to-end time, failure rate
```

Those runs write to `out/dev/rules/`, `out/dev/cheap/`, `out/dev/strong/`, and
`out/dev/routed/`. Set `OUT=another/path` if you need a different location. Use
the same fixed query rows for all four modes when reporting accuracy, cost, and
runtime.

The equivalent direct command, when your shell is in `track-1/starter/`, is:

```bash
RCA_ROUTING_MODE=routed python3 run.py \
  --dataset ../data/Market-cloudbed-1 \
  --queries ../data/Market-cloudbed-1/dev/query_dev.csv \
  --out ../out/dev/routed \
  --agent agents.rootroute

python3 score.py --predictions ../out/dev/routed/predictions.csv \
  --queries ../data/Market-cloudbed-1/dev/query_dev.csv
```

Preparation scans the requested telemetry once for the run, so RootRoute is not
the same under-a-minute baseline as `agents.heuristic`. Use `N=2` for quick shape
checks and measure the full run on the target machine.

## How RootRoute works

RootRoute does the expensive data work with regular Python before considering a
model. It parses the requested time windows and failure counts, loads the needed
metrics and traces once, detects anomaly events, joins supporting facts, and ranks
candidate root causes. It then measures objective ambiguity: candidate support,
the relative score margin at the selection boundary, fact coverage, metric/trace
disagreement, overlapping failures, alternative network endpoints, incomplete
trace topology, and multiple legal reasons.

The four modes use that same deterministic candidate set:

- `rules` always returns the validated deterministic result and makes no model
  call.
- `cheap` asks the cheap tier to choose only from the verified candidates. The
  preferred models are `zai-org/GLM-4.7-Flash` and then
  `zai-org/GLM-5.3-Flash` as an availability fallback.
- `strong` asks the strong tier directly. The preferred models are
  `zai-org/GLM-5.2` and then `zai-org/GLM-5.1` as an availability fallback.
- `routed` returns a clear case without a model call. An ambiguous case goes to
  the cheap tier first and escalates to the strong tier at most once.

In `routed` mode, the initial ambiguity gate opens when the top support is below
`0.75`, the relative boundary margin is below `0.10`, evidence is missing, or a
detector conflict such as metric/trace disagreement, endpoint ambiguity,
overlapping events, incomplete topology, or multiple possible reasons exists.
After a cheap selection, strong escalation requires a narrower objective trigger:
a boundary margin below `0.05`, incomplete evidence coverage, metric/trace
disagreement, an unresolved network endpoint, overlapping failures, too few
candidates, or an unavailable/invalid cheap response.

Models never receive raw telemetry files or arbitrary component names. They see a
small packet of event IDs, allowed component and reason choices, fixed onsets, and
detector-produced fact IDs. Deterministic validation rejects malformed JSON,
wrong failure counts, unknown events or components, illegal reasons, changed
timestamps, and unsupported fact IDs. If a tier is unavailable or invalid,
RootRoute uses its deterministic exact-count fallback instead of losing the case.

### No key

`MODE=rules` never needs `FEATHERLESS_API_KEY`. In `cheap`, `strong`, or `routed`
mode, an absent key also degrades safely to the deterministic result, records the
`deterministic-no-model` route, and uses zero model tokens. This is useful for
testing the full telemetry and output pipeline, but it does not measure how the
model-assisted paths perform.

### RootRoute outputs

In addition to the required files, each output directory contains diagnostics:

```text
predictions.csv        final scorer input, written after every case
evidence/<row_id>.md   verified facts, alternatives, confidence, and route decision
usage.jsonl            wall time and per-model token counts for each case
routing.jsonl          ambiguity measurements, chosen tier, validation, and fallback
```

Evidence prose is rendered by Python from verified facts; the model does not write
the report. `routing.jsonl` is the easiest place to prove why a particular case was
free, cheap, escalated, or fell back.

### Current limitation

RootRoute currently detects metric anomalies and trace-latency/topology anomalies,
but mesh-specific retransmission and connection signals are not yet fully ingested
and mapped. In particular, coverage for `container network packet retransmission`
still needs a mesh detector and evaluation. Keep strict validation in place rather
than allowing a model to invent this reason. No RootRoute accuracy claim should be
made until all four modes have been run on the same fixed holdout cases.

## The original free baseline

To run the starter heuristic itself, explicitly select it:

```bash
python3 run.py --dataset ../data/Market-cloudbed-1 \
  --queries ../data/Market-cloudbed-1/dev/query_dev.csv \
  --out ../out/baseline \
  --agent agents.heuristic

python3 score.py --predictions ../out/baseline/predictions.csv \
  --queries ../data/Market-cloudbed-1/dev/query_dev.csv
```

That baseline costs nothing and is the original floor described below.

## What you submit

```
predictions.csv        row_id, prediction
evidence/<row_id>.md   one per case
```

`predictions.csv` goes to OpenRCA's own evaluator, unchanged. `evidence/` is read
by humans and is worth **35% of your grade against accuracy's 20%** — an
explained wrong answer beats a bare right one.

### The key-order trap

The evaluator's regex requires the keys in this order — datetime, component,
reason. Reorder them and it matches nothing and scores zero **silently**:

```json
{"1": {"root cause occurrence datetime": "...",
       "root cause component": "...",
       "root cause reason": "..."}}
```

Use `format_prediction()` in `run.py` and this cannot happen to you. Run
`scripts/validate_submission.py` before you submit; it checks for exactly this.

### Two more rules that cost whole cases

**Get the failure count right.** The instruction tells you how many failures are
in the window. If your JSON has a different number of objects, the case scores
zero however good each answer is.

**Always guess.** Blank and wrong both score zero, so a guess is free upside.
Abstain in your *evidence*, not in your prediction — "I could not separate these
two candidates, here is why" is worth marks. An empty prediction is not.

## Writing an agent

Any module exposing `solve(instruction, dataset_dir, ctx) -> Solution`:

```python
from run import Solution, format_prediction

def solve(instruction, dataset_dir, ctx):
    ...
    return Solution(
        prediction=format_prediction([
            {"datetime": "2022-03-20 09:02:00",
             "component": "adservice-0",
             "reason": "container CPU load"}]),
        evidence="## What I looked at\n...",
        usage=llm.usage)       # tokens per model, from llm.LLM
```

Then `--agent agents.yours` — and when it is your submission, make it `run.py`'s
default `--agent`, because we run `run.py` without the flag. RootRoute is the
current default. `run.py` writes after every case, so a run that dies at case 60
keeps the first 59, and `--resume` picks up where it stopped.

## The older routed example

`agents/routed.py` shows the plumbing, not a good agent. On top of the baseline's
ranking it makes three calls per case: a cheap model reads the question, a strong one
picks the root cause from the ranked candidates, and a cheap one writes the evidence
file. Each tier names a fallback model, so a busy provider does not end the run.
It checks the pick against the data and keeps the baseline's answer if a call fails.

```bash
export FEATHERLESS_API_KEY=<your key>
make dev AGENT=agents.routed OUT=out/dev/legacy-routed
RCA_MODEL=zai-org/GLM-5.2 make dev AGENT=agents.routed OUT=out/dev/legacy-strong
make cost OUT=out/dev/legacy-routed
```

That pair — routed against a single model — is the minimum comparison your eval
needs for that example. RootRoute's four-mode comparison above is more complete.
`run.py` writes the tokens each model used to `<OUT>/usage.jsonl`; `cost.py` prices
them at the table in `docs/models.md`, which is the table we price your judged run
at.

## The baseline, and why it is bad

`agents/heuristic.py` has no model in it. It parses the time window, computes a
robust z-score for every `(component, kpi)` series against the rest of the day,
ranks components by their strongest anomaly, and keyword-matches the winning KPI
to a reason.

On all 70 cases of `Market/cloudbed-1`:

```
mean score   0.073
fully solved 2 / 70
easy  0.083   middle 0.060   hard 0.075
```

**It solves almost nothing outright.** It exists so you know what free looks like. Where
it is obviously weak, and none of this is subtle:

- **It never opens a log or a trace.** Roughly half the signal, untouched.
- **It has no notion of causality.** Twelve components go anomalous together and
  it takes the loudest, which is usually a symptom rather than the cause.
- **The reason is a keyword match**, not an inference.
- **The time is the peak of one series** — when the symptom was largest, not when
  the fault began.

If your agent cannot beat 0.073, the model is not adding value and you want to
know that on day one.

## Scoring honestly

All 70 cases come with answers, and we score you on a different deployment of the
same system. **Tune against all 70 and your number here is optimistic** — hold some
back, or say in your writeup that it's optimistic.

`score.py` breaks results down by difficulty and task type. Report it that way.
An agent that only localises in time looks nothing like one that closes `task_7`,
and a single mean hides the difference.
