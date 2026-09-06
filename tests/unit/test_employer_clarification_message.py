"""Helpful review replies cannot themselves resolve the review."""

import pytest

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.llm.guarded import deterministic_fallback_message


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("field", ["employer_name", "employer_address", "employer_phone"])
@pytest.mark.parametrize("clarified", [False, True])
def test_specific_clarification_and_followup_preserve_hold(language, field, clarified):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic-test", customer_language=language, status=CaseStatus.HUMAN_REVIEW_REQUIRED,
        human_review_reason=f"Conflicting current employer statements for {field}; clarification is required.")
    if clarified:
        case.latest_received_facts = {field: "fictional supplied detail"}
    before = case.model_dump(mode="json")
    reply = deterministic_fallback_message(case, "blocked")
    assert case.model_dump(mode="json") == before
    if clarified:
        assert ("review is still open" if language == "en" else "没有解除复核") in reply
        assert ("Could you explain" if language == "en" else "方便说明") not in reply
    else:
        assert ("Could you explain which applies now?" if language == "en" else "方便说明哪项") in reply
    assert ("not be finalised" in reply or "before the pack is finalised" in reply) if language == "en" else "定稿" in reply


def test_employer_clarification_does_not_hide_serious_history_instructions():
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic-test", status=CaseStatus.HUMAN_REVIEW_REQUIRED,
        human_review_reason="Conflicting current employer statements for employer_name; clarification is required.")
    case.profile.has_serious_history = True
    case.latest_received_facts = {"has_serious_history": "True"}
    reply = deterministic_fallback_message(case, "blocked")
    assert "refusal decision" in reply and "which applies now?" in reply
