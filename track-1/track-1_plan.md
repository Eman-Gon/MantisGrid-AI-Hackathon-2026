# Track 1 — Revised RCA Agent Execution Plan

**Revised:** 2026-09-17, approximately 11:00 PDT
**Deadline:** repository pushed and submission form completed before **3:00 PM PDT today**
**Goal:** ship a headless, evidence-grounded RCA agent that runs through the judges' exact Docker command, handles one- and two-failure cases, and demonstrates measurable routing value against a single-model control.

This replaces the earlier SP0–SP6 plan. It keeps the useful evidence, budget, fallback, and clean-clone discipline while removing gate overhead and fixing the multi-failure, repeated-I/O, evidence-generation, and late-integration risks.

---

## 1. Source authority and resolved conflicts

When sources disagree, use this order for implementation decisions:

1. `PARTICIPANT_AGREEMENT.md` for event rules.
2. `track-1/docs/submission.md`, `scoring.md`, `models.md`, and `data.md` for the judged interface and technical constraints.
3. Day-of deck and organizer briefings for presentation, emphasis, and logistics.
4. Older event pages only where newer sources are silent.

Corrections to the compiled master reference that affect implementation:

- The provided 70 dev cases are for development and self-scoring. Official `docs/scoring.md` says final accuracy is measured on **20 cases from another deployment that the team does not have**. Do not encode dev answers, component aliases learned from gold, or row-specific rules.
- No exact Track 1 sub-score formula is published in `docs/scoring.md`. Record the published criteria and constraints; do not block waiting for a formula that is not present.
- Network faults **barely show in metrics** and require trace analysis; do not treat the stronger phrase "only show in traces" as a schema invariant.
- The event's "18 of 70" statement may be cited as a keynote reference. The repository's published benchmark table is over 335 cases; do not mix the two denominators in evaluation results.
- The original OpenRCA **code and paper may be studied**, but its dataset must not be downloaded or used.

## 2. Current verified state

- Fork remote: `Eman-Gon/MantisGrid-AI-Hackathon-2026`, default branch `main`.
- `track-1/data` points to `/Volumes/T7/mantis-data`.
- Dataset is present: approximately 11 GiB on disk, two telemetry days, and all 16 expected telemetry CSV files.
- `query_dev.csv` has 70 cases: **44 contain one failure and 26 contain two failures**.
- The current shell has `python3` but no `python`; commands must use `PY=python3` or the Makefile must default to `python3`. `make PY=python3 validate` succeeds against the starter.
- `FEATHERLESS_API_KEY` is not visible in the current process environment. A human must activate the $25 event credit at `https://featherless.ai/join/feather_request_pricing/MANTIS26`, verify code `MANTIS26` is applied, and export the `fw-...` key before live-model tests.
- Docker CLI is installed, but the Docker daemon is not running. A human must start Docker Desktop during Gate A rather than discovering this during release.
- `track-1/.gitignore` is committed and ignores the external `data` symlink.

## 3. Non-negotiable judged behavior

The shipped repository must satisfy all of these:

- Exactly one `Dockerfile`, at repository root.
- Image working directory contains `run.py` and supports:

  ```bash
  python run.py --dataset /data --queries /data/query.csv --out /out
  ```

- Read telemetry only through `--dataset`; write artifacts and runtime caches only through `--out`.
- Read `FEATHERLESS_API_KEY` and optional `FEATHERLESS_BASE_URL` from the environment. No other network dependency at runtime.
- Emit one `predictions.csv` row and one `evidence/<row_id>.md` file per query row.
- Emit exactly the number of failures stated in the instruction. Preserve output-key order: datetime, component, reason.
- Use exact reason strings and telemetry-derived component names. Serialize answer timestamps in UTC+8 with one-minute tolerance in mind.
- Always emit a best guess, including after an LLM, parser, verifier, or capacity failure.
- Enforce internal stop conditions comfortably below the official **10 minutes and $3 per case** limits.
- Stay comfortably below 20 minutes, $25 total, 2 CPUs, and 8 GiB RAM for 20 cases.

