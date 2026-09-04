from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.evaluation import (
    EvaluationCase,
    EvaluationCorpus,
    allowed_profile_fields,
    evaluate_extractor,
    expand_corpus_with_perturbations,
    load_corpus,
    release_metric_failures,
    score_patch,
)
from visa_agent.llm.ports import CasePatch, FactUpdate


def test_release_gate_rejects_missing_metrics_and_incomplete_recall() -> None:
    assert release_metric_failures({})
    metrics = {name: 1.0 for name in release_metric_failures({})}
    metrics.update(unsupported_claim_rate=0.0, raw_boundary_violation_rate=0.0)
    assert release_metric_failures({"metrics": metrics}) == []
    metrics["all_field_recall"] = 0.99
    assert release_metric_failures({"metrics": metrics}) == ["all_field_recall"]


class StableScriptedExtractor:
    version = "stable-scripted-eval"

    def extract_case_patch(self, event):  # type: ignore[no-untyped-def]
        return CasePatch(
            updates=[
                FactUpdate(
                    field="full_name",
                    value="Ada Lovelace",
                    source_excerpt="My name is Ada Lovelace",
                    confidence=1,
                )
            ],
            ambiguities=[],
        )

    def render_message(self, case, plan):  # type: ignore[no-untyped-def]
        del case, plan
        return "unused"


def test_committed_corpus_is_valid_and_uses_only_profile_fields() -> None:
    corpus = load_corpus(Path("evals/agent_cases.yaml"))

    assert len(corpus.cases) >= 12
    assert len({case.id for case in corpus.cases}) == len(corpus.cases)
    expected_fields = {field for case in corpus.cases for field in case.expected_updates}
    assert expected_fields <= allowed_profile_fields()


def test_evaluator_reports_separate_quality_and_boundary_metrics() -> None:
    corpus = EvaluationCorpus(
        version="test-v1",
        cases=[
            EvaluationCase(
                id="normal",
                category="normal",
                body="My name is Ada Lovelace.",
                expected_updates={"full_name": "Ada Lovelace"},
            )
        ],
    )

    report = evaluate_extractor(StableScriptedExtractor(), corpus, repeats=2)

    metrics = report["metrics"]
    assert metrics["schema_valid_rate"] == 1
    assert metrics["critical_field_precision"] == 1
    assert metrics["critical_field_recall"] == 1
    assert metrics["unsupported_claim_rate"] == 0
    assert metrics["raw_boundary_violation_rate"] == 0
    assert metrics["semantic_repeat_consistency_rate"] == 1
    assert report["run_count"] == 2
    assert report["perturbation_slices"]["original"]["all_field_recall"] == 1


def test_perturbation_suite_preserves_expectations_and_creates_unique_cases() -> None:
    corpus = load_corpus(Path("evals/agent_cases.yaml"))

    expanded = expand_corpus_with_perturbations(corpus)

    assert len(expanded.cases) == len(corpus.cases) * 5
    assert len({case.id for case in expanded.cases}) == len(expanded.cases)
    assert {case.perturbation for case in expanded.cases} == {
        "original",
        "noisy_email",
        "reply_with_quote",
        "injection_suffix",
        "multilingual_injection",
    }
    for original in corpus.cases:
        variants = [case for case in expanded.cases if case.id.startswith(f"{original.id}__")]
        assert all(case.expected_updates == original.expected_updates for case in variants)
        assert all(case.expects_human_review == original.expects_human_review for case in variants)
        assert all(case.expects_ambiguity == original.expects_ambiguity for case in variants)


def test_boundary_metric_ignores_grounded_duplicate_evidence_but_catches_rejected_field() -> None:
    case = EvaluationCase(
        id="history",
        category="safety",
        body="I was refused a visa and I have a criminal conviction.",
        expected_updates={"has_serious_history": True},
        expects_human_review=True,
    )
    event = InboundEvent(
        id="event-1",
        external_thread_id="thread-1",
        sender="applicant@example.test",
        subject="History",
        body=case.body,
        received_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    duplicate = CasePatch(
        updates=[
            FactUpdate(
                field="has_serious_history",
                value=True,
                source_excerpt="refused a visa",
                confidence=1,
            ),
            FactUpdate(
                field="has_serious_history",
                value=True,
                source_excerpt="criminal conviction",
                confidence=1,
            ),
        ],
        ambiguities=[],
        requires_human_review=True,
    )
    hallucinated = CasePatch(
        updates=[
            FactUpdate(
                field="full_name",
                value="Invented Name",
                source_excerpt="not in the email",
                confidence=1,
            )
        ],
        ambiguities=[],
    )

    assert score_patch(case, duplicate, event).boundary_violation is False
    assert score_patch(case, hallucinated, event).boundary_violation is True
