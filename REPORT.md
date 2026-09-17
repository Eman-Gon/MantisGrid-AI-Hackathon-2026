# Track 1 report — root cause analysis with per-call model routing

This upstream report describes `agents.routed`, which the `eval/` harness selects
explicitly. The working-tree default is now `agents.rootroute`; its implementation
and separately measured results are documented in [REPORT_ROOTROUTE.md](REPORT_ROOTROUTE.md).
Results for either agent should not be attributed to the other.

## Problem

A microservice shop (OpenRCA `Market/cloudbed-1`) emits metrics, logs and traces.
Given a 30-minute window and a stated number of failures, name the root-cause
component, the reason (one of 15 fixed strings) and the onset time within 60 s.
The published state of the art on this benchmark is about 11% strict. The
telemetry (~12 GB for two days) does not fit in a context window or in the
judged machine's 8 GB, so the model cannot read it; something has to read it
for the model.

## Approach

Deterministic code does the reading and the arithmetic; the model does one
thing — choose among candidates it is shown — and only the cheapest model that
can do that is paid for.

```
run.py ─► prepare()  one streaming pass per CSV per run → per-minute aggregates + exemplar rows
       └► per case:  detect_metrics + detect_traces → CandidateEvent[] + EvidenceFact[]
                     rank_events   → independent onset clusters; replicas coalesced to a service
                     route.decide  → Flash picks; escalate ONCE to GLM-5.2 on an objective rule
                     verify        → hard rules or refill from the deterministic ranking
                     evidence      → the four judged sections, rendered from facts only
```

**Prepare** (`rca/prepare.py`). Each physical CSV is opened once per run, read in
200k-row chunks with explicit dtypes, reduced to one row per (component, signal,
minute) inside every requested window plus a baseline outside it. Trace timestamps
are milliseconds and durations microseconds; the scale is audited against
parent/child span enclosure rather than assumed from a column name. Two days of
metrics and traces prepare in ~95 s at 1.6 GB peak RSS on a laptop; `log_proxy.csv`
(2.9 GB/day) is skipped by design.

**Detect** (`rca/detect_metrics.py`, `rca/detect_traces.py`). Features aligned to the
15 legal reasons rather than one generic z-score: CPU/memory level and spike, disk
and container I/O, process disappearance, network errors/retransmits, and trace
edge latency and error shifts between parent and child spans. Onset is the first
sustained deviation, not the peak. Every detection keeps an `EvidenceFact` with
the source file, a locator (minute, component, signal), the observed value, the
baseline and the method — the only things the evidence file is allowed to print.

**Rank** (`rca/rank.py`). Candidates are clustered by component *and* onset, so a
two-failure window on one component yields two events. Sibling replicas
(`emailservice-0/1/2`) that share a reason family within 90 s are coalesced into
one service-level candidate (`emailservice`) with the pods as alternatives. That
matters: on the dev split, container faults are labelled at service level 14
times and at pod level 8 times, so a per-pod pipeline is wrong on the majority of
them by construction.

**Route** (`rca/route.py`). The model sees the top eight candidates with up to
three facts each — never raw telemetry. One call to `GLM-4.7-Flash` returns strict
JSON: exactly `failure_count` picks by candidate index, a legal reason, the fact
ids it leans on and a confidence. The picks go through `verify`. The agent
escalates to `GLM-5.2` **at most once**, and only when an objective rule fires:

- the cheap output failed hard validation (unparseable, illegal reason, unknown
  component, unresolvable citation, onset outside the window);
- the deterministic margin between the N-th and (N+1)-th candidate is small
  (ratio > 0.8);
- the metric and trace detectors disagree about the leader;
- a chosen candidate has no resolving fact.

The model saying "low confidence" is deliberately *not* an escalation trigger.
Each tier names a fallback model (`GLM-5.3-Flash`, `GLM-5.1`); `llm.py` retries with
backoff, walks the list, and drops a model after two failures for the rest of the
run. A model that never answers leaves the deterministic top events as the answer.

