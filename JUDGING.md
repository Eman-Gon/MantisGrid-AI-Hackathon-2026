# Judging

Written for the judges, and shared with participants. Each track's README repeats its
weights; if the two ever disagree, this document is the one that counts.

---

## What we are actually testing

Two tracks, two artifacts — an agent and its eval in Track 1, a dashboard and its
argument in Track 2. They look unrelated. They are graded against the same four
beliefs, and every dimension in both rubrics is one of these wearing a costume:

1. **Does it answer the question a human actually has?** Not the question that was
   convenient to answer. A CFO asks *where do I cut*; an on-call engineer asks
   *which of these twelve is the cause*. Neither asks for a metric.
2. **Can you get from the conclusion to the evidence?** Every claim should be one
   click, one chart, or one query away from the raw data that supports it. This is
   the dimension that separates the top submissions in both tracks and it is
   consistently the rarest.
3. **Is uncertainty stated honestly?** Confidently wrong is worse than uncertain
   and close. This is scored, not merely admired.
4. **What does being wrong cost?** An answer with no stated failure mode is a
   guess wearing a suit.

Judges: when a submission does something the rubric didn't anticipate, score it
against these four, not against the table. The table is a tool for consistency,
not a definition of merit. See **Creativity** below.

---

## Mechanics

**Scale.** Each dimension is scored **0–5**, then weighted. Final score is out of
100.

| Score | Meaning |
|---|---|
| 0 | Absent. Not attempted. |
| 1 | Attempted, does not work or does not hold up to one question. |
| 2 | Works in the demo path. Falls over off it. |
| 3 | Solid. Does what was asked, competently, no surprises. |
| 4 | Strong. Anticipated a problem the brief didn't name, and handled it. |
| 5 | We learned something. Changes how we'd build this ourselves. |

**3 is a good score.** Judges drift high; a room where everyone gets 4s has
measured nothing. Reserve 5 for work you would want to show someone outside the
company.

**Two judges per submission, independently, then reconcile.** Discuss any
dimension where you differ by 2 or more. Do not average away a real disagreement —
one of you saw something the other didn't, and finding out which is the point.

**Tie-break order:** evidence (dim 2) → uncertainty (dim 3) → everything else.

**Judges score cross-track.** Both rubrics share a spine deliberately so a Track 1
judge can sanity-check a Track 2 score. Ranking is *within* track; the two tracks
are not compared to each other.

---

## Track 1 — Root cause analysis

| Dimension | Weight |
|---|---|
| Accuracy on 20 held-out cases, perturbed | 20 |
| Evidence and explainability | 35 |
| Evaluation quality | 25 |
| Cost efficiency | 20 |

Four dimensions, one artifact. There is no interface dimension in Track 1 — the
agent runs headless and writes files, and visualization is Track 2's problem. What
a human reads is the evidence file, which is judged as evidence.

Every judged run uses the **model rule** below. Read that first — it is what makes
two of these dimensions comparable across teams at all.

### The model rule

**One family, the same menu for everyone, and the agent chooses per call.** Every
team's agent may use any model in the GLM family (`zai-org/*`) and must pick the model
for each call itself. Building that routing — cheap models for cheap calls, strong ones
where the reasoning decides the answer — is the heart of the track.

This replaces an earlier rule that fixed one model for the scored run. That rule made
tokens comparable across teams, but it made the most interesting engineering decision
in an agent — *what to spend where* — impossible to express. Routing makes it the point.

Two things keep it fair and measurable:

- **Cost is measured in dollars, not tokens.** Across a 21× input-price spread, a token
  count is not a unit. Per-model usage priced at the published rates is. Spending more
  therefore costs a team points directly, which is what stops budget standing in for
  skill.
- **The family is fixed.** Token counts and quality across unrelated families are not
  comparable, and an ablation table spanning Qwen, Mistral and GLM is a model-shopping
  report rather than evidence about a harness. Featherless serves nearly 12,000 models,
  some fine-tuned for observability; finding one of those is model selection doing the
  work. Holding the family constant makes every result attributable to a team decision.
  **This applies to ablations too** — the participant docs say GLM for everything.

