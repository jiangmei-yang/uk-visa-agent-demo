"""List exhaustiveness is not approval of subsequently supplied contact details."""

import pytest

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    QuotedText,
    RecordCommand,
    UKContactFields,
    UKContactInput,
    apply_record_commands,
)


def transact(ledger, event, commands=(), declarations=()):
    return apply_record_commands(ledger, case_id=ledger.case_id, event_id=event,
        body="Example Doe sister London Paris New Name brother TEST00001 TEST00002 12345 staying with me full list change",
        commands=list(commands), declarations=list(declarations))


def command(action, values, prior=None):
    fields = UKContactFields(**{key: QuotedText(value=value, source_excerpt=value) for key, value in values.items()})
    kwargs = {} if prior is None else dict(target_id=prior.record_id, expected_revision_digest=prior.digest(), change_excerpt="change")
    return RecordCommand(action=action, record=UKContactInput(fields=fields), **kwargs)


def complete():
    ledger = ApplicationRecordLedger(case_id="scope-fixture")
    ledger = transact(ledger, "contact", [command("add", {"name": "Example Doe", "relationship": "sister", "address": "London"})])
    return transact(ledger, "assertion", declarations=[CollectionDeclaration(kind="uk_contact", state="complete_declared",
        source_excerpt="full list", expected_records_digest=ledger.records_digest("uk_contact"))])


@pytest.mark.parametrize("field,value", [("passport_number", "TEST00001"), ("phone", "12345"), ("support_details", "staying with me")])
def test_supplemental_detail_preserves_original_assertion_but_changes_full_fingerprint(field, value):
    original = complete()
    revised = transact(original, "detail", [command("amend", {field: value}, next(iter(original.current().values())))])
    reloaded = ApplicationRecordLedger.model_validate_json(revised.model_dump_json())
    assert reloaded.collection_state("uk_contact") == "complete_declared"
    assert reloaded.latest_declarations() == original.latest_declarations()
    assert reloaded.fingerprint() != original.fingerprint()
    assert reloaded.records_digest("uk_contact") != original.records_digest("uk_contact")
    assert next(iter(reloaded.current().values())).fields[field].source_event_id == "detail"


@pytest.mark.parametrize("field,value", [("name", "New Name"), ("relationship", "brother"), ("address", "Paris")])
def test_identity_or_location_change_cannot_inherit_list_scope_even_if_later_reverted(field, value):
    original = complete()
    prior = next(iter(original.current().values()))
    revised = transact(original, "change", [command("amend", {field: value}, prior)])
    reverted = transact(revised, "revert", [command("amend", {field: prior.fields[field].value}, next(iter(revised.current().values())))])
    assert revised.collection_state("uk_contact") == reverted.collection_state("uk_contact") == "partial"


@pytest.mark.parametrize("action", ["add", "withdraw"])
def test_membership_change_invalidates_exhaustiveness(action):
    original = complete()
    cmd = command("add", {"name": "New Name"}) if action == "add" else command("withdraw", {}, next(iter(original.current().values())))
    revised = transact(original, "membership", [cmd])
    assert revised.collection_state("uk_contact") == "partial"
    if action == "add":
        new_record = next(item for key, item in revised.current().items() if key not in original.current())
        undone = transact(revised, "undo", [command("withdraw", {}, new_record)])
        assert undone.collection_state("uk_contact") == "partial"


def test_supplemental_correction_does_not_reassert_a_partial_list():
    original = complete()
    prior = next(iter(original.current().values()))
    revised = transact(original, "identity", [command("amend", {"name": "New Name"}, prior)])
    revised = transact(revised, "number", [command("amend", {"passport_number": "TEST00001"}, next(iter(revised.current().values())))])
    assert revised.collection_state("uk_contact") == "partial"


def test_reconfirmation_establishes_a_new_scope_without_rewriting_history():
    original = complete()
    revised = transact(original, "identity", [command("amend", {"name": "New Name"}, next(iter(original.current().values())))])
    revised = transact(revised, "reassert", declarations=[CollectionDeclaration(kind="uk_contact", state="complete_declared",
        source_excerpt="full list", expected_records_digest=revised.records_digest("uk_contact"))])
    revised = transact(revised, "number", [command("amend", {"passport_number": "TEST00002"}, next(iter(revised.current().values())))])
    assert revised.collection_state("uk_contact") == "complete_declared"
    assert len(revised.declarations) == 2
    assert revised.declarations[0] == original.declarations[0]
