"""Draft record transactions through the case/store/summary boundary, no model or mail."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from visa_agent.delivery.pack import _profile_rows
from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    QuotedText,
    RecordCommand,
    TravelFields,
    TravelInput,
    UKContactFields,
    UKContactInput,
)
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.application_records import case_with_record_commands
from visa_agent.workflow.conversation import confirmation_message, summary_fingerprint


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Record persistence tests cannot use a model or mailbox")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.socket.connect", deny)


def quote(value):
    return QuotedText(value=value, source_excerpt=value)


def initial():
    case = Case(id="fictional-record-case", external_thread_id="fictional-record-thread",
                applicant_contact="records@example.test", policy_version="2026-02-25")
    event = InboundEvent(id="record-event-1", channel="gmail", external_thread_id=case.external_thread_id,
                         sender=case.applicant_contact, subject="Visa preparation", received_at=datetime(2026, 9, 6, tzinfo=UTC),
                         body="I visited Japan in May 2023 for tourism. My sister Fictional Example lives at 1 Fictional Street, London.")
    commands = [RecordCommand(action="add", record=TravelInput(fields=TravelFields(
        country=quote("Japan"), period=quote("May 2023"), purpose=quote("tourism")))),
        RecordCommand(action="add", record=UKContactInput(fields=UKContactFields(
            name=quote("Fictional Example"), relationship=quote("sister"), address=quote("1 Fictional Street, London"))))]
    return case, event, commands


def test_case_records_survive_sqlite_reopen_and_match_both_summary_projections(tmp_path):
    case, event, commands = initial()
    updated = case_with_record_commands(case, event, commands)
    with_store = SQLiteStore(tmp_path / "records.db")
    try:
        with_store.save_case(updated)
    finally:
        with_store.close()
    reopened = SQLiteStore(tmp_path / "records.db")
    try:
        saved = reopened.get_case(case.id)
    finally:
        reopened.close()
    assert saved == updated and saved.application_records.case_id == case.id
    for text in [confirmation_message(saved), "\n".join(_profile_rows(saved))]:
        assert all(value in text for value in ["Japan", "May 2023", "tourism", "Fictional Example",
                                              "sister", "1 Fictional Street, London"])
        assert "applicant declarations are not independent verification" in text
        assert "the list has not been confirmed as exhaustive" in text
    assert not saved.profile_confirmed and not saved.final_summary_confirmed
    assert saved.profile == case.profile  # contact is not sponsor, address or risk flag


@pytest.mark.parametrize("include_documents", [False, True])
def test_record_correction_invalidates_previously_matching_summary_without_granting_anything(include_documents):
    case, event, commands = initial()
    saved = case_with_record_commands(case, event, commands)
    before_fingerprint = summary_fingerprint(saved, include_documents=include_documents)
    saved.profile_confirmed = saved.final_summary_confirmed = True
    saved.confirmation_fingerprint = before_fingerprint
    saved.confirmation_kind = "final" if include_documents else "profile"
    saved.confirmation_request_event_id = "old-summary-event"
    saved.preparation_paused = True
    trip = next(r for r in saved.application_records.current().values() if r.kind == "travel")
    correction = RecordCommand(action="amend", record=TravelInput(fields=TravelFields(period=quote("June 2023"))),
                               target_id=trip.record_id, expected_revision_digest=trip.digest(),
                               change_excerpt="The Japan trip was in June 2023")
    next_event = event.model_copy(update={"id": "record-event-2", "body": correction.change_excerpt})
    revised = case_with_record_commands(saved, next_event, [correction])
    assert summary_fingerprint(revised, include_documents=include_documents) != before_fingerprint
    assert not revised.profile_confirmed and not revised.final_summary_confirmed
    assert revised.confirmation_fingerprint is revised.confirmation_kind is revised.confirmation_request_event_id is None
    assert revised.preparation_paused and revised.profile == saved.profile
    assert saved.profile_confirmed and saved.final_summary_confirmed  # input snapshot is untouched
    assert "June 2023" in confirmation_message(revised.model_copy(update={"preparation_paused": False}))
    assert "May 2023" not in "\n".join(_profile_rows(revised))
    assert revised.application_records.revisions[0].fields["period"].value == "May 2023"


def test_empty_new_ledger_does_not_change_legacy_summary_fingerprint():
    case, _, _ = initial()
    before = summary_fingerprint(case, include_documents=True)
    reloaded = Case.model_validate_json(case.model_dump_json(exclude={"application_records"}))
    assert reloaded.application_records is None
    reloaded.application_records = ApplicationRecordLedger(case_id=case.id)
    assert summary_fingerprint(reloaded, include_documents=True) == before


@pytest.mark.parametrize("status", [CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION,
                                    CaseStatus.HUMAN_REVIEW_REQUIRED])
def test_record_adapter_cannot_bypass_non_draft_revision_or_review_workflow(status):
    case, event, commands = initial()
    case.status = status
    with pytest.raises(ValueError, match="revision workflow"):
        case_with_record_commands(case, event, commands)
    assert case.application_records is None


@pytest.mark.parametrize("change", [{"sender": "someone-else@example.test"}, {"external_thread_id": "other-thread"}])
def test_record_adapter_rejects_foreign_applicant_or_thread(change):
    case, event, commands = initial()
    with pytest.raises(ValueError, match="applicant thread"):
        case_with_record_commands(case, event.model_copy(update=change), commands)


def test_case_reload_rejects_foreign_record_ledger():
    case, _, _ = initial()
    data = case.model_dump(mode="json")
    data["application_records"] = ApplicationRecordLedger(case_id="other-case").model_dump()
    with pytest.raises(ValidationError, match="different case"):
        Case.model_validate(data)


def test_customer_projection_omits_withdrawn_records_but_ledger_retains_them():
    case, event, commands = initial()
    saved = case_with_record_commands(case, event, commands)
    trip = next(r for r in saved.application_records.current().values() if r.kind == "travel")
    removal = RecordCommand(action="withdraw", record=TravelInput(fields=TravelFields()),
                            target_id=trip.record_id, expected_revision_digest=trip.digest(),
                            change_excerpt="Please remove the Japan trip; that entry was a mistake")
    revised = case_with_record_commands(saved, event.model_copy(update={"id": "record-event-2", "body": removal.change_excerpt}),
                                       [removal])
    snapshot = revised.application_records.customer_snapshot()
    assert snapshot["completeness"] == "not_assessed"
    assert "Japan" not in str(snapshot) and "Japan" not in "\n".join(_profile_rows(revised))
    assert "Fictional Example" in str(snapshot)
    assert "processed_batches" not in snapshot
    assert "Japan" in revised.application_records.model_dump_json()


@pytest.mark.parametrize("include_documents", [False, True])
@pytest.mark.parametrize("state", ["unknown", "none_declared"])
def test_declaration_without_records_invalidates_summary_and_survives_database_reload(tmp_path, include_documents, state):
    case, event, _ = initial()
    previous = summary_fingerprint(case, include_documents=include_documents)
    case.profile_confirmed = case.final_summary_confirmed = True
    case.confirmation_fingerprint = previous
    case.confirmation_kind = "final" if include_documents else "profile"
    case.confirmation_request_event_id = "old-summary-event"
    case.preparation_paused = True
    ledger = ApplicationRecordLedger(case_id=case.id)
    excerpt = "I am unsure about my travel history." if state == "unknown" else "I have never travelled abroad."
    assertion = CollectionDeclaration(kind="travel", state=state, source_excerpt=excerpt,
                                      expected_records_digest=ledger.records_digest("travel"))
    updated = case_with_record_commands(case, event.model_copy(update={"body": excerpt}), [], declarations=[assertion])
    assert updated.application_records.revisions == []
    assert summary_fingerprint(updated, include_documents=include_documents) != previous
    assert not updated.profile_confirmed and not updated.final_summary_confirmed
    assert updated.confirmation_fingerprint is updated.confirmation_kind is updated.confirmation_request_event_id is None
    assert updated.preparation_paused and updated.profile == case.profile
    assert case.application_records is None and case.profile_confirmed and case.final_summary_confirmed
    store = SQLiteStore(tmp_path / "declarations.db")
    try:
        store.save_case(updated)
    finally:
        store.close()
    reopened = SQLiteStore(tmp_path / "declarations.db")
    try:
        restored = reopened.get_case(case.id)
    finally:
        reopened.close()
    assert restored == updated and restored.application_records.collection_state("travel") == state
    assert summary_fingerprint(restored, include_documents=include_documents) != previous
    assert "never travelled" not in "\n".join(_profile_rows(restored))  # no unreviewed source-quote insertion


def test_adding_a_record_after_none_clears_new_confirmation_without_erasing_the_declaration():
    case, event, commands = initial()
    ledger = ApplicationRecordLedger(case_id=case.id)
    assertion = CollectionDeclaration(kind="travel", state="none_declared", source_excerpt="I have never travelled abroad.",
                                      expected_records_digest=ledger.records_digest("travel"))
    declared = case_with_record_commands(case, event.model_copy(update={"body": assertion.source_excerpt}), [], declarations=[assertion])
    declared.profile_confirmed = declared.final_summary_confirmed = True
    declared.confirmation_fingerprint = summary_fingerprint(declared, include_documents=True)
    revised = case_with_record_commands(declared, event.model_copy(update={"id": "record-event-2"}), commands)
    assert revised.application_records.collection_state("travel") == "partial"
    assert revised.application_records.declarations[0].state == "none_declared"
    assert not revised.profile_confirmed and not revised.final_summary_confirmed
    assert "You explicitly stated there are no relevant records." not in _profile_rows(revised)


def test_identical_declaration_event_replay_does_not_invalidate_a_later_confirmation():
    case, event, _ = initial()
    ledger = ApplicationRecordLedger(case_id=case.id)
    assertion = CollectionDeclaration(kind="travel", state="unknown", source_excerpt="I am unsure.",
                                      expected_records_digest=ledger.records_digest("travel"))
    event = event.model_copy(update={"body": assertion.source_excerpt})
    updated = case_with_record_commands(case, event, [], declarations=[assertion])
    updated.profile_confirmed = updated.final_summary_confirmed = True
    updated.confirmation_fingerprint = summary_fingerprint(updated, include_documents=True)
    replayed = case_with_record_commands(updated, event, [], declarations=[assertion])
    assert replayed == updated
