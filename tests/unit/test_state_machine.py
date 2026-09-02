from __future__ import annotations

from visa_agent.domain.models import Case, CaseStatus, WorkflowStage
from visa_agent.domain.rules import advance_stage, transition


def make_case() -> Case:
    return Case(
        id="case-test",
        email_thread_id="thread-test",
        applicant_email="applicant@example.test",
        policy_version="2026-02-25",
    )


def test_stage_progression_is_allow_listed() -> None:
    case = make_case()
    advance_stage(case, WorkflowStage.DOCUMENT_REVIEW)
    assert case.stage == WorkflowStage.DOCUMENT_REVIEW


def test_stage_cannot_move_backwards() -> None:
    case = make_case()
    advance_stage(case, WorkflowStage.DOCUMENT_REVIEW)
    try:
        advance_stage(case, WorkflowStage.INTAKE)
    except ValueError as error:
        assert "not allowed" in str(error)
    else:
        raise AssertionError("Backward transition unexpectedly succeeded")


def test_public_status_cannot_skip_to_delivered() -> None:
    case = make_case()
    try:
        transition(case, CaseStatus.DELIVERED_AFTER_CONFIRMATION)
    except ValueError as error:
        assert "not allowed" in str(error)
    else:
        raise AssertionError("Invalid public status transition unexpectedly succeeded")