**Thinking is off on both tiers.** GLM's chain of thought counts as output tokens
— the priciest and slowest thing a call does — and on this prompt GLM-5.2 spent
its entire 4,000-token budget thinking without reaching an answer on two of three
canary cases (65–70 s per case against a budget of one minute). With thinking off
the same model answers in ~200 output tokens and 5–10 s. `RCA_THINK=1` re-enables
it on the strong tier as an ablation.

**Verify** (`rca/verify.py`). Every rule maps to a way the evaluator zeroes a
correct diagnosis: wrong count, reason not in the 15, component not seen in
telemetry, cited fact that does not resolve, onset outside the window (±120 s).
A rejected slot is refilled from the deterministic ranking with a warning that
lands in the evidence file. A pod and its service with the same reason count as
one failure. Answers are emitted in onset order.

**Evidence** (`rca/evidence.py`). No model writes a number. `## Answer`,
`## Confidence`, `## Evidence` and `## Ruled out` are rendered from verified
structures: for every cited fact the file, locator, signal, observed vs baseline,
ratio, onset and method. "Ruled out" is claimed only for a candidate that was
scored and lost by a margin; anything closer is "could not be ruled out". When
the runner-up is within 30% of the leader the file says so. Warnings — what was
rejected, what escalated and why — are appended verbatim.

**Always answers.** `run.py`'s exception boundary now returns the no-model
baseline's answer, or exactly `failure_count` placeholder guesses, never a blank.
The count comes from the instruction parser (44 one-failure and 26 two-failure
cases on the dev split), never from a model.

## Eval

Frozen case set `eval/cases.json` → `holdout`: rows 10, 17, 24, 35, 13, 20, 33 —
one per task type, four single- and three two-failure cases, chosen before
tuning and disjoint from the three canary rows (27, 38, 56) used for development.
Every configuration runs the same ids through the real `run.py` with the same
prompts and the same detector output; only the model choice differs:

| config | what `route.decide` does |
|---|---|
| `routed` | Flash first; escalate once to GLM-5.2 on the rules above |
| `single-strong` | every call pinned to `zai-org/GLM-5.2` (`RCA_MODEL`) |
| `single-flash` | every call pinned to `zai-org/GLM-4.7-Flash` |

Scored with the benchmark's own `evaluate.py` (`score.py`, unchanged); priced at
the table in `docs/models.md` (`cost.py`); wall time per case from `usage.jsonl`.
Two repeats per configuration.

### Results (final pipeline)

<!-- RESULTS_V3 -->

### Earlier iterations, kept for honesty

`eval/results_v1/` is the same harness before replica coalescing existed:

| config | runs | mean score | $/case | s/case |
|---|---|---|---|---|
| routed | 2 | 0.536 ± 0.051 | 0.0024 | 11.0 |
| single-strong | 2 | 0.429 ± 0.000 | 0.0033 | 8.8 |
| single-flash | 1 | 0.429 | 0.0002 | 7.8 |

Here routing beat both single models while costing 27% less than all-strong, and
the per-case detail shows why: on row 24 (`node-3`) Flash was right and GLM-5.2
wrong on both repeats; on row 20 (`shippingservice2-0`) GLM-5.2 was right and
Flash wrong. The routed agent got both. Escalation fired on five of seven cases;
the two it kept on Flash cost $0.0002 each.

`eval/results_v2/` is an intermediate version that inserted a service-level
candidate *ahead of* its replicas unconditionally; it scored worse (routed 0.39,
single-strong 0.50) and was replaced by the coalescing in `rank.py`. We report it
because it is a real negative result: naming the service is right more often than
not, but forcing it first costs the cases where one pod genuinely moved alone.

### Reading the numbers

- **Seven cases is small.** One case is 0.14 of the mean; run-to-run sd is
  0.05–0.10 at temperature 0 (Featherless sampling is not deterministic). The
  organisers treat a one- or two-case difference as a tie, and so do we.
- **Cost is not the constraint here.** Every configuration is under $0.004 a
  case — the pipeline sends ~2k input tokens and asks for ~200 output tokens per
  call. All-Flash is 12× cheaper than routed and only slightly less accurate;
  that is a defensible point on the curve too. The judged run of 20 cases costs
  well under $0.10 and finishes in about 6 minutes, of which ~95 s is preparation.
