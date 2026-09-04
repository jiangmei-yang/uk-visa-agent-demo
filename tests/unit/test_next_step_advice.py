"""Pure next-step selection from synthetic state; no extraction, network or files."""

from datetime import date
from pathlib import Path

import pytest

from visa_agent.domain.models import (
    Case,
    CaseProfile,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    GateResult,
    Issue,
    IssueSeverity,
)
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import build_requirements
from visa_agent.workflow.next_step import select_next_step

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))


def _case(language: str = "en") -> Case:
    case = Case(
        id="synthetic-next-step", external_thread_id="synthetic-next-step-thread",
        applicant_contact="applicant@example.test", primary_channel="gmail", customer_language=language,
        policy_version=POLICY.version, profile=CaseProfile(
            full_name="Sample Applicant", date_of_birth=date(1995, 7, 23),
            nationality_country="China", application_country="Hong Kong", visit_purpose="tourism",
            occupation_status="student", funding_source="self", estimated_trip_cost_gbp=1600,
            current_address="Room 4, Example Hall, 88 Synthetic Road, Hong Kong", uk_accommodation="Planned stay in London",
            has_serious_history=False, route_confirmed_standard_visitor=True,
        ), deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )
    case.requirements = build_requirements(case, POLICY)
    return case


def _gate(**changes: bool) -> GateResult:
    # A controlled gate result isolates selector routing; this fixture is not a
    # claim that the synthetic profile/evidence passes the real delivery gate.
    checks = {
        "preparation_active": True, "route_in_scope": True, "policy_snapshot_is_current": True,
        "applicant_age_at_least_18": True, "all_held_updates_reviewed": True,
        "passport_valid_through_stay": False,
        "travel_dates_are_valid_and_within_six_months": False,
        "applicant_explicitly_confirmed_final_summary": False,
    } | changes
    return GateResult(allowed=False, checks=checks, reasons=[])


def _only_missing(case: Case, identifier: str) -> None:
    for item in case.requirements:
        item.satisfied = item.id != identifier


def _document(kind: str, status: DocumentStatus, identifier: str = "synthetic-file") -> Document:
    return Document(
        id=identifier, filename=f"{identifier}.pdf", kind=kind, sha256="a" * 64,
        mime_type="application/pdf", status=status, source_event_id="synthetic-source",
        path=f"/synthetic-not-read/{identifier}.pdf", language="en",
    )


@pytest.mark.parametrize("language", ["zh", "en"])
def test_one_missing_fact_ignores_old_pacing_but_not_date_deferrals(language: str) -> None:
    case = _case(language)
    case.profile.full_name = None
    case.profile.date_of_birth = None
    case.question_plan = []
    case.pending_question_fields = ["full_name", "date_of_birth"]
    case.customer_answers = ["Synthetic separate FAQ answer"]
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "question" and result.question_field == "full_name"
    assert result.requirement_id is None
    assert "?" not in result.message and "？" not in result.message
    assert case.model_dump_json() == before


@pytest.mark.parametrize("language", ["zh", "en"])
def test_newly_filled_fact_is_not_asked_again(language: str) -> None:
    case = _case(language)
    case.profile.date_of_birth = None
    case.latest_received_facts = {"full_name": "Sample Applicant"}
    case.question_plan = ["full_name", "planned_arrival_date"]
    result = select_next_step(case, POLICY, _gate())
    assert result.question_field == "date_of_birth"


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("missing", ["passport", "status_evidence", "funding_evidence"])
def test_date_deferred_customer_gets_one_actual_missing_document_with_why_and_pdf_how(
    language: str, missing: str,
) -> None:
    case = _case(language)
    _only_missing(case, missing)
    case.customer_answers = ["Synthetic independent booking FAQ"]
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "document" and result.requirement_id == missing
    assert result.question_field is None
    assert "PDF" in result.message and "GOV.UK:" in result.message
    assert ("先核对内容" if language == "zh" else "check its contents") in result.message
    if missing == "status_evidence":
        assert ("在读证明" if language == "zh" else "student status") in result.message
        assert ("学校" if language == "zh" else "school") in result.message
    elif missing == "funding_evidence":
        assert ("网银" if language == "zh" else "online banking") in result.message
    else:
        assert ("资料页" if language == "zh" else "details page") in result.message
    assert "Apply now" not in result.message
    assert case.model_dump_json() == before


def test_already_satisfied_document_is_skipped() -> None:
    case = _case()
    case.requirements[0].satisfied = True
    result = select_next_step(case, POLICY, _gate())
    assert result.requirement_id == "status_evidence"


@pytest.mark.parametrize("status", [DocumentStatus.RECEIVED, DocumentStatus.PROCESSING])
def test_received_file_is_checked_instead_of_requested_again(status: DocumentStatus) -> None:
    case = _case()
    _only_missing(case, "status_evidence")
    case.documents = [_document("student_letter", status)]
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "waiting" and result.requirement_id == "status_evidence"
    assert "already been received" in result.message and "not send another copy" in result.message
    assert result.question_field is None


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("has_missing_fact", [False, True])
def test_paused_step_is_conditional_information_without_question_or_upload_request(
    language: str, has_missing_fact: bool,
) -> None:
    case = _case(language)
    case.preparation_paused = True
    case.preparation_control_epoch = 5
    if has_missing_fact:
        case.profile.full_name = None
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate(preparation_active=False))
    assert result.kind == "paused" and result.question_field is None
    assert "?" not in result.message and "？" not in result.message
    assert ("之后" if language == "zh" else "later") in result.message
    assert ("现在不用" if language == "zh" else "now") in result.message
    assert "PDF" not in result.message and "Apply now" not in result.message
    assert case.model_dump_json() == before


