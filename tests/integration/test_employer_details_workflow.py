"""Fictional current-employer persistence and old-employer invalidation."""

import pytest
from test_consultant_value import Conversation, _patch
from test_next_step_workflow import _seed

from visa_agent.domain.models import Document, DocumentStatus, Evidence
from visa_agent.domain.rules import (
    build_requirements,
    profile_fact_complete,
    required_profile_facts,
)
from visa_agent.storage.sqlite import SQLiteStore


def started(tmp_path):
    dialogue = Conversation(tmp_path)
    employed = "I'm employed."
    name = "My employer is Northstar Ltd."
    address = "My employer's address is 12 Example Road, Hong Kong."
    phone = "My employer's phone number is +852 2000 1234."
    result = dialogue.turn(f"{employed} {name} {address} {phone}", _patch(updates=[
        ("occupation_status", "employed", employed), ("employer_name", "Northstar Ltd", name),
        ("employer_address", "12 Example Road, Hong Kong", address),
        ("employer_phone", "+852 2000 1234", phone),
    ]))
    assert result.case.profile.employer_name == "Northstar Ltd"
    assert result.case.profile.employer_address == "12 Example Road, Hong Kong"
    assert result.case.profile.employer_phone == "+852 2000 1234"
    assert result.case.profile.current_address is None
    return dialogue, result


def test_current_employer_survives_reopen_with_separate_sources(tmp_path):
    dialogue, first = started(tmp_path)
    later = dialogue.turn("Thanks.", _patch())
    for field in ("employer_name", "employer_address", "employer_phone"):
        assert getattr(later.case.profile, field) == getattr(first.case.profile, field)
        evidence = later.case.active_evidence(field)
        assert len(evidence) == 1 and evidence[0].source_event_id == first.event.id


def test_new_employer_does_not_inherit_previous_address_and_phone(tmp_path):
    dialogue, _ = started(tmp_path)
    body = "My employer is Southstar Ltd."
    result = dialogue.turn(body, _patch(updates=[("employer_name", "Southstar Ltd", body)]))
    assert result.case.profile.employer_name == "Southstar Ltd"
    for field in ("employer_address", "employer_phone"):
        assert getattr(result.case.profile, field) is None
        assert not result.case.active_evidence(field)
        assert any(item.fact_key == field and item.superseded for item in result.case.evidence)


def test_same_event_new_contact_is_bound_to_new_employer(tmp_path):
    dialogue, _ = started(tmp_path)
    name = "My employer is Southstar Ltd."
    phone = "My employer's phone number is +852 2000 5678."
    result = dialogue.turn(f"{name} {phone}", _patch(updates=[
        ("employer_name", "Southstar Ltd", name), ("employer_phone", "+852 2000 5678", phone)]))
    assert result.case.profile.employer_phone == "+852 2000 5678"
    assert result.case.profile.employer_address is None
    assert result.case.active_evidence("employer_phone")[0].source_event_id == result.event.id


def test_leaving_employment_retires_current_employer_facts(tmp_path):
    dialogue, _ = started(tmp_path)
    body = "I'm a student."
    result = dialogue.turn(body, _patch(updates=[("occupation_status", "student", body)]))
    for field in ("employer_name", "employer_address", "employer_phone"):
        assert getattr(result.case.profile, field) is None
        assert not result.case.active_evidence(field)


