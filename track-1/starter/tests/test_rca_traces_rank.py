from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import CandidateEvent, parse_case
from rca.detect_traces import detect_traces
from rca.rank import rank_events

TRACE_COLUMNS = (
    "source_file",
    "component",
    "operation",
    "minute_epoch_s",
    "count",
    "mean_duration_s",
    "max_duration_s",
    "error_rate",
    "exemplar_epoch_s",
    "baseline_mean_duration_s",
    "baseline_std_duration_s",
    "baseline_error_rate",
)
EDGE_COLUMNS = (
    "source_file",
    "parent_component",
    "child_component",
    "operation",
    "minute_epoch_s",
    "count",
    "mean_duration_s",
    "max_duration_s",
    "error_rate",
    "exemplar_epoch_s",
    "baseline_mean_duration_s",
    "baseline_std_duration_s",
    "baseline_error_rate",
)


def _frame(columns: tuple[str, ...], rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _trace_row(
    component: str,
    minute: float,
    *,
    mean: float = 0.08,
    maximum: float = 0.12,
    error: float = 0.0,
    operation: str = "grpc.Test/Call",
) -> dict[str, object]:
    return {
        "source_file": "Market-cloudbed-1/telemetry/2022_03_20/trace/trace_span.csv",
        "component": component,
        "operation": operation,
        "minute_epoch_s": minute,
        "count": 100,
        "mean_duration_s": mean,
        "max_duration_s": maximum,
        "error_rate": error,
        # Prepared timestamps are epoch seconds.  The fractional part retains
        # the exact raw millisecond exemplar used in the evidence locator.
        "exemplar_epoch_s": minute + 0.321,
        "baseline_mean_duration_s": 0.01,
        "baseline_std_duration_s": 0.002,
        "baseline_error_rate": 0.0,
    }


def _edge_row(
    parent: str,
    child: str,
    minute: float,
    *,
    mean: float = 0.09,
    maximum: float = 0.15,
    error: float = 0.20,
) -> dict[str, object]:
    row = _trace_row(
        child,
        minute,
        mean=mean,
        maximum=maximum,
        error=error,
        operation="grpc.Payment/Charge",
    )
    row.pop("component")
    row["parent_component"] = parent
    row["child_component"] = child
    return row


def _candidate(
    *,
    component: str,
    onset: float,
    score: float,
    reason: str,
    event_id: str,
    modality: str,
    fact_id: str,
    topology: float = 0.0,
) -> CandidateEvent:
    return CandidateEvent(
        component=component,
        onset_epoch_s=onset,
        score=score,
        reason_candidates=(reason,),
        supporting_fact_ids=(fact_id,),
        modality=modality,
        event_id=event_id,
        feature_scores=(
            ("magnitude", score),
            ("cross_signal", 0.0),
            ("sustained_minutes", 1.0),
            ("topology_support", topology),
        ),
    )


class TraceDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = parse_case(
            901,
            "task_7",
            "One failure on March 20, 2022, from 08:00 to 08:30.",
        )
        self.start = self.spec.start_epoch_s

    def prepared(
        self, trace_rows: list[dict[str, object]], edge_rows: list[dict[str, object]]
    ) -> SimpleNamespace:
        return SimpleNamespace(
            spec=self.spec,
            trace_minutes=_frame(TRACE_COLUMNS, trace_rows),
            trace_edges=_frame(EDGE_COLUMNS, edge_rows),
        )

    def test_latency_error_and_parent_child_evidence_form_one_episode(self) -> None:
        minute = self.start + 60.0
        case = self.prepared(
            [
                _trace_row("paymentservice-0", minute),
                _trace_row("paymentservice-0", minute + 60.0, mean=0.10, maximum=0.18),
            ],
            [_edge_row("checkoutservice-0", "paymentservice-0", minute)],
        )
        trace_before = case.trace_minutes.copy(deep=True)
        edges_before = case.trace_edges.copy(deep=True)

        events, facts = detect_traces(case)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.component, "paymentservice-0")
        self.assertEqual(event.onset_epoch_s, minute)
        self.assertEqual(event.modality, "trace")
        self.assertEqual(event.reason_candidates[0], "container network latency")
        self.assertEqual(event.reason_candidates, ("container network latency",))
        self.assertEqual(event.alternatives, ("checkoutservice-0",))
        features = dict(event.feature_scores)
        self.assertGreater(features["topology_support"], 0.0)
        self.assertGreaterEqual(features["sustained_minutes"], 2.0)

        facts_by_id = {fact.fact_id: fact for fact in facts}
        self.assertEqual(set(event.supporting_fact_ids), set(facts_by_id))
        self.assertTrue(
            all("timestamp_ms=" in fact.locator for fact in facts),
            "every compact fact retains an exact raw-row timestamp locator",
        )
        self.assertTrue(
            any(
                "timestamp_ms=" + str(round((minute + 0.321) * 1000.0))
                in fact.locator
                for fact in facts
            )
        )
        self.assertTrue(
            any("raw trace microseconds" in fact.method for fact in facts)
        )
        self.assertTrue(
            any("span_id/parent_span" in fact.method for fact in facts)
        )
        duration_observations = [
            fact.observed for fact in facts if "duration_s" in fact.signal
        ]
        self.assertIn(0.08, duration_observations)
        self.assertNotIn(80_000.0, duration_observations)

        # Detectors may not annotate or otherwise mutate the shared prepared cache.
        pd.testing.assert_frame_equal(case.trace_minutes, trace_before)
        pd.testing.assert_frame_equal(case.trace_edges, edges_before)

        repeated_events, repeated_facts = detect_traces(case)
        self.assertEqual(events, repeated_events)
        self.assertEqual(facts, repeated_facts)

    def test_two_same_component_onset_clusters_keep_distinct_event_ids(self) -> None:
        early = self.start + 60.0
        late = self.start + 600.0
        case = self.prepared(
            [
                _trace_row("shippingservice-1", early),
                _trace_row("shippingservice-1", late, mean=0.12, maximum=0.20),
            ],
            [],
        )

        events, facts = detect_traces(case)

        self.assertEqual(len(events), 2)
        self.assertEqual({event.component for event in events}, {"shippingservice-1"})
        self.assertEqual({event.onset_epoch_s for event in events}, {early, late})
        self.assertEqual(len({event.event_id for event in events}), 2)
        self.assertTrue(all(event.supporting_fact_ids for event in events))
        self.assertEqual(
            {fact.fact_id for fact in facts},
            {fact_id for event in events for fact_id in event.supporting_fact_ids},
        )

    def test_generic_error_only_episode_does_not_fabricate_packet_reason(self) -> None:
        minute = self.start + 120.0
        row = _trace_row(
            "cartservice-0",
            minute,
            mean=0.01,
            maximum=0.011,
            error=0.35,
        )
        case = self.prepared([row], [])

        events, facts = detect_traces(case)

        self.assertEqual(events, ())
        self.assertEqual(facts, ())

    def test_empty_compact_frames_do_not_touch_large_telemetry_files(self) -> None:
        # This is deliberately an in-memory fixture; the detector has no dataset
        # path and therefore cannot accidentally scan the multi-gigabyte traces.
        case = self.prepared([], [])
        self.assertEqual(detect_traces(case), ((), ()))


