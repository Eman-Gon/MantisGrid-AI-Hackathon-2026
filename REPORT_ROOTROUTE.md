# RootRoute — Track 1 technical report

## Result in one sentence

RootRoute analyzes telemetry locally, stops without an LLM when the evidence is
clear, asks a cheap GLM to resolve ambiguous cases, and permits at most one
strong-tier escalation under measured conditions. Every model choice is restricted
to detector-produced candidates and revalidated by Python before it can become an
answer.

This report separates implemented behavior and measured observations from work that
could not be evaluated before submission. In particular, the paid four-way model
comparison is **pending**. The key was unavailable during the original evaluation;
the subsequent Docker API smoke test below verifies two cheap-mode cases only.

## Docker API verification — September 17, 2026

`make docker MODE=cheap DOCKER_OUT=out/docker-api-final` built the root image and
ran development rows 0 and 1 with 2 CPUs and an 8-GB memory limit, reading telemetry
from the mounted T7 drive. The root `.env` is loaded by the development launcher;
the API key is passed through the container environment and excluded from the image.

Both cases used `zai-org/GLM-4.7-Flash` successfully: one call per case, valid
candidate selections, no fallback, and no validation warnings. Both scored 1.000
with the benchmark evaluator. Total usage was 6,478 input tokens and 302 output
tokens, approximately $0.000542 using the repository's price table. End-to-end
runtime was 85.27 seconds, including 72.34 seconds of shared preparation.

Live testing exposed two integration issues: thinking consumed the bounded output
budget, and the model returned too many answers. Selection calls now disable
thinking and explicitly state the required answer count. The first two diagnostic
runs used local fallback; only the final run above used validated model answers.
The starter suite passes 63 tests. This small smoke test does not establish broad
accuracy or replace the pending four-mode comparison.

## Architecture

The judged entry point is `track-1/starter/run.py`, whose default agent is
`agents.rootroute`. The pipeline is:

1. The runner passes only `row_id`, task type, and instruction into a run-wide
   preparation hook. Development answers are not passed into the agent.
2. `rca/prepare.py` reads the required metric and trace CSVs in chunks, once per
   physical source, and reduces them to per-minute summaries and baselines. This
   bounds memory and avoids rescanning the multi-gigabyte data for every case.
3. `rca/detect_metrics.py` and `rca/detect_traces.py` create typed candidate events
   and addressable evidence facts. Facts retain their source file and locator so the
   evidence report can be checked against raw telemetry.
4. `rca/rank.py` puts detector-specific scores on a comparable scale, fuses matching
   metric/trace evidence, coalesces correlated replicas, and preserves separate
   failure episodes for multi-failure questions.
5. `rca/routing.py` measures ambiguity and chooses deterministic, cheap, or strong
   selection. Models receive a bounded packet of candidate IDs and verified facts,
   never raw telemetry.
6. `rca/validation.py` validates any model selection against the candidate packet and
   telemetry inventory. `rca/evidence_rootroute.py` then writes the explanation directly from
   verified facts; the LLM does not write the evidence report.
7. The runner writes `predictions.csv`, `evidence/<row_id>.md`, `usage.jsonl`, and
   `routing.jsonl`. Predictions are checkpointed after each case.

This is a custom Python agent. It has no runtime MCP dependency and does not use a
third-party agent framework.

## Routing policy

Every mode uses the same deterministic preparation, detectors, ranking, validation,
and fallback. `RCA_ROUTING_MODE` changes only how the final candidate selection is
made.

| Mode | Selection behavior |
|---|---|
| `rules` | Always use the deterministic result. No model call. |
| `cheap` | Ask `zai-org/GLM-4.7-Flash`, with `zai-org/GLM-5.3-Flash` as its availability fallback. |
| `strong` | Ask `zai-org/GLM-5.2`, with `zai-org/GLM-5.1` as its availability fallback. |
| `routed` | Stop deterministically on a clear case. Otherwise call the cheap tier first and escalate to the strong tier at most once. This is the default. |

Provider retry and within-tier availability fallback are handled by `llm.py`, with a
45-second request timeout, no hidden SDK retries, a bounded retry loop, and a
run-wide circuit breaker for repeatedly unavailable models.

### Initial ambiguity gate

The support score is a bounded transform of the ranker's comparable evidence score:

```text
support = 1 - exp(-comparable_score / 5)
```

For a question requesting `k` failures, the boundary margin compares the last
selected candidate with the first candidate outside the answer:

```text
margin = (score[k - 1] - score[k]) / score[k - 1]
```

In routed mode, a case is not considered deterministically clear when any of these
conditions holds:

- selected support is below `0.75`;
- the relative boundary margin is below `0.10`;
- a selected fact is missing;
- the strongest metric and trace candidates disagree within three minutes and both
  have at least `0.60` support;
- a trace-derived network edge has two verified endpoint choices;
- required failure events overlap in time and reason family;
- selected trace evidence has incomplete topology;
- a selected event supports multiple legal reasons; or
- fewer candidates exist than the required failure count.

### Strong-tier escalation gate

After the cheap result, routed mode makes at most one strong-tier decision call. The
strong gate opens only when at least one of these conditions holds:

- the relative boundary margin is below `0.05`;
- evidence coverage is incomplete;
- metrics and traces select different components;
- trace evidence cannot distinguish the network endpoints;
- multiple requested failures overlap;
- too few candidates exist; or
- the cheap response is unavailable or fails validation.

Incomplete trace topology by itself sends an unclear case to Flash but does not force
the strong tier. When edge resolution is incomplete, deterministic fallback may
prefer a direct metric event only if it has a strict resource fingerprint: at least
two agreeing signals, at least two sustained minutes, no isolated impulse, and—for
I/O—at least one related supporting signal.

## Validation and fallback

A model is a selector, not a source of observations. A response is rejected if it
has invalid JSON or any of the following problems:

- the answer count differs from the failure count in the query;
- an event ID was not offered or is selected twice;
- a component does not exist in telemetry or is not a verified endpoint for that
  event;
- a reason is outside the legal enumeration, unsupported by the event, or mismatched
  with node/container level;
- a timestamp is non-finite, outside the query window, or more than 60 seconds from
  the detector onset;
- a fact list is empty, contains an unknown fact, or cites a fact that does not
  support the selected event; or
- confidence is outside `[0, 1]`.

Validated timestamps are replaced with the detector's exact onset. If the cheap tier
is invalid, routed mode may make its single strong escalation. If the chosen tier is
unavailable or invalid, RootRoute returns the deterministic answer. If the detector
pipeline itself fails, an emergency path still emits the required number of legal
best guesses and records very low confidence. This preserves the benchmark's
exact-count requirement without presenting unsupported output as evidence.

## Verified end-to-end results

The official two-case submission validator was run through the actual `run.py`
interface in routed mode without an API key. It completed with zero shape warnings.

| Case | Task | Result | Strict score | Model calls | Tokens | Solve time | Shared preparation allocation |
|---|---|---|---:|---:|---:|---:|---:|
| row 0 | `task_6` | `shippingservice-1` / `container read I/O load` | **1.000** | **0** | **0** | 1.37 s | 76.73 s |
| row 1 | `task_1` | `2022-03-20 09:55:00` | **1.000** | **0** | **0** | 1.57 s | 76.73 s |

The benchmark scorer reported two fully solved cases out of two. For row 0, the
routing diagnostic recorded 33 candidates, 87 facts, support `0.957943`, relative
margin `0.191093`, and 100% evidence coverage. Both cases recorded the
`deterministic-no-model` route because no key was present; therefore these results
demonstrate the local pipeline and fallback, not GLM quality. They incurred no model
cost. The run-wide preparation took approximately 153.46 seconds and is included
exactly once in the end-to-end runtime figures above.

## Real-data canary observations

These canaries were inspected against the development labels to test failure modes.
They were diagnostic checks, not a holdout accuracy estimate.

| Row | Development label | Observed behavior | What it taught us |
|---:|---|---|---|
| 27 | Two network-latency failures: `cartservice` at 08:39:11 and `adservice` at 08:48:49 | The deterministic path found the cartservice event at 08:39 correctly. For the later event it found the correct minute and reason but chose the opposite endpoint, `frontend`, instead of `adservice`. | A slow edge can implicate either endpoint. The current validator exposes an alternative only when an exact joined trace fact proves that parent/child pair and both components exist in telemetry, allowing a model to choose the verified opposite endpoint without inventing one. |
| 38 | `node-6`, node disk write I/O consumption, 03:39:14 | Metric-only ranking put `node-6` first with the correct reason and a 03:39:00 onset, within the evaluator tolerance. The combined ranking was instead crowded by noisy trace-latency candidates such as `cartservice-2`. | Incomplete trace topology should not erase direct, sustained resource evidence. The current deterministic fallback includes the strict multi-signal metric preference described above. This guard is unit-tested, but row 38 was not rerun end to end after the change, so a final-case claim is not made. |
| 56 | Two `emailservice` faults: read I/O at 14:08:21 and write I/O at 14:16:53 | The deterministic top two identified emailservice read I/O at 14:08 and write I/O at 14:17. Both minute onsets fall within the benchmark's 60-second tolerance. | Separate episodes on the same component must remain eligible; deduplicating only by component would destroy a valid two-failure answer. |

