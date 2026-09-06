"""Late-intake fictional family-detail conversation with actual captured SENT replies."""

from test_application_record_workflow import patch, record
from test_consultant_value import Conversation
from test_next_step_workflow import _seed

from visa_agent.llm.application_records import CollectionDeclarationProposal
from visa_agent.storage.sqlite import SQLiteStore


def test_family_passport_question_can_be_deferred_then_supplied_without_reasking(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn("Hello, I would like to prepare a UK visitor application.", patch())
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        # Only the scalar profile is a pre-populated fictional fixture. Contact
        # and deferral evidence below must enter through actual inbound turns.
        seed = _seed()
        case.profile = seed.profile
        case.deferred_fields = seed.deferred_fields
        store.save_case(case)
    finally:
        store.close()
    dialogue.turn("Please help me organise these details.", patch())
    statement = "My sister Example Doe lives at 1 Example Road, London, UK."
    body = statement + " My UK contacts list is complete. I have no travel history."
    asked = dialogue.turn(body, patch(
        records=[record(statement, {"name": "Example Doe", "relationship": "sister", "address": "1 Example Road, London, UK"}, kind="uk_contact")],
        assertions=[
            CollectionDeclarationProposal(kind="uk_contact", state="complete_declared", source_excerpt="My UK contacts list is complete.", confidence=1),
            CollectionDeclarationProposal(kind="travel", state="none_declared", source_excerpt="I have no travel history.", confidence=1),
        ],
    ))
    assert "relative's passport number" in asked.body
    assert "could you provide it after checking?" in asked.body
    unknown = dialogue.turn("I'm not sure.", patch())
    deferrals = unknown.case.application_records.active_field_deferrals()
    assert len(deferrals) == 1 and deferrals[0].field == "passport_number"
    assert deferrals[0].question_event_id == asked.event.id
    assert "could you provide it after checking?" not in unknown.body
    assert unknown.case.application_records.collection_state("uk_contact") == "complete_declared"
    # Deliberately synthetic value; avoid describing the applicant's statement
    # as hypothetical inside the actual body being tested.
    correction = "Please correct Example Doe's passport number to TEST00001."
    supplied = dialogue.turn(correction, patch(records=[record(correction, {"passport_number": "TEST00001"},
        kind="uk_contact", action="amend", reference="Example Doe")]))
    ledger = supplied.case.application_records
    assert ledger.collection_state("uk_contact") == "complete_declared"
    assert ledger.latest_declarations()["uk_contact"] == asked.case.application_records.latest_declarations()["uk_contact"]
    assert not ledger.active_field_deferrals() and len(ledger.field_deferrals) == 1
    assert next(iter(ledger.current().values())).fields["passport_number"].source_event_id == supplied.event.id
    assert "could you provide it after checking?" not in supplied.body
    assert "Are there any other UK relatives or contacts to add?" not in supplied.body
    assert supplied.case.delivery_path is None and not supplied.case.final_summary_confirmed
