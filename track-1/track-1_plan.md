# Part 1 — Track 1 RCA Agent: routed, evidence-verified root-cause analysis over Market-cloudbed-1

**Status (2026-09-17, 10:45 PDT):** Done — repo forked to `Eman-Gon/MantisGrid-AI-Hackathon-2026` and pushed; official starter present under `track-1/`; data download to `/Volumes/T7` initiated (completion unverified). Pending — everything else: SP0 through SP6. Hard external deadline: **repo pushed and Google Form submitted by 3:00 PM PDT today.** No sub-part below is marked complete; no completion evidence exists yet.

---

## How to execute this plan

This document is a **sequence of review gates, not one big prompt.**

- An agent (Claude Code, Codex, or any other) works on **exactly one sub-part at a time**, the one it was explicitly assigned.
- An agent must **NOT infer authorization to continue into the next sub-part** because the broader plan, the hackathon, or "the Track 1 agent" was mentioned. Assignment to SP-N authorizes SP-N only.
- Each sub-part ends at a **stop point**: the agent reports evidence and waits. A human (Steven or teammate) approves, redirects, or kills the work, and separately gives any commit instruction.
- Two humans run this plan in parallel lanes (Section 4). Each human may drive 3–5 agents, but agents within a lane still respect sub-part boundaries and file ownership (Section 4.3).
- Given the 3:00 PM deadline, gate reviews are expected to take **2–5 minutes**, not longer. A gate review that would take longer than the remaining fix time triggers the cut order (Section 6.2) instead.

## End-of-sub-part requirements

Every sub-part, without exception, ends with the agent doing all of the following:

1. **Run the deterministic acceptance checks** listed in that sub-part's "Required tests and commands" block, exactly as written (adjusted only for paths recorded in `FACTS.md` by SP0).
2. **Inspect actual output** — open the produced files, print the produced rows, read the produced evidence — not just a green exit code or test summary.
3. **Update the progress ledger** in this file (status, and commit hashes once they exist).
4. **Prepare a checkpoint** (staged diff, proposed commit message) but **commit only on explicit instruction** from the human reviewer.
5. **Produce a handoff** containing: scope actually covered, files changed, decisions made (with one-line rationale each), exact measured results (numbers, not adjectives), known risks or gaps, commit hash if one was authorized, and the proposed next step.
6. **Stop and wait** for explicit approval before any further work.
7. Record self-review findings separately from the independent cross-review (the other human's lane reviews it; Section 4.4). Both are logged in the sub-part's Completion evidence.

## Progress ledger

| Sub-part | Deliverable | Status | Implementation commit | Review commit |
|---|---|---|---|---|
| SP0 | FACTS.md + CLAUDE.md + AGENTS.md + starter proven end-to-end | Pending | — | — |
| SP1 | Data contracts + per-case slicer with unit normalization | Pending | — | — |
| SP2 | Deterministic detectors + candidate ranker | Pending | — | — |
| SP3 | Routed LLM hypothesis + evidence writer | Pending | — | — |
| SP4 | Programmatic verifier + always-guess fallback | Pending | — | — |
| SP5 | Eval: routed vs single-model on dev gold set | Pending | — | — |
| SP6 | Submission package proven from a clean clone; form submitted | Pending | — | — |

---

## 1. Known facts and open unknowns

### 1.1 Facts (from organizer docs, slides, and Discord briefings, recorded 2026-09-17 morning)

- Task: for each case in `query.csv`, name **when the failure started, which component caused it, and why** (one of **15 possible reasons**), with evidence.
- Data: `track-1/data/Market-cloudbed-1/` — `telemetry/` (metrics, logs, traces; one folder per day), `query.csv` (cases), `dev/query_dev.csv` (same cases **with answers**), `manifest.json`. ~12 GB unpacked. Derived from OpenRCA with answers stripped; **using the original OpenRCA download disqualifies scoring.**
- Known traps: traces are in **ms**, metrics in **seconds**; all times **UTC+8**; **network faults appear only in traces**; the data does not fit in context and must be queried.
- Models: **GLM family only** (`zai-org/*`) via Featherless; 7 models; ~18× price spread; a typical case costs ~$0.04 on Flash vs ~$0.81 on GLM-5.2. Busy model returns HTTP 200 with an `error` body — check `error` before `choices`, retry, fall back. Key and endpoint must come from env (`FEATHERLESS_API_KEY`, `FEATHERLESS_BASE_URL`); hard-coded values fail at judging. Live catalog: `https://api.featherless.ai/v1/models`.
- Judging limits: **20 cases in 20 minutes and $25, on 2 CPUs / 8 GB RAM**, run headless via the exact command in `docs/submission.md`.
- Scoring: evidence is the heaviest-weighted component; **evidence not present in the data scores zero**; always put a guess in the prediction and doubts in the evidence file; eval must compare **routed vs single-model** on accuracy, dollars, and time.
- Baselines: starter baseline ≈ 0.073; best published agent solves 18/70.
- Starter: `make validate` → `make dev AGENT=agents.routed N=2` → `make cost` → `make score`; `starter/llm.py` already reads env and handles the busy-model error path.
- Submission: public repo, one Dockerfile or docker-compose at repo root, one-command run, `predictions.csv` + `evidence/<row_id>.md` + `REPORT.md` + `eval/`, README must disclose AI tools used and what was AI-generated. Form deadline 3:00 PM; judges take the latest commit on the default branch.

### 1.2 Open unknowns — resolved by SP0, consumed by later sub-parts

| # | Unknown | Needed by | Where it lives |
|---|---|---|---|
| U1 | Verbatim list of the 15 reasons (the output enum) | SP3, SP4 | `docs/data.md` |
| U2 | Exact `predictions.csv` columns and evidence-file required structure | SP3, SP6 | `docs/submission.md` |
| U3 | Exact scoring formula (accuracy vs cost/time weighting; time tolerance for "when"; component-name matching rules) | SP2, SP5 | `docs/scoring.md` |
| U4 | The 7 GLM model IDs and per-token prices | SP3 | `docs/models.md` |
| U5 | Starter module layout (what slicing/parsing already exists to reuse) | SP1–SP3 | `starter/` source |
| U6 | Case row schema in `query.csv` / gold schema in `dev/query_dev.csv` | SP1, SP5 | data files |
| U7 | Metric/log/trace file schemas per day-folder | SP1, SP2 | `docs/data.md` + data files |

No later sub-part may guess at U1–U7. If `FACTS.md` does not answer it, the sub-part stops and reports the gap.

## 2. Architecture and data contracts

### 2.1 Pipeline (per case)

```
case row ─▶ [SP1 Slicer] ─▶ [SP2 Detectors] ─▶ [SP2 Ranker] ─▶ [SP3 Evidence Packer]
                                                                     │
[SP5 Eval] ◀── outputs ◀── [SP4 Verifier] ◀── [SP3 Routed LLM] ◀─────┘
                   │
                   ▶ predictions.csv + evidence/<row_id>.md
```

- SP1, SP2, SP4 are plain Python — no LLM calls anywhere in them.
- SP3 makes **1 LLM call per case on the cheap tier, plus at most 1 escalation call** (Section 3 budget).
- The LLM never sees raw telemetry; it sees only the packed evidence bundle (a few KB).

### 2.2 Contracts (frozen at end of SP1; changing them afterward requires both humans' sign-off)

```python
# rca/contracts.py (path provisional until SP0 records starter layout)
CaseSlice   = {"case_id", "window_utc": (start, end), "metrics_df", "logs_df", "traces_df"}
Anomaly     = {"component", "signal", "start_utc", "severity", "source": "metric|log|trace", "pointer"}
Candidate   = {"component", "start_utc", "score", "supporting": [Anomaly, ...]}
Hypothesis  = {"component", "reason_enum", "start_utc", "citations": [pointer, ...], "confidence", "model_used"}
```

`pointer` must be resolvable back to a concrete row in the sliced data (file + row key / span id / timestamp+metric name). This is what makes SP4's verification and the evidence files mechanical.

### 2.3 Normalization rules (live in the slicer only, nowhere else)

- Internal time is UTC epoch seconds everywhere. Convert UTC+8 wall times once, on ingest.
- Trace durations ms → seconds on ingest.
- Component naming: adopt whatever `dev/query_dev.csv` gold labels use (U6) as canonical, mapped on ingest.

## 3. Cost and time budget

| Budget | Judging constraint | Design consequence |
|---|---|---|
| Time | 20 cases / 20 min → ~60 s/case | Slicing + detection must run in seconds; ≤2 LLM calls/case |
| Money | $25 / 20 cases → $1.25/case ceiling | Default tier Flash (~$0.04); escalation (~$0.81) only on disagreement/low confidence; worst case ~$0.85/case |
| Memory | 8 GB RAM vs 12 GB data | Never load whole files; slice per case (DuckDB over the files, or targeted reads) |
| Dev spend | $25/person | Develop on Flash and N≤5 case runs; full-70 runs only at SP5 |

Escalation policy (SP3): accept Flash's answer when it agrees with the ranker's #1 candidate and self-reports adequate confidence; otherwise one adjudication call to the strongest GLM within budget. Log `model_used`, tokens, dollars, wall time per case — SP5's table is built from these logs.

## 4. Two-person, multi-agent orchestration

### 4.1 Lanes

- **Lane A (Steven):** SP1, SP2 — data layer and deterministic analysis. Also owns the T7 data mount.
- **Lane B (teammate):** SP3, SP4 — LLM routing, evidence writing, verification. Starts immediately against **hand-built fixtures** (3 synthetic CaseSlices with planted anomalies) so it never waits on Lane A.
- **Joint:** SP0 (kickoff), SP5, SP6.

### 4.2 Agent fan-out within a lane

A human may run several agents in parallel **only on disjoint files within their own lane** (e.g., Lane A: one agent on the metric detector, one on the trace detector, one on the log detector — three files, one sub-part). Fan-out never crosses a sub-part boundary or a lane boundary.

### 4.3 File ownership (merge-conflict prevention)

| Path | Owner |
|---|---|
| `rca/slicer.py`, `rca/detect_*.py`, `rca/rank.py` | Lane A |
| `rca/route.py`, `rca/hypothesize.py`, `rca/evidence.py`, `rca/verify.py` | Lane B |
| `rca/contracts.py` | Written jointly in SP1, then frozen |
| `eval/`, `REPORT.md`, Dockerfile, README | Joint, SP5–SP6 only |
| This file's ledger + FACTS.md | Whoever is at a gate, one at a time |

Both humans commit to `main`, pulling before pushing. Anything outside your lane's paths: don't touch it, file a note in the handoff instead.

### 4.4 Independent review

Every gate is reviewed by the **other** lane's human (A reviews B's sub-parts, B reviews A's). Self-review findings and cross-review findings are recorded separately in Completion evidence. The Review commit column in the ledger records the commit at which the cross-review was performed.

## 5. Validation strategy

- **Canary cases:** SP1 picks 3 dev cases with distinct gold reasons (once U6 is known) as the fast regression set; every later sub-part re-runs them.
- **Gold set:** `dev/query_dev.csv` is the only accuracy oracle. Never eyeball-grade.
- **Benchmark design (required by the track):** SP5 runs three configurations on the same case set — routed (our policy), Flash-only, strongest-GLM-only — reporting accuracy, total $, total time from the SP3 logs.
- **Evidence validity:** SP4's verifier is itself acceptance-checked by mutation: corrupt a citation on purpose and confirm rejection.
- **Submission validity:** SP6 is only passable from a **clean clone in a fresh directory**, never from the working tree.

## 6. Wall-clock schedule and cut order

### 6.1 Gate targets (PDT, today)

| Gate | Target | Slack behavior |
|---|---|---|
| SP0 | 11:10 | If data still downloading, pass SP0 provisionally minus data heads; SP1 blocks on data |
| SP1 | 11:50 | — |
| SP2 | 12:50 | Runs parallel with SP3 |
| SP3 | 12:50 | Fixtures until SP1 lands, then real slices |
| SP4 | 1:20 | — |
| SP5 | 2:15 | Case count scales to remaining time/budget |
| SP6 | 2:50 | Form submitted by 2:55; judges see latest `main` at 3:00 |

### 6.2 Cut order when behind (apply top-down; record each cut in REPORT.md)

1. Drop escalation → Flash-only routing (the benchmark then compares Flash-only vs strongest-only).
2. Drop trace-topology ranking → rank by earliest anomaly only.
3. Shrink SP5 to 20 dev cases.
4. Drop the log detector (keep metric + trace — traces are the only source for network faults, so never drop those).
5. **Never cut:** evidence files, the routed-vs-single table (even if tiny), the honest REPORT.md, the clean-clone proof.

---

## Approval-gated implementation sub-parts

### SP0 — Ground-truth intake and environment proof

**Goal:** Convert the repo's own docs into recorded facts (U1–U7), prove the starter runs end to end on this machine, and produce the agent-guidance files so every later agent works from verified facts instead of guesses.

**In scope**
- Read `track-1/docs/{data,models,scoring,submission}.md` and `starter/` source; record U1–U7 answers verbatim into `track-1/FACTS.md` with file-and-line citations.
- Write `CLAUDE.md` and `AGENTS.md` at repo root: project context, the gate rules from this plan, file ownership table, normalization rules, budget policy, "never commit data or keys", commands cheat-sheet.
- Verify Featherless: key in env, one live call via `starter/llm.py`.
- Confirm data landed: symlink `track-1/data → /Volumes/T7/mantis-data` resolves; record U6/U7 heads into FACTS.md.
- Run the reference agent on 2 cases and score it.

**Out of scope**
- Any new agent code; any modification to starter code; any full-data processing.

**Implementation order**
1. `FACTS.md` from docs (U1–U5).
2. Starter proof run (commands below).
3. Data confirmation + U6/U7 heads (or mark "download pending", which blocks SP1 only).
4. `CLAUDE.md` / `AGENTS.md` generated from FACTS.md + this plan.

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
make validate
python -c "import os; assert os.environ.get('FEATHERLESS_API_KEY','').startswith('fw-') or os.environ.get('FEATHERLESS_API_KEY'), 'key missing'"
make dev AGENT=agents.routed N=2
make cost
make score
ls data/Market-cloudbed-1 && head -3 data/Market-cloudbed-1/dev/query_dev.csv
```

**Acceptance gate**
- `make validate` passes; reference agent completes 2 cases; `make score` prints a number.
- FACTS.md answers U1–U5 (and U6–U7 if data is present) with citations; the 15 reasons are listed **verbatim**.
- CLAUDE.md and AGENTS.md exist and contain the ownership table and gate rules.
- No key material appears in any file (`git grep -I "fw-"` returns nothing).

**Stop point** — prepare FACTS.md, CLAUDE.md, AGENTS.md, and the starter run logs as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP1 — Data contracts and per-case slicer

**Goal:** One function that turns a case row into a normalized `CaseSlice` in seconds, within the 8 GB memory envelope, with all unit/timezone conversion done exactly once.

**In scope**
- `rca/contracts.py` (both humans agree, then frozen).
- `rca/slicer.py`: `load_case(case_id) -> CaseSlice`, targeted reads of only the day-folders overlapping the case window; ms→s and UTC+8→UTC on ingest; canonical component naming per gold labels.
- Selection of the 3 canary cases (distinct gold reasons).
- 3 hand-built fixture CaseSlices handed to Lane B.

**Out of scope**
- Any anomaly logic; any LLM usage; any caching layer beyond what speed requires.

**Implementation order**
1. Contracts file (10 min, joint).
2. Fixtures for Lane B (unblocks SP3 immediately).
3. Case-row parsing per FACTS.md U6.
4. Targeted telemetry reads per U7; normalization; memory check.

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
python -m rca.slicer --case <canary_1> --stats   # prints row counts, time range, peak RSS
python - <<'EOF'
from rca.slicer import load_case
s = load_case("<canary_1>")
assert s["metrics_df"].ts.is_monotonic_increasing
assert s["traces_df"].duration_s.max() < 1e4      # ms->s conversion sanity
print("window UTC:", s["window_utc"])
EOF
```

**Acceptance gate**
- All 3 canary slices load in **< 10 s each** with **peak RSS < 2 GB**.
- Timestamps are UTC seconds; a hand-checked known event in canary 1 appears at the correct converted time.
- Lane B confirms fixtures conform to `contracts.py`.

**Stop point** — prepare the slicer, contracts, and canary stats as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP2 — Deterministic detectors and candidate ranker

**Goal:** From a CaseSlice, produce ranked root-cause candidates with supporting anomalies and a start time, using statistics and topology only — no LLM.

**In scope**
- `rca/detect_metrics.py` (rolling z-score per component/metric → anomaly windows), `rca/detect_traces.py` (per-edge latency/error shifts — sole detector able to see network faults), `rca/detect_logs.py` (error/warn burst detection).
- `rca/rank.py`: call graph from traces; score = earliest onset among anomalous components, upstream position, severity; emit top-3 `Candidate`s with `start_utc`.

**Out of scope**
- Reason classification (SP3); any tuning against more than the 3 canaries + ≤10 dev cases.

**Implementation order**
1. Metric detector → 2. Trace detector → 3. Ranker on those two → 4. Log detector last (first cut candidate).

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
python -m rca.rank --case <canary_1> --top 3     # repeat for canaries 2, 3
python -m rca.rank --cases dev:10 --report       # gold component in top-3? onset error vs gold?
```

**Acceptance gate**
- On ≥ 2 of 3 canaries the gold component is in the top-3 (record the miss honestly if not — do not tune past the 10-case set).
- Per-case detector+ranker wall time **< 15 s**; zero LLM calls (assert no network to Featherless in this path).
- Each candidate's every `supporting` anomaly carries a resolvable `pointer`.

**Stop point** — prepare the detectors, ranker, and the 10-case report as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP3 — Routed LLM hypothesis and evidence writer

**Goal:** Turn top candidates into a `Hypothesis` (component, one of the 15 reasons, start time, citations) using the two-tier routing policy, and write the per-case evidence file in the required format.

**In scope**
- `rca/route.py`: tier policy per Section 3, using model IDs/prices from FACTS.md (U4); per-case logging of model, tokens, $, seconds; busy-model handling via starter `llm.py`.
- `rca/hypothesize.py`: packs each candidate's evidence (≤ ~4 KB), constrains output to the verbatim 15-reason enum (U1), requires citations by pointer.
- `rca/evidence.py`: writes `evidence/<row_id>.md` exactly per U2 format — claim, cited artifacts, what was ruled out, stated doubts.
- Developed against Lane A's fixtures first; canaries once SP1 is approved.

**Out of scope**
- Verification of citations (SP4); any non-GLM model; any call not logged.

**Implementation order**
1. Route + logging on fixtures. 2. Hypothesis prompt with enum + citation requirement. 3. Evidence writer. 4. Swap fixtures for canaries.

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
python -m rca.hypothesize --fixture 1 --tier flash --dry-run   # prompt inspection, no spend
python -m rca.hypothesize --cases canary:3 --routed
cat evidence/<canary_1>.md
python -m rca.route --show-log                                  # per-case $, tokens, model, seconds
```

**Acceptance gate**
- All 3 canaries produce a Hypothesis whose `reason_enum` is verbatim from U1 and whose citations are pointers into the slice.
- Evidence files match the U2 required structure (checked against `docs/submission.md`, not memory).
- Cost log shows Flash-path cases **≤ $0.10** and any escalated case **≤ $0.90**; escalation rate on canaries ≤ 2/3.

**Stop point** — prepare route, hypothesize, evidence writer, and the canary cost log as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP4 — Programmatic verifier and always-guess fallback

**Goal:** Reject any hypothesis whose evidence is not really in the data or whose causality is temporally/topologically impossible, retry once with the objection, and guarantee every case still emits a prediction.

**In scope**
- `rca/verify.py`: every citation resolves to a real row; cited cause `start_utc` ≤ every downstream symptom onset; named component exists and (when topology is available) is upstream; reason is in the enum.
- Retry-once-with-objection; final fallback = ranker's #1 with mechanical evidence and doubts recorded in the evidence file. **A prediction is always emitted.**

**Out of scope**
- Semantic plausibility judging by a second LLM (only if time remains after SP5 — record as a deliberate limit otherwise).

**Implementation order**
1. Citation resolution. 2. Temporal check. 3. Enum/topology checks. 4. Retry + fallback wiring.

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
python -m rca.verify --case <canary_1>
python -m rca.verify --case <canary_1> --mutate-citation      # planted-fake test: MUST reject
python -m rca.pipeline --cases canary:3                        # end-to-end: slice->...->verified output
```

**Acceptance gate**
- Mutation test rejects the planted fake citation; unmutated canaries pass.
- End-to-end pipeline emits predictions + evidence files for 3/3 canaries even when verification forces the fallback path (force it once to prove it).

**Stop point** — prepare the verifier and end-to-end canary run as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP5 — Eval: routed vs single-model on the dev gold set

**Goal:** Produce the required benchmark — routed vs Flash-only vs strongest-GLM-only — on as many dev cases as time and budget allow, scored by `make score`, with dollars and seconds from the SP3 logs.

**In scope**
- `eval/run_eval.py` orchestrating the three configurations on an identical case set (target 30–70 cases; floor 20 per cut order).
- `eval/results.md`: one table — config × {accuracy, total $, total s, $/case, s/case} — plus 3 example evidence files linked.
- Extrapolated judging-envelope check: projected 20-case time ≤ 20 min and spend ≤ $25 for the routed config.

**Out of scope**
- Any tuning after the final eval run starts (results are what they are; honesty over polish).

**Implementation order**
1. Routed on 10 (sanity + projection). 2. If projection fits the envelope, full routed run. 3. Flash-only. 4. Strongest-only on a subset if budget-constrained (state subset size in the table).

**Required tests and commands**
```bash
cd ~/hackathon/hackathon-2026-official/track-1
python eval/run_eval.py --config routed --cases dev:10
python eval/run_eval.py --all --cases dev:<N>
make score
cat eval/results.md
```

**Acceptance gate**
- Table complete for all three configs (subset sizes stated); numbers come from logs and `make score`, not estimates.
- Routed projection fits 20 cases / 20 min / $25 with ≥ 20% margin on both axes.
- Accuracy vs the 0.073 baseline stated plainly, better or worse.

**Stop point** — prepare eval/ and results.md as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

### SP6 — Submission package proven from a clean clone

**Goal:** The repo judges see at 3:00 runs their exact command from a fresh clone with no manual steps, contains every required artifact, leaks nothing, and the form is submitted.

**In scope**
- Dockerfile (or compose) at **repo root** wired to the exact command in `docs/submission.md` (U2); key/endpoint read from env only.
- `REPORT.md`: what was built, measured results, what surprised us, where it breaks, cuts taken (from 6.2), routed-vs-single summary.
- README: AI-tools disclosure (which agents/models used, what was AI-generated), run instructions.
- `.gitignore` covers `data/`; secret sweep; final push; Google Form (team members + student/career status, track, title, description, repo link, slides link); 4-minute demo dry run once against a timer.

**Out of scope**
- Any feature work of any kind after 2:30.

**Implementation order**
1. Dockerfile + env plumbing. 2. Clean-clone rehearsal (commands below). 3. REPORT.md + README. 4. Sweep, push, form, demo dry run.

**Required tests and commands**
```bash
cd /tmp && rm -rf judge-test && git clone https://github.com/Eman-Gon/MantisGrid-AI-Hackathon-2026.git judge-test
cd judge-test
git grep -I "fw-" && echo "LEAK FOUND - STOP" || echo "no keys"
# then the EXACT run command from track-1/docs/submission.md, e.g.:
docker build -t rca . && docker run -e FEATHERLESS_API_KEY -e FEATHERLESS_BASE_URL rca <args from submission.md>
ls track-1/evidence/ | head; head -3 track-1/predictions.csv; ls track-1/eval/
```

**Acceptance gate**
- Clean clone builds and runs the judges' command with **zero manual steps**; predictions.csv, evidence/, eval/, REPORT.md all present in the clone.
- Key sweep clean; `data/` not in the repo; README disclosure present.
- Form submitted (screenshot kept) **before 2:55**; final commit hash recorded in the ledger.

**Stop point** — prepare the final commit and form receipt as a checkpoint, report evidence, and wait for approval and any explicit commit instruction.

**Completion evidence (dated)** — *(pending — appended after execution)*

---

## 7. Explicit scope and known limits

### In scope
- One agent for Track 1 only: deterministic slice→detect→rank front end, two-tier routed GLM hypothesis step, programmatic citation/temporal verifier, per-case evidence files, routed-vs-single benchmark, one-command dockerized submission from a public repo.
- Two-human, multi-agent execution under the gate discipline above, with independent cross-lane review at every gate.

### Deliberate limits (stated, not hidden)
- **No Track 2 work in this plan.** If Track 2 is attempted today it is a separate decision at the 2:00 gate, never a silent scope creep here.
- **No model training** (banned by rules) and no non-GLM models (banned by rules).
- **No semantic second-LLM verification** unless SP5 lands early; the shipped verifier is rules-based, and REPORT.md says so plainly.
- **No tuning beyond canaries + ≤10 dev cases before SP5**, to keep the final dev-set numbers honest.
- **No pursuit of the 18/70 frontier.** Success today = beat 0.073 clearly, evidence files that verify, an honest three-config benchmark, and a submission that runs from a clean clone. Higher accuracy is upside, not the bar.
- Anything cut under Section 6.2 is recorded in REPORT.md as a cut, not silently absent.