## 4. Submission-first repository layout

Create this shape in Gate A, not at the end:

```text
Dockerfile                         # the only Dockerfile
.dockerignore                      # excludes .git, data, out, caches, other tracks
AGENTS.md                          # terse commands, invariants, and file ownership
README.md                          # run instructions and AI/team-work disclosure
REPORT.md                          # final write-up, filled incrementally
eval/                              # harness, fixed case set, raw logs, results
track-1/
  Makefile
  starter/
    run.py                         # exact CLI retained; default agent changed
    llm.py                         # reuse its retries, fallback, breaker, usage
    requirements.txt
    agents/routed.py
    rca/
      contracts.py
      prepare.py
      detect_metrics.py
      detect_traces.py
      detect_logs.py               # optional/cut first
      rank.py
      route.py
      verify.py
      evidence.py
```

The root Dockerfile copies `track-1/starter/` into `/app`. The fork currently contains exact-name Dockerfiles under both tracks. Remove or rename every nested exact-name `Dockerfile` so `find . -type f -name Dockerfile` returns only `./Dockerfile`; update `track-1/Makefile` so its Docker target builds from repository root.

Every gate after Gate A must exercise `run.py`; module-level CLIs are diagnostics, not acceptance tests.

## 5. Frozen contracts

Freeze these at Gate A. Later changes require both lane owners to acknowledge them.

```python
CaseSpec = {
    "row_id",
    "task_type",             # task_1 ... task_7
    "failure_count",         # 1 or 2 in the dev bundle; parse from instruction
    "requested_fields",      # datetime/component/reason
    "window_utc8",           # wall-clock bounds from the instruction
    "window_epoch_s",        # normalized bounds for filtering
}

EvidenceFact = {
    "fact_id",               # stable within the case
    "source_file",
    "locator",               # timestamp + component + signal; span/log id if present
    "component",
    "signal",
    "onset_epoch_s",
    "observed",
    "baseline",
    "method",                # how the comparison was calculated
}

CandidateEvent = {
    "component",
    "onset_epoch_s",
    "score",
    "reason_candidates",
    "supporting_fact_ids",
    "alternatives",
}

Hypothesis = {
    "component",
    "reason_enum",
    "onset_epoch_s",
    "fact_ids",
    "confidence",
    "model_used",
}

CaseResult = {
    "hypotheses",            # list length MUST equal CaseSpec.failure_count
    "usage",
    "warnings",
}
```

Important implications:

- A case produces a **list** of hypotheses, never one case-level hypothesis.
- Two failures may name the same component with different reasons and times.
- Canonical component names come from telemetry parsing rules, not `scoring_points`.
- Internal comparisons use epoch seconds. Query windows and final answer strings are explicitly converted to/from UTC+8 at the boundary.

## 6. Runtime architecture

### 6.1 Parse all queries before telemetry work

Keep the CLI unchanged, but let `run.py` expose the full query frame in `ctx` or call an optional agent `prepare(dataset, queries, ctx)` hook once before its case loop. Preparation must know every requested window so it can avoid rescanning physical files per case.

The query parser must extract and unit-test:

- `row_id`
- task type and requested fields
- one vs two failures
- start/end wall time, including midnight crossing

Output count is taken from the instruction parser, never from an LLM.

### 6.2 One-pass, bounded-memory telemetry preparation

**Invariant: each physical telemetry file is scanned no more than once per run.** Record scan counts and preparation wall time.

The CSVs are not timestamp-sorted: sampled `metric_container` chunks each span essentially the whole day. A per-case pandas or DuckDB predicate would therefore reparse the physical CSV, not seek to a narrow time range. The current starter's one-day `metric_container` plus `metric_node` load is about 4.2 million rows, roughly 0.9 GiB in pandas, and approximately 12 seconds; caching both days before traces/logs already approaches 1.8 GiB. A true streaming batch aggregation is required.

