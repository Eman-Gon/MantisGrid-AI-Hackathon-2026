from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from agents import rootroute


class RootRouteAgentTests(unittest.TestCase):
    def test_prepare_degrades_to_metrics_when_trace_preparation_fails(self) -> None:
        sentinel = object()
        with tempfile.TemporaryDirectory() as directory:
            ctx = {"out_dir": Path(directory), "rootroute_llm": None}
            with patch.object(
                rootroute,
                "prepare_run",
                side_effect=(RuntimeError("bad traces"), sentinel),
            ) as mocked:
                prepared = rootroute.prepare(Path(directory), (), ctx)

        self.assertIs(prepared, sentinel)
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(mocked.call_args_list[0].kwargs["include_trace"])
        self.assertFalse(mocked.call_args_list[1].kwargs["include_trace"])
        self.assertIn("retried with metrics only", ctx["rootroute_prepare_warning"])

    def test_internal_failure_still_returns_exact_count_and_required_sections(
        self,
    ) -> None:
        instruction = (
            "The system experienced two failures on March 20, 2022, from 10:00 "
            "to 10:30. Identify the root cause component and root cause reason."
        )
        with tempfile.TemporaryDirectory() as directory:
            ctx = {
                "row_id": 990,
                "task_index": "task_6",
                "out_dir": Path(directory),
            }
            with patch.object(
                rootroute, "_solve", side_effect=RuntimeError("detector broke")
            ):
                solution = rootroute.solve(instruction, Path(directory), ctx)

            payload = json.loads(
                solution.prediction.removeprefix("```json\n").removesuffix("\n```")
            )
            self.assertEqual(len(payload), 2)
            for answer in payload.values():
                self.assertIn("root cause component", answer)
                self.assertIn("root cause reason", answer)
            for heading in (
                "## Answer",
                "## Confidence",
                "## Evidence",
                "## Ruled out",
            ):
                self.assertIn(heading, solution.evidence)
            self.assertEqual(solution.usage, {})
            diagnostic = json.loads(
                (Path(directory) / "routing.jsonl").read_text().splitlines()[-1]
            )
            self.assertEqual(diagnostic["route"], "emergency-fallback")

    def test_usage_delta_prevents_run_wide_client_double_counting(self) -> None:
        before = {
            "zai-org/GLM-4.7-Flash": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "calls": 1,
            }
        }
        after = {
            "zai-org/GLM-4.7-Flash": {
                "prompt_tokens": 175,
                "completion_tokens": 32,
                "calls": 2,
            }
        }

        self.assertEqual(
            rootroute._usage_delta(before, after),
            {
                "zai-org/GLM-4.7-Flash": {
                    "prompt_tokens": 75,
                    "completion_tokens": 12,
                    "calls": 1,
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
