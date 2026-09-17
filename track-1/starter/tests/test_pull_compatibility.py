"""Integration checks for the two agents preserved by the upstream pull."""

from __future__ import annotations

import importlib
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

import run
from rca.contracts import CandidateEvent, EvidenceFact, parse_case
from rca.evidence import render, render_evidence
from rca.routing import route_case


class PullCompatibilityTests(unittest.TestCase):
    def test_both_agents_import_and_both_evidence_apis_render(self) -> None:
        for name in ("agents.routed", "agents.rootroute"):
            agent = importlib.import_module(name)
            self.assertTrue(callable(agent.prepare))
            self.assertTrue(callable(agent.solve))

        case = parse_case(
            4,
            "task_7",
            "One failure on March 20, 2022, from 10:00 to 10:30. "
            "Identify the root cause occurrence datetime, component, and reason.",
        )
        fact = EvidenceFact(
            "fact-cpu", "metric_container.csv", "minute=10:02", "checkoutservice-0",
            "cpu_usage", case.start_epoch_s + 120, 9.0, 1.0, "fixture comparison",
        )
        candidate = CandidateEvent(
            component=fact.component,
            onset_epoch_s=fact.onset_epoch_s,
            score=9.0,
            reason_candidates=("container CPU load",),
            supporting_fact_ids=(fact.fact_id,),
            modality="metric",
            event_id="event-cpu",
            feature_scores=(("magnitude", 9.0),),
        )
        facts = {fact.fact_id: fact}
        outcome = route_case(
            case=case,
            candidates=(candidate,),
            facts=facts,
            telemetry_components=(candidate.component,),
            llm=None,
            policy="rules",
        )
        reports = (
            render(case, outcome.hypotheses, (candidate,), facts),
            render_evidence(
                case=case, outcome=outcome, candidates=(candidate,), facts=facts
            ),
        )
        for report in reports:
            for heading in ("## Answer", "## Confidence", "## Evidence", "## Ruled out"):
                self.assertIn(heading, report)
            self.assertIn(fact.source_file, report)
            self.assertIn(candidate.component, report)

    def _run_fixture(self, agent, *, completed=()):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dataset = base / "dataset"
            dataset.mkdir()
            queries = base / "queries.csv"
            pd.DataFrame([
                {"row_id": 4, "task_index": "task_3", "instruction": "case four",
                 "scoring_points": "secret answer four"},
                {"row_id": 9, "task_index": "task_2", "instruction": "case nine",
                 "scoring_points": "secret answer nine"},
            ]).to_csv(queries, index=False)
            out = base / "out"
            argv = ["run.py", "--dataset", str(dataset), "--queries", str(queries),
                    "--out", str(out), "--agent", "fixture_agent"]
            if completed:
                out.mkdir()
                pd.DataFrame([
                    {"row_id": row_id, "prediction": "previous answer", "wall_s": 0.0,
                     "prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
                    for row_id in completed
                ]).to_csv(out / "predictions.csv", index=False)
                argv.append("--resume")
            output = io.StringIO()
            with patch.object(sys, "argv", argv), patch.object(
                run.importlib, "import_module", return_value=agent
            ), redirect_stdout(output):
                run.main()
            return pd.read_csv(out / "predictions.csv"), output.getvalue()

    def _solution(self, ctx):
        self.assertNotIn("scoring_points", ctx["queries"].columns)
        return run.Solution(
            run.format_prediction([{"component": f"component-{ctx['row_id']}"}]),
            evidence="Fixture evidence.",
        )

    def test_prepare_runs_once_for_return_value_and_side_effect_agents(self) -> None:
        for style in ("returned", "side-effect"):
            with self.subTest(style=style):
                sentinel = object()
                seen_rows = []

                def prepare(dataset, queries, ctx):
                    self.assertEqual(list(queries.columns), ["row_id", "task_index", "instruction"])
                    self.assertNotIn("scoring_points", ctx["queries"].columns)
                    seen_rows.append(tuple(queries.row_id))
                    if style == "side-effect":
                        ctx["rca_prepared"] = sentinel
                        return None
                    return sentinel

                def solve(instruction, dataset, ctx):
                    key = "prepared_run" if style == "returned" else "rca_prepared"
                    self.assertIs(ctx[key], sentinel)
                    return self._solution(ctx)

                agent = SimpleNamespace(prepare=Mock(side_effect=prepare), solve=Mock(side_effect=solve))
                predictions, _ = self._run_fixture(agent)
                self.assertEqual(agent.prepare.call_count, 1)
                self.assertEqual(agent.solve.call_count, 2)
                self.assertEqual(seen_rows, [(4, 9)])
                self.assertEqual(list(predictions.row_id), [4, 9])

    def test_resume_prepares_only_pending_cases_and_skips_completed_run(self) -> None:
        for completed, expected in (((4,), (9,)), ((4, 9), ())):
            with self.subTest(completed=completed):
                prepared_rows = []

                def prepare(dataset, queries, ctx):
                    prepared_rows.extend(queries.row_id)

                agent = SimpleNamespace(
                    prepare=Mock(side_effect=prepare),
                    solve=Mock(side_effect=lambda instruction, dataset, ctx: self._solution(ctx)),
                )
                predictions, _ = self._run_fixture(agent, completed=completed)
                self.assertEqual(tuple(prepared_rows), expected)
                self.assertEqual(agent.prepare.call_count, int(bool(expected)))
                self.assertEqual(agent.solve.call_count, len(expected))
                self.assertEqual(list(predictions.row_id), [4, 9])
                for row_id in completed:
                    self.assertEqual(
                        predictions.loc[predictions.row_id == row_id, "prediction"].iloc[0],
                        "previous answer",
                    )

    def test_preparation_exception_still_allows_per_case_recovery(self) -> None:
        agent = SimpleNamespace(
            prepare=Mock(side_effect=RuntimeError("fixture preparation failed")),
            solve=Mock(side_effect=lambda instruction, dataset, ctx: self._solution(ctx)),
        )
        predictions, output = self._run_fixture(agent)
        self.assertEqual(agent.prepare.call_count, 1)
        self.assertEqual(agent.solve.call_count, 2)
        self.assertEqual(list(predictions.row_id), [4, 9])
        self.assertTrue(predictions.prediction.str.contains("component-").all())
        self.assertIn("fixture preparation failed", output)

    def test_case_exception_uses_runner_last_resort(self) -> None:
        agent = SimpleNamespace(solve=Mock(side_effect=RuntimeError("fixture solve failed")))
        fallback = run.Solution(run.format_prediction([{"component": "fallback"}]), "fallback evidence")
        with patch.object(run, "last_resort", return_value=fallback) as last_resort:
            predictions, _ = self._run_fixture(agent)
        self.assertEqual(last_resort.call_count, 2)
        self.assertTrue(predictions.prediction.str.contains("fallback").all())


if __name__ == "__main__":
    unittest.main()