- Read CSVs in chunks with explicit columns and dtypes.
- Normalize trace timestamps from milliseconds and metric/log timestamps from seconds at ingest.
- Do not assume the unit of the trace `duration` column from its name; verify it against the provided files/documentation before converting it.
- Build compact per-case/per-minute aggregates and retain a few exact exemplar rows as `EvidenceFact`s. Do not retain the union of raw rows in memory.
- Maintain compact day-level or outside-window baseline summaries for the same component/signal. A 30-minute incident window without a control baseline is insufficient for anomaly scoring.
- Cache only under `ctx["out_dir"] / "cache"` if disk materialization is needed.
- Start with container/node/service metrics and trace spans. Skip `log_proxy.csv` initially; it is the first data source cut if preparation is slow.
- Materialize useful metric and trace summaries progressively before optional logs, so an external timeout does not leave every case unanswered.
- Preserve trace parent/child information needed for edge latency/error summaries and topology scoring.
- Treat `metric_mesh.cmdb_id` as a source/destination pair and parse it with a CSV reader; never split raw lines on commas.

Target for the judged-sized run: preparation plus 20 cases under 16 minutes and peak RSS under 6 GiB, leaving at least 20% margin.

### 6.3 Reason-aware deterministic detection

Generate features aligned to the 15 legal reasons rather than a single generic rolling z-score:

- CPU/memory level and spike changes
- disk read/write/space changes
- container read/write I/O changes
- process disappearance/termination evidence
- network latency, retransmission, corruption, and loss indicators
- trace edge latency/error shifts and affected parent/child components

Estimate onset with change-point/first-sustained-deviation logic; do not substitute peak time for start time. Group candidates into distinct onset clusters so two-failure cases can produce two events. Rank with deterministic features: onset, magnitude, cross-signal support, component level, and upstream/topology support.

### 6.4 Minimal routed LLM decision

The LLM sees only compact candidates and verified `EvidenceFact`s, never raw telemetry.

1. A Flash-tier call returns strict JSON containing exactly `failure_count` selections, legal reason enums, fact IDs, and calibrated confidence.
2. Escalate at most once when an objective rule fires:
   - top candidate margin is small;
   - modalities disagree;
   - a network edge has ambiguous endpoints;
   - required fields lack evidence coverage; or
   - the cheap result fails hard validation.
3. Do **not** escalate solely because the model self-reports low confidence.
4. If every model is unavailable or output remains invalid, use the deterministic top event(s).

Use model preference lists with `starter/llm.py` so capacity failures fall through. Merge usage for every successful call and record all attempted tiers.

The `run.py` exception boundary must also parse the instruction and emit exactly `failure_count` deterministic guesses. The starter's current empty prediction on an uncaught exception is not an acceptable final fallback.

### 6.5 Deterministic evidence rendering

Do not ask an LLM to write factual evidence prose. Render the four required sections from verified structures:

- `## Answer`: one line per predicted failure.
- `## Confidence`: calibrated level plus explicit ambiguity.
- `## Evidence`: source file, concrete locator, signal, observed value, baseline, onset, and calculation method for every cited fact.
- `## Ruled out`: only alternatives for which a real comparison was computed; otherwise say that the alternative could not be ruled out.

No number, timestamp, ordering claim, or negative claim may appear unless it is present in an `EvidenceFact` or mechanically derived from cited facts.

### 6.6 Hard validation vs soft causal checks

Hard failures, corrected or sent to fallback:

- hypothesis count differs from `failure_count`;
- illegal reason enum;
- component absent from telemetry-derived candidate names;
- citation/fact ID does not resolve;
- predicted time falls outside the query window;
- required prediction keys do not match task/output formatting rules.

Soft signals that affect confidence/ranking but do not universally reject:

- root cause appears downstream in the inferred trace graph;
- cause onset appears slightly after a symptom;
- only one telemetry modality supports the claim.

Sampling jitter, independent concurrent failures, node faults, and process termination make those relationships non-universal. Apply explicit tolerances and report ambiguity.

## 7. Canary and evaluation policy

### 7.1 Canary cases

Use these only for rapid development:

- Row 27: `task_7`, two network-latency failures — exercises trace analysis and exact count.
- Row 38: `task_7`, one node disk-write failure — exercises node naming and onset.
- Row 56: `task_7`, two I/O failures on the same component — exercises repeated component with distinct events.