| | |
|---|---|
| **Provider** | Featherless AI — OpenAI-compatible, `https://api.featherless.ai/v1` |
| **Models** | any of the seven GLM models below, chosen per call by the agent |
| **Key and endpoint** | the agent reads `FEATHERLESS_API_KEY`, and `FEATHERLESS_BASE_URL` if set |
| **Budget** | $25 of credit per participant, pooled per team (~$125 for five), development only |
| **Cost cap** | **$3 per case**, priced at the table below (see *Three limits*). The heaviest measured case costs $2.33 with every call on GLM-5.2, so a sensible agent never reaches it |
| **Wall-clock cap** | 10 minutes per case |
| **Exceeding either** | that case is scored zero, the run continues |
| **Run budget** | **$25 per team** for the 20 judged cases |
| **Exceeding it** | the run stops; the cases not yet reached score zero |

GLM was chosen because it is the only family on Featherless that clears three bars
at once: **seven usable models**, a **21× input-price spread**, and **every model ≥202K
context**. That last one is not negotiable — measured peak single-request context on
this benchmark is 38K–71K, so the 32K models that make up most of the catalogue (all of
Qwen3 included) cannot run this agent at all. Only **37 of 11,984** models on the
platform clear 128K.

Costed against **measured** usage — 17 real cases of this agent pattern, mean 474K
input and 34K output tokens per case, as if every call went to that one model:

| Model | Context | $/M in | $/M out | Per case | 78-case pass | Passes per $125 |
|---|---|---|---|---|---|---|
| GLM-4.7-Flash | 202K | 0.065 | 0.40 | $0.045 | $3.47 | 36 |
| GLM-5.3-Flash | 262K | 0.15 | 0.50 | $0.088 | $6.87 | 18 |
| GLM-4.6 / GLM-4.7 | 202K | 0.55 | 2.20 | $0.336 | $26.17 | 4.8 |
| GLM-5 | 202K | 0.95 | 3.15 | $0.557 | $43.48 | 2.9 |
| GLM-5.1 | 202K | 1.30 | 4.30 | $0.762 | $59.47 | 2.1 |
| GLM-5.2 | 262K | 1.40 | 4.40 | $0.813 | $63.43 | 2.0 |

Re-check `/v1/models` before publishing — prices move and these are a snapshot. **Pin
the price table used for scoring** and publish it, so a mid-event price change cannot
move anyone's cost score.

A routed agent lands somewhere between the first and last rows. The 18× spread per case
is what makes routing worth doing, and what makes a team's decision visible in the
numbers.

**We pay for the judged run.** Teams' credit is entirely theirs to develop with; we run
every submission on an organiser key. Per-submission cost is now set by the agent rather
than fixed, but it is bounded: at most the cost cap times the number of cases evaluated.

**Metering.** To score cost per submission we need each run's per-model usage. Either
issue a fresh key per evaluation run, or set `FEATHERLESS_BASE_URL` to a small metering
proxy that forwards to Featherless and records each call's model and token counts. The
proxy is the better option: it attributes cost exactly, and it can enforce the cost cap
itself.

### Three limits, and only two of them are scoring rules

| Level | Value | What happens when it trips |
|---|---|---|
| **Per case** | $3; 10 min wall-clock | that case scores zero, the run continues |
| **Per run** | $25 for the 20 judged cases | the run stops; cases not yet reached score zero |
| **Global** | the judging budget line | alert at 70%, hard stop at 100% |

**The run budget sits below the sum of the per-case caps ($60), on purpose.** A
submission can obey every per-case cap and still run out — so it is a scoring rule,
published in advance: teams must average under $1.25 a case. That clears an agent that
runs every call on GLM-5.2 (about $0.81 on a typical case) and only binds one that is
heavy across the board. Cases run in a fixed order, the same for every team, so which
cases a budget-limited run reaches is not luck.

The run budget does not cover **our** runner misbehaving — a retry loop, a restart
storm. That is what the global line is for, and when it trips someone looks at why; a
team is never scored down for our runner's fault.

**Judging spend, per team, at typical usage:** about $0.90 for 20 cases if every call
goes to GLM-4.7-Flash, $16 if every call goes to GLM-5.2, and never more than $25.
**Scoring twice** (original and perturbed, below — kept, decided 2026-09-11) doubles
that: at most $50 per team, $25 per run.

**Ask Featherless whether keys carry native spend limits.** A provider-side cap is worth
more than one we implement, because it holds even when our runner is the thing that has
gone wrong.

