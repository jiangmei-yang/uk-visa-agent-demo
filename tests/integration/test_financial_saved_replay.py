"""Offline replay of every retained financial provider outcome; no network calls."""

import hashlib
import json
import runpy
from collections.abc import Callable
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from pypdf import PdfReader

from visa_agent.documents.natural import DocumentProposal, validate_document
from visa_agent.domain.financial_review import apply_financial_consistency_checks
from visa_agent.domain.models import (
    Case,
    CaseProfile,
    Document,
    DocumentStatus,
    Evidence,
    IssueStatus,
    ProvenanceState,
)
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.storage.sqlite import SQLiteStore

ROOT = Path(__file__).parents[2]
PROBE = runpy.run_path(str(ROOT / "scripts" / "financial_document_probe.py"))
PROBE_DOCUMENTS = cast(dict[str, dict[str, Any]], PROBE["DOCUMENTS"])
VERIFY_FICTIONAL_PDF = cast(
    Callable[[Path, dict[str, Any]], str], PROBE["_verify_fictional_pdf"]
)
REPORTS = [ROOT / "eval_output" / name for name in (
    "financial_document_deepseek_2026-09-05.json",
    "financial_document_deepseek_2026-09-05-v2.json",
    "financial_document_deepseek_2026-09-05-v3.json",
    "financial_document_deepseek_2026-09-05-v4.json",
)]
PDFS = ROOT / "output" / "pdf" / "financial-document-eval"
ROLLOUT = ROOT / "eval_output" / "financial_document_rollout_2026-09-05.json"
POLICY = load_policy(ROOT / "knowledge" / "uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 5)


def test_failed_provider_history_is_retained_before_green_replay() -> None:
    rollout = json.loads(ROLLOUT.read_text())
    report_hashes = {item["report"]: item["sha256"] for item in rollout["provider_runs"]}
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in REPORTS
    } == report_hashes
    reports = [json.loads(path.read_text()) for path in REPORTS]
    assert [(len(item["results"]), sum(row["passed"] for row in item["results"]))
            for item in reports] == [(4, 1), (3, 0), (3, 3), (1, 1)]
    assert all(item["completed"] for item in reports)
    assert reports[-1]["all_passed"] is True


@pytest.mark.parametrize("filename", tuple(PROBE_DOCUMENTS))
def test_retained_pdf_is_the_frozen_fictional_probe_input(filename: str) -> None:
    expected = PROBE_DOCUMENTS[filename]
    assert VERIFY_FICTIONAL_PDF(PDFS / filename, expected) == expected["sha256"]


def test_probe_refuses_same_named_but_different_pdf_bytes(tmp_path: Path) -> None:
    filename = "fictional_bank_statement_a.pdf"
    changed = tmp_path / filename
    changed.write_bytes((PDFS / filename).read_bytes() + b"changed after the PDF trailer")

    with pytest.raises(ValueError, match="non-frozen PDF bytes"):
        VERIFY_FICTIONAL_PDF(changed, PROBE_DOCUMENTS[filename])


def test_v3_raw_proposals_replay_against_retained_pdf_text_and_detect_conflict(
    tmp_path: Path,
) -> None:
    reports = [json.loads(path.read_text()) for path in REPORTS[-2:]]
    case = Case(id="saved-financial-replay", external_thread_id="offline",
        applicant_contact="offline@example.test", policy_version="2026-02-25",
        profile=CaseProfile(full_name="Kai Example", sponsor_name="Mina Example",
                            funding_source="self"))
    for report in reports:
        for row in report["results"]:
            content = row["raw_model_response"]
            assert isinstance(content, str)
            proposal = DocumentProposal.model_validate_json(content)
            pdf_path = PDFS / row["filename"]
            actual_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            assert actual_sha256 == row["sha256"]
            pages = [page.extract_text() or "" for page in PdfReader(pdf_path).pages]
            result = validate_document(proposal, pages, method="saved_provider_replay",
                                       version=report["model"])
            assert not result.requires_review and len(result.financial_observations) == 1
            document_id = "doc-" + row["filename"]
            case.documents.append(Document(id=document_id, filename=row["filename"], kind=result.kind,
                sha256=row["sha256"], mime_type="application/pdf",
                status=DocumentStatus.ACCEPTED_FOR_REVIEW, source_event_id="saved-provider",
                path=str(pdf_path), page_count=result.page_count))
            observation = result.financial_observations[0]
            case.evidence.append(Evidence(id="evidence-" + row["filename"],
                fact_key="financial_observation", value=observation.model_dump(mode="json",
                    exclude={"amount_page", "amount_excerpt", "confidence"}),
                source_event_id="saved-provider", source_document_id=document_id,
                source_excerpt=observation.amount_excerpt, page=observation.amount_page,
                extraction_method=result.method, model_version=result.model_version,
                confidence=observation.confidence,
                provenance_state=ProvenanceState.EXTRACTED_UNVERIFIED))
    apply_financial_consistency_checks(case)
    conflicts = [issue for issue in case.open_blockers()
        if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_")]
    assert len(conflicts) == 1
    assert set(conflicts[0].related_document_ids) == {
        "doc-fictional_bank_statement_a.pdf", "doc-fictional_bank_statement_b.pdf"
    }
    assert not any(issue.code.startswith("FINANCIAL_OWNER_MISMATCH_")
                   for issue in case.open_blockers())

    gate_before = evaluate_gate(case, POLICY, TODAY)
    assert gate_before.checks["no_unresolved_blocker_issue"] is False
    assert not gate_before.allowed
    funding = next(item for item in case.requirements if item.id == "funding_evidence")
    assert funding.applicable and funding.satisfied

    database = tmp_path / "saved-financial-replay.db"
    with closing(SQLiteStore(database)) as store:
        store.save_case(case)
    with closing(SQLiteStore(database)) as store:
        reopened = store.get_case(case.id)
    assert reopened is not None
    gate_after = evaluate_gate(reopened, POLICY, TODAY)
    reopened_conflicts = [
        issue
        for issue in reopened.issues
        if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_")
    ]
    assert len(reopened_conflicts) == 1
    assert reopened_conflicts[0].status == IssueStatus.OPEN
    assert set(reopened_conflicts[0].related_document_ids) == {
        "doc-fictional_bank_statement_a.pdf",
        "doc-fictional_bank_statement_b.pdf",
    }
    assert gate_after == gate_before
    assert gate_after.checks["no_unresolved_blocker_issue"] is False
    assert not gate_after.allowed