Add format-only unit cases for tasks 1–6 so omitted/requested fields and key order are tested without additional model spend. Debug against no more than seven additional dev rows before the final evaluation set is frozen.

### 7.2 Fair comparison

Before tuning finishes, write the fixed, stratified holdout list `[10, 17, 24, 35, 13, 20, 33]` to `eval/cases.json`. It contains one case from every task type, a mixture of single- and two-failure cases, and is disjoint from the three canaries. Every compared configuration uses the same case IDs, prompt content, output limits, and detector results:

Build the answer-free subset from `query.csv`; only the scoring step may join results to `dev/query_dev.csv`. Production preparation and solving code must ignore or drop `scoring_points` even if a dev frame is supplied.

1. `routed`
2. `single-strong` — all LLM calls pinned to the chosen strong GLM
3. `single-flash` — useful third point if time permits

Report for each:

- case count and exact IDs;
- mean partial score and fully solved/strict rate;
- score by task type;
- total and per-case dollars;
- total, mean, p50, and max wall time;
- dollars per fully solved case where meaningful;
- model/capacity failures;
- a short error taxonomy.

Use at least two repeats for a small identical subset to report cost/time/accuracy variance. If the full comparison cannot be repeated, label the repeated subset and do not imply full-set variance.

If constrained, shrink the **same case set for every configuration**. Never compare routed on one set with strong-only on another. No tuning after the final evaluation begins.

## 8. Gates and wall-clock schedule

There are three human review pauses, not seven. Within a gate, agents may work on disjoint owned files and checkpoint normally; one human integrator owns merges to `main`.

| Phase | Target | Deliverable | Pass condition |
|---|---:|---|---|
| Gate A — runnable release skeleton | 11:25 | root Docker path, real entrypoint/default agent, environment/data proof, frozen multi-failure contracts | `python3` validation passes; Docker daemon is up; exact local judge CLI emits parseable output; no data/key tracked |
| Gate B — end-to-end product | 12:35 | one-pass preparation, metric + trace signal, grouping/ranker, one routed decision, verifier, deterministic evidence | rows 27/38/56 run through real `run.py`; forced bad citation and model outage still emit guesses; projected runtime <45 s/case |
| Gate C — harden and freeze | 1:20 | correctness/performance repairs plus working-tree Docker smoke | two real cases pass through the default agent and root image; features freeze at 1:20 |
| Evaluation | 1:20–1:55 | fixed-case routed vs single-strong results and draft REPORT | same cases and two fresh repeats; strict/partial, cost, time, and variance reported honestly |
| Release | 1:55–2:35 | release-candidate push, clean-clone proof, README/report, final repair push | exact judge command succeeds; artifacts inspected; secret/data sweep clean |
| Form + demo | form by 2:15; done by 2:50 | form receipt and timed four-minute rehearsal | form accepted; final `main` pushed by 2:35; emergency-only after 2:50 |

Prefill the form and start the two-slide deck during Gate A. The form can be submitted once its repository and deck links exist because judges clone the latest default branch when they evaluate; do not risk waiting until 2:55.

Submission form: `https://forms.gle/UbPSwZhKNfkovM8s5`. Prepare every member's student/career status, Track 1, title, short description, public repository link, presentation link, and demo link if used.

At each review pause, record exact commands, elapsed time, peak RSS, output paths, and known failures. A review should take no more than five minutes. If no custom end-to-end path works by 12:20, stop architecture expansion and ship an improved starter routed agent with correct multi-failure output and deterministic evidence.

## 9. Required acceptance commands

Use `python3` consistently on this machine.

### Gate A

```bash
cd track-1
make PY=python3 validate
python3 -c "import os; k=os.environ.get('FEATHERLESS_API_KEY',''); assert k.startswith('fw-'), 'export the Featherless fw- key'"
docker info >/dev/null
make PY=python3 dev AGENT=agents.routed N=2
make PY=python3 cost
make PY=python3 score
```