## Preparation, runtime, and memory measurements

Measurements below came from local runs on the provided `Market-cloudbed-1`
development bundle. They are not extrapolated judge results.

| Path measured | Wall time | Peak resident memory | Notes |
|---|---:|---:|---|
| One-day metrics + traces preparation | **145–161 s** | **1.67–1.99 GiB** | Approximately 4.21 million metric rows and 9.13 million trace rows were processed; each selected source was scanned once. |
| One-day metrics-only preparation | **32–39 s** | approximately **1.8 GiB** | This is the automatic degraded path if trace-inclusive preparation fails. |

A preparation covering both development dates is projected to take approximately
five minutes from the one-day measurements. That estimate is below the published
20-minute and 8-GiB limits, but it is not a measured full-run result or a guarantee
for a different deployment or host. The trace
audit resolved only **22.8%** of parent/child edges in the measured development data;
that incompleteness is recorded in evidence and directly affects routing confidence.

The solve-time figures above exclude the shared preparation phase, while each
end-to-end `wall_s` includes an equal preparation share. Reporting both is important:
hiding preparation would materially understate runtime.

## Required model comparison: pending

The intended fair experiment uses the same fixed cases for all four configurations:

```bash
make dev MODE=rules
make dev MODE=cheap
make dev MODE=strong
make dev MODE=routed

make score MODE=rules && make cost MODE=rules
make score MODE=cheap && make cost MODE=cheap
make score MODE=strong && make cost MODE=strong
make score MODE=routed && make cost MODE=routed
make compare
```

The comparison must report strict score, partial score, dollars per case, wall time
per case, and failure/fallback rate. This paid comparison was **not run** because
`FEATHERLESS_API_KEY` was absent. Consequently:

- no claim is made that routed mode is more accurate or cheaper than cheap-only or
  strong-only mode;
- no GLM latency, token, or dollar result is reported;
- the only verified model cost is zero for the no-key row 0 run; and
- no event development credit was consumed by that verified run.

## Known limitations

1. **Mesh/retransmission coverage is incomplete.** Container metrics in the current
   preparation do not provide a usable retransmission signal, and mesh metrics are not
   yet ingested by a dedicated detector. The legal reason
   `container network packet retransmission` therefore lacks adequate deterministic
   coverage. Strict validation deliberately prevents a model from inventing it.
2. **Trace topology is sparse.** Only 22.8% of sampled parent/child relations resolved
   in the measured data. Endpoint uncertainty is surfaced rather than hidden, but it
   can still reduce localization accuracy.
3. **The paid comparison is missing.** Without it, the central accuracy/cost benefit
of routing remains a design hypothesis rather than a measured result.
4. **The end-to-end scored sample is small.** Rows 0 and 1 prove the integrated output
   path, not general accuracy. Rows 27, 38, and 56 are transparent diagnostic
   canaries, not an unbiased test set.
5. **Preparation dominates local runtime.** Per-case decisions are fast after caching,
   but the current full trace scan is material. Further column/filter optimization and
   mesh coverage must be evaluated together rather than trading correctness for a
   faster-looking benchmark.

## Reproduction

From `track-1/`:

```bash
# Free local path
make validate MODE=rules
make dev MODE=rules
make score MODE=rules
make cost MODE=rules

# Default routed path; uses deterministic fallback if the key is absent
FEATHERLESS_API_KEY=<key> make dev MODE=routed
make score MODE=routed
make cost MODE=routed
```

The full command and output layout are documented in
`track-1/starter/README.md`. No API key is stored in the repository.

After the upstream merge, `make validate` checks rows 0 and 27 to cover both
single-failure and two-failure output shape. The earlier scored smoke run above
used rows 0 and 1; these are distinct checks.

## Release verification

- The starter suite passes **63 tests**.
- `make validate` passes the official two-case shape check with zero warnings.
- Python compilation and `git diff --check` pass.
- The repository contains exactly one exact-name `Dockerfile`, at its root.
- The Docker image builds and completes the two-case live API check described above.