**Plan type: confirmed per-request keys**, billed by usage, **256K context**, and **no
model restriction**. That matters — Featherless's flat-rate Chat plan is scoped to
*"interactive, human-driven use"* and prohibits background automation and benchmarking,
which is exactly what a submission does. We are not on that plan, and nobody should be
steered onto it.

**Setting the cost cap.** There is no reference run any more, so the cap is set from
measured usage and the price table. It must not penalise any legitimate routing choice —
including routing *everything* to the most expensive model on a hard case. Across 17
measured cases the distribution is heavy-tailed (median 361K total tokens, mean 508K,
**max 1.45M**); that heaviest case would cost about **$2.33** if every call went to
GLM-5.2. A cap below that converts a slow, careful, correct answer into a zero, and the
team cannot tell which happened. **A cap that kills legitimate runs is worse than no
cap.** The cap exists to stop runaway loops; cost is already scored separately.

**This number must be set, and published, before the event.**

**We measure cost, not the teams.** Usage comes from Featherless's accounting or our
metering proxy, never from self-reporting. Self-reported usage is unverifiable and
trivially gameable, and asking teams to report a number that partly determines their
rank invites a problem nobody needs.

**Expect lower accuracy than the published leaderboard.** Its 11.34% figure is Claude
3.5 Sonnet; the same agent on Llama 3.1 scored 3.28%. Modern open-weights models are far
stronger than 2025 Llama, and nobody yet knows where GLM lands. With no reference run
of our own, the published figures are the only anchor — judge the band descriptors
against those.


### Accuracy — 20%

Strict accuracy from `main/evaluate.py` on the evaluation set, after perturbation.

Read it knowing what it can and cannot tell you. The published state of the art is
**11.34% strict**; teams will cluster in a narrow band near the floor, and the
spread between two teams is often a handful of cases. Everyone draws from the same
menu of models, which compresses that band further. **Treat a one-or-two
case difference as a tie and let the other dimensions break it** — that is what
the tie-break order in Mechanics is for. A large gap is real and should be scored
as real.

| | |
|---|---|
| 0 | No runnable agent, or scores 0 on every case. |
| 1–2 | Runs. Scores below the partial-credit floor. |
| 3 | Roughly matches published baselines (~10% strict). |
| 4 | Clearly beats them (15%+) and the improvement survives perturbation. |
| 5 | Substantially beats them **and** the team can explain *why* their approach wins, with evidence rather than a hypothesis. |

Report strict and partial separately. Report per-difficulty (easy / middle /
hard) — an agent that only solves `task_1` time-localisation is a different
animal from one that gets `task_7` end to end, and the aggregate hides it.

### Evidence and explainability — 35%

For any answer the agent gives, can it show the telemetry that supports it?

| | |
|---|---|
| 0 | Answer only. No trace of reasoning. |
| 1 | A prose rationale. Unverifiable — no reference to actual data. |
| 2 | Cites data in general terms ("CPU was high on the container"). |
| 3 | Points at specific records: file, timestamp range, metric, the values. A judge could check it by hand. |
| 4 | Shows the evidence *and* what it ruled out. Negative evidence is the tell that something reasoned rather than pattern-matched. |
| 5 | The explanation is good enough that a judge who disagrees with the answer can locate the exact step where the agent went wrong. |

**Correctness is not a gate here.** An answer that is wrong can score full marks
on evidence, and usually the wrong ones are the more informative test — when the
answer is right you cannot tell reasoning from a story told afterward. Score the
path, not the destination. This is also forced by the numbers: at an 11% ceiling
nearly every answer is wrong, so gating on correctness would collapse a quarter of
the rubric to zero for everyone.

**What is a gate is faithfulness.** The explanation must reflect what the agent
actually did, and the data must actually say what the agent claims. Spot-check one
claim per submission against the raw file. **Fabricated evidence scores 0** and is
worse than no explanation — a citation that sends an engineer chasing a spike that
isn't in the data costs more than silence.

Test it live: pick a case the agent got **wrong** and ask the team to walk you
through what it did. Teams that built explainability can do this immediately.
Teams that bolted it on cannot.

### Evaluation quality — 25%

The harness and the ablations. The dimension most likely to separate the field,
because most teams will spend the weekend on the agent and write the eval on
Sunday morning — so the ones who didn't will be visible immediately.