The Makefile validator currently supplies an agent explicitly, so it does not prove the shipped default. Also run `run.py` without `--agent` after changing its default, and update validation to include both a single- and a two-failure case. Inspect prediction counts, parseability, all four evidence headings, and the actual files—not only command exit codes.

### Gates B/C

```bash
cd track-1/starter
python3 ../../eval/select_cases.py \
  --queries ../data/Market-cloudbed-1/dev/query_dev.csv \
  --row-ids 27,38,56 \
  --out ../../out/canary-queries.csv
python3 run.py \
  --dataset ../data/Market-cloudbed-1 \
  --queries ../../out/canary-queries.csv \
  --out ../../out/canary \
  --agent agents.routed
```

The canary harness may select rows 27, 38, and 56, but acceptance must still enter through `run.py`. Assert emitted object counts `2, 1, 2`, evidence fact resolution, UTC+8 serialization, and fallback behavior.

### Evaluation

```bash
python3 eval/run_eval.py --config routed --cases-file eval/cases.json
python3 eval/run_eval.py --config single-strong --cases-file eval/cases.json
python3 eval/run_eval.py --config single-flash --cases-file eval/cases.json
python3 eval/summarize.py
```

If the third configuration is cut, the required comparison remains routed vs single-strong.

### Release: exact clean-clone proof

```bash
judge_dir="$(mktemp -d)"
out_dir="$(mktemp -d)"
git clone https://github.com/Eman-Gon/MantisGrid-AI-Hackathon-2026.git "$judge_dir/repo"
cd "$judge_dir/repo"
docker build -t mantis-rca .
docker run --rm --cpus=2 --memory=8g \
  -e FEATHERLESS_API_KEY \
  -e FEATHERLESS_BASE_URL \
  -v /Volumes/T7/mantis-data/Market-cloudbed-1:/data:ro \
  -v "$out_dir":/out \
  mantis-rca \
  python run.py --dataset /data --queries /data/query.csv --out /out
```

Then inspect `predictions.csv`, multiple evidence files, `usage.jsonl`, total wall time, and logs. Confirm the container created no required state outside `/out`.

Secret/data checks must cover tracked and untracked repository files while excluding telemetry and `.git`; `git grep` alone is insufficient for untracked files. Never print a discovered secret in logs.

## 10. Revised cut order

Apply top-down and record every cut in `REPORT.md`:

1. Skip `log_proxy.csv`.
2. Drop all log detection; retain metrics and trace summaries.
3. Drop the optional `single-flash` third configuration; retain two fresh repeats of the mandatory pair.
4. Disable expensive escalation and ship Flash plus deterministic fallback.
5. Simplify topology scoring, but retain basic trace latency/error features.
6. Shrink the fixed evaluation set equally from seven to four cases for every configuration; keep both configurations and both repeats.

Never cut:

- exact judge entrypoint and root Dockerfile;
- multi-failure parsing and exact output count;
- at least basic trace handling for network faults;
- deterministic, data-grounded evidence files;
- always-guess fallback;
- routed-vs-one-model comparison;
- secret sweep, clean-clone proof, public push, or form submission.

## 11. Parallel ownership

- **Person A / Lane A — data and deterministic analysis:** `contracts.py`, `prepare.py`, detectors, ranker, and resource benchmarks.
- **Person B / Lane B — judged interface and reasoning:** root Dockerfile, `run.py` integration, route, verifier, evidence renderer, and eval harness.
- **Person B is the default integrator** (not a third person): owns plan/REPORT/README, contract integration, merges, clean-clone proof, final push, and form. Person A takes this role instead if the team explicitly swaps it.

Both people work concurrently on their owned files. At Gates A, B, and C, each performs a five-minute cross-review of the other lane. Do not have both people push competing edits directly to `main`; Person B integrates to `main` by default. The progress ledger records the reviewed implementation hash; a separate "review commit" is unnecessary when review makes no code change.

## 12. Final presentation checklist

Prepare no more than two slides and no more than two presenters, per the day-of briefing:

1. Problem, architecture, and why deterministic evidence matters.
2. Live case plus the routed-vs-single table: partial/strict accuracy, dollars, and seconds.