class EventRankerTests(unittest.TestCase):
    def test_requested_two_can_be_two_events_on_same_component(self) -> None:
        first = _candidate(
            component="shippingservice-1",
            onset=100.0,
            score=7.0,
            reason="container network latency",
            event_id="shipping-early",
            modality="trace",
            fact_id="fact-early",
        )
        second = _candidate(
            component="shippingservice-1",
            onset=700.0,
            score=6.0,
            reason="container network latency",
            event_id="shipping-late",
            modality="trace",
            fact_id="fact-late",
        )

        ranked = rank_events([second, first], 2)

        self.assertEqual(len(ranked), 2)
        self.assertEqual({event.event_id for event in ranked}, {"shipping-early", "shipping-late"})
        self.assertEqual({event.component for event in ranked}, {"shippingservice-1"})

    def test_cross_modality_duplicates_fuse_before_count_limited_selection(self) -> None:
        metric = _candidate(
            component="paymentservice-0",
            onset=100.0,
            score=6.0,
            reason="container network latency",
            event_id="metric-payment",
            modality="metric",
            fact_id="fact-metric",
        )
        trace = _candidate(
            component="paymentservice-0",
            onset=130.0,
            score=5.0,
            reason="container packet loss",
            event_id="trace-payment",
            modality="trace",
            fact_id="fact-trace",
        )
        independent = _candidate(
            component="node-2",
            onset=400.0,
            score=4.0,
            reason="node memory consumption",
            event_id="node-memory",
            modality="metric",
            fact_id="fact-memory",
        )

        ranked = rank_events([independent, trace, metric], 2)

        self.assertEqual({event.component for event in ranked}, {"paymentservice-0", "node-2"})
        fused = next(event for event in ranked if event.component == "paymentservice-0")
        self.assertEqual(fused.modality, "metric+trace")
        self.assertEqual(set(fused.supporting_fact_ids), {"fact-metric", "fact-trace"})
        self.assertEqual(dict(fused.feature_scores)["modality_support"], 2.0)

    def test_topology_is_a_soft_bonus_not_a_hard_filter(self) -> None:
        direct = _candidate(
            component="frontend-0",
            onset=100.0,
            score=10.0,
            reason="container CPU load",
            event_id="direct",
            modality="metric",
            fact_id="fact-direct",
            topology=0.0,
        )
        downstream = _candidate(
            component="paymentservice-0",
            onset=200.0,
            score=3.0,
            reason="container network latency",
            event_id="topology",
            modality="trace",
            fact_id="fact-topology",
            topology=4.0,
        )

        self.assertEqual(rank_events([downstream, direct], 1)[0].event_id, "direct")
        self.assertEqual(
            {event.event_id for event in rank_events([downstream, direct], 2)},
            {"direct", "topology"},
        )

    def test_ranking_is_input_order_independent_and_count_aware(self) -> None:
        candidates = [
            _candidate(
                component=f"service-{index}",
                onset=100.0 + index * 60.0,
                score=5.0,
                reason="container CPU load",
                event_id=f"event-{index}",
                modality="metric",
                fact_id=f"fact-{index}",
            )
            for index in range(3)
        ]
        forward = rank_events(candidates, 2)
        reverse = rank_events(reversed(candidates), 2)
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward), 2)
        self.assertEqual(len(rank_events(candidates[:1], 2)), 1)

    def test_invalid_failure_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rank_events([], 0)
        with self.assertRaises(TypeError):
            rank_events([], None)


if __name__ == "__main__":
    unittest.main()