- **Time is dominated by preparation, not by models.** A judged run at 2 CPUs
  will be slower to prepare than our laptop; we budget 3 minutes and still
  finish inside 20.
- **Dev-split numbers are optimistic.** Judging is on a different deployment of
  the same shop; component names will differ and we have never seen its cases.

### Where it fails

From the per-case `failed` lists in `eval/results*/`:

| failure class | cases | why |
|---|---|---|
| **Network packet faults** (retransmission / corruption / loss) | 17, 35, 33 | 9 of 55 dev reasons; the detectors can emit these reasons but rarely do — the container `network_*_errors` / `packets_dropped` and node `tcp.retrans` signals are read but their mapping to the three packet reasons is weak. The trace detector only ever says "network latency". This is the single largest gap. |
| **Upstream cause hidden behind downstream victims** | 27, 13 | Truth is `adservice` / `currencyservice-0`; the ranking is full of cartservice, checkout and frontend latency — the callers. Topology weighting toward the upstream-most anomalous service is bounded and not decisive. |
| **Read vs write I/O on nodes** | 38 | `node-1` and the onset are right; `system.io.r_s` mapped to disk *read* where the truth is *write*. |
| **Second failure's time** | 10 (one repeat) | First event right, second event's onset off by more than 60 s. |

Task-type view: tasks 1 and 3 (time only, component only) are reliably solved;
task 7 (all three fields) is not solved on the holdout by any configuration.

### Knowing when it doesn't know

The evidence file carries the model's confidence and a mechanical caveat when the
runner-up is within 30% of the leader or only one modality supports the answer.
On the holdout, every wrong answer had at least one of those caveats; every
fully-solved case had a confidence ≥ 0.85 and an uncontested leader. We did not
have time to turn that into an abstention curve; the data to do so is in
`eval/results/*/run*/evidence/`.

### Cuts made (from the plan's cut order)

1. `log_proxy.csv` skipped — 2.9 GB/day, and metrics + traces already exceed the
   time budget for reading.
2. All log detection dropped — `log_service.csv` is not read either.
3. Thinking disabled on the strong tier (see above); kept as an ablation flag.
4. Abstention analysis not done; confidence and caveats are recorded per case.

### Not cut

Root Dockerfile and exact judge entry point; multi-failure parsing and exact
count; trace-based network detection; deterministic evidence; always-guess
fallback; routed-vs-single comparison with repeats; secret sweep and clean-clone
proof.

## Reproduce

```bash
cd track-1/starter && pip install -r requirements.txt && cd ..
export FEATHERLESS_API_KEY=...
make validate                            # default agent, rows 0 and 27
python3 ../eval/run_eval.py --config routed --repeat 2
python3 ../eval/run_eval.py --config single-strong --repeat 2
python3 ../eval/run_eval.py --config single-flash --repeat 2
python3 ../eval/summarize.py             # -> eval/results/summary.md
```

## AI disclosure

- **Models called at runtime:** `zai-org/GLM-4.7-Flash` (cheap tier, fallback
  `zai-org/GLM-5.3-Flash`) and `zai-org/GLM-5.2` (strong tier, fallback
  `zai-org/GLM-5.1`) on Featherless. No other provider, no other family.
- **Coding assistants:** Claude Code (Claude Opus 5) was used throughout, by both
  team members, as a pair programmer. It generated most of the first drafts of
  `rca/route.py`, `rca/verify.py`, `rca/evidence.py`, the `eval/` harness, the
  `run.py` hardening, the root `Dockerfile`, and this report's structure. The
  Lane A modules (`rca/contracts.py`, `rca/prepare.py`, `rca/detect_*.py`,
  `rca/rank.py`) and their tests were likewise drafted with assistant help. The
  team designed the pipeline and its contracts, chose the routing policy and the
  escalation rules, decided to disable thinking after measuring it, chose the
  frozen case set, ran and interpreted every eval, and reviewed every module.
- **Reused as-is from the organisers' starter:** `run.py`'s CLI and output
  writing, `llm.py` (one patch: read the answer from the reasoning field when
  content is empty), `cost.py`, `score.py`, `agents/heuristic.py` (the fallback).
- **Agent frameworks:** none. Plain Python, pandas, and the OpenAI SDK pointed at
  Featherless.
