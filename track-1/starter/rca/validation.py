"""Strict validation for model-selected root-cause hypotheses.

Models never get authority to create observations.  They may only select an
event already emitted by the deterministic detectors, choose one of that
event's legal reasons, and cite facts attached to that event.  This module is
the boundary that enforces that contract.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import (
    LEGAL_REASON_SET,
    CandidateEvent,
    CaseSpec,
    EvidenceFact,
    Hypothesis,
    require_hypothesis_count,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class ModelOutputError(ValueError):
    """Raised when a model reply cannot be grounded in the candidate packet."""

    def __init__(self, issues: Iterable[ValidationIssue | str]) -> None:
        materialized = tuple(
            issue if isinstance(issue, ValidationIssue) else ValidationIssue("$", issue)
            for issue in issues
        )
        self.issues = materialized
        super().__init__("; ".join(str(issue) for issue in materialized))


def extract_json_object(text: str) -> Mapping[str, Any]:
    """Extract exactly one top-level JSON object, allowing prose or fences around it."""

    source = str(text).strip()
    decoder = json.JSONDecoder()
    starts = [index for index, character in enumerate(source) if character == "{"]
    decoded: list[Mapping[str, Any]] = []
    for start in starts:
        try:
            value, _ = decoder.raw_decode(source[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            decoded.append(value)
            break
    if not decoded:
        raise ModelOutputError((ValidationIssue("$", "reply contains no JSON object"),))
    return decoded[0]


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _component_kind_matches(component: str, reason: str) -> bool:
    if component.startswith("node-"):
        return reason.startswith("node ")
    return reason.startswith("container ")


def verified_component_choices(
    candidate: CandidateEvent,
    facts: Mapping[str, EvidenceFact] | Sequence[EvidenceFact],
    telemetry_components: Iterable[str],
) -> tuple[str, ...]:
    """Return the detected component plus trace-proven opposite endpoints.

    Trace latency establishes an abnormal edge, but the failing endpoint may be
    either side.  ``detect_traces`` records the child as the candidate and the
    exact joined parent in ``alternatives``.  An alternative becomes selectable
    only when a supporting fact's locator names that exact parent/child pair and
    both names exist in telemetry.  Metric ``alternatives`` are reason labels,
    so they cannot pass this check.
    """

    fact_by_id = (
        dict(facts)
        if isinstance(facts, Mapping)
        else {fact.fact_id: fact for fact in facts}
    )
    component_set = frozenset(str(component) for component in telemetry_components)
    choices = [candidate.component]
    modalities = frozenset(
        item.strip()
        for item in str(candidate.modality).replace(",", "+").split("+")
        if item.strip()
    )
    if "trace" not in modalities:
        return tuple(choices)
    alternative_set = frozenset(candidate.alternatives)

    def service_base(component: str) -> str | None:
        match = re.fullmatch(r"(.+)-(\d+)", component)
        return match.group(1) if match else None

    for fact_id in candidate.supporting_fact_ids:
        if fact_id not in fact_by_id:
            continue
        locator_fields = {}
        for field in fact_by_id[fact_id].locator.split(";"):
            if "=" in field:
                key, value = field.split("=", 1)
                locator_fields[key] = value
        parent = locator_fields.get("parent_component")
        child = locator_fields.get("child_component")
        if not parent or not child:
            continue
        child_matches_event = (
            child == candidate.component
            or child in alternative_set
            or service_base(child) == candidate.component
        )
        if child_matches_event and child in component_set:
            choices.append(child)
        if (
            child_matches_event
            and parent in alternative_set
            and parent in component_set
        ):
            choices.append(parent)
    return tuple(dict.fromkeys(choices))


def validate_model_selection(
    payload: Mapping[str, Any],
    *,
    case: CaseSpec,
    candidates: Sequence[CandidateEvent],
    facts: Mapping[str, EvidenceFact] | Sequence[EvidenceFact],
    telemetry_components: Iterable[str],
    model_used: str,
    onset_tolerance_s: float = 60.0,
) -> tuple[Hypothesis, ...]:
    """Validate and convert one model selection.

    The reply schema is deliberately narrow::

        {"answers": [{"event_id": "...", "component": "...",
          "reason": "...", "onset_epoch_s": 123.0,
          "fact_ids": ["..."], "confidence": 0.0}]}

    ``event_id`` makes repeated failures on the same component unambiguous.
    Timestamps may differ from the detector onset by at most one telemetry
    sampling minute, but are clamped back to the verified detector onset in the
    resulting hypothesis.
    """

    issues: list[ValidationIssue] = []
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise ModelOutputError((ValidationIssue("$.answers", "must be a list"),))
    if len(answers) != case.failure_count:
        issues.append(
            ValidationIssue(
                "$.answers",
                f"requires exactly {case.failure_count} answer(s), got {len(answers)}",
            )
        )

    event_by_id = {
        candidate.event_id: candidate for candidate in candidates if candidate.event_id
    }
    fact_by_id = (
        dict(facts)
        if isinstance(facts, Mapping)
        else {fact.fact_id: fact for fact in facts}
    )
    component_set = frozenset(str(component) for component in telemetry_components)
    seen_events: set[str] = set()
    hypotheses: list[Hypothesis] = []

    for index, answer in enumerate(answers):
        path = f"$.answers[{index}]"
        if not isinstance(answer, Mapping):
            issues.append(ValidationIssue(path, "must be an object"))
            continue

        event_id = answer.get("event_id")
        if not isinstance(event_id, str) or event_id not in event_by_id:
            issues.append(
                ValidationIssue(f"{path}.event_id", "is not an offered event")
            )
            continue
        if event_id in seen_events:
            issues.append(
                ValidationIssue(f"{path}.event_id", "duplicates another answer")
            )
            continue
        seen_events.add(event_id)
        candidate = event_by_id[event_id]

        component = answer.get("component")
        component_choices = verified_component_choices(
            candidate, fact_by_id, component_set
        )
        if not isinstance(component, str) or component not in component_choices:
            issues.append(
                ValidationIssue(
                    f"{path}.component",
                    f"must be one of the verified event endpoints {component_choices!r}",
                )
            )
        elif component not in component_set:
            issues.append(
                ValidationIssue(f"{path}.component", "does not exist in telemetry")
            )

        reason = answer.get("reason")
        if not isinstance(reason, str) or reason not in LEGAL_REASON_SET:
            issues.append(ValidationIssue(f"{path}.reason", "is not a legal reason"))
        elif reason not in candidate.reason_candidates:
            issues.append(
                ValidationIssue(
                    f"{path}.reason", "is not supported by the selected event"
                )
            )
        elif isinstance(component, str) and not _component_kind_matches(
            component, reason
        ):
            issues.append(
                ValidationIssue(f"{path}.reason", "does not match the component level")
            )

        onset = _finite_number(answer.get("onset_epoch_s"))
        if onset is None:
            issues.append(ValidationIssue(f"{path}.onset_epoch_s", "must be finite"))
        else:
            if not case.start_epoch_s <= onset < case.end_epoch_s:
                issues.append(
                    ValidationIssue(
                        f"{path}.onset_epoch_s", "falls outside the query window"
                    )
                )
            if abs(onset - candidate.onset_epoch_s) > onset_tolerance_s:
                issues.append(
                    ValidationIssue(
                        f"{path}.onset_epoch_s",
                        "is not within one sampling minute of the selected event",
                    )
                )

        fact_ids = answer.get("fact_ids")
        valid_fact_ids: tuple[str, ...] = ()
        if not isinstance(fact_ids, list) or not fact_ids:
            issues.append(
                ValidationIssue(f"{path}.fact_ids", "must be a non-empty list")
            )
        elif not all(isinstance(fact_id, str) for fact_id in fact_ids):
            issues.append(ValidationIssue(f"{path}.fact_ids", "must contain strings"))
        else:
            valid_fact_ids = tuple(dict.fromkeys(fact_ids))
            unknown = [
                fact_id for fact_id in valid_fact_ids if fact_id not in fact_by_id
            ]
            unsupported = [
                fact_id
                for fact_id in valid_fact_ids
                if fact_id not in candidate.supporting_fact_ids
            ]
            if unknown:
                issues.append(
                    ValidationIssue(
                        f"{path}.fact_ids", f"unknown fact id(s): {unknown}"
                    )
                )
            if unsupported:
                issues.append(
                    ValidationIssue(
                        f"{path}.fact_ids",
                        f"fact id(s) do not support the event: {unsupported}",
                    )
                )

        confidence = _finite_number(answer.get("confidence"))
        if confidence is None or not 0.0 <= confidence <= 1.0:
            issues.append(
                ValidationIssue(f"{path}.confidence", "must be between 0 and 1")
            )

        if not any(issue.path.startswith(path) for issue in issues):
            assert isinstance(component, str)
            assert isinstance(reason, str)
            assert confidence is not None
            hypotheses.append(
                Hypothesis(
                    component=component,
                    reason_enum=reason,
                    onset_epoch_s=float(candidate.onset_epoch_s),
                    fact_ids=valid_fact_ids,
                    confidence=float(confidence),
                    model_used=model_used,
                )
            )

    if issues:
        raise ModelOutputError(issues)
    require_hypothesis_count(case, hypotheses)
    return tuple(
        sorted(hypotheses, key=lambda item: (item.onset_epoch_s, item.component))
    )


__all__ = [
    "ModelOutputError",
    "ValidationIssue",
    "extract_json_object",
    "validate_model_selection",
    "verified_component_choices",
]
