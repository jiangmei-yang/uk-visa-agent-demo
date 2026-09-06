"""Actual persisted intake plus negative source bindings; zero external sends."""

import pytest
from test_application_record_workflow import patch, record
from test_consultant_value import Conversation

from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.record_source_audit import audit_application_record_sources


def test_actual_record_and_correction_sources_are_registered_in_the_same_case(tmp_path):
    dialogue = Conversation(tmp_path)
    text = "I visited Japan in May 2023 for tourism."
    first = dialogue.turn(text, patch(records=[record(text, {
        "country": "Japan", "period": "May 2023", "purpose": "tourism",
    })]))
    text = "Please correct the Japan trip to June 2023."
    second = dialogue.turn(text, patch(records=[record(text, {"period": "June 2023"}, action="amend", reference="Japan")]))
    store = SQLiteStore(dialogue.path)
    try:
        before = store.get_case(second.case.id).model_dump_json()
        result = audit_application_record_sources(store, second.case)
        assert result.registered_sources_match
        assert result.checked_event_ids == (first.event.id, second.event.id)
        assert store.get_case(second.case.id).model_dump_json() == before
        assert second.case.delivery_path is None and not second.case.final_summary_confirmed
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["missing", "foreign"])
def test_quote_and_hash_alone_cannot_prove_a_registered_source(tmp_path, failure):
    dialogue = Conversation(tmp_path)
    text = "I visited Japan in May 2023."
    turn = dialogue.turn(text, patch(records=[record(text, {"country": "Japan", "period": "May 2023"})]))
    store = SQLiteStore(dialogue.path)
    try:
        if failure == "missing":
            store.connection.execute("DELETE FROM processed_events WHERE event_id=?", (turn.event.id,))
        else:
            store.connection.execute("UPDATE processed_events SET case_id=? WHERE event_id=?", ("another-case", turn.event.id))
        result = audit_application_record_sources(store, turn.case)
        assert not result.registered_sources_match
        assert result.issues == (("source_event_not_registered:" if failure == "missing" else "source_event_case_mismatch:") + turn.event.id,)
    finally:
        store.close()
