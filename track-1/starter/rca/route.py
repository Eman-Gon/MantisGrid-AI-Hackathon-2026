"""The one routed model decision: pick the root cause(s) from ranked candidates.

Policy, in full:

  1. A CHEAP call sees compact candidates (never raw telemetry) and returns
     strict JSON: exactly ``failure_count`` picks, each a candidate index, a
     legal reason, the fact ids it leans on, a confidence in [0, 1] and a short
     why.
  2. The picks go through verify.py's hard rules.
  3. ESCALATE to a STRONG call at most once, only when an objective rule fires:
       - the cheap output failed hard validation, or
       - the deterministic top-two margin is small, or
       - metric and trace detectors disagree about the leader, or
       - a requested field has no supporting fact on the chosen candidate.
     A model saying "low confidence" is NOT, on its own, a reason to escalate.
  4. If every model is unavailable, or the output is still invalid, the
     deterministic top events are the answer.

RCA_MODEL pins every call to one model for the single-model comparison.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence

from .contracts import (
    CandidateEvent,
    CaseSpec,
    EvidenceFact,
    Hypothesis,
    LEGAL_REASONS,
    format_utc8,
)
from .verify import fill_from_candidate, verify

CHEAP = ["zai-org/GLM-4.7-Flash", "zai-org/GLM-5.3-Flash"]
STRONG = ["zai-org/GLM-5.2", "zai-org/GLM-5.1"]
SHOWN = 8                    # candidates the model sees
FACTS_EACH = 3               # facts shown per candidate
MARGIN = 0.8                 # escalate when second/first score ratio is above this

# Thinking is off by default on both tiers: GLM's chain of thought counts as
# output tokens (the priciest and slowest thing a call does) and on this task
# GLM-5.2 regularly spent 4000 of them without reaching an answer -- 60-70 s a
# case against a budget of one minute. The tiers differ by model size, which
# is the comparison the eval is about. RCA_THINK=1 turns thinking on for the
# strong tier, as an ablation.
NO_THINK = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}, "max_tokens": 700}
THINK = {"max_tokens": 2500}


def _gen(tier: list[str]) -> dict:
    return THINK if (tier is STRONG and os.environ.get("RCA_THINK")) else NO_THINK


def models(tier: list[str]) -> list[str]:
    return [os.environ["RCA_MODEL"]] if os.environ.get("RCA_MODEL") else tier


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _num(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.3g}"


def describe(case: CaseSpec, ranked: Sequence[CandidateEvent],
             facts: Mapping[str, EvidenceFact]) -> str:
    lines = []
    for i, c in enumerate(ranked[:SHOWN]):
        level = (f"  SERVICE-LEVEL: replicas {list(c.alternatives)} all deviated together"
                 if c.alternatives and not re.match(r"^(.+)-(\d+)$", c.component) else "")
        lines.append(f"[{i}] {c.component}  onset={format_utc8(c.onset_epoch_s)}  "
                     f"score={c.score:.2f}  modality={c.modality or '?'}  "
                     f"reasons={list(c.reason_candidates[:3])}{level}")
        shown = 0
        for fid in c.supporting_fact_ids:
            f = facts.get(fid)
            if f is None:
                continue
            lines.append(f"      {fid}: {f.signal} observed {_num(f.observed)} vs baseline "
                         f"{_num(f.baseline)} from {format_utc8(f.onset_epoch_s)} ({f.source_file.split('/')[-1]})")
            shown += 1
            if shown >= FACTS_EACH:
                break
    return "\n".join(lines)


def prompt(case: CaseSpec, ranked: Sequence[CandidateEvent],
           facts: Mapping[str, EvidenceFact]) -> str:
    lo, hi = case.window_utc8
    return (
        "You are doing root cause analysis on a microservice system (an online shop: pods "
        "like adservice-0 on nodes like node-3; several pods form one service).\n"
        f"Between {lo:%Y-%m-%d %H:%M} and {hi:%H:%M} (UTC+8) there were exactly "
        f"{case.failure_count} independent failure(s). Deterministic detectors found these "
        "candidate events, ranked, with the telemetry facts behind each:\n\n"
        f"{describe(case, ranked, facts)}\n\n"
        "Guidance: anomalies spread downstream, so the loudest component is often a victim; "
        "prefer the earliest onset with a plausible mechanism. A node-* component takes a "
        "node reason; a pod takes a container reason. Network reasons need trace latency or "
        "packet/retransmit evidence. Two failures are two distinct events, possibly on the "
        "same component. A candidate named without a replica suffix (e.g. cartservice, not "
        "cartservice-1) means every replica of that service deviated together: the fault was "
        "injected on the service, so name the service -- pick a single pod only when it "
        "clearly moved first or alone.\n\n"
        f"Legal reasons: {json.dumps(list(LEGAL_REASONS))}\n\n"
        f"Reply with JSON only, no prose:\n"
        '{"picks": [{"candidate": <index>, "reason": "<legal reason>", '
        '"fact_ids": ["<id>", ...], "confidence": <0..1>}, ...exactly '
        f'{case.failure_count} item(s)...], "why": "<two sentences>"}}'
    )


def _to_hypotheses(d: dict, case: CaseSpec, ranked: Sequence[CandidateEvent],
                   facts: Mapping[str, EvidenceFact], model: str) -> list[Hypothesis]:
    out = []
    for p in (d.get("picks") or [])[: case.failure_count + 2]:
        if not isinstance(p, dict):
            continue
        try:
            c = ranked[int(p.get("candidate"))]
        except (TypeError, ValueError, IndexError):
            continue
        reason = p.get("reason") if p.get("reason") in LEGAL_REASONS else c.reason_candidates[0]
        cited = tuple(f for f in (p.get("fact_ids") or []) if isinstance(f, str) and f in facts)
        if not cited:                          # lean on the candidate's own facts
            cited = tuple(c.supporting_fact_ids)
        try:
            conf = min(1.0, max(0.0, float(p.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        out.append(Hypothesis(component=c.component, reason_enum=reason,
                              onset_epoch_s=float(c.onset_epoch_s), fact_ids=cited,
                              confidence=conf, model_used=model))
    return out


def escalation_reasons(case: CaseSpec, ranked: Sequence[CandidateEvent],
                       facts: Mapping[str, EvidenceFact],
                       hyps: Sequence[Hypothesis], warnings: Sequence[str]) -> list[str]:
    why = []
    if any(w.startswith("hypothesis") for w in warnings):
        why.append("cheap output failed hard validation")
    n = case.failure_count
    if len(ranked) > n and ranked[0].score > 0 and ranked[n].score / ranked[0].score > MARGIN:
        why.append(f"top-{n} vs next margin is small "
                   f"({ranked[n].score:.2f}/{ranked[0].score:.2f})")
    mods = {c.modality for c in ranked[:3] if c.modality}
    if len(mods) > 1 and ranked[0].modality and ranked[1].modality != ranked[0].modality \
            and ranked[1].score / max(ranked[0].score, 1e-9) > 0.5:
        why.append("metric and trace detectors disagree about the leader")
    for h in hyps:
        if not any(f in facts for f in h.fact_ids):
            why.append(f"{h.component} has no resolving fact")
            break
    return why


def decide(case: CaseSpec, ranked: Sequence[CandidateEvent],
           facts: Mapping[str, EvidenceFact], llm,
           telemetry_components=None) -> tuple[tuple[Hypothesis, ...], tuple[str, ...], str]:
    """Return (hypotheses, warnings, model_reasoning) for one case.

    ``llm`` is an llm.LLM (or None for the no-model path).  ``ranked`` is the
    deterministic ranking, best first, at least ``failure_count`` long when
    the detectors found anything at all.
    """
    notes: list[str] = []
    deterministic = [fill_from_candidate(c) for c in ranked[: case.failure_count]]
    if llm is None or not ranked:
        hyps, w = verify(deterministic, case, ranked, facts, telemetry_components)
        return hyps, tuple(notes) + w + ("no model call: deterministic answer",), ""

    text = prompt(case, ranked, facts)
    reasoning = ""

    def ask(tier: list[str]) -> tuple[list[Hypothesis], str, str]:
        ms = models(tier)
        reply = llm.ask(ms, text, temperature=0, **_gen(tier))
        used = next((m for m in ms if m in llm.usage), ms[0])
        d = _json(reply)
        return _to_hypotheses(d, case, ranked, facts, used), str(d.get("why", "")), used

    # 1. cheap
    try:
        hyps, reasoning, used = ask(CHEAP)
        if not hyps:
            raise ValueError("no parseable picks in the reply")
        hyps, w = verify(hyps, case, ranked, facts, telemetry_components)
        notes += [f"cheap tier `{used}`"] + list(w)
    except Exception as e:
        notes.append(f"cheap tier failed ({type(e).__name__}: {e})")
        hyps, w = verify(deterministic, case, ranked, facts, telemetry_components)
        notes += list(w)
        w = ("hypothesis 1 rejected: no model output",)   # force escalation

    # 3. escalate once, on a rule
    why = escalation_reasons(case, ranked, facts, hyps, w)
    if why and models(STRONG) != models(CHEAP):
        notes.append("escalated: " + "; ".join(why))
        try:
            h2, r2, used2 = ask(STRONG)
            if not h2:
                raise ValueError("no parseable picks in the reply")
            h2, w2 = verify(h2, case, ranked, facts, telemetry_components)
            if not any(x.startswith("hypothesis") for x in w2):
                hyps, reasoning = h2, r2 or reasoning
                notes += [f"strong tier `{used2}` decided"] + list(w2)
            else:
                notes += ["strong tier output failed validation; kept the cheap decision"] + list(w2)
        except Exception as e:
            notes.append(f"strong tier failed ({type(e).__name__}: {e}); kept the cheap decision")
    elif why:
        notes.append("would escalate (" + "; ".join(why) + ") but RCA_MODEL pins one model")

    return hyps, tuple(notes), reasoning
