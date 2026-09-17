from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import CandidateEvent, EvidenceFact, parse_case
from rca.evidence import render_evidence
from rca.routing import CHEAP_MODELS, STRONG_MODELS, route_case


class FakeLLM:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[str], object]] = []
        self.usage: dict[str, dict[str, int]] = {}

    def ask(self, model, prompt, **kwargs):
        assert kwargs.get("extra_body") == {
            "chat_template_kwargs": {"enable_thinking": False}
        }, "bounded selection calls must disable thinking to leave room for JSON"
        models = [model] if isinstance(model, str) else list(model)
        self.calls.append((models, prompt))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        chosen = models[0]
        usage = self.usage.setdefault(
            chosen, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        )
        usage["prompt_tokens"] += 100
        usage["completion_tokens"] += 20
        usage["calls"] += 1
        return str(response)


def event_and_fact(
    case,
    *,
    suffix: str,
    component: str,
    score: float,
    minute: int,
    reason: str = "container CPU load",
    modality: str = "metric",
    alternatives: tuple[str, ...] = (),
    feature_scores: tuple[tuple[str, float], ...] | None = None,
):
    fact = EvidenceFact(
        fact_id=f"fact-{suffix}",
        source_file=f"summary-{modality}.csv",
        locator=f"minute={minute}",
        component=component,
        signal=f"{modality}.signal.{suffix}",
        onset_epoch_s=case.start_epoch_s + minute * 60,
        observed=9.0,
        baseline=1.0,
        method="verified compact detector summary",
    )
    event = CandidateEvent(
        component=component,
        onset_epoch_s=fact.onset_epoch_s,
        score=score,
        reason_candidates=(reason,),
        supporting_fact_ids=(fact.fact_id,),
        alternatives=alternatives,
        modality=modality,
        event_id=f"event-{suffix}",
        feature_scores=feature_scores or (("magnitude", score),),
    )
    return event, fact


