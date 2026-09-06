"""Per-field uncertainty is sourced, local, durable and never a supplied value."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_contextual_collection_answer import context

from visa_agent.domain.application_records import ApplicationRecordLedger, application_record_rows
from visa_agent.domain.models import InboundEvent
from visa_agent.workflow.record_collection_plan import (
    collection_question_text,
    contextual_collection_declaration,
    contextual_record_field_deferral,
    plan_collection_follow_up,
)
from visa_agent.workflow.record_intake import plan_record_intake


def detail_context():
    case, row = context(partial=True)
    question = next(item for item in plan_collection_follow_up(case.application_records, case_id=case.id).pending
                    if item.field == "period")
    case.collection_question_event_ids = {question.key: [row["event_id"]]}
    row["payload"] = collection_question_text(question, case.application_records, "en")
    return case, row


def apply(case, row, body="I need to check."):
    deferred = contextual_record_field_deferral(case, body, "answer", [row])
    event = InboundEvent(id="answer", channel="gmail", external_thread_id=case.external_thread_id,
                         sender=case.applicant_contact, subject="Fictional records", body=body,
                         received_at=datetime(2026, 9, 7, 2, tzinfo=UTC))
    result = plan_record_intake(event, case.application_records, case_id=case.id,
                               records=[], declarations=[], contextual_field_deferral=deferred)
    return result, deferred, event


def test_detail_uncertainty_survives_reload_without_becoming_collection_unknown():
    case, row = detail_context()
    before = case.application_records.fingerprint()
    result, deferred, event = apply(case, row)
    assert result.changed and not result.requires_review
    ledger = ApplicationRecordLedger.model_validate_json(result.ledger.model_dump_json())
    assert ledger.active_field_deferrals() == [deferred]
    assert ledger.collection_state("travel") == "partial"
    assert "period" not in next(iter(ledger.current().values())).fields
    assert ledger.fingerprint() != before
    assert ledger.customer_snapshot()["deferred_details"][0]["source_event_id"] == "answer"
    questions = plan_collection_follow_up(ledger, case_id=case.id).pending
    assert not any(item.field == "period" for item in questions)
    assert any(item.field == "purpose" for item in questions)
    replay = plan_record_intake(event, ledger, case_id=case.id, records=[], declarations=[], contextual_field_deferral=deferred)
    assert not replay.changed and replay.ledger == ledger


@pytest.mark.parametrize("language,label,uncertain", [
    ("en", "Travel period", "You said you are unsure; deferred for checking."),
    ("zh", "旅行时间", "你表示暂时不清楚，留待核实。"),
])
def test_readable_summary_distinguishes_deferred_from_unprovided(language, label, uncertain):
    case, row = detail_context()
    result, _, _ = apply(case, row)
    rows = application_record_rows(result.ledger, language)
    assert f"{label}: {uncertain}" in rows
    assert f"{label}: Not provided" not in rows and f"{label}: 尚未提供" not in rows
    assert not any("source_event_id" in line or "question_key" in line for line in rows)


@pytest.mark.parametrize("body", ["No.", "没有。", "If I need to check.", "> I need to check."])
def test_detail_negative_or_noncurrent_text_does_not_defer_or_declare_absence(body):
    case, row = detail_context()
    assert contextual_record_field_deferral(case, body, "answer", [row]) is None
    assert contextual_collection_declaration(case, body, [row]) is None


@pytest.mark.parametrize("field,value", [("case_id", "foreign"), ("record_id", "other"),
                                       ("record_digest", "0" * 64), ("field", "country"), ("field", "unsupported")])
def test_reload_rejects_foreign_stale_or_already_supplied_target(field, value):
    case, row = detail_context()
    result, _, _ = apply(case, row)
    data = result.ledger.model_dump(mode="json")
    data["field_deferrals"][0][field] = value
    with pytest.raises(ValidationError):
        ApplicationRecordLedger.model_validate(data)


@pytest.mark.parametrize("field,value", [("source_event_id", "foreign"), ("source_excerpt", "different words"),
                                       ("source_body_sha256", "0" * 64)])
def test_intake_rejects_mismatched_current_answer_source_atomically(field, value):
    case, row = detail_context()
    _, deferred, event = apply(case, row)
    original = case.application_records.model_dump_json()
    result = plan_record_intake(event, case.application_records, case_id=case.id, records=[], declarations=[],
                               contextual_field_deferral=deferred.model_copy(update={field: value}))
    assert result.requires_review and not result.changed
    assert case.application_records.model_dump_json() == original


def test_send_timestamp_tie_is_ambiguous_not_arbitrary_id_order():
    case, row = detail_context()
    other = {**row, "id": "other", "event_id": "other-event"}
    assert contextual_record_field_deferral(case, "Not sure", "answer", [row, other]) is None