@pytest.mark.parametrize("reason", ["human_review", "held", "issue", "expired_policy", "document_review"])
@pytest.mark.parametrize("paused", [False, True])
def test_review_and_held_boundaries_take_priority_over_new_intake(reason: str, paused: bool) -> None:
    case = _case()
    case.preparation_paused = paused
    case.profile.full_name = None
    gate = _gate(preparation_active=not paused)
    if reason == "human_review":
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    elif reason == "held":
        gate.checks["all_held_updates_reviewed"] = False
    elif reason == "issue":
        case.issues = [Issue(id="synthetic-issue", code="DATE_CONFLICT", title="Date conflict",
                             detail="Synthetic dates require review.", severity=IssueSeverity.BLOCKER)]
    elif reason == "expired_policy":
        gate.checks["policy_snapshot_is_current"] = False
    else:
        case.documents = [_document("unknown", DocumentStatus.HUMAN_REVIEW_REQUIRED)]
    result = select_next_step(case, POLICY, gate)
    assert result.kind == "review" and result.question_field is None and result.requirement_id is None
    if paused:
        assert "remains on hold" in result.message and "send anything now" in result.message


def test_known_unsupported_purpose_is_reviewed_even_when_identity_is_missing() -> None:
    case = _case()
    case.profile.visit_purpose = "medical_visit"
    case.profile.full_name = None
    result = select_next_step(case, POLICY, _gate(route_in_scope=False))
    assert result.kind == "review" and result.question_field is None


@pytest.mark.parametrize("unsafe_check", ["applicant_age_at_least_18", "passport_valid_through_stay"])
def test_existing_failed_identity_or_passport_check_is_not_skipped(unsafe_check: str) -> None:
    case = _case()
    if unsafe_check == "passport_valid_through_stay":
        case.documents = [_document("passport", DocumentStatus.ACCEPTED_FOR_REVIEW)]
        case.profile.planned_departure_date = date(2026, 12, 20)
    result = select_next_step(case, POLICY, _gate(**{unsafe_check: False}))
    assert result.kind == "review" and result.question_field is None


def test_passport_received_with_unknown_deferred_dates_does_not_block_next_document() -> None:
    case = _case()
    passport = _document("passport", DocumentStatus.ACCEPTED_FOR_REVIEW)
    case.documents = [passport]
    case.evidence = [Evidence(
        id="synthetic-expiry", fact_key="passport_expiry_date", value="2032-08-14",
        source_event_id="synthetic-source", source_document_id=passport.id,
        source_excerpt="Expiry 14 August 2032", extraction_method="synthetic-test",
        model_version="synthetic-test", confidence=1,
    )]
    case.requirements = build_requirements(case, POLICY)
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate(passport_valid_through_stay=False))
    assert result.kind == "document" and result.requirement_id == "status_evidence"
    assert result.question_field is None
    assert case.model_dump_json() == before


def test_standalone_replacement_need_is_not_hidden_by_a_no_resend_receipt() -> None:
    case = _case()
    case.documents = [_document("student_letter", DocumentStatus.NEEDS_REPLACEMENT, "unreadable-letter")]
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "review" and result.question_field is None
    assert "readable PDF replacement" in result.message and "unreadable-letter.pdf" in result.message
    assert "no need to resend" not in result.message


def test_issue_intro_preserves_concrete_issue_renderer_without_a_no_resend_claim() -> None:
    case = _case()
    case.issues = [Issue(id="synthetic-issue", code="DATE_CONFLICT", title="Date conflict",
                        detail="Synthetic actual dates need clarification.", severity=IssueSeverity.BLOCKER)]
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "review" and "issues set out below" in result.message
    assert "no need to resend" not in result.message


def test_no_missing_material_does_not_create_consent_or_delivery() -> None:
    case = _case()
    for item in case.requirements:
        item.satisfied = True
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "waiting" and result.question_field is None and result.requirement_id is None
    assert "current summary" in result.message
    assert case.model_dump_json() == before
    assert not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("confirmation_kind", ["profile", "final"])
def test_current_summary_request_precedes_new_pdf_advice(language: str, confirmation_kind: str) -> None:
    case = _case(language)
    case.confirmation_kind = confirmation_kind
    case.confirmation_fingerprint = "synthetic-current-summary"
    case.confirmation_request_event_id = "synthetic-current-event"
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "waiting" and result.question_field is None and result.requirement_id is None
    assert ("摘要" if language == "zh" else "summary below") in result.message
    assert ("姓名" if language == "zh" else "name") in result.message
    assert "PDF" not in result.message and "not confirmation" not in result.message
    assert case.model_dump_json() == before


@pytest.mark.parametrize("language", ["zh", "en"])
def test_fully_allowed_confirmed_case_is_not_asked_to_confirm_again(language: str) -> None:
    case = _case(language)
    case.final_summary_confirmed = case.profile_confirmed = True
    gate = _gate()
    gate.allowed = True
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, gate)
    assert result.kind == "waiting" and result.question_field is None and result.requirement_id is None
    assert ("已经确认" if language == "zh" else "your confirmed details") in result.message
    assert ("顾问复核" if language == "zh" else "adviser review") in result.message
    assert "summary below" not in result.message and "PDF" not in result.message
    assert case.model_dump_json() == before and case.delivery_path is None


@pytest.mark.parametrize("language,body", [("zh", "下一份先准备什么？不要链接。"),
                                          ("en", "What should I prepare next? No links, please.")])
def test_explicit_no_links_request_keeps_the_document_step_without_urls(language: str, body: str) -> None:
    case = _case(language)
    case.latest_customer_message = body
    result = select_next_step(case, POLICY, _gate())
    assert result.kind == "document" and "PDF" in result.message
    assert "https://" not in result.message and "GOV.UK:" not in result.message