| | |
|---|---|
| 0 | No eval beyond the provided scorer. |
| 1 | Ran the scorer, reported one number. |
| 2 | Compared two configurations. No cost or latency. |
| 3 | Compared multiple configurations across at least two axes (routing, model, prompt, architecture) with accuracy, dollar cost, and wall-clock for each — including the routed agent against a single-model version of itself. |
| 4 | Plus variance across repeat runs, and a failure taxonomy — *which kinds* of case it misses, not just how many. |
| 5 | The eval produced a decision the team then acted on, and they can show the before and after. The eval drove the build rather than documenting it. |

An honest negative result scores well here. *"We tried three architectures, two
were worse, here is the data"* is a 4. A suspiciously clean sweep with no
methodology is a 2.

### Cost efficiency — 20%

How much inference the answer cost, **in dollars**. Measured, not judged: per-model
usage from Featherless's accounting or our metering proxy, priced at the pinned table.
It is the most objective number on this rubric — which is also why it must never be
read alone.

Dollars rather than tokens because the agent routes across models: a token on GLM-5.2
costs up to 21× a token on Flash, so token counts across two routed agents are not the
same unit. Dollars are.

**Two numbers, and they must be read together.**

| Metric | Denominator | Use |
|---|---|---|
| **Dollars per case** | cases evaluated — stable | the primary measurement |
| **Dollars per correct answer** | correct cases — small and noisy | secondary; undefined at zero |

Lead with dollars per case. Cost per *correct answer* is the metric that means
something, but at a ~11% accuracy ceiling its denominator is a handful of cases, so one
lucky answer moves it a lot. Report it, don't rank on it alone.

**Neither number means anything without accuracy beside it.** An agent that answers
`null` to everything costs almost nothing. Score the pair, always.

| | |
|---|---|
| 0 | Not measured, or the team cannot say what their agent spent. |
| 1 | Total spend only, no per-case breakdown. |
| 2 | Per-case cost and latency reported, no analysis. |
| 3 | Per-case cost broken down **by model**, and the routing compared against the same agent on a single model — at comparable accuracy, with before-and-after numbers. |
| 4 | Plus a **deliberate optimisation with measured effect** beyond routing itself — caching the per-case static prefix, cheap triage before expensive reasoning, early termination when the evidence is conclusive, retrieval that narrows before it reads. |
| 5 | A defensible position on the accuracy-vs-cost curve for a real on-call deployment, with the numbers behind it — including what they gave up to sit there. |

**Hard floor: a team with zero correct answers cannot score above 2 here**,
however little it spent. Efficiency is a ratio, and a zero numerator is not
frugality.

**A blank prediction is not frugality either.** The scorer gives zero for a wrong
answer and zero for a blank one, so an agent that abstains in the prediction file
is spending tokens to buy nothing. The brief tells teams to always emit a best
guess and put their uncertainty in the explanation instead; a submission that
abstains in the answer file has misread it, and the tokens it saved do not count
in its favour.

**Prompt caching deserves specific credit.** The telemetry schema, the tool
definitions, and the agent scaffolding are identical across all 78 cases. A team
that noticed and structured their prompts so the static part is cached is doing
exactly the engineering this dimension is for, and it is invisible unless you look
at the cached-token split. Ask for it.

**Report the distribution, not just the mean.** One case that burned 40× the
median is a different agent from one that is uniformly expensive — usually it means
an agent with no termination condition that ground away until the wall-clock cap.
That is worth finding, and the mean hides it.

There is no reference run to score against. For orientation only: a typical case costs
about **$0.045** if every call goes to GLM-4.7-Flash and **$0.81** if every call goes to
GLM-5.2. A routed agent should land between them; where, and what it bought, is what
this dimension reads.

---

## Track 2 — Cluster efficiency

| Dimension | Weight |
|---|---|
| Actionability | 25 |
| Cost of being wrong, and calibration | 25 |
| Evidence drill-down | 20 |
| Business framing | 20 |
| Beyond the brief | 10 |

### What arrives

A repository that comes up with one command and serves a dashboard, plus two
files:

| Artifact | Feeds |
|---|---|
| the running dashboard | actionability, business framing, drill-down |
| `claims.json` | calibration, checked against our ground truth |
| `REPORT.md` | the reasoning behind all of it |

