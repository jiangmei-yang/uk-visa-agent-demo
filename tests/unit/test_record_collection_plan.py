"""A follow-up plan cannot silently declare completeness or forget uncertainty."""

import pytest

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    QuotedText,
    RecordCommand,
    TravelFields,
    TravelInput,
    apply_record_commands,
)
from visa_agent.workflow.record_collection_plan import (
    collection_question_text,
    plan_collection_follow_up,
)


def ledger_with(state=None, trip=False):
    ledger = ApplicationRecordLedger(case_id="fictional-plan")
    if trip:
        ledger = apply_record_commands(
            ledger, case_id=ledger.case_id, event_id="trip", body="Japan",
            commands=[RecordCommand(action="add", record=TravelInput(fields=TravelFields(
                country=QuotedText(value="Japan", source_excerpt="Japan"))))],
        )
    if state:
        ledger = apply_record_commands(
            ledger, case_id=ledger.case_id, event_id="assertion", body="Explicit statement",
            commands=[], declarations=[CollectionDeclaration(
                kind="travel", state=state, source_excerpt="Explicit statement",
                expected_records_digest=ledger.records_digest("travel"),
            )],
        )
    return ledger


def plan(ledger):
    return plan_collection_follow_up(ledger, case_id="fictional-plan")


def test_legacy_missing_ledger_is_unasked_not_none():
    result = plan(None)
    assert [(item.kind, item.reason) for item in result.pending] == [
        ("travel", "unasked"), ("uk_contact", "unasked"),
    ]
    assert not result.deferred
    assert result == plan(ledger_with())


@pytest.mark.parametrize("trip", [False, True])
def test_unknown_defers_whole_collection_even_with_incomplete_records(trip):
    ledger = ledger_with("unknown", trip)
    before = ledger.model_dump_json()
    result = plan(ledger)
    assert result.deferred == ("travel",)
    assert all(item.kind != "travel" for item in result.pending)
    assert ledger.model_dump_json() == before


def test_explicit_none_has_no_follow_up_for_that_collection():
    result = plan(ledger_with("none_declared"))
    assert not result.deferred
    assert [item.kind for item in result.pending] == ["uk_contact"]


@pytest.mark.parametrize("state", [None, "partial", "complete_declared"])
def test_full_list_is_not_complete_details(state):
    result = plan(ledger_with(state, trip=True))
    travel = [item for item in result.pending if item.kind == "travel"]
    assert [item.field for item in travel if item.reason == "missing_detail"] == ["period", "purpose"]
    assert any(item.reason == "partial" for item in travel) == (state != "complete_declared")


def test_reopening_keeps_question_keys_and_does_not_reask_pending_question():
    ledger = ledger_with(trip=True)
    initial = plan(ledger)
    reopened = plan(ApplicationRecordLedger.model_validate_json(ledger.model_dump_json()))
    assert reopened == initial
    asked = frozenset(item.key for item in initial.pending)
    assert reopened.next_unasked(asked) is None
    assert initial.next_unasked(frozenset()) == initial.pending[0]


def test_foreign_ledger_cannot_plan_another_customers_questions():
    with pytest.raises(ValueError, match="case-local"):
        plan_collection_follow_up(ledger_with(), case_id="another-customer")


def test_stale_none_assertion_cannot_hide_a_new_trip():
    ledger = ledger_with("none_declared")
    ledger = apply_record_commands(
        ledger, case_id=ledger.case_id, event_id="later", body="Japan",
        commands=[RecordCommand(action="add", record=TravelInput(fields=TravelFields(
            country=QuotedText(value="Japan", source_excerpt="Japan"))))],
    )
    assert any(item.kind == "travel" and item.reason == "partial" for item in plan(ledger).pending)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_one_detail_question_uses_stored_record_identity_and_allows_uncertainty(language):
    ledger = ledger_with("complete_declared", trip=True)
    question = next(item for item in plan(ledger).pending if item.field == "period")
    text = collection_question_text(question, ledger, language)
    assert "Japan" in text
    assert ("暂时不清楚" if language == "zh" else "unsure") in text
    assert "2023" not in text
    assert text.count("？" if language == "zh" else "?") == 1


def test_missing_detail_record_cannot_be_rendered_from_another_ledger():
    ledger = ledger_with(trip=True)
    question = next(item for item in plan(ledger).pending if item.field == "period")
    with pytest.raises(ValueError, match="current record"):
        collection_question_text(question, ledger_with(), "en")
