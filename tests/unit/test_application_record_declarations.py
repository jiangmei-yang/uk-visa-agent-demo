"""Collection assertions require explicit provenance and never authorize delivery."""

import pytest
from pydantic import ValidationError

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    QuotedText,
    RecordCommand,
    TravelFields,
    TravelInput,
    application_record_rows,
    apply_record_commands,
)


def blank():
    return ApplicationRecordLedger(case_id="fictional-declarations")


def trip(country="Japan"):
    return RecordCommand(action="add", record=TravelInput(fields=TravelFields(
        country=QuotedText(value=country, source_excerpt=country))))


def transaction(ledger, *, event="fictional-event-1", body="I visited Japan.", commands=None, declarations=None):
    return apply_record_commands(ledger, case_id=ledger.case_id, event_id=event, body=body,
                                 commands=commands or [], declarations=declarations)


def declaration(ledger, *, kind="travel", state="unknown", excerpt="I am unsure."):
    return CollectionDeclaration(kind=kind, state=state, source_excerpt=excerpt,
                                 expected_records_digest=ledger.records_digest(kind))


@pytest.mark.parametrize("kind", ["travel", "uk_contact"])
def test_silence_is_unasked_and_an_empty_transaction_cannot_mean_none(kind):
    ledger = blank()
    updated = transaction(ledger, body="Thank you.")
    assert updated.collection_state(kind) == ledger.collection_state(kind) == "unasked"
    assert updated.fingerprint() == ledger.fingerprint()
    assert not updated.declarations


def test_a_trip_is_partial_not_an_exhaustive_travel_history():
    updated = transaction(blank(), commands=[trip()])
    assert updated.collection_state("travel") == "partial"
    assert updated.collection_state("uk_contact") == "unasked"
    assert updated.customer_snapshot()["completeness"] == "not_assessed"


@pytest.mark.parametrize("kind", ["travel", "uk_contact"])
@pytest.mark.parametrize("state", ["unknown", "partial", "none_declared"])
def test_explicit_empty_collection_state_survives_reload_with_original_source(kind, state):
    ledger = blank()
    # Semantic interpretation is a trusted-planner responsibility; this tests
    # the provenance/integrity boundary, not an English/Chinese classifier.
    command = declaration(ledger, kind=kind, state=state, excerpt="explicit fictional statement")
    updated = transaction(ledger, body=command.source_excerpt, declarations=[command])
    reloaded = ApplicationRecordLedger.model_validate_json(updated.model_dump_json())
    assert reloaded == updated and reloaded.collection_state(kind) == state
    receipt = reloaded.latest_declarations()[kind]
    assert receipt.source_event_id == "fictional-event-1"
    assert len(receipt.source_body_sha256) == 64
    assert receipt.source_excerpt == command.source_excerpt
    assert receipt.expected_records_digest == ledger.records_digest(kind)
    assert ledger.fingerprint() != reloaded.fingerprint()
    assert reloaded.customer_snapshot()["completeness"] == "not_assessed"


def test_uncertainty_is_preserved_across_unrelated_messages_and_partial_new_information():
    ledger = blank()
    updated = transaction(ledger, body="I am unsure.", declarations=[declaration(ledger)])
    later = transaction(updated, event="fictional-event-2", body="Thank you.")
    added = transaction(later, event="fictional-event-3", commands=[trip()])
    assert added.collection_state("travel") == "unknown"
    assert added.latest_declarations()["travel"].source_event_id == "fictional-event-1"
    assert "deferred for checking" in "\n".join(application_record_rows(added))
    assert "暂时不确定" in "\n".join(application_record_rows(added, "zh"))


def test_none_is_explicit_and_does_not_hide_later_records():
    ledger = blank()
    command = declaration(ledger, state="none_declared", excerpt="I have never travelled abroad.")
    updated = transaction(ledger, body=command.source_excerpt, declarations=[command])
    assert updated.collection_state("travel") == "none_declared"
    assert "You explicitly stated there are no relevant records." in application_record_rows(updated)
    later = transaction(updated, event="fictional-event-2", commands=[trip()])
    assert later.collection_state("travel") == "partial"
    assert "You explicitly stated there are no relevant records." not in application_record_rows(later)
    assert len(later.declarations) == 1  # old assertion retained, not rewritten


def test_complete_declaration_is_bound_to_the_exact_records_and_not_verified_details():
    ledger = transaction(blank(), commands=[trip()])
    command = declaration(ledger, state="complete_declared", excerpt="That is my full travel history.")
    complete = transaction(ledger, event="fictional-event-2", body=command.source_excerpt, declarations=[command])
    assert complete.collection_state("travel") == "complete_declared"
    assert "details still need checking" in "\n".join(application_record_rows(complete))
    assert "Travel period: Not provided" in application_record_rows(complete)
    assert complete.customer_snapshot()["completeness"] == "not_assessed"
    later = transaction(complete, event="fictional-event-3", body="I also visited Korea.", commands=[trip("Korea")])
    assert later.collection_state("travel") == "partial"
    assert ApplicationRecordLedger.model_validate_json(later.model_dump_json()) == later