**`claims.json` is Track 2's evidence file.** The rubric has always said to check
confidence claims against the answer key; until now there was nowhere teams
actually stated them, so calibration was unscoreable in practice. Now every team
writes the same fields and the checkable ones are checked the same way.

### What the answer key actually adjudicates

Some of your claims have a ground truth we check against. Some do not — they are
judgment calls with no correct answer, and are scored on the reasoning and the
honesty of the interval instead.

**You are told the split exists. You are not told which field is on which side.**
That is deliberate: a team that could see the checkable subset would optimise for
it and skip the judgment calls, which are the harder and more interesting half.

What follows from that, and it is worth taking seriously:

- A well-argued 40,000 and a well-argued 90,000 recoverable GPU-hours can **both**
  score full marks. A bare 61,200 with no interval and no basis cannot.
- Being wrong on a checkable field costs you far less than being *confidently*
  wrong on one. See the calibration section above.
- Omitting a field you did not investigate is neutral. Filling it in to look
  complete is not.

*(The organizer copy of this document lists which fields are adjudicated and what
their values are. That section is removed from the participant copy — everything
else is identical.)*

### Abstention runs the opposite way from Track 1

Worth stating because judges will work both tracks. Track 1 tells teams to always
guess, because its scorer gives zero for a blank and zero for a wrong answer, so
a guess is free upside. Track 2 tells teams to **omit what they did not
investigate**, because here a confidently wrong claim actively costs them on
calibration while an omission is neutral. Both are correct for their own
mechanics. Do not penalise a Track 2 team for a short `claims.json` — penalise a
long one that is confidently wrong.


### Judging the node triage

`node_triage` is marked on **how the team reasoned**, not on which cause label they
wrote. There is no hidden list to find: `rules::node-elevated-failure-rate` fires
113 times across 87 machines and every one of those findings ships. The finding
states the symptom only — a failure count against the cluster rate in the same
two-week window — and deliberately withholds owner concentration, hardware flags
and any `probable_cause`. Everything needed to reach a cause is in the parquet.

Our cause for each node-window is **one defensible read, not ground truth.** On the
repeat-flagged nodes the data supports an argument either way, and that argument is
the deliverable.

`cannot_determine` is a **first-class correct answer.** It is the right call on
about 28% of these. A team that reaches for a cause on every one is over-reading,
not out-performing.

| | |
|---|---|
| **1** | No reasoning, or a bare assertion. A cause label with no evidence lands here even if it matches ours |
| **2** | Cites a ranked endpoint or a raw count. `/v1/resources/underperforming` top-N, or "most failures" |
| **3** | Normalises by exposure — failures per job run rather than raw count. Notices the detector already did this and reads the p-value rather than re-deriving it |
| **4** | Attributes by joining. Groups the node's FAILED jobs in that window by `id_user`, or checks `hit_node_failure`, or compares the node's median failed-job duration against the cluster's, and names the numbers |
| **5** | The above, plus uses `cannot_determine` where it belongs, treats repeat-flagging as a confidence signal rather than a cause signal, and says what acting wrongly would cost |

**Do not reward a matching cause label with weak evidence, and do not punish a
differing label backed by a real join.** A team that says "cannot determine — here
is what I ruled out and how" has done better work than one that guessed our label.

Persistence is worth checking they understood: 23 of the 87 nodes fire in more than
one window, and that is **not evidence of hardware**. Only two node-windows in the
key are hardware at all — both happen to be on repeat-flagged machines — which is far
too few to say persistence predicts it. Most repeat-flagged machines are one person's
recurring work. Persistence says "not noise". It does not say "bad machine".

### Judging the card imbalance

`rules::gpu-imbalance` reports jobs that held more than one GPU and drove only one.
The detector's volume is visible to anyone who counts it in `findings.json`; our
idle-GPU-hour total is in the answer key and the scorer prints it beside whatever
the team claimed.

The number is not the test — the threshold is a judgement call. **Whether they found
the population at all is the test**, because `sm_util_avg` in `jobs.parquet` is
*exactly* the mean of the job's cards. A job with one card at 0% and one at 65%
reads there as an ordinary 35%-utilised job, against a two-card median of 0.5. These
jobs look **healthier than average**. Anyone filtering the job table for low
utilisation actively skips them. Getting here requires pivoting `gpus.parquet` per
card.

Three discriminators, and the scorer prints all three:

| | |
|---|---|
| **node-vs-user test** | Did they test whether the idle-card index is a property of the machine or of the user? It is **user-scoped** — 34 of 195 nodes have had *both* cards be the idle one. A team that ran this test resolved something real |
| **declined to resolve the index mapping** | The idle card is DCGM index 0 about 92% of the time. Whether that means `cuda:0` **cannot be resolved** — DCGM follows NVML/PCI order, CUDA's default `FASTEST_FIRST` is documented as "unspecified" on identical cards. A team that says so is **right**. Credit that above a fluent wrong answer |
| **avoided the consolidation trap** | "Bin-pack the 1-GPU jobs" looks like an 84,000 GPU-hour finding. It is not one: 48% of card-hours sit on wholly idle nodes, queue depth does not correlate with stranded cards, and the release is a **sample**, so no occupancy finding is derivable at all. The imbalance survives because it is measured *within a single job* and needs no denominator |

A team that articulates why one finding survives sampling and the other does not has
understood the dataset better than one that produced a bigger number.

### Actionability — 25%

*Does a non-engineer know what to do after thirty seconds?*

| | |
|---|---|
| 0 | A metrics dashboard. |
| 1 | Charts that require an engineer to interpret. |
| 2 | A conclusion, but no recommended action. |
| 3 | A specific, ranked, owned recommendation. Not "improve utilization." |
| 4 | The recommendation names the tradeoff it's making and who it affects. |
| 5 | A CFO could forward it and an SRE receiving it would know exactly what to do Monday. |

Judge this by actually giving it thirty seconds. Set a timer. Look away. Say what
you'd do. If you can't, it's a 2.

### Cost of being wrong, and calibration — 25%

The tile most teams skip, and the one we care about most.

| | |
|---|---|
| 0 | Not addressed. |
| 1 | Acknowledged in prose. |
| 2 | An error bar on the headline number. |
| 3 | Quantified downside: if the estimate is wrong in the stated direction, here is the cost in dollars or capacity. |
| 4 | Plus the tradeoff made explicit — cutting this recovers X and risks Y — with the threshold defended. |
| 5 | A decision framework rather than a number: under what conditions the recommendation flips. |

**Calibration is scored inside this dimension**, using `claims.json` against
our ground truth for the fields the key adjudicates, and using
judgment for the rest. A team at 60% confidence that is right 60% of the time
outscores a team at 95% confidence that is right 70% of the time, even if the
second has higher raw accuracy. Say this out loud when scoring — it is
counterintuitive and judges will fight it.

The brief warns that summing overlapping findings inflates recoverable capacity
(~60% of low-utilization jobs also carry a memory finding), and that treating
`CANCELLED` as waste swings the headline by ~2×. A team that hit either trap and
*noticed* is at 4. A team that hit it and reported the inflated number confidently
cannot score above 2 here regardless of how good the dashboard looks.

### Evidence drill-down — 20%

Business number → recommendation → findings → resources → raw telemetry.

| | |
|---|---|
| 0 | Numbers with no provenance. |
| 1 | Numbers traceable to an endpoint. |
| 2 | Drill-down exists but breaks partway. |
| 3 | The full path works, end to end, for the headline number. |
| 4 | Works for every claim on the dashboard, and shows the `fact` / `judgment` distinction the API exposes. |
| 5 | Verified the API's own arithmetic against the raw parquet and can show where it agrees or doesn't. |

Test by picking one number and clicking until you reach a raw record. The brief
tells participants this separates the top submissions, so a team that ignored it
ignored the brief.

**Correctness is not a gate**, same as Track 1. A team whose recoverable-capacity
estimate you disagree with can score full marks here if every number is traceable
and the method is sound — most of Track 2's headline claims have no correct
answer, so grading traceability on whether you like the conclusion would be
grading nothing.

**Faithfulness is the gate.** A number that does not reconcile with the data it
claims to come from scores **0** on this dimension, however polished the
dashboard. Check one. An unreconcilable number on a CFO dashboard is worse than a
missing one, because it will be forwarded and acted on.

### Business framing — 20%

| | |
|---|---|
| 0 | Metric names on axes. |
| 1 | Percentages without denominators. |
| 2 | Some dollarisation, inconsistently applied. |
| 3 | Every number in dollars, hours, or percent of capacity. |
| 4 | Correct use of the price book, versioned, with the assumption stated. |
| 5 | Reframed something we hadn't — e.g. the queue tail as **98,213 engineer-hours**, a salary line rather than an infrastructure line. |

