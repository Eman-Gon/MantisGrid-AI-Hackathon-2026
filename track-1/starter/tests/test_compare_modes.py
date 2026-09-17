from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

TRACK_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TRACK_DIR / "scripts"))

from compare_modes import (
    MODES,
    ComparisonError,
    compare_modes,
    summarize_mode,
)


def _prediction(component: str, reason: str | None = None) -> str:
    answer = {"root cause component": component}
    if reason is not None:
        answer["root cause reason"] = reason
    return json.dumps({"1": answer})


class CompareModesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"
        self.queries = self.root / "queries.csv"
        pd.DataFrame(
            [
                {
                    "row_id": 1,
                    "task_index": "task_3",
                    "instruction": "one failure",
                    "scoring_points": (
                        "The only predicted root cause component is service-a"
                    ),
                },
                {
                    "row_id": 2,
                    "task_index": "task_6",
                    "instruction": "one failure",
                    "scoring_points": (
                        "The only predicted root cause component is service-b\n"
                        "The only predicted root cause reason is container CPU load"
                    ),
                },
            ]
        ).to_csv(self.queries, index=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_mode(
        self,
        mode: str,
        predictions: tuple[str, str],
        *,
        failed_row: int | None = None,
    ) -> Path:
        run = self.runs / mode
        (run / "cache").mkdir(parents=True)
        pd.DataFrame(
            [
                {"row_id": 1, "prediction": predictions[0]},
                {"row_id": 2, "prediction": predictions[1]},
            ]
        ).to_csv(run / "predictions.csv", index=False)

        if mode == "rules":
            models = {}
        elif mode == "strong":
            models = {
                "zai-org/GLM-5.2": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 0,
                    "calls": 1,
                }
            }
        else:
            models = {
                "zai-org/GLM-4.7-Flash": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 0,
                    "calls": 1,
                }
            }
        usage = [
            {
                "row_id": 1,
                "wall_s": 99,
                "solve_wall_s": 98.5,
                "prepare_share_s": 0.5,
                "models": models,
            },  # stale record
            {
                "row_id": 1,
                "wall_s": 2,
                "solve_wall_s": 1.5,
                "prepare_share_s": 0.5,
                "models": models,
            },
            {
                "row_id": 2,
                "wall_s": 3,
                "solve_wall_s": 2.5,
                "prepare_share_s": 0.5,
                "models": models,
            },
        ]
        (run / "usage.jsonl").write_text(
            "\n".join(json.dumps(record) for record in usage) + "\n",
            encoding="utf-8",
        )

        routing = []
        for row_id in (1, 2):
            failed = row_id == failed_row
            routing.append(
                {
                    "row_id": row_id,
                    "policy": mode,
                    "route": "deterministic-fallback" if failed else mode,
                    "used_fallback": failed,
                    "cheap_valid": False if failed else None,
                    "strong_valid": True if failed else None,
                }
            )
        (run / "routing.jsonl").write_text(
            "\n".join(json.dumps(record) for record in routing) + "\n",
            encoding="utf-8",
        )
        (run / "cache" / "preparation_manifest.json").write_text(
            json.dumps({"preparation_wall_s": 1.0}) + "\n",
            encoding="utf-8",
        )
        return run

    def _write_all_modes(self) -> None:
        correct_first = _prediction("service-a")
        wrong_first = _prediction("wrong-service")
        correct_second = _prediction("service-b", "container CPU load")
        partial_second = _prediction("service-b", "container memory load")
        wrong_second = _prediction("wrong-service", "container memory load")
        self._write_mode("rules", (correct_first, wrong_second))
        self._write_mode("cheap", (correct_first, correct_second))
        self._write_mode("strong", (wrong_first, partial_second))
        self._write_mode(
            "routed", (correct_first, partial_second), failed_row=2
        )

    def test_comparison_uses_scores_costs_shared_preparation_and_diagnostics(self) -> None:
        self._write_all_modes()

        summaries = {item.mode: item for item in compare_modes(self.runs, self.queries)}

        self.assertEqual(tuple(summaries), MODES)
        self.assertEqual(summaries["rules"].strict_solved, 1)
        self.assertEqual(summaries["rules"].strict_accuracy, 0.5)
        self.assertEqual(summaries["rules"].mean_accuracy, 0.5)
        self.assertEqual(summaries["rules"].average_cost_usd, 0.0)
        # Stale wall_s=99 is ignored, and inclusive wall_s is not charged twice.
        self.assertEqual(summaries["rules"].average_runtime_s, 2.5)
        self.assertEqual(summaries["rules"].average_solve_runtime_s, 2.0)
        self.assertEqual(summaries["cheap"].strict_accuracy, 1.0)
        self.assertAlmostEqual(summaries["cheap"].average_cost_usd, 0.000065)
        self.assertEqual(summaries["routed"].diagnostic_failures, 1)
        self.assertEqual(summaries["routed"].failure_rate, 0.5)

    def test_missing_preparation_time_is_reported_as_unavailable_not_zero(self) -> None:
        run = self._write_mode(
            "rules",
            (_prediction("service-a"), _prediction("service-b", "container CPU load")),
        )
        (run / "cache" / "preparation_manifest.json").unlink()
        records = []
        for line in (run / "usage.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            record.pop("solve_wall_s")
            record.pop("prepare_share_s")
            records.append(record)
        (run / "usage.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        query = pd.read_csv(self.queries).set_index("row_id", drop=False)

        summary, _ = summarize_mode("rules", run, query)

        self.assertIsNone(summary.total_runtime_s)
        self.assertIsNone(summary.average_runtime_s)
        self.assertTrue(any("runtime is unavailable" in item for item in summary.warnings))

    def test_mismatched_case_sets_are_rejected(self) -> None:
        self._write_all_modes()
        cheap_predictions = pd.read_csv(self.runs / "cheap" / "predictions.csv")
        cheap_predictions.head(1).to_csv(
            self.runs / "cheap" / "predictions.csv", index=False
        )

        with self.assertRaisesRegex(ComparisonError, "did not run the same cases"):
            compare_modes(self.runs, self.queries)


if __name__ == "__main__":
    unittest.main()
