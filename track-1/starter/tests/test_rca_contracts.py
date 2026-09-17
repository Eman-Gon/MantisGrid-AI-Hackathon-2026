from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
import unittest

import pandas as pd


STARTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STARTER))

from rca.contracts import (  # noqa: E402
    CandidateEvent,
    REQUESTED_FIELDS,
    UTC_PLUS_8,
    format_utc8,
    parse_case,
    parse_cases,
)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = STARTER.parent / "data" / "Market-cloudbed-1"

    def test_all_dev_queries_parse_without_reading_answers(self) -> None:
        path = self.bundle / "query.csv"
        if not path.exists():
            self.skipTest("development bundle is not mounted")
        query = pd.read_csv(path)
        cases = parse_cases(query)
        self.assertEqual(len(cases), 70)
        self.assertEqual(sum(case.failure_count == 2 for case in cases), 26)
        self.assertEqual(sum(case.failure_count == 1 for case in cases), 44)
        for case in cases:
            self.assertEqual(case.requested_fields, REQUESTED_FIELDS[case.task_type])
            self.assertEqual(case.window_utc8[0].tzinfo, UTC_PLUS_8)
            self.assertEqual(
                case.window_utc8[1] - case.window_utc8[0], timedelta(minutes=30)
            )

    def test_canary_counts_and_fields(self) -> None:
        path = self.bundle / "query.csv"
        if not path.exists():
            self.skipTest("development bundle is not mounted")
        query = pd.read_csv(path).set_index("row_id")
        cases = {
            row_id: parse_case(
                row_id,
                query.loc[row_id, "task_index"],
                query.loc[row_id, "instruction"],
            )
            for row_id in (27, 38, 56)
        }
        self.assertEqual(cases[27].failure_count, 2)
        self.assertEqual(cases[38].failure_count, 1)
        self.assertEqual(cases[56].failure_count, 2)
        self.assertTrue(
            all(case.requested_fields == ("datetime", "component", "reason")
                for case in cases.values())
        )

    def test_explicit_and_implicit_midnight_windows(self) -> None:
        explicit = parse_case(
            1,
            "task_7",
            "One failure from March 20, 2022, 23:30 to March 21, 2022, at 00:00.",
        )
        implicit = parse_case(
            2,
            "task_7",
            "One failure on March 20, 2022, from 23:30 to 00:00.",
        )
        for case in (explicit, implicit):
            self.assertEqual(case.window_utc8[1] - case.window_utc8[0], timedelta(minutes=30))
            self.assertEqual(format_utc8(case.start_epoch_s), "2022-03-20 23:30:00")
            self.assertEqual(format_utc8(case.end_epoch_s), "2022-03-21 00:00:00")

    def test_same_component_can_represent_two_distinct_events(self) -> None:
        first = CandidateEvent(
            component="shippingservice-1",
            onset_epoch_s=100.0,
            score=8.0,
            reason_candidates=("container read I/O load",),
            supporting_fact_ids=("fact-1",),
            event_id="event-1",
        )
        second = CandidateEvent(
            component="shippingservice-1",
            onset_epoch_s=500.0,
            score=7.0,
            reason_candidates=("container write I/O load",),
            supporting_fact_ids=("fact-2",),
            event_id="event-2",
        )
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(first.component, second.component)


if __name__ == "__main__":
    unittest.main()
