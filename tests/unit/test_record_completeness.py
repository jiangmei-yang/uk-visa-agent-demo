"""Universal intake checks: empty state is never an absence declaration."""

import pytest
from test_application_record_declarations import blank, declaration, transaction, trip

from visa_agent.domain.record_completeness import application_record_checks


def checks(ledger):
    return application_record_checks(ledger, case_id="fictional-declarations")


def test_legacy_missing_ledger_fails_universal_declaration_check():
    assert not checks(None)["application_collections_explicitly_declared"]
    assert not checks(blank())["application_collections_explicitly_declared"]


@pytest.mark.parametrize("state", ["unknown", "partial", "none_declared"])
def test_one_collection_statement_cannot_cover_both_collections(state):
    ledger = blank()
    updated = transaction(ledger, body="I am unsure.", declarations=[declaration(ledger, state=state)])
    assert not checks(updated)["application_collections_explicitly_declared"]


def test_two_explicit_none_declarations_pass_intake_without_adding_records():
    ledger = blank()
    updated = transaction(ledger, body="Explicit fictional statement.", declarations=[
        declaration(ledger, kind=kind, state="none_declared", excerpt="Explicit fictional statement.")
        for kind in ("travel", "uk_contact")
    ])
    assert all(checks(updated).values())
    assert not updated.current()


def test_full_list_does_not_hide_missing_record_details():
    ledger = transaction(blank(), commands=[trip()])
    updated = transaction(ledger, event="list", body="Explicit fictional statement.", declarations=[
        declaration(ledger, state="complete_declared", excerpt="Explicit fictional statement."),
        declaration(ledger, kind="uk_contact", state="none_declared", excerpt="Explicit fictional statement."),
    ])
    assert checks(updated)["application_collections_explicitly_declared"]
    assert not checks(updated)["application_record_descriptive_fields_complete"]


def test_foreign_ledger_cannot_satisfy_a_customers_intake():
    with pytest.raises(ValueError, match="case-local"):
        application_record_checks(blank(), case_id="another-customer")
