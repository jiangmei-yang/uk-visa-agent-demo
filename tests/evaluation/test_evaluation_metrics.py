from __future__ import annotations

from pathlib import Path

from visa_agent.llm.evaluation import (
    EvaluationCase,
    EvaluationCorpus,
    allowed_profile_fields,
    evaluate_extractor,
    load_corpus,
)
from visa_agent.llm.ports import CasePatch, FactUpdate


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