The report and slides must make the judging story explicit: problem/impact, differentiated approach, substantive technical execution, why AI and routing are central, and measurable demo results. Clearly distinguish reused starter/open-source work from what the team built today.

Four-minute flow:

- 0:00–0:35 — downstream symptoms vs root cause problem.
- 0:35–1:35 — run one cached/fast case live.
- 1:35–2:35 — open its evidence file and show what was verified and ruled out.
- 2:35–3:25 — show the fair comparison and routing decision.
- 3:25–4:00 — honest failure mode, impact, and close.

Working and reproducible beats feature breadth. Do not start Track 2 work from this plan.

## 13. Timeline

This is the quick visual record of the team's current position. Keep exactly one
**← CURRENT** marker while work remains. Move it only after the current row's pass
condition has been met and its output has been inspected. When a row finishes, change
`[ ]` to `[x]` and record the actual start and finish times in PDT. Use `[-]` for a
deliberately cut phase and explain the cut in `REPORT.md`.

**Current position:** Gate A — release integration is still open; Gate B's
deterministic-analysis lane is progressing in parallel.

| Done | Phase | Target | Started (PDT) | Finished (PDT) | Status / evidence / commit |
|---|---|---:|---|---|---|
| [x] | Plan revision | 11:00 AM | 10:45 AM | ~11:00 AM | Complete; this document incorporates the repository audit and official Track 1 docs |
| [ ] | Gate A — runnable release skeleton | 11:25 AM | Active by 12:03 PM | — | ← **CURRENT / LATE**; `make PY=python3 validate` passes, but root Dockerfile, routed default, single-Dockerfile layout, Docker daemon, key proof, and exact judge-command proof remain open |
| [ ] | Gate B — end-to-end product | 12:35 PM | Active by 12:03 PM | — | **In progress in parallel:** contracts, one-pass preparation, metric/trace detectors, and ranker are committed through `00dde3d`; 30/30 RCA unit tests pass. Routed integration, verifier, deterministic evidence, real `run.py` canaries, outage fallback, and runtime projection remain open |
| [ ] | Gate C — harden and freeze | 1:20 PM | — | — | Pending Gate B |
| [ ] | Evaluation | 1:55 PM | — | — | Pending Gate C; use the same fixed cases for every configuration |
| [ ] | Release | 2:35 PM | — | — | Pending evaluation; requires the exact clean-clone judge command |
| [ ] | Form submitted | 2:15 PM | — | — | Pending repository and presentation links; do not wait for the release deadline |
| [ ] | Demo rehearsal and final check | 2:50 PM | — | — | Pending; four-minute timed rehearsal and final `main` verification |

#### 2026-09-17 12:03 PDT — timeline audit

- Repository: `main` at `00dde3d`, one commit ahead of `origin/main`; working tree
  was clean before this timeline edit.
- Gate A evidence: `make PY=python3 validate` passed on two heuristic cases and
  produced parseable predictions plus two evidence files.
- Gate A blockers: `run.py` still defaults to `agents.heuristic`; there is no root
  `Dockerfile`; two nested Dockerfiles remain; `docker info` failed because the
  daemon is not available; the exact container judge command has not run.
- Gate B evidence: `python3 -m pytest -q starter/tests` passed **30/30** tests in
  **1.07 seconds**. The deterministic RCA core files are present and committed.
- Next current phase: finish Gate A integration immediately while Lane A continues
  the remaining Gate B detector/ranker work.

### Timeline completion note

Add a note like this beneath the table whenever a phase finishes:

```markdown
#### YYYY-MM-DD HH:MM PDT — <phase> completed
- Started: HH:MM PDT
- Finished: HH:MM PDT
- Acceptance commands: <exact commands>
- Measured results: <elapsed time, peak RSS, cost, scores, or output counts>
- Output inspected: <specific files and rows opened>
- Commit: <hash or "not committed">
- Cross-review: <reviewer and result>
- Remaining risks: <specific gaps or "none known">
- Next current phase: <phase>
```
