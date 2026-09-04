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
CANONICAL_SHA256 = cast(Callable[[object], str], PROBE["_canonical_sha256"])
REQUEST_FINGERPRINTS = cast(Callable[[], dict[str, str]], PROBE["_request_fingerprints"])
SOURCE_MANIFEST = cast(
    Callable[[list[str]], dict[str, dict[str, Any]]], PROBE["_source_manifest"]
)
IMPLEMENTATION_FILES = cast(tuple[str, ...], PROBE["IMPLEMENTATION_FILES"])
REPORTS = [ROOT / "eval_output" / name for name in (
    "financial_document_deepseek_2026-09-05.json",
    "financial_document_deepseek_2026-09-05-v2.json",
    "financial_document_deepseek_2026-09-05-v3.json",
    "financial_document_deepseek_2026-09-05-v4.json",
)]
PDFS = ROOT / "output" / "pdf" / "financial-document-eval"
ROLLOUT = ROOT / "eval_output" / "financial_document_rollout_2026-09-05.json"
CURRENT_REPORT = ROOT / "eval_output" / "financial_document_deepseek_2026-09-05-v6.json"
POLICY = load_policy(ROOT / "knowledge" / "uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 5)


def test_failed_provider_history_is_retained_before_green_replay() -> None:
    rollout = json.loads(ROLLOUT.read_text())
    report_hashes = {item["report"]: item["sha256"] for item in rollout["provider_runs"]}
    current_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in REPORTS
    }
    assert current_hashes == {name: report_hashes[name] for name in current_hashes}
    reports = [json.loads(path.read_text()) for path in REPORTS]
    assert [(len(item["results"]), sum(row["passed"] for row in item["results"]))
            for item in reports] == [(4, 1), (3, 0), (3, 3), (1, 1)]
    assert all(item["completed"] for item in reports)
    assert reports[-1]["all_passed"] is True


def test_v6_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set() -> None:
    report = json.loads(CURRENT_REPORT.read_text())
    rollout = json.loads(ROLLOUT.read_text())
    run_entry = next(item for item in rollout["provider_runs"]
                     if item["report"] == CURRENT_REPORT.name)
    assert hashlib.sha256(CURRENT_REPORT.read_bytes()).hexdigest() == run_entry["sha256"]
    assert report["evidence_contract_version"] == "financial-document-probe-v2"
    assert report["completed"] is True and report["all_passed"] is True
    assert report["maximum_model_calls"] == len(report["results"]) == 4
    assert report["mailbox_calls"] == 0 and report["real_documents"] == 0

    selected = report["selected_documents"]
    assert selected == list(PROBE_DOCUMENTS)
    expected_source_manifest = SOURCE_MANIFEST(selected)
    assert report["document_source_manifest"] == expected_source_manifest
    assert report["document_source_bundle_sha256"] == CANONICAL_SHA256(expected_source_manifest)
    assert report["document_source_sha256"] == {
        filename: hashlib.sha256((PDFS / filename).read_bytes()).hexdigest()
        for filename in selected
    }

    expected_implementation = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in IMPLEMENTATION_FILES
    }
    assert len(expected_implementation) == 73  # probe plus every one of 72 source files
    assert report["implementation_files_sha256"] == expected_implementation
    assert report["implementation_bundle_sha256"] == CANONICAL_SHA256(expected_implementation)
    assert report["document_schema_sha256"] == CANONICAL_SHA256(
        DocumentProposal.model_json_schema()
    )
    assert {
        "document_prompt_sha256": report["document_prompt_sha256"],
        "request_config_sha256": report["request_config_sha256"],
    } == REQUEST_FINGERPRINTS()
    expected_run_id = CANONICAL_SHA256({
        key: report[key]
        for key in (
            "run_started_at", "git_head", "document_source_bundle_sha256",
            "implementation_bundle_sha256", "document_schema_sha256",
            "document_prompt_sha256", "request_config_sha256",
        )
    })
    assert report["run_id"] == expected_run_id
    assert report["provider_response_models"] == ["deepseek-v4-flash"]
    for row in report["results"]:
        assert row["passed"] is True and all(row["checks"].values())
        assert row["provider_response_model"] == "deepseek-v4-flash"
        assert row["provider_response_model_sha256"] == hashlib.sha256(
            b"deepseek-v4-flash"
        ).hexdigest()
        proposal = DocumentProposal.model_validate_json(row["raw_model_response"])
        pages = [page.extract_text() or "" for page in PdfReader(PDFS / row["filename"]).pages]
        result = validate_document(
            proposal, pages, method="saved_provider_replay", version=report["model"]
        )
        assert not result.requires_review and len(result.financial_observations) == 1


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