Penalise a dashboard that ranks named users by waste. The brief calls this out:
these tiles locate recoverable capacity, they do not assign blame. Cap
actionability at 2 if a submission does this.

### Beyond the brief — 10%

Three tiles were required. This dimension is entirely about the fourth thing.

Explicitly includes **pushing back on Layer B**. Those endpoints are a guess, and
finding out where the guess is wrong is a stated goal of the event. A team that
argues, with evidence, that `/v1/recommendations` has the wrong shape is doing
exactly what we asked for and should score 5.

---

## What arrives, and how it is scored uniformly

Teams submit a repository, not results. We run every one the same way.

```
submission/
├── Dockerfile / requirements.txt
├── run.py            python run.py --dataset <dir> --queries <csv> --out <dir>
├── REPORT.md
└── eval/
```

The entry point reads only from `--dataset`, writes only into `--out`, takes the
model key from the environment, and runs unattended. It produces:

| Artifact | Feeds |
|---|---|
| `predictions.csv` | accuracy, scored automatically |
| `evidence/<row_id>.md`, one per case | evidence and explainability — 35% |
| `REPORT.md` | evaluation quality — 25% |
| Featherless's billing of our key during the run | cost efficiency — 20% |

**`evidence/<row_id>.md` is what makes the heaviest dimension comparable.** Without a
fixed artifact, one team hands us JSON, another a notebook, another a paragraph in
a README, and there is no scale that fits all three. Every team writes the same
file per case — answer, confidence, evidence examined with file and time range,
what was ruled out — so judges open the same thing for every submission. The
format inside is loose enough that a team can still be good at it.

### The pipeline

**Track 1**

| Step | Judgment required |
|---|---|
| Run every agent → predictions + evidence | none, automated |
| Score accuracy on original and perturbed | none, automated |
| Pull token usage from billing | none, automated |
| Read *N* evidence files per team, spot-check *one* claim against the raw data | manual, same *N* for everyone |
| Read `REPORT.md` | manual |
| Live demo | manual, same time per team |

Forty percent of the grade — accuracy and tokens — comes out of the first three
rows with no judgment at all.

**Track 2**

| Step | Judgment required |
|---|---|
| Bring up every dashboard | none, automated |
| Score `claims.json` against the answer key, adjudicated fields only | none, automated |
| Thirty-second actionability test, timed | manual, same for everyone |
| Click one number through to a raw record | manual, one per team |
| Read `REPORT.md` | manual |
| Live demo | manual, same time per team |

Less is automatable here, because most of what Track 2 asks for is a judgment with
no correct answer. That is the nature of the task, not a gap in the rubric — but
it means Track 2 scores drift more between judges, so the two-judge reconciliation
matters more. Pick *N* before you start reading and hold it constant;
reading twelve evidence files for the team you like and three for the team you
don't is how a rubric stops working.

### The failure mode to prevent

**A submission that does not execute cannot be judged.** Both tracks get a `make
validate` target — Track 1's runs the agent on two cases and checks the output
shape, Track 2's brings the dashboard up and checks `claims.json` parses — and
both briefs tell teams to run it. Expect to have to enforce this anyway. Decide
in advance what happens to a submission that does not run — our position should be
that we make one good-faith attempt to fix an environment problem, timeboxed, and
past that it scores what it scores. Agree the timebox before judging day, not
during it.

## Creativity

The rubrics above describe what we expect. They will systematically undervalue
anything we didn't expect, which is the opposite of what a hackathon is for. Three
provisions:

**Judge the artifact, not the checklist.** If a submission is manifestly good and
scores 3s, the rubric is wrong for that submission. Score what it is, write down
why, and flag it in reconciliation. The four beliefs at the top are the appeal
court.

**A wrong turn taken seriously beats a safe path taken lazily.** A team that
attempted something hard, failed, measured the failure, and can explain it has
demonstrated more than a team that shipped the three required tiles cleanly. Score
the reasoning, not the outcome. This is what dimension 4 is for in both rubrics.

**Reframing the question is allowed.** A team that decides the CFO is asking the
wrong question, and shows why with data, has not gone off-brief — that is
senior-engineer behaviour and we should reward it. The requirement is that the
argument be *made*, with evidence, not merely asserted.

