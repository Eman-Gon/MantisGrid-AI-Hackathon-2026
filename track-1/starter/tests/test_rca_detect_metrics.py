from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest

import pandas as pd


STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import CaseSpec, REQUESTED_FIELDS, UTC_PLUS_8  # noqa: E402
from rca.detect_metrics import detect_metrics  # noqa: E402
from rca.rank import rank_events  # noqa: E402


@dataclass(frozen=True)
class PreparedFixture:
    spec: CaseSpec
    metric_minutes: pd.DataFrame


COLUMNS = (
    "source_file",
    "source_kind",
    "component",
    "signal",
    "minute_epoch_s",
    "count",
    "mean",
    "min",
    "max",
    "delta",
    "baseline_mean",
    "baseline_std",
    "baseline_delta_mean",
    "baseline_delta_std",
)


class MetricDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        start = datetime(2022, 3, 20, 9, 0, tzinfo=UTC_PLUS_8)
        end = start + timedelta(minutes=30)
        self.start_s = start.timestamp()
        self.spec = CaseSpec(
            row_id=901,
            task_type="task_7",
            failure_count=2,
            requested_fields=REQUESTED_FIELDS["task_7"],
            window_utc8=(start, end),
            window_epoch_s=(start.timestamp(), end.timestamp()),
        )

    def _frame(self, rows: list[dict]) -> pd.DataFrame:
        defaults = {
            "source_file": "telemetry/2022_03_20/metric/metric_container.csv",
            "source_kind": "container",
            "component": "checkoutservice-0",
            "signal": "container_cpu_load_average_10s",
            "count": 6,
            "mean": 10.0,
            "min": 10.0,
            "max": 10.0,
            "delta": 0.0,
            "baseline_mean": 10.0,
            "baseline_std": 1.0,
            "baseline_delta_mean": 0.0,
            "baseline_delta_std": 1.0,
        }
        materialised = []
        for row in rows:
            merged = dict(defaults)
            merged.update(row)
            materialised.append(merged)
        return pd.DataFrame(materialised, columns=COLUMNS)

    def _detect(self, rows: list[dict]):
        return detect_metrics(PreparedFixture(self.spec, self._frame(rows)))

    def test_empty_frame_returns_empty_tuples(self) -> None:
        events, facts = detect_metrics(
            PreparedFixture(self.spec, pd.DataFrame(columns=COLUMNS))
        )
        self.assertEqual(events, ())
        self.assertEqual(facts, ())

    def test_onset_is_first_sustained_deviation_not_peak(self) -> None:
        events, facts = self._detect(
            [
                {"minute_epoch_s": self.start_s, "mean": 10.0, "max": 10.0},
                {"minute_epoch_s": self.start_s + 60, "mean": 14.0, "max": 15.0},
                {"minute_epoch_s": self.start_s + 120, "mean": 17.0, "max": 18.0},
                {"minute_epoch_s": self.start_s + 180, "mean": 25.0, "max": 30.0},
            ]
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].onset_epoch_s, self.start_s + 60)
        self.assertIn("container CPU load", events[0].reason_candidates)
        self.assertEqual(facts[0].onset_epoch_s, self.start_s + 60)
        self.assertEqual(facts[0].observed, 14.0)

    def test_same_component_keeps_distinct_onset_clusters(self) -> None:
        signal = "container_fs_reads_MB./dev/vda"
        rows = []
        for minute, delta in ((1, 8.0), (2, 9.0), (3, 0.0), (8, 10.0), (9, 12.0)):
            rows.append(
                {
                    "minute_epoch_s": self.start_s + minute * 60,
                    "signal": signal,
                    "mean": 100.0 + delta,
                    "min": 100.0 + delta,
                    "max": 100.0 + delta,
                    "delta": delta,
                    "baseline_mean": 100.0,
                    "baseline_std": 2.0,
                    "baseline_delta_mean": 0.0,
                    "baseline_delta_std": 1.0,
                }
            )
        events, facts = self._detect(rows)
        self.assertEqual(len(events), 2)
        self.assertEqual({event.component for event in events}, {"checkoutservice-0"})
        self.assertEqual(
            {event.onset_epoch_s for event in events},
            {self.start_s + 60, self.start_s + 8 * 60},
        )
        self.assertEqual(len({event.event_id for event in events}), 2)
        self.assertEqual(len(facts), 2)

    def test_zero_variance_baseline_and_output_are_deterministic(self) -> None:
        rows = [
            {
                "minute_epoch_s": self.start_s + 60,
                "signal": "container_network_receive_packets_dropped.eth0",
                "mean": 1.0,
                "min": 1.0,
                "max": 1.0,
                "delta": 1.0,
                "baseline_mean": 0.0,
                "baseline_std": 0.0,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": float("nan"),
            },
            {
                "minute_epoch_s": self.start_s + 120,
                "signal": "container_network_receive_packets_dropped.eth0",
                "mean": 3.0,
                "min": 3.0,
                "max": 3.0,
                "delta": 2.0,
                "baseline_mean": 0.0,
                "baseline_std": 0.0,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 0.0,
            },
        ]
        expected = self._detect(rows)
        actual = detect_metrics(
            PreparedFixture(
                self.spec,
                self._frame(rows).sample(frac=1.0, random_state=12).reset_index(drop=True),
            )
        )
        self.assertEqual(actual, expected)
        events, facts = actual
        self.assertEqual(events[0].reason_candidates[0], "container packet loss")
        self.assertTrue(events[0].event_id)
        self.assertEqual(set(events[0].supporting_fact_ids), {facts[0].fact_id})

    def test_strong_single_minute_node_cpu_spike_is_not_called_sustained_load(self) -> None:
        events, _ = self._detect(
            [
                {
                    "source_file": "telemetry/2022_03_20/metric/metric_node.csv",
                    "source_kind": "node",
                    "component": "node-3",
                    "signal": "system.cpu.pct_usage",
                    "minute_epoch_s": self.start_s + 60,
                    "mean": 20.0,
                    "min": 9.0,
                    "max": 95.0,
                    "delta": 80.0,
                    "baseline_mean": 10.0,
                    "baseline_std": 2.0,
                    "baseline_delta_mean": 0.0,
                    "baseline_delta_std": 1.0,
                }
            ]
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].reason_candidates, ("node CPU spike",))
        self.assertIn(("component_level", 1.0), events[0].feature_scores)

    def test_abrupt_direct_node_io_rate_uses_delta_without_lowering_all_gauges(self) -> None:
        events, facts = self._detect(
            [
                {
                    "source_file": "telemetry/2022_03_21/metric/metric_node.csv",
                    "source_kind": "node",
                    "component": "node-6",
                    "signal": "system.io.w_s",
                    "minute_epoch_s": self.start_s + 9 * 60,
                    "mean": 502.5,
                    "min": 502.5,
                    "max": 502.5,
                    "delta": 502.5,
                    "baseline_mean": 8.0,
                    "baseline_std": 187.60,
                    "baseline_delta_mean": 0.0,
                    "baseline_delta_std": 75.98,
                }
            ]
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].reason_candidates, ("node disk write I/O consumption",)
        )
        self.assertEqual(events[0].onset_epoch_s, self.start_s + 9 * 60)
        self.assertEqual(facts[0].observed, 502.5)
        self.assertEqual(facts[0].baseline, 0.0)
        self.assertIn("delta", facts[0].method)

    def test_persistent_io_context_outweighs_a_larger_single_impulse(self) -> None:
        rows = [
            # Large, uncorroborated read impulse.  The equivalent byte-rate
            # signal is just below the event threshold and await peaked before
            # this onset, so neither is independent post-onset support.
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-1",
                "signal": "system.io.r_await",
                "minute_epoch_s": self.start_s + 4 * 60,
                "mean": 30.0,
                "min": 30.0,
                "max": 30.0,
                "delta": 0.0,
                "baseline_mean": 0.0,
                "baseline_std": 5.0,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-1",
                "signal": "system.io.r_s",
                "minute_epoch_s": self.start_s + 5 * 60,
                "mean": 989.0,
                "min": 989.0,
                "max": 989.0,
                "delta": 989.0,
                "baseline_mean": 0.0,
                "baseline_std": 42.68,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 42.68,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-1",
                "signal": "system.io.r_s",
                "minute_epoch_s": self.start_s + 6 * 60,
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
                "delta": -989.0,
                "baseline_mean": 0.0,
                "baseline_std": 42.68,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 42.68,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-1",
                "signal": "system.io.rkb_s",
                "minute_epoch_s": self.start_s + 5 * 60,
                "mean": 59.99,
                "min": 59.99,
                "max": 59.99,
                "delta": 59.99,
                "baseline_mean": 0.0,
                "baseline_std": 10.0,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 10.0,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-1",
                "signal": "system.io.rkb_s",
                "minute_epoch_s": self.start_s + 6 * 60,
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
                "delta": -59.99,
                "baseline_mean": 0.0,
                "baseline_std": 10.0,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 10.0,
            },
            # Smaller write deviation with two kinds of post-onset context:
            # the throughput level persists and its directional await rises.
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-6",
                "signal": "system.io.w_s",
                "minute_epoch_s": self.start_s + 9 * 60,
                "mean": 502.5,
                "min": 502.5,
                "max": 502.5,
                "delta": 502.5,
                "baseline_mean": 8.0,
                "baseline_std": 187.6,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 76.0,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-6",
                "signal": "system.io.w_s",
                "minute_epoch_s": self.start_s + 10 * 60,
                "mean": 171.0,
                "min": 171.0,
                "max": 171.0,
                "delta": -331.5,
                "baseline_mean": 8.0,
                "baseline_std": 187.6,
                "baseline_delta_mean": 0.0,
                "baseline_delta_std": 76.0,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-6",
                "signal": "system.io.w_await",
                "minute_epoch_s": self.start_s + 9 * 60,
                "mean": 30.32,
                "min": 30.32,
                "max": 30.32,
                "delta": 0.0,
                "baseline_mean": 0.5,
                "baseline_std": 18.6,
            },
            {
                "source_file": "metric_node.csv",
                "source_kind": "node",
                "component": "node-6",
                "signal": "system.io.w_await",
                "minute_epoch_s": self.start_s + 10 * 60,
                "mean": 49.88,
                "min": 49.88,
                "max": 49.88,
                "delta": 0.0,
                "baseline_mean": 0.5,
                "baseline_std": 18.6,
            },
        ]
        events, facts = self._detect(rows)
        by_component = {event.component: event for event in events}
        self.assertEqual(set(by_component), {"node-1", "node-6"})

        impulse = by_component["node-1"]
        supported = by_component["node-6"]
        impulse_features = dict(impulse.feature_scores)
        supported_features = dict(supported.feature_scores)
        self.assertGreater(impulse_features["raw_magnitude"], 20.0)
        self.assertEqual(impulse_features["magnitude"], 6.0)
        self.assertEqual(impulse_features["isolated_impulse"], 1.0)
        self.assertGreater(supported_features["post_onset_persistence"], 0.2)
        self.assertGreater(supported_features["related_signal_support"], 1.5)
        self.assertEqual(supported_features["cross_signal"], 2.0)
        self.assertEqual(supported_features["sustained_minutes"], 2.0)
        self.assertEqual(supported_features["isolated_impulse"], 0.0)
        self.assertGreater(supported.score, impulse.score)
        self.assertEqual(rank_events(events, failure_count=1)[0].component, "node-6")
        self.assertEqual(len(supported.supporting_fact_ids), 3)
        self.assertTrue(set(supported.supporting_fact_ids) <= {fact.fact_id for fact in facts})

    def test_service_metrics_do_not_originate_root_candidates(self) -> None:
        events, facts = self._detect(
            [
                {
                    "source_kind": "service",
                    "component": "checkoutservice",
                    "signal": "latency_ms",
                    "minute_epoch_s": self.start_s + 60,
                    "mean": 1000.0,
                    "max": 1200.0,
                    "baseline_mean": 10.0,
                    "baseline_std": 1.0,
                },
                {
                    "source_kind": "service",
                    "component": "checkoutservice",
                    "signal": "latency_ms",
                    "minute_epoch_s": self.start_s + 120,
                    "mean": 1100.0,
                    "max": 1300.0,
                    "baseline_mean": 10.0,
                    "baseline_std": 1.0,
                },
            ]
        )
        self.assertEqual(events, ())
        self.assertEqual(facts, ())

    def test_reason_aware_signal_mapping_covers_all_legal_metric_reasons(self) -> None:
        cases = (
            ("container", "container_cpu_load_average_10s", "container CPU load", 1),
            ("container", "container_memory_working_set_MB", "container memory load", 1),
            ("container", "network_latency_ms", "container network latency", 1),
            ("container", "container_network_receive_errors.eth0", "container network packet corruption", 1),
            ("container", "container_network_tcp_retransmits", "container network packet retransmission", 1),
            ("container", "container_network_receive_packets_dropped.eth0", "container packet loss", 1),
            ("container", "container_tasks_state.stopped", "container process termination", 1),
            ("container", "container_fs_reads_MB./dev/vda", "container read I/O load", 1),
            ("container", "container_fs_writes_MB./dev/vda", "container write I/O load", 1),
            ("node", "system.cpu.pct_usage", "node CPU load", 1),
            ("node", "system.cpu.pct_usage", "node CPU spike", 1),
            ("node", "system.io.rkb_s", "node disk read I/O consumption", 1),
            ("node", "system.disk.free", "node disk space consumption", -1),
            ("node", "system.io.w_s", "node disk write I/O consumption", 1),
            ("node", "system.mem.free", "node memory consumption", -1),
        )
        for index, (kind, signal, reason, direction) in enumerate(cases):
            with self.subTest(reason=reason):
                source = "metric_node.csv" if kind == "node" else "metric_container.csv"
                component = "node-2" if kind == "node" else "cartservice-1"
                baseline = 100.0
                means = (60.0, 50.0) if direction < 0 else (140.0, 150.0)
                rows = []
                for minute, mean in enumerate(means, 1):
                    delta = -40.0 if direction < 0 else 40.0
                    rows.append(
                        {
                            "source_file": source,
                            "source_kind": kind,
                            "component": component,
                            "signal": signal,
                            "minute_epoch_s": self.start_s + minute * 60,
                            "mean": mean,
                            "min": mean,
                            "max": mean,
                            "delta": delta,
                            "baseline_mean": baseline,
                            "baseline_std": 5.0,
                            "baseline_delta_mean": 0.0,
                            "baseline_delta_std": 1.0,
                        }
                    )
                events, facts = self._detect(rows)
                found = {candidate for event in events for candidate in event.reason_candidates}
                self.assertIn(reason, found)
                self.assertTrue(facts)
                self.assertTrue(
                    all(event.modality == "metric" and event.event_id for event in events)
                )

    def test_nonempty_malformed_frame_names_missing_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "baseline_std"):
            detect_metrics(
                PreparedFixture(
                    self.spec,
                    pd.DataFrame(
                        [{"source_file": "metric_container.csv", "mean": 1.0}]
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()
