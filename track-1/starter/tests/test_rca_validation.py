from __future__ import annotations

import sys
import unittest
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import CandidateEvent, EvidenceFact, parse_case
from rca.rank import rank_events
from rca.validation import (
    ModelOutputError,
    extract_json_object,
    validate_model_selection,
    verified_component_choices,
)


class ModelValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = parse_case(
            700,
            "task_7",
            "One failure on March 20, 2022, from 09:00 to 09:30. "
            "Identify the root cause occurrence datetime, component, and reason.",
        )
        self.fact = EvidenceFact(
            fact_id="fact-700-a",
            source_file="metric/metric_container.csv",
            locator="timestamp=1;component=shippingservice-1",
            component="shippingservice-1",
            signal="container_fs_reads_bytes_total",
            onset_epoch_s=self.case.start_epoch_s + 120,
            observed=90.0,
            baseline=10.0,
            method="first sustained read-rate shift",
        )
        self.event = CandidateEvent(
            component="shippingservice-1",
            onset_epoch_s=self.fact.onset_epoch_s,
            score=8.0,
            reason_candidates=("container read I/O load",),
            supporting_fact_ids=(self.fact.fact_id,),
            modality="metric",
            event_id="event-700-a",
        )

    def payload(self) -> dict:
        return {
            "answers": [
                {
                    "event_id": self.event.event_id,
                    "component": self.event.component,
                    "reason": self.event.reason_candidates[0],
                    "onset_epoch_s": self.event.onset_epoch_s,
                    "fact_ids": [self.fact.fact_id],
                    "confidence": 0.8,
                }
            ]
        }

    def validate(self, payload: dict):
        return validate_model_selection(
            payload,
            case=self.case,
            candidates=(self.event,),
            facts=(self.fact,),
            telemetry_components=(self.event.component,),
            model_used="test-model",
        )

    def test_valid_closed_choice_becomes_hypothesis(self) -> None:
        hypotheses = self.validate(self.payload())

        self.assertEqual(len(hypotheses), 1)
        self.assertEqual(hypotheses[0].component, "shippingservice-1")
        self.assertEqual(hypotheses[0].fact_ids, ("fact-700-a",))
        self.assertEqual(hypotheses[0].model_used, "test-model")

    def test_json_can_be_fenced_but_must_contain_an_object(self) -> None:
        parsed = extract_json_object('answer follows\n```json\n{"answers": []}\n```')
        self.assertEqual(parsed, {"answers": []})
        with self.assertRaises(ModelOutputError):
            extract_json_object("there is no object here")

    def test_rejects_every_ungrounded_dimension(self) -> None:
        mutations = {
            "wrong count": lambda value: value.update(answers=[]),
            "invented event": lambda value: value["answers"][0].update(
                event_id="event-made-up"
            ),
            "invented component": lambda value: value["answers"][0].update(
                component="frontend-99"
            ),
            "illegal reason": lambda value: value["answers"][0].update(
                reason="disk trouble"
            ),
            "unsupported legal reason": lambda value: value["answers"][0].update(
                reason="container CPU load"
            ),
            "outside time": lambda value: value["answers"][0].update(
                onset_epoch_s=self.case.end_epoch_s + 1
            ),
            "changed onset": lambda value: value["answers"][0].update(
                onset_epoch_s=self.event.onset_epoch_s + 61
            ),
            "invented fact": lambda value: value["answers"][0].update(
                fact_ids=["fact-made-up"]
            ),
            "missing facts": lambda value: value["answers"][0].update(fact_ids=[]),
            "bad confidence": lambda value: value["answers"][0].update(confidence=2.0),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = self.payload()
                mutate(payload)
                with self.assertRaises(ModelOutputError):
                    self.validate(payload)

    def test_two_events_on_same_component_are_valid_when_event_ids_differ(self) -> None:
        case = parse_case(
            701,
            "task_6",
            "Two failures on March 20, 2022, from 09:00 to 09:30. "
            "Identify the root cause component and reason.",
        )
        facts = []
        events = []
        answers = []
        for index, reason in enumerate(
            ("container read I/O load", "container write I/O load"), 1
        ):
            fact = EvidenceFact(
                fact_id=f"fact-701-{index}",
                source_file="metric/metric_container.csv",
                locator=f"row={index}",
                component="shippingservice-1",
                signal=f"io-{index}",
                onset_epoch_s=case.start_epoch_s + index * 300,
                observed=10 * index,
                baseline=1,
                method="test fact",
            )
            event = CandidateEvent(
                component="shippingservice-1",
                onset_epoch_s=fact.onset_epoch_s,
                score=7.0,
                reason_candidates=(reason,),
                supporting_fact_ids=(fact.fact_id,),
                modality="metric",
                event_id=f"event-701-{index}",
            )
            facts.append(fact)
            events.append(event)
            answers.append(
                {
                    "event_id": event.event_id,
                    "component": event.component,
                    "reason": reason,
                    "onset_epoch_s": event.onset_epoch_s,
                    "fact_ids": [fact.fact_id],
                    "confidence": 0.7,
                }
            )

        hypotheses = validate_model_selection(
            {"answers": answers},
            case=case,
            candidates=events,
            facts=facts,
            telemetry_components=("shippingservice-1",),
            model_used="test-model",
        )
        self.assertEqual(len(hypotheses), 2)
        self.assertEqual({item.component for item in hypotheses}, {"shippingservice-1"})

    def test_trace_proven_parent_is_a_valid_endpoint_but_plain_alternative_is_not(
        self,
    ) -> None:
        fact = EvidenceFact(
            fact_id="fact-700-edge",
            source_file="trace/trace_span.csv",
            locator=(
                "timestamp_ms=1;parent_component=checkoutservice-0;"
                "child_component=shippingservice-1;operation=grpc/Call"
            ),
            component="shippingservice-1",
            signal="trace.edge.mean_duration_s:grpc/Call",
            onset_epoch_s=self.case.start_epoch_s + 120,
            observed=0.5,
            baseline=0.01,
            method="exact span_id/parent_span join",
        )
        event = CandidateEvent(
            component="shippingservice-1",
            onset_epoch_s=fact.onset_epoch_s,
            score=8.0,
            reason_candidates=("container network latency",),
            supporting_fact_ids=(fact.fact_id,),
            alternatives=("checkoutservice-0", "unverifiedservice-0"),
            modality="trace",
            event_id="event-700-edge",
        )
        base = {
            "event_id": event.event_id,
            "component": "checkoutservice-0",
            "reason": "container network latency",
            "onset_epoch_s": event.onset_epoch_s,
            "fact_ids": [fact.fact_id],
            "confidence": 0.7,
        }

        hypotheses = validate_model_selection(
            {"answers": [base]},
            case=self.case,
            candidates=(event,),
            facts=(fact,),
            telemetry_components=(
                "shippingservice-1",
                "checkoutservice-0",
                "unverifiedservice-0",
            ),
            model_used="test-model",
        )
        self.assertEqual(hypotheses[0].component, "checkoutservice-0")

        invalid = dict(base, component="unverifiedservice-0")
        with self.assertRaises(ModelOutputError):
            validate_model_selection(
                {"answers": [invalid]},
                case=self.case,
                candidates=(event,),
                facts=(fact,),
                telemetry_components=(
                    "shippingservice-1",
                    "checkoutservice-0",
                    "unverifiedservice-0",
                ),
                model_used="test-model",
            )

    def test_replica_coalescing_preserves_trace_endpoint_choices(self) -> None:
        events = []
        facts = []
        for replica in (0, 1):
            component = f"shippingservice-{replica}"
            fact = EvidenceFact(
                fact_id=f"fact-700-replica-{replica}",
                source_file="trace/trace_span.csv",
                locator=(
                    "timestamp_ms=1;parent_component=checkoutservice-0;"
                    f"child_component={component};operation=grpc/Call"
                ),
                component=component,
                signal="trace.edge.mean_duration_s:grpc/Call",
                onset_epoch_s=self.case.start_epoch_s + 120,
                observed=0.5,
                baseline=0.01,
                method="exact span_id/parent_span join",
            )
            events.append(
                CandidateEvent(
                    component=component,
                    onset_epoch_s=fact.onset_epoch_s,
                    score=8.0 - replica,
                    reason_candidates=("container network latency",),
                    supporting_fact_ids=(fact.fact_id,),
                    alternatives=("checkoutservice-0",),
                    modality="trace",
                    event_id=f"event-700-replica-{replica}",
                )
            )
            facts.append(fact)

        ranked = rank_events(events, 1)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].component, "shippingservice")
        self.assertIn("checkoutservice-0", ranked[0].alternatives)
        choices = verified_component_choices(
            ranked[0],
            facts,
            (
                "shippingservice",
                "shippingservice-0",
                "shippingservice-1",
                "checkoutservice-0",
            ),
        )
        self.assertEqual(
            set(choices),
            {
                "shippingservice",
                "shippingservice-0",
                "shippingservice-1",
                "checkoutservice-0",
            },
        )


if __name__ == "__main__":
    unittest.main()