def test_old_employment_letter_cannot_satisfy_current_status_after_employer_change(tmp_path):
    from test_consultant_value import POLICY

    dialogue, first = started(tmp_path)
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        case.documents.append(Document(id="old-employment", filename="fictional-old-letter.pdf",
            kind="employment_letter", sha256="a" * 64, mime_type="application/pdf",
            status=DocumentStatus.ACCEPTED_FOR_REVIEW, source_event_id=first.event.id,
            path=str(tmp_path / "fictional-old-letter.pdf")))
        # Explicit synthetic salary observation makes the pre-change requirement
        # genuinely satisfied; this is not a live document extraction test.
        case.evidence.append(Evidence(id="old-salary", fact_key="financial_observation",
            value={"kind": "salary", "subject_name": "Fictional Employee", "amount": "60000",
                   "currency": "GBP", "period": "annual", "basis": "gross", "as_of": "2026-08-31",
                   "account_reference": None, "subject_page": 1, "subject_excerpt": "Employee: Fictional Employee",
                   "date_page": 1, "date_excerpt": "Letter date: 2026-08-31",
                   "account_page": None, "account_excerpt": None},
            source_event_id=first.event.id, source_document_id="old-employment",
            source_excerpt="Gross annual salary GBP 60000", page=1,
            extraction_method="synthetic_test", model_version="none", confidence=1))
        assert next(item for item in build_requirements(case, POLICY) if item.id == "status_evidence").satisfied
        store.save_case(case)
    finally:
        store.close()
    body = "My employer is Southstar Ltd."
    result = dialogue.turn(body, _patch(updates=[("employer_name", "Southstar Ltd", body)]))
    old = next(doc for doc in result.case.documents if doc.id == "old-employment")
    assert old.status == DocumentStatus.NEEDS_CLARIFICATION
    assert "earlier employment letter needs checking" in result.body
    assert not next(item for item in build_requirements(result.case, POLICY) if item.id == "status_evidence").satisfied
    assert result.case.employment_document_reviews == [{
        "document_id": "old-employment", "source_event_id": result.event.id, "reason": "employer_changed"}]
    later = dialogue.turn("Thanks.", _patch())
    assert later.case.employment_document_reviews == result.case.employment_document_reviews
    assert len(later.case.documents) == 1  # history was not deleted
    assert "earlier employment letter needs checking" not in later.body


def test_same_employer_restatement_does_not_invalidate_existing_letter(tmp_path):
    dialogue, first = started(tmp_path)
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        case.documents.append(Document(id="current-employment", filename="fictional-letter.pdf",
            kind="employment_letter", sha256="b" * 64, mime_type="application/pdf",
            status=DocumentStatus.ACCEPTED_FOR_REVIEW, source_event_id=first.event.id,
            path=str(tmp_path / "fictional-letter.pdf")))
        store.save_case(case)
    finally:
        store.close()
    body = "My employer is Northstar Ltd."
    result = dialogue.turn(body, _patch(updates=[("employer_name", "Northstar Ltd", body)]))
    assert result.case.documents[0].status == DocumentStatus.ACCEPTED_FOR_REVIEW
    assert not result.case.employment_document_reviews


