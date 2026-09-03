from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from visa_agent.domain.models import CaseProfile, InboundEvent
from visa_agent.domain.rules import CRITICAL_FACTS
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, LLMClient


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    body: str
    expected_updates: dict[str, str | int | bool] = Field(default_factory=dict)
    expects_human_review: bool = False
    expects_ambiguity: bool = False


class EvaluationCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    cases: list[EvaluationCase] = Field(min_length=1)


@dataclass(frozen=True)
class RunScore:
    schema_valid: bool
    true_positive: int
    unsupported: int
    missed: int
    critical_true_positive: int
    critical_unsupported: int
    critical_missed: int
    boundary_violation: bool
    human_review_correct: bool
    ambiguity_correct: bool
    latency_ms: float
    semantic_signature: str | None
    raw_updates: tuple[dict[str, Any], ...] = ()
    guarded_updates: tuple[dict[str, Any], ...] = ()
    guarded_ambiguities: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    error_type: str | None = None


def load_corpus(path: Path) -> EvaluationCorpus:
    return EvaluationCorpus.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _normalise(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value).strip().casefold()


def score_patch(case: EvaluationCase, raw_patch: CasePatch, event: InboundEvent) -> RunScore:
    guarded = validate_case_patch(event, raw_patch)
    raw_dump = [update.model_dump() for update in raw_patch.updates]
    guarded_dump = [update.model_dump() for update in guarded.updates]
    boundary_violation = raw_dump != guarded_dump
    predicted = {update.field: update.value for update in guarded.updates}
    semantic_signature = json.dumps(
        {
            "updates": predicted,
            "requires_human_review": guarded.requires_human_review,
            "has_ambiguity": bool(guarded.ambiguities),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    true_positive = 0
    unsupported = 0
    for field, value in predicted.items():
        expected = case.expected_updates.get(field)
        if expected is not None and _normalise(value) == _normalise(expected):
            true_positive += 1
        else:
            unsupported += 1
    missed = sum(
        1
        for field, expected in case.expected_updates.items()
        if field not in predicted or _normalise(predicted[field]) != _normalise(expected)
    )
    critical_true_positive = sum(
        1
        for field, value in predicted.items()
        if field in CRITICAL_FACTS
        and field in case.expected_updates
        and _normalise(value) == _normalise(case.expected_updates[field])
    )
    critical_unsupported = sum(
        1
        for field, value in predicted.items()
        if field in CRITICAL_FACTS
        and (
            field not in case.expected_updates
            or _normalise(value) != _normalise(case.expected_updates[field])
        )
    )
    critical_missed = sum(
        1
        for field, expected in case.expected_updates.items()
        if field in CRITICAL_FACTS
        and (field not in predicted or _normalise(predicted[field]) != _normalise(expected))
    )
    return RunScore(
        schema_valid=True,
        true_positive=true_positive,
        unsupported=unsupported,
        missed=missed,
        critical_true_positive=critical_true_positive,
        critical_unsupported=critical_unsupported,
        critical_missed=critical_missed,
        boundary_violation=boundary_violation,
        human_review_correct=guarded.requires_human_review == case.expects_human_review,
        ambiguity_correct=bool(guarded.ambiguities) == case.expects_ambiguity,
        latency_ms=0,
        semantic_signature=semantic_signature,
        raw_updates=tuple(raw_dump),
        guarded_updates=tuple(guarded_dump),
        guarded_ambiguities=tuple(guarded.ambiguities),
    )


def evaluate_extractor(
    llm: LLMClient,
    corpus: EvaluationCorpus,
    *,
    repeats: int = 3,
    on_run: Callable[[EvaluationCase, int, RunScore], None] | None = None,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least one")
    scores: list[RunScore] = []
    case_results: list[dict[str, Any]] = []
    for case in corpus.cases:
        for repeat in range(repeats):
            event = InboundEvent(
                id=f"eval-{case.id}-{repeat}",
                external_thread_id=f"eval-thread-{case.id}",
                sender="synthetic-applicant@example.test",
                subject=f"Evaluation case: {case.category}",
                body=case.body,
                received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
            )
            started = time.perf_counter()
            try:
                patch = llm.extract_case_patch(event)
                score = score_patch(case, patch, event)
                usage = getattr(llm, "last_usage", None) or {}
                score = replace(
                    score,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                )
            except Exception as error:
                score = RunScore(
                    schema_valid=False,
                    true_positive=0,
                    unsupported=0,
                    missed=len(case.expected_updates),
                    critical_true_positive=0,
                    critical_unsupported=0,
                    critical_missed=sum(field in CRITICAL_FACTS for field in case.expected_updates),
                    boundary_violation=False,
                    human_review_correct=False,
                    ambiguity_correct=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    semantic_signature=None,
                    error_type=type(error).__name__,
                )
            scores.append(score)
            case_results.append({"case_id": case.id, "repeat": repeat + 1, **score.__dict__})
            if on_run is not None:
                on_run(case, repeat + 1, score)

    total = len(scores)
    true_positive = sum(item.true_positive for item in scores)
    unsupported = sum(item.unsupported for item in scores)
    missed = sum(item.missed for item in scores)
    all_precision_denominator = true_positive + unsupported
    all_recall_denominator = true_positive + missed
    critical_true_positive = sum(item.critical_true_positive for item in scores)
    critical_unsupported = sum(item.critical_unsupported for item in scores)
    critical_missed = sum(item.critical_missed for item in scores)
    critical_precision_denominator = critical_true_positive + critical_unsupported
    critical_recall_denominator = critical_true_positive + critical_missed
    signatures_by_case = {
        case.id: {
            item.semantic_signature
            for item, result in zip(scores, case_results, strict=True)
            if result["case_id"] == case.id
        }
        for case in corpus.cases
    }
    stable_cases = sum(
        len(signatures) == 1 and None not in signatures
        for signatures in signatures_by_case.values()
    )
    return {
        "corpus_version": corpus.version,
        "model_version": getattr(llm, "version", "unknown"),
        "case_count": len(corpus.cases),
        "repeat_count": repeats,
        "run_count": total,
        "metrics": {
            "schema_valid_rate": sum(item.schema_valid for item in scores) / total,
            "critical_field_precision": (
                critical_true_positive / critical_precision_denominator
                if critical_precision_denominator
                else 1.0
            ),
            "critical_field_recall": (
                critical_true_positive / critical_recall_denominator
                if critical_recall_denominator
                else 1.0
            ),
            "all_field_precision": true_positive / all_precision_denominator
            if all_precision_denominator
            else 1.0,
            "all_field_recall": true_positive / all_recall_denominator
            if all_recall_denominator
            else 1.0,
            "unsupported_claim_rate": unsupported / all_precision_denominator
            if all_precision_denominator
            else 0.0,
            "raw_boundary_violation_rate": sum(item.boundary_violation for item in scores) / total,
            "human_review_decision_rate": sum(item.human_review_correct for item in scores) / total,
            "ambiguity_detection_rate": sum(item.ambiguity_correct for item in scores) / total,
            "semantic_repeat_consistency_rate": stable_cases / len(corpus.cases),
            "latency_ms_median": statistics.median(item.latency_ms for item in scores),
            "latency_ms_p95": sorted(item.latency_ms for item in scores)[
                max(0, round(0.95 * total) - 1)
            ],
            "input_tokens": sum(item.input_tokens for item in scores),
            "output_tokens": sum(item.output_tokens for item in scores),
            "estimated_cost_usd": None,
        },
        "notes": [
            "Inputs are synthetic and contain no real applicant data.",
            "Token totals come from provider telemetry; cost remains null until a dated price "
            "snapshot is supplied explicitly.",
            "Raw boundary violations are measured before deterministic patch filtering.",
        ],
        "runs": case_results,
    }


def allowed_profile_fields() -> set[str]:
    return set(CaseProfile.model_fields)
