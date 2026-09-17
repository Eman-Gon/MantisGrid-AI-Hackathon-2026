from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pandas as pd

STARTER = Path(__file__).resolve().parents[1]


class RunPrepareHookTests(unittest.TestCase):
    def test_prepare_sees_answer_free_queries_once_and_case_context_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            dataset = temp / "dataset"
            dataset.mkdir()
            queries = temp / "queries.csv"
            pd.DataFrame(
                [
                    {
                        "row_id": 4,
                        "task_index": "task_3",
                        "instruction": "case four",
                        "scoring_points": "secret answer four",
                    },
                    {
                        "row_id": 9,
                        "task_index": "task_2",
                        "instruction": "case nine",
                        "scoring_points": "secret answer nine",
                    },
                ]
            ).to_csv(queries, index=False)
            (temp / "fake_prepared_agent.py").write_text(
                textwrap.dedent(
                    """
                    from run import Solution, format_prediction

                    def prepare(dataset_dir, queries, ctx):
                        assert list(queries.columns) == [
                            "row_id", "task_index", "instruction"
                        ]
                        assert "scoring_points" not in queries.columns
                        return {"rows": tuple(queries.row_id.astype(int))}

                    def solve(instruction, dataset_dir, ctx):
                        assert ctx["row_id"] in ctx["prepared_run"]["rows"]
                        assert ctx["task_index"] in {"task_2", "task_3"}
                        return Solution(
                            prediction=format_prediction(
                                [{"component": f"component-{ctx['row_id']}"}]
                            ),
                            evidence="prepared hook and per-case context were available\\n",
                        )
                    """
                ),
                encoding="utf-8",
            )
            out = temp / "out"
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                (str(temp), str(STARTER), env.get("PYTHONPATH", ""))
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(STARTER / "run.py"),
                    "--dataset",
                    str(dataset),
                    "--queries",
                    str(queries),
                    "--out",
                    str(out),
                    "--agent",
                    "fake_prepared_agent",
                ],
                cwd=STARTER,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            predictions = pd.read_csv(out / "predictions.csv")
            self.assertEqual(set(predictions.row_id), {4, 9})
            self.assertIn("solve_wall_s", predictions.columns)
            self.assertIn("prepare_share_s", predictions.columns)
            usage = [
                json.loads(line)
                for line in (out / "usage.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(usage), 2)
            self.assertTrue(all("prepare_share_s" in row for row in usage))
            self.assertTrue(
                all(row["prepare_share_s"] >= 0.0 for row in usage)
            )
            self.assertTrue(
                all(row["wall_s"] >= row["solve_wall_s"] for row in usage)
            )
            self.assertTrue((out / "evidence" / "4.md").exists())
            self.assertTrue((out / "evidence" / "9.md").exists())


if __name__ == "__main__":
    unittest.main()