@pytest.mark.parametrize("action", ["amend", "withdraw"])
def test_correction_or_withdrawal_invalidates_old_exhaustive_assertion(action):
    ledger = transaction(blank(), commands=[trip()])
    command = declaration(ledger, state="complete_declared", excerpt="That is the full list.")
    complete = transaction(ledger, event="fictional-event-2", body=command.source_excerpt, declarations=[command])
    target = next(iter(complete.current().values()))
    fields = TravelFields(period=QuotedText(value="2023", source_excerpt="2023")) if action == "amend" else TravelFields()
    change = RecordCommand(action=action, record=TravelInput(fields=fields), target_id=target.record_id,
                           expected_revision_digest=target.digest(), change_excerpt="Correct that trip: 2023")
    updated = transaction(complete, event="fictional-event-3", body=change.change_excerpt, commands=[change])
    assert updated.collection_state("travel") == "partial"
    assert not updated.current() if action == "withdraw" else bool(updated.current())


def test_unrelated_collection_does_not_invalidate_travel_assertion():
    ledger = transaction(blank(), commands=[trip()])
    command = declaration(ledger, state="complete_declared", excerpt="That is the full list.")
    complete = transaction(ledger, event="fictional-event-2", body=command.source_excerpt, declarations=[command])
    contact = declaration(complete, kind="uk_contact", state="none_declared", excerpt="No UK contacts.")
    updated = transaction(complete, event="fictional-event-3", body=contact.source_excerpt, declarations=[contact])
    assert updated.collection_state("travel") == "complete_declared"
    assert updated.collection_state("uk_contact") == "none_declared"


def test_add_and_explicit_full_list_are_one_atomic_replayable_event():
    ledger = blank()
    body = "I visited Japan. That is the full list."
    preview = transaction(ledger, body=body, commands=[trip()])
    command = declaration(preview, state="complete_declared", excerpt="That is the full list.")
    updated = transaction(ledger, body=body, commands=[trip()], declarations=[command])
    assert updated.collection_state("travel") == "complete_declared"
    assert transaction(updated, body=body, commands=[trip()], declarations=[command]) == updated
    with pytest.raises(ValueError, match="reinterpreted"):
        transaction(updated, body=body, commands=[trip()])
    assert not ledger.revisions and not ledger.declarations


def test_failed_declaration_rolls_back_records_and_replay_bookkeeping():
    ledger = blank()
    before = ledger.model_dump_json()
    command = declaration(ledger, state="complete_declared", excerpt="That is the full list.")
    with pytest.raises(ValueError, match="stale"):
        transaction(ledger, body="I visited Japan. That is the full list.", commands=[trip()], declarations=[command])
    assert ledger.model_dump_json() == before


@pytest.mark.parametrize("failure", ["source", "foreign", "duplicate", "none_with_records", "complete_without_records"])
def test_invalid_declarations_reject_without_changing_the_ledger(failure):
    ledger = transaction(blank(), commands=[trip()]) if failure == "none_with_records" else blank()
    command = declaration(ledger)
    body = command.source_excerpt
    if failure == "source":
        body = "No such statement."
    elif failure == "foreign":
        command = declaration(ApplicationRecordLedger(case_id="foreign-case"))
    elif failure == "none_with_records":
        command = declaration(ledger, state="none_declared")
    elif failure == "complete_without_records":
        command = declaration(ledger, state="complete_declared")
    before = ledger.model_dump_json()
    with pytest.raises(ValueError):
        transaction(ledger, event="fictional-event-2", body=body,
                    declarations=[command, command] if failure == "duplicate" else [command])
    assert ledger.model_dump_json() == before


def test_a_later_explicit_declaration_supersedes_uncertainty_without_erasing_it():
    ledger = blank()
    uncertain = transaction(ledger, body="I am unsure.", declarations=[declaration(ledger)])
    clear = declaration(uncertain, state="none_declared", excerpt="I checked: no travel history.")
    updated = transaction(uncertain, event="fictional-event-2", body=clear.source_excerpt, declarations=[clear])
    assert updated.collection_state("travel") == "none_declared" and len(updated.declarations) == 2
    assert updated.declarations[1].predecessor_digest == updated.declarations[0].digest()
    assert updated.declarations[0].state == "unknown"
    assert ApplicationRecordLedger.model_validate_json(updated.model_dump_json()) == updated


@pytest.mark.parametrize("mutation", ["revision", "predecessor", "snapshot", "contradiction"])
def test_reload_rejects_broken_declaration_history(mutation):
    ledger = blank()
    updated = transaction(ledger, body="I am unsure.", declarations=[declaration(ledger)])
    data = updated.model_dump(mode="json")
    item = data["declarations"][0]
    if mutation == "revision":
        item["revision"] = 2
    elif mutation == "predecessor":
        item["predecessor_digest"] = "0" * 64
    elif mutation == "snapshot":
        item["expected_records_digest"] = "0" * 64
    else:
        item["state"] = "complete_declared"
    with pytest.raises(ValidationError):
        ApplicationRecordLedger.model_validate(data)


def test_blank_statement_or_implicit_not_applicable_state_is_not_accepted():
    with pytest.raises(ValidationError):
        declaration(blank(), excerpt="  ")
    with pytest.raises(ValidationError):
        declaration(blank(), state="not_applicable")


def test_old_serialized_ledgers_load_without_inventing_declarations():
    ledger = transaction(blank(), commands=[trip()])
    payload = ledger.model_dump(mode="json", exclude={"declarations"})
    reloaded = ApplicationRecordLedger.model_validate(payload)
    assert not reloaded.declarations and reloaded.fingerprint() == ledger.fingerprint()
    assert reloaded.collection_state("travel") == "partial"