The one thing creativity does not excuse: an unverifiable claim. Novelty raises
the ceiling; it does not lower the evidence bar.

---

## Anti-memorization, operationally

Track 1 only. Track 2's answer key was never published.

**Policy, published in advance:** teams submit an agent, we run it, we score twice
— original and perturbed — and publish `memorization gap = score(original) −
score(perturbed)` next to accuracy.

**The perturbation.** Two transforms, both provably structure-preserving:

- **Rename components** through a consistent mapping applied everywhere the name
  appears — metrics, logs, traces, topology files, and the records. Scoring is
  exact string match, so this is a total defense for the four task types that
  include a component. Map to names with the same *shape*: `node-5.adservice-2`
  encodes pod-on-node deployment, so the replacement must too, or the difficulty
  changes and the perturbation leaks.
- **Shift timestamps by a whole number of days.** A day is 48 half-hours, so the
  30-minute bucketing that determines how many failures group into one case is
  preserved exactly. Date folders are renamed to match. This covers the task types
  that ask only for time or reason.

Do **not** jitter events independently. Causal ordering is the thing being
reasoned over; break it and cases become unsolvable.

Then regenerate queries against the transformed records:

```bash
python -m main.generate -s main/task_specification.json \
    -r <perturbed record.csv> -q <new query.csv> -t Asia/Shanghai
```

**Verify before it touches a submission.** Run the baseline agent on the perturbed
set. If its score moves materially, the transform broke something. An unfair eval
set is worse than a memorizable one.

**Sandbox.** Filesystem scoped to the telemetry directory. `record.csv` absent
from the container, and `query.csv` present with `scoring_points` stripped — note
that the scoring points column states the answer in plain English, so it is a
second answer key and is easy to forget.

**Egress is allowlisted to the model API endpoint only.** The agent must reach the
standard model, so this cannot be a fully closed box. One destination, nothing
else: no GitHub, no Drive, no arbitrary fetch. That is enough to stop an agent
pulling `record.csv` off the web at runtime. It does not stop answers already
baked into a submission's own prompts — that is what perturbation is for, and it
always was.

**Read a large gap charitably first.** An agent can overfit to naming conventions
without anyone intending to cheat. That is a real finding about the agent. Ask the
team before concluding anything.

---

## Red flags

Not disqualifying on their own. Each one is a question to ask.

| Signal | Ask |
|---|---|
| Accuracy far above the state of the art | Show us a live run on a case you haven't seen. |
| Accuracy flat across model swaps | What is the model actually contributing? |
| Token usage near-identical across every case | Is the agent actually adapting, or running a fixed script? |
| One case burned 40× the median | Does the agent have a termination condition at all? |
| Large memorization gap | Walk us through one perturbed case that failed. |
| Dashboard totals exceed cluster capacity | Did you deduplicate overlapping findings? |
| Confidence uniformly high | How was this calibrated? Against what? |
| No negative results anywhere | What did you try that didn't work? |

The last one is the most reliable signal in the list. Every real project has
failures in it. A submission with none has either hidden them or didn't explore.

---

## Practical

- **Live demo, 15 minutes per team, before scores are final.** Judges pick the
  cases. This is where dimension-2 scores get confirmed or collapse, and it is
  much harder to fake than a submitted artifact.
- **Score the demo and the artifact together.** Neither alone is enough — a great
  artifact with a demo the team can't drive is a 2 on evidence.
- **Write one sentence per dimension** as you score. Reconciliation without notes
  is two people arguing about a vibe.
- **Publish the rubric before the event.** It is already in both participant
  briefs. Scoring people against criteria they couldn't see is how you get a
  recruiting event that damages recruiting.

---

## Open

- **Prize structure and team size** — not yet decided, and prize structure changes
  behaviour. If there is a single accuracy prize, the rubric above is decorative.
- **The cost cap number.** Now in dollars per case, with no reference run behind it.
  It must clear the heaviest measured case even if every call goes to the most
  expensive model — about **$2.33** — or it will zero legitimate careful runs. Must be
  set and published before the event.
- **Development credit.** The participant docs promise **$25 of Featherless credit per
  participant**, and that judged runs are on our key. Confirm the credit with
  Featherless, and whether it can be pooled per team.
- **Judge count and availability.** Two independent judges per submission is the
  design; it needs enough judges to hold at the expected team count.
