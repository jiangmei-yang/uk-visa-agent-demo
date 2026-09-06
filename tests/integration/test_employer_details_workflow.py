"""Fictional current-employer persistence and old-employer invalidation."""

from test_consultant_value import Conversation, _patch

from visa_agent.domain.models import Document, DocumentStatus, Evidence
from visa_agent.domain.rules import build_requirements
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
