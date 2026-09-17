from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import UTC_PLUS_8  # noqa: E402
from rca.prepare import (  # noqa: E402
    infer_trace_duration_scale,
    normalize_component,
    parse_mesh_components,
    prepare_run,
    service_component,
)


def _epoch(hour: int, minute: int = 0) -> int:
    return int(datetime(2022, 3, 20, hour, minute, tzinfo=UTC_PLUS_8).timestamp())


class PreparationTests(unittest.TestCase):
    def _build_bundle(self, root: Path) -> Path:
        bundle = root / "bundle"
        day = bundle / "telemetry" / "2022_03_20"
        (day / "metric").mkdir(parents=True)
        (day / "trace").mkdir(parents=True)

        metric_rows = []
        for minute, value in ((0, 1.0), (31, 50.0), (32, 55.0), (10, 2.0)):
            metric_rows.append(
                {
                    "timestamp": _epoch(8, minute),
                    "cmdb_id": "node-6.emailservice-1",
                    "kpi_name": "container_fs_reads_MB./dev/vda",
                    "value": value,
                }
            )
        # Deliberately unsorted: preparation must not seek/early-stop by timestamp.
        pd.DataFrame(metric_rows[::-1]).to_csv(
            day / "metric" / "metric_container.csv", index=False
        )
        pd.DataFrame(
            [
                {
                    "timestamp": _epoch(8, 31),
                    "cmdb_id": "node-6",
                    "kpi_name": "system.io.w_s",
                    "value": 12.0,
                },
                {
                    "timestamp": _epoch(8, 0),
                    "cmdb_id": "node-6",
                    "kpi_name": "system.io.w_s",
                    "value": 1.0,
                },
            ]
        ).to_csv(day / "metric" / "metric_node.csv", index=False)
        pd.DataFrame(
            [
                {
                    "service": "emailservice-grpc",
                    "timestamp": _epoch(8, 31),
                    "rr": 99.0,
                    "sr": 80.0,
                    "mrt": 20.0,
                    "count": 10,
                }
            ]
        ).to_csv(day / "metric" / "metric_service.csv", index=False)

        trace_rows = []
        for index in range(30):
            start_ms = (_epoch(8, 31) * 1000) + index * 100
            parent = f"p{index}"
            trace_rows.extend(
                [
                    {
                        "timestamp": start_ms,
                        "cmdb_id": "frontend-0",
                        "span_id": parent,
                        "duration": 50_000,
                        "type": "rpc",
                        "status_code": "0",
                        "operation_name": "checkout",
                        "parent_span": "",
                    },
                    {
                        "timestamp": start_ms + 10,
                        "cmdb_id": "emailservice-1",
                        "span_id": f"c{index}",
                        "duration": 20_000,
                        "type": "rpc",
                        "status_code": "13" if index == 0 else "0",
                        "operation_name": "email",
                        "parent_span": parent,
                    },
                ]
            )
        pd.DataFrame(trace_rows[::-1]).to_csv(
            day / "trace" / "trace_span.csv", index=False
        )
        return bundle

    def test_schema_component_rules(self) -> None:
        self.assertEqual(
            normalize_component("container", "node-6.adservice2-0"),
            "adservice2-0",
        )
        self.assertEqual(normalize_component("node", "node-6"), "node-6")
        self.assertEqual(normalize_component("runtime", "adservice.ts:8088"), "adservice")
        self.assertEqual(normalize_component("service", "adservice-grpc"), "adservice")
        self.assertEqual(service_component("adservice-2"), "adservice")
        self.assertEqual(service_component("adservice2-0"), "adservice2")
        self.assertIsNone(service_component("node-6"))
        self.assertEqual(
            parse_mesh_components("adservice-0.destination.frontend.adservice"),
            ("frontend", "adservice-0"),
        )
        self.assertEqual(
            parse_mesh_components("adservice-0.source.adservice.basic-tidb"),
            ("adservice-0", "basic-tidb"),
        )

    def test_duration_audit_selects_microseconds(self) -> None:
        rows = []
        for index in range(25):
            rows.append(
                {"timestamp": 1_000_000 + index * 100, "span_id": f"p{index}",
                 "parent_span": "", "duration": 50_000}
            )
            rows.append(
                {"timestamp": 1_000_010 + index * 100, "span_id": f"c{index}",
                 "parent_span": f"p{index}", "duration": 5_000}
            )
        audit = infer_trace_duration_scale(pd.DataFrame(rows))
        self.assertEqual(audit.raw_unit, "microseconds")
        self.assertEqual(audit.seconds_scale, 1e-6)
        self.assertGreater(audit.enclosure_fraction, 0.99)

    def test_prepare_scans_each_file_once_and_slices_unsorted_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = self._build_bundle(root)
            out = root / "out"
            queries = pd.DataFrame(
                [
                    {
                        "row_id": 27,
                        "task_index": "task_7",
                        "instruction": (
                            "Cloudbed experienced two failures in the time range of "
                            "March 20, 2022, from 08:30 to 09:00."
                        ),
                        "scoring_points": "must be ignored",
                    }
                ]
            )
            prepared = prepare_run(
                bundle,
                queries,
                out,
                chunk_rows=100,
                parent_cache_rows=100,
            )
            self.assertTrue(prepared.scan_counts)
            self.assertTrue(all(count == 1 for count in prepared.scan_counts.values()))
            self.assertEqual(len(prepared.scan_counts), 4)
            self.assertEqual(prepared.duration_audit.raw_unit, "microseconds")
            self.assertIn("emailservice-1", prepared.telemetry_components)
            self.assertIn("emailservice", prepared.telemetry_components)
            self.assertIn("node-6", prepared.telemetry_components)

            case = prepared.for_case(27)
            self.assertFalse(case.metric_minutes.empty)
            self.assertFalse(case.trace_minutes.empty)
            self.assertFalse(case.trace_edges.empty)
            self.assertTrue(
                case.metric_minutes.minute_epoch_s.between(_epoch(8, 30), _epoch(9)).all()
            )
            self.assertEqual(case.spec.failure_count, 2)
            self.assertNotIn("scoring_points", case.metric_minutes.columns)

            manifest = out / "cache" / "preparation_manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text())
            self.assertEqual(payload["cases"], 1)
            self.assertEqual(payload["trace_duration"]["raw_unit"], "microseconds")


if __name__ == "__main__":
    unittest.main()
