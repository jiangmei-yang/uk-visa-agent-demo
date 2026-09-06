"""Source conflict detection must not depend on model reporting every value."""

import pytest

from visa_agent.domain.employer_evidence import conflicting_employer_fields
from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


@pytest.mark.parametrize("proposal", ["empty", "first", "last"])
@pytest.mark.parametrize(("first", "last", "one", "two"), [
    ("My employer is Northstar Ltd.", "My employer is Southstar Ltd.", "Northstar Ltd", "Southstar Ltd"),
    ("我的雇主是甲公司。", "我的雇主是乙公司。", "甲公司", "乙公司"),
])
def test_two_current_employers_cannot_be_silently_selected(proposal, first, last, one, two):
    phone = "My employer's phone number is +852 2000 1234."
    event = InboundEvent(id="fictional", external_thread_id="fictional", channel="gmail",
        sender="fictional@example.test", subject="Fictional", body=f"{first} {last} {phone}",
        received_at="2026-09-07T00:00:00+00:00")
    updates = [] if proposal == "empty" else [FactUpdate(field="employer_name",
        value=one if proposal == "first" else two,
        source_excerpt=first if proposal == "first" else last, confidence=1)]
    updates.append(FactUpdate(field="employer_phone", value="+852 2000 1234", source_excerpt=phone, confidence=1))
    result = validate_case_patch(event, CasePatch(updates=updates, ambiguities=[]))
    assert result.requires_human_review and result.ambiguities
    assert not result.updates


@pytest.mark.parametrize("body", [
    "My employer is Northstar Ltd. My employer is Northstar Ltd.",
    "Previously, my employer is Northstar Ltd. My employer is Southstar Ltd.",
    "If my employer is Northstar Ltd. My employer is Southstar Ltd.",
    "My friend's employer is Northstar Ltd. My employer is Southstar Ltd.",
])
def test_repetition_history_and_other_owners_are_not_current_conflicts(body):
    assert not conflicting_employer_fields(body)


@pytest.mark.parametrize(("field", "first", "last"), [
    ("employer_phone", "My employer's phone number is +852 2000 1234.",
     "My employer's phone number is +852 2000 5678."),
    ("employer_address", "My employer's address is 12 Example Road, Hong Kong.",
     "My employer's address is 34 Another Road, Hong Kong."),
])
def test_contact_conflict_is_detected_even_with_empty_model_patch(field, first, last):
    event = InboundEvent(id="fictional", external_thread_id="fictional", channel="gmail",
        sender="fictional@example.test", subject="Fictional", body=f"{first} {last}",
        received_at="2026-09-07T00:00:00+00:00")
    result = validate_case_patch(event, CasePatch(updates=[], ambiguities=[]))
    assert result.requires_human_review and field in result.ambiguities[0]
    assert not result.updates
