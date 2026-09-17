"""Shared, answer-free contracts for the RCA pipeline.

The judged query describes the number of independent failure *events*.  Events are
therefore first-class throughout the pipeline: a case never collapses its failures
into one component-level answer, and two events may name the same component.

All internal timestamps are Unix epoch seconds.  Query prose and final answer text
use the benchmark's fixed UTC+8 wall clock and are converted only at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence


UTC_PLUS_8 = timezone(timedelta(hours=8), name="UTC+8")

REQUESTED_FIELDS: dict[str, tuple[str, ...]] = {
    "task_1": ("datetime",),
    "task_2": ("reason",),
    "task_3": ("component",),
    "task_4": ("datetime", "reason"),
    "task_5": ("datetime", "component"),
    "task_6": ("component", "reason"),
    "task_7": ("datetime", "component", "reason"),
}

LEGAL_REASONS: tuple[str, ...] = (
    "container CPU load",
    "container memory load",
    "container network latency",
    "container network packet corruption",
    "container network packet retransmission",
    "container packet loss",
    "container process termination",
    "container read I/O load",
    "container write I/O load",
    "node CPU load",
    "node CPU spike",
    "node disk read I/O consumption",
    "node disk space consumption",
    "node disk write I/O consumption",
    "node memory consumption",
)
LEGAL_REASON_SET = frozenset(LEGAL_REASONS)

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "single": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


class CaseParseError(ValueError):
    """Raised when the load-bearing fields of a query cannot be parsed."""


@dataclass(frozen=True, slots=True)
class CaseSpec:
    row_id: int | str
    task_type: str
    failure_count: int
    requested_fields: tuple[str, ...]
    window_utc8: tuple[datetime, datetime]
    window_epoch_s: tuple[float, float]

    def __post_init__(self) -> None:
        if self.task_type not in REQUESTED_FIELDS:
            raise ValueError(f"unknown task type: {self.task_type!r}")
        if self.failure_count < 1:
            raise ValueError("failure_count must be positive")
        if tuple(self.requested_fields) != REQUESTED_FIELDS[self.task_type]:
            raise ValueError("requested_fields do not match task_type")
        lo, hi = self.window_utc8
        lo_s, hi_s = self.window_epoch_s
        if lo.tzinfo is None or hi.tzinfo is None:
            raise ValueError("window_utc8 must contain timezone-aware datetimes")
        if hi <= lo or hi_s <= lo_s:
            raise ValueError("query window must have positive duration")
        if abs(lo.timestamp() - lo_s) > 1e-6 or abs(hi.timestamp() - hi_s) > 1e-6:
            raise ValueError("wall-clock and epoch query windows disagree")

    @property
    def start_epoch_s(self) -> float:
        return self.window_epoch_s[0]

    @property
    def end_epoch_s(self) -> float:
        return self.window_epoch_s[1]


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    fact_id: str
    source_file: str
    locator: str
    component: str
    signal: str
    onset_epoch_s: float
    observed: float | int | str | None
    baseline: float | int | str | None
    method: str

    def __post_init__(self) -> None:
        if not self.fact_id or not self.source_file or not self.locator:
            raise ValueError("evidence facts require an id, source, and locator")
        if not self.component or not self.signal or not self.method:
            raise ValueError("evidence facts require component, signal, and method")


@dataclass(frozen=True, slots=True)
class CandidateEvent:
    component: str
    onset_epoch_s: float
    score: float
    reason_candidates: tuple[str, ...]
    supporting_fact_ids: tuple[str, ...]
    alternatives: tuple[str, ...] = ()
    # Deterministic detector metadata.  These optional fields do not alter the
    # frozen public fields above, but let the ranker explain its tie-breaks.
    modality: str = ""
    event_id: str = ""
    feature_scores: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.component:
            raise ValueError("candidate component may not be empty")
        illegal = set(self.reason_candidates) - LEGAL_REASON_SET
        if illegal:
            raise ValueError(f"illegal reason candidate(s): {sorted(illegal)}")
        if not self.reason_candidates:
            raise ValueError("candidate requires at least one legal reason")


@dataclass(frozen=True, slots=True)
class Hypothesis:
    component: str
    reason_enum: str
    onset_epoch_s: float
    fact_ids: tuple[str, ...]
    confidence: float
    model_used: str

    def __post_init__(self) -> None:
        if self.reason_enum not in LEGAL_REASON_SET:
            raise ValueError(f"illegal reason: {self.reason_enum!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class CaseResult:
    hypotheses: tuple[Hypothesis, ...]
    usage: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def parse_failure_count(instruction: str) -> int:
    """Extract the stated event count without consulting model output.

    The benchmark prose alternates among "one failure", "a single failure",
    "a failure", and their plural forms.  Explicit counts win; the singular
    article is accepted only when no explicit count is present.
    """

    lowered = " ".join(str(instruction).lower().split())
    match = re.search(
        r"\b(zero|one|single|two|three|four|five|\d+)\s+"
        r"(?:known\s+)?failures?\b",
        lowered,
    )
    if match:
        token = match.group(1)
        count = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, -1)
        if count > 0:
            return count
        raise CaseParseError(f"invalid stated failure count: {token!r}")
    if re.search(r"\b(?:a|the)\s+(?:single\s+)?failure\b", lowered):
        return 1
    raise CaseParseError("instruction does not state a failure count")


def parse_window_utc8(instruction: str) -> tuple[datetime, datetime]:
    """Parse a benchmark prose window as UTC+8, including midnight crossings."""

    text = " ".join(str(instruction).split())
    dates = list(_DATE_RE.finditer(text))
    if not dates:
        raise CaseParseError("instruction has no calendar date")

    first_date = dates[0]
    times = [m for m in _TIME_RE.finditer(text) if m.start() > first_date.end()]
    if len(times) < 2:
        raise CaseParseError("instruction has fewer than two clock times")
    start_clock, end_clock = times[0], times[1]

    def date_parts(match: re.Match[str]) -> tuple[int, int, int]:
        month, day, year = match.groups()
        return int(year), _MONTHS[month.lower()], int(day)

    start_year, start_month, start_day = date_parts(first_date)
    explicit_end_date = next(
        (
            match
            for match in dates[1:]
            if start_clock.end() <= match.start() <= end_clock.start()
        ),
        None,
    )
    if explicit_end_date is None:
        end_year, end_month, end_day = start_year, start_month, start_day
    else:
        end_year, end_month, end_day = date_parts(explicit_end_date)

    lo = datetime(
        start_year,
        start_month,
        start_day,
        int(start_clock.group(1)),
        int(start_clock.group(2)),
        tzinfo=UTC_PLUS_8,
    )
    hi = datetime(
        end_year,
        end_month,
        end_day,
        int(end_clock.group(1)),
        int(end_clock.group(2)),
        tzinfo=UTC_PLUS_8,
    )
    if hi <= lo and explicit_end_date is None:
        hi += timedelta(days=1)
    if hi <= lo:
        raise CaseParseError("parsed end of window is not after its start")
    return lo, hi


def parse_case(
    row_id: int | str,
    task_type: str,
    instruction: str,
) -> CaseSpec:
    task = str(task_type).strip().lower()
    if task not in REQUESTED_FIELDS:
        raise CaseParseError(f"unsupported task type: {task_type!r}")
    lo, hi = parse_window_utc8(instruction)
    return CaseSpec(
        row_id=row_id,
        task_type=task,
        failure_count=parse_failure_count(instruction),
        requested_fields=REQUESTED_FIELDS[task],
        window_utc8=(lo, hi),
        window_epoch_s=(lo.timestamp(), hi.timestamp()),
    )


def parse_cases(rows: Any) -> tuple[CaseSpec, ...]:
    """Parse a DataFrame or iterable of mappings; ignore answer-key columns."""

    if hasattr(rows, "to_dict"):
        records: Iterable[Any] = rows.to_dict("records")
    else:
        records = rows
    parsed: list[CaseSpec] = []
    seen: set[int | str] = set()
    for row in records:
        if isinstance(row, Mapping):
            get = row.__getitem__
        else:
            get = lambda key, obj=row: getattr(obj, key)
        case = parse_case(get("row_id"), get("task_index"), get("instruction"))
        if case.row_id in seen:
            raise CaseParseError(f"duplicate row_id: {case.row_id!r}")
        seen.add(case.row_id)
        parsed.append(case)
    if not parsed:
        raise CaseParseError("query set is empty")
    return tuple(parsed)


def stable_fact_id(row_id: int | str, *parts: object) -> str:
    """Return a short, deterministic fact id without leaking arbitrary text."""

    payload = "\x1f".join(str(part) for part in (row_id, *parts)).encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:12]
    return f"fact-{row_id}-{digest}"


def stable_event_id(row_id: int | str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in (row_id, *parts)).encode("utf-8")
    return f"event-{row_id}-{hashlib.sha1(payload).hexdigest()[:12]}"


def format_utc8(epoch_s: float) -> str:
    return datetime.fromtimestamp(float(epoch_s), tz=UTC_PLUS_8).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def require_hypothesis_count(case: CaseSpec, hypotheses: Sequence[Hypothesis]) -> None:
    if len(hypotheses) != case.failure_count:
        raise ValueError(
            f"case {case.row_id!r} requires {case.failure_count} hypotheses, "
            f"got {len(hypotheses)}"
        )
