# Track 1 report — root cause analysis with per-call model routing

<!-- Filled incrementally. Sections marked TODO are pending eval results. -->

## Problem

A microservice shop emits metrics, logs and traces. Given a 30-minute window and a
failure count, name the root-cause component, the reason (one of 15) and the onset
time within 60 s. State of the art on this benchmark is ~11% strict.

## Approach

1. **Prepare** — one streaming pass per telemetry file per run, into compact per-minute
   summaries plus exact exemplar rows kept as evidence facts.
2. **Detect** — deterministic detectors aligned to the 15 legal reasons over metrics and
   trace edges; onset by first sustained deviation, not peak.
3. **Rank** — candidates grouped into distinct onset clusters so two-failure windows
   yield two events.
4. **Route** — one Flash-tier call chooses among candidates; escalate once to a strong
   model only when an objective rule fires (small margin, modalities disagree, missing
   evidence coverage, invalid output). Never on self-reported confidence alone.
5. **Verify** — hard checks (count, enum, component exists, citations resolve, time in
   window) or fall back to the deterministic top events.
6. **Evidence** — rendered from verified facts. No model writes a number.

## Eval

Frozen case set: `eval/cases.json` (`holdout`: rows 10, 17, 24, 35, 13, 20, 33 — one per
task type, mixed one- and two-failure). Every config runs the same ids, prompts and
detector output through the real `run.py`. Scored by the benchmark's `evaluate.py`,
priced at the `docs/models.md` table.

### Results

TODO — paste `eval/results/summary.md` after the runs.

| config | cases | mean score | strict | $/case | s/case |
|---|---|---|---|---|---|
| routed | | | | | |
| single-strong (GLM-5.2) | | | | | |
| single-flash (GLM-4.7-Flash) | | | | | |

Variance: TODO — two repeats on the same subset.

### Where it fails

TODO — error taxonomy by task type and reason family (node vs pod, network vs resource).

### Cuts made

TODO — list every cut from the plan's cut order and why.

## What is estimate vs measured

Dev-split numbers are on `Market-cloudbed-1`; judging is on a different deployment of
the same system. Expect our number to be optimistic.

## AI disclosure

- **Models called at runtime:** GLM family on Featherless (`zai-org/GLM-4.7-Flash`,
  `zai-org/GLM-5.3-Flash`, `zai-org/GLM-5.2`, `zai-org/GLM-5.1`).
- **Coding assistants:** Claude Code (Claude Opus 5). Used to scaffold the eval
  harness, the `run.py` hardening, the root Dockerfile and this document's skeleton;
  the team designed the pipeline, chose the routing policy, and wrote/reviewed the
  detectors. TODO: finalise once the day's work is in.
- **Reused as-is from the organisers' starter:** `run.py`'s CLI and output writing,
  `llm.py`, `cost.py`, `score.py`, `agents/heuristic.py`.
- **Agent frameworks:** none; plain Python + the OpenAI SDK against Featherless.