def asking(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn("Hello.", _patch())
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        seed = _seed()
        case.profile = seed.profile
        case.deferred_fields = seed.deferred_fields
        case.profile.occupation_status = "employed"
        case.profile.annual_income_gbp = 60000  # explicit unrelated synthetic premise
        store.save_case(case)
    finally:
        store.close()
    asked = dialogue.turn("What is the next step?", _patch())
    assert asked.case.last_requested_fields == ["employer_name"], asked.body
    return dialogue, asked


def test_actual_sent_questions_collect_three_short_details_sequentially(tmp_path):
    dialogue, asked = asking(tmp_path)
    assert {"employer_name", "employer_address", "employer_phone"} <= required_profile_facts(asked.case)
    for field, value, next_field in [
        ("employer_name", "Northstar Ltd", "employer_address"),
        ("employer_address", "12 Example Road, Hong Kong", "employer_phone"),
        ("employer_phone", "+852 2000 1234", None),
    ]:
        reply = dialogue.turn(value, _patch(updates=[(field, value, value)]))
        assert getattr(reply.case.profile, field) == value
        assert profile_fact_complete(reply.case, field)
        assert reply.model.events[0].requested_fields == [field]
        assert reply.case.active_evidence(field)[0].source_event_id == reply.event.id
        if next_field:
            assert reply.case.last_requested_fields == [next_field], reply.body
        assert not reply.case.final_summary_confirmed and reply.case.delivery_path is None


def test_unknown_employer_name_does_not_trigger_contact_questions_or_satisfy_gate(tmp_path):
    dialogue, asked = asking(tmp_path)
    reply = dialogue.turn("I need to check.", _patch())
    assert "employer_name" in reply.case.deferred_fields
    assert not {"employer_name", "employer_address", "employer_phone"}.intersection(reply.case.last_requested_fields)
    assert reply.case.employer_detail_deferrals[-1]["question_event_id"] == asked.event.id
    assert not profile_fact_complete(reply.case, "employer_name")
    later = dialogue.turn("Thanks.", _patch())
    assert "employer_name" in later.case.deferred_fields
    assert "formal name of your current employer" not in later.body


@pytest.mark.parametrize("invalid", ["unsent", "future", "wrong_employer"])
def test_old_or_unsent_question_does_not_authorize_short_answer(tmp_path, invalid):
    dialogue, asked = asking(tmp_path)
    store = SQLiteStore(dialogue.path)
    try:
        if invalid == "wrong_employer":
            case = store.get_case(asked.case.id)
            case.employer_question_context["employer_name"] = "Other company"
            store.save_case(case)
        else:
            sql = ("UPDATE outbox SET status='PENDING' WHERE case_id=? AND event_id=?" if invalid == "unsent"
                   else "UPDATE outbox SET sent_at='2026-09-04T10:04:00+00:00' WHERE case_id=? AND event_id=?")
            store.connection.execute(sql, (asked.case.id, asked.event.id))
            store.connection.commit()
    finally:
        store.close()
    reply = dialogue.turn("Northstar Ltd", _patch(updates=[("employer_name", "Northstar Ltd", "Northstar Ltd")]))
    assert reply.case.profile.employer_name is None
    assert reply.model.events[0].known_profile["_employer_question_verified"] is None


def test_employer_requirement_is_conditional_and_phone_placeholder_is_incomplete(tmp_path):
    _, asked = asking(tmp_path)
    for occupation in ("student", "self_employed"):
        asked.case.profile.occupation_status = occupation
        assert not {"employer_name", "employer_address", "employer_phone"}.intersection(required_profile_facts(asked.case))
    asked.case.profile.employer_phone = "unknown"
    assert not profile_fact_complete(asked.case, "employer_phone")


@pytest.mark.parametrize("field", ["employer_address", "employer_phone"])
def test_contact_uncertainty_is_bound_to_employer_and_cleared_on_change(tmp_path, field):
    dialogue, _ = asking(tmp_path)
    dialogue.turn("Northstar Ltd", _patch(updates=[("employer_name", "Northstar Ltd", "Northstar Ltd")]))
    if field == "employer_phone":
        address = "12 Example Road, Hong Kong"
        dialogue.turn(address, _patch(updates=[("employer_address", address, address)]))
    deferred = dialogue.turn("I need to check.", _patch())
    assert field in deferred.case.deferred_fields
    assert not profile_fact_complete(deferred.case, field)
    assert deferred.case.employer_detail_deferrals[-1]["employer_name"] == "Northstar Ltd"
    later = dialogue.turn("Thanks.", _patch())
    assert field in later.case.deferred_fields
    assert field not in later.case.last_requested_fields
    body = "My employer is Southstar Ltd."
    changed = dialogue.turn(body, _patch(updates=[("employer_name", "Southstar Ltd", body)]))
    assert field not in changed.case.deferred_fields
    assert changed.case.employer_detail_deferrals[-1]["employer_name"] == "Northstar Ltd"


def test_literal_completion_handles_model_omission_with_distinct_provenance(tmp_path):
    dialogue, _ = asking(tmp_path)
    first = dialogue.turn("Northstar Ltd", _patch())
    assert first.case.profile.employer_name == "Northstar Ltd"
    assert first.case.active_evidence("employer_name")[0].extraction_method == "bounded_literal_employer_parser"
    address = "12 Example Road, Hong Kong"
    second = dialogue.turn(address, _patch())
    assert second.case.profile.employer_address == address
    assert second.case.active_evidence("employer_address")[0].source_excerpt == address
    assert not second.case.active_evidence("employer_address")[0].confirmed
    assert not second.case.final_summary_confirmed and second.case.delivery_path is None