def reply(event, fact, confidence: float = 0.7) -> str:
    return json.dumps(
        {
            "answers": [
                {
                    "event_id": event.event_id,
                    "component": event.component,
                    "reason": event.reason_candidates[0],
                    "onset_epoch_s": event.onset_epoch_s,
                    "fact_ids": [fact.fact_id],
                    "confidence": confidence,
                }
            ]
        }
    )


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = parse_case(
            800,
            "task_7",
            "One failure on March 20, 2022, from 10:00 to 10:30. "
            "Identify the root cause occurrence datetime, component, and reason.",
        )

    def route(self, candidates, facts, llm=None, policy="routed"):
        return route_case(
            case=self.case,
            candidates=tuple(candidates),
            facts=tuple(facts),
            telemetry_components=tuple(event.component for event in candidates),
            llm=llm,
            policy=policy,
        )

    def test_clear_case_stops_before_any_model(self) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="clear",
            component="checkoutservice-0",
            score=9.0,
            minute=2,
        )
        llm = FakeLLM([])

        outcome = self.route([event], [fact], llm)

        self.assertEqual(outcome.route, "deterministic")
        self.assertFalse(outcome.escalated)
        self.assertEqual(llm.calls, [])

    def test_uncertain_but_not_hard_uses_cheap_tier_only(self) -> None:
        event, fact = event_and_fact(
            self.case, suffix="weak", component="checkoutservice-0", score=5.0, minute=2
        )
        llm = FakeLLM([reply(event, fact)])

        outcome = self.route([event], [fact], llm)

        self.assertEqual(outcome.route, "cheap")
        self.assertFalse(outcome.escalated)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0][0], list(CHEAP_MODELS))

    def test_invalid_cheap_reply_escalates_once_then_accepts_strong(self) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="invalid",
            component="checkoutservice-0",
            score=5.0,
            minute=2,
        )
        llm = FakeLLM(["not json", reply(event, fact, 0.8)])

        outcome = self.route([event], [fact], llm)

        self.assertEqual(outcome.route, "strong")
        self.assertTrue(outcome.escalated)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[0][0], list(CHEAP_MODELS))
        self.assertEqual(llm.calls[1][0], list(STRONG_MODELS))

    def test_strong_ablation_calls_only_strong_tier(self) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="strong-only",
            component="checkoutservice-0",
            score=9.0,
            minute=2,
        )
        llm = FakeLLM([reply(event, fact)])

        outcome = self.route([event], [fact], llm, policy="strong")

        self.assertEqual(outcome.route, "strong")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0][0], list(STRONG_MODELS))

    def test_close_candidates_force_one_strong_escalation(self) -> None:
        first, first_fact = event_and_fact(
            self.case,
            suffix="first",
            component="checkoutservice-0",
            score=5.0,
            minute=2,
        )
        second, second_fact = event_and_fact(
            self.case,
            suffix="second",
            component="shippingservice-1",
            score=4.9,
            minute=8,
        )
        llm = FakeLLM([reply(first, first_fact), reply(second, second_fact, 0.75)])

        outcome = self.route([first, second], [first_fact, second_fact], llm)

        self.assertTrue(outcome.escalated)
        self.assertEqual(outcome.route, "strong")
        self.assertEqual(len(llm.calls), 2)

    def test_invalid_strong_reply_falls_back_deterministically(self) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="fallback",
            component="checkoutservice-0",
            score=5.0,
            minute=2,
        )
        llm = FakeLLM(["not json", "still not json"])

        outcome = self.route([event], [fact], llm)

        self.assertTrue(outcome.escalated)
        self.assertTrue(outcome.used_fallback)
        self.assertEqual(outcome.route, "deterministic-fallback")
        self.assertEqual(outcome.hypotheses[0].model_used, "deterministic")
        self.assertEqual(len(llm.calls), 2)

    def test_missing_key_path_never_calls_and_always_returns_exact_count(self) -> None:
        case = parse_case(
            801,
            "task_6",
            "Two failures on March 20, 2022, from 10:00 to 10:30. "
            "Identify the root cause component and reason.",
        )
        event, fact = event_and_fact(
            case, suffix="only", component="checkoutservice-0", score=3.0, minute=2
        )

        outcome = route_case(
            case=case,
            candidates=(event,),
            facts=(fact,),
            telemetry_components=("checkoutservice-0", "node-1"),
            llm=None,
        )

        self.assertEqual(len(outcome.hypotheses), 2)
        self.assertEqual(outcome.route, "deterministic-no-model")
        self.assertTrue(outcome.used_fallback)

    def test_prompt_keeps_metric_candidate_when_trace_candidates_dominate(self) -> None:
        candidates = []
        facts = []
        for index in range(10):
            event, fact = event_and_fact(
                self.case,
                suffix=f"trace-{index}",
                component=f"service-{index}",
                score=10.0 - index * 0.2,
                minute=1 + index,
                reason="container network latency",
                modality="trace",
                alternatives=("frontend-0",),
            )
            candidates.append(event)
            facts.append(fact)
        metric, metric_fact = event_and_fact(
            self.case,
            suffix="metric-tail",
            component="node-6",
            score=3.0,
            minute=1,
            reason="node disk write I/O consumption",
            modality="metric",
        )
        candidates.append(metric)
        facts.append(metric_fact)
        llm = FakeLLM([reply(candidates[0], facts[0]), reply(candidates[0], facts[0])])

        self.route(candidates, facts, llm)

        first_prompt = json.dumps(llm.calls[0][1])
        self.assertIn("event-metric-tail", first_prompt)
        self.assertIn("node disk write I/O consumption", first_prompt)

    def test_incomplete_topology_uses_flash_but_does_not_alone_force_strong(
        self,
    ) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="topology",
            component="paymentservice-0",
            score=9.0,
            minute=2,
            reason="container network latency",
            modality="trace",
        )
        llm = FakeLLM([reply(event, fact, 0.9)])

        outcome = route_case(
            case=self.case,
            candidates=(event,),
            facts=(fact,),
            telemetry_components=(event.component,),
            llm=llm,
            pipeline_warnings=("trace parent/child edge resolution was 22.8%",),
        )

        self.assertTrue(outcome.ambiguity.topology_incomplete)
        self.assertEqual(outcome.route, "cheap")
        self.assertFalse(outcome.escalated)
        self.assertEqual(len(llm.calls), 1)
        self.assertLessEqual(outcome.hypotheses[0].confidence, 0.65)

    def test_incomplete_topology_fallback_prefers_specific_metric(self) -> None:
        trace, trace_fact = event_and_fact(
            self.case,
            suffix="noisy-trace",
            component="cartservice-2",
            score=12.0,
            minute=2,
            reason="container network latency",
            modality="trace",
        )
        metric, metric_fact = event_and_fact(
            self.case,
            suffix="direct-disk",
            component="node-6",
            score=5.0,
            minute=3,
            reason="node disk write I/O consumption",
            modality="metric",
            feature_scores=(
                ("magnitude", 5.0),
                ("cross_signal", 2.0),
                ("sustained_minutes", 2.0),
                ("related_signal_support", 1.0),
                ("isolated_impulse", 0.0),
            ),
        )

        outcome = route_case(
            case=self.case,
            candidates=(trace, metric),
            facts=(trace_fact, metric_fact),
            telemetry_components=(trace.component, metric.component),
            llm=None,
            pipeline_warnings=("trace parent/child edge resolution was 22.8%",),
        )

        self.assertEqual(outcome.hypotheses[0].component, "node-6")
        self.assertIn("multi-signal metric evidence", " ".join(outcome.warnings))

    def test_incomplete_topology_does_not_promote_weak_metric(self) -> None:
        trace, trace_fact = event_and_fact(
            self.case,
            suffix="grounded-trace",
            component="adservice-0",
            score=12.0,
            minute=2,
            reason="container network latency",
            modality="trace",
        )
        metric, metric_fact = event_and_fact(
            self.case,
            suffix="weak-metric",
            component="frontend-0",
            score=5.0,
            minute=3,
            reason="container memory load",
            modality="metric",
            feature_scores=(
                ("magnitude", 5.0),
                ("cross_signal", 1.0),
                ("sustained_minutes", 1.0),
                ("isolated_impulse", 0.0),
            ),
        )

        outcome = route_case(
            case=self.case,
            candidates=(trace, metric),
            facts=(trace_fact, metric_fact),
            telemetry_components=(trace.component, metric.component),
            llm=None,
            pipeline_warnings=("trace parent/child edge resolution was 22.8%",),
        )

        self.assertEqual(outcome.hypotheses[0].component, "adservice-0")
        self.assertNotIn("multi-signal metric evidence", " ".join(outcome.warnings))

    def test_evidence_is_rendered_from_verified_facts(self) -> None:
        event, fact = event_and_fact(
            self.case,
            suffix="report",
            component="checkoutservice-0",
            score=9.0,
            minute=2,
        )
        outcome = self.route([event], [fact], FakeLLM([]))

        report = render_evidence(
            case=self.case,
            outcome=outcome,
            candidates=(event,),
            facts=(fact,),
        )

        self.assertIn("fact-report", report)
        self.assertIn("verified compact detector summary", report)
        self.assertIn("Path used: `deterministic`", report)
        self.assertIn("language model was not allowed to add facts", report)


if __name__ == "__main__":
    unittest.main()
