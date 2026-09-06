"""Actual workflow/store/reviewed sender with fictional extraction and captured Gmail.

Provider proposals are substituted, not measured DeepSeek output. No network or
real mailbox is used; existing Conversation dispatches and verifies actual SENT
payloads and reopens SQLite on every turn.
"""

import pytest
from test_consultant_value import Conversation, _patch

from visa_agent.domain.models import CaseStatus
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.application_records import (
    ApplicationRecordProposal,
    CollectionDeclarationProposal,
)
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import summary_fingerprint


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Record workflow regressions cannot contact a provider or mailbox")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def record(body, values, *, kind="travel", action="add", reference=None):
    return ApplicationRecordProposal.model_validate({
        "action": action, "record": {"kind": kind, "fields": {
            key: {"value": value, "source_excerpt": value} for key, value in values.items()}},
        "source_excerpt": body, "confidence": 1,
        "target_reference": {"value": reference, "source_excerpt": reference} if reference else None,
    })


def patch(*, records=(), assertions=(), updates=()):
    result = _patch(updates=updates)
    result.application_records = list(records)
    result.collection_declarations = list(assertions)
    return result


def test_collection_question_is_actually_sent_and_remembered_after_reopen(tmp_path):
    from datetime import date

    dialogue = Conversation(tmp_path)
    first = dialogue.turn("我想准备英国旅游签证。", patch())
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        # Seed a fictional late-intake state; this is not evidence of extraction
        # or applicant confirmation. Deferred future dates must not stop intake.
        case.profile.full_name = "Fictional Example"
        case.profile.date_of_birth = date(1997, 7, 1)
        case.profile.nationality_country = "China"
        case.profile.application_country = "China"
        case.profile.visit_purpose = "tourism"
        case.profile.uk_accommodation = "Hotel in London"
        case.profile.estimated_trip_cost_gbp = 2000
        case.profile.current_address = "1 Fictional Road, Beijing, China"
        case.profile.occupation_status = "student"
        case.profile.funding_source = "self"
        case.profile.has_serious_history = False
        case.profile.route_confirmed_standard_visitor = True
        case.deferred_fields = ["planned_arrival_date", "planned_departure_date"]
        store.save_case(case)
    finally:
        store.close()
    guidance = dialogue.turn("我的资料先按这些整理。", patch())
    assert not guidance.case.collection_question_event_ids  # material guidance takes priority
    quiet = dialogue.turn("收到，我正在整理资料。", patch())
    assert not quiet.case.collection_question_event_ids
    statement = "我在英国没有亲属或联系人。"
    second = dialogue.turn(statement, patch(assertions=[CollectionDeclarationProposal(
        kind="uk_contact", state="none_declared", source_excerpt=statement, confidence=1)]))
    assert "你以前有过出境旅行吗" in second.body
    assert list(second.case.collection_question_event_ids.values()) == [[second.event.id]]
    third = dialogue.turn("记不清。", patch())
    assert "你以前有过出境旅行吗" not in third.body
    assert third.case.application_records.collection_state("travel") == "unknown"
    assert "你在英国有亲属或联系人吗" not in third.body
    assert third.case.application_records.collection_state("uk_contact") == "none_declared"
    assertion = third.case.application_records.latest_declarations()["travel"]
    assert assertion.source_excerpt == "记不清。"
    assert assertion.source_event_id == third.event.id
    assert assertion.question_event_id == second.event.id
    assert assertion.question_key in second.case.collection_question_event_ids


@pytest.mark.parametrize("language", ["zh", "en"])
def test_plain_message_to_persisted_record_correction_and_actual_sent_receipt(tmp_path, language):
    dialogue = Conversation(tmp_path)
    zh = language == "zh"
    first = "我2023年夏天去过日本旅游。" if zh else "I visited Japan in May 2023 for tourism."
    country, period, purpose = ("日本", "2023年夏天", "旅游") if zh else ("Japan", "May 2023", "tourism")
    initial = dialogue.turn(first, patch(records=[record(first, {"country": country, "period": period, "purpose": purpose})]))
    assert initial.case.application_records is not None
    before = summary_fingerprint(initial.case, include_documents=True)
    trip = next(iter(initial.case.application_records.current().values()))
    assert trip.fields["period"].value == period
    assert trip.fields["country"].source_event_id == initial.event.id
    assert ("已记下" if zh else "recorded") in initial.body
    change = "请更正日本那次，是2023年秋天。" if zh else "Please correct the Japan trip to June 2023."
    new_period = "2023年秋天" if zh else "June 2023"
    updated = dialogue.turn(change, patch(records=[record(change, {"period": new_period}, action="amend", reference=country)]))
    changed = updated.case.application_records.current()[trip.record_id]
    assert changed.revision == 2 and changed.fields["period"].value == new_period
    assert changed.fields["period"].source_event_id == updated.event.id
    assert changed.fields["country"].source_event_id == initial.event.id
    assert summary_fingerprint(updated.case, include_documents=True) != before
    context = updated.model.events[0].known_profile["_application_record_context"]
    assert context == [{"kind": "travel", "fields": {"country": country, "period": period, "purpose": purpose}}]
    assert "record_id" not in str(context) and "source_body_sha256" not in str(context)
    assert updated.case.status == CaseStatus.DRAFT and updated.case.delivery_path is None


def test_explicit_uncertainty_survives_other_messages_without_changing_future_travel_dates(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "我记不清我的出境记录。"
    first = dialogue.turn(body, patch(assertions=[CollectionDeclarationProposal(
        kind="travel", state="unknown", source_excerpt=body, confidence=1)]))
    assert first.case.application_records.collection_state("travel") == "unknown"
    second = dialogue.turn("我护照上的姓名是示例安宁。", patch(updates=[("full_name", "示例安宁", "我护照上的姓名是示例安宁。")]))
    assert second.case.application_records.collection_state("travel") == "unknown"
    assert second.case.profile.planned_arrival_date is None and second.case.profile.planned_departure_date is None
    assert second.case.application_records.declarations[0].source_event_id == first.event.id
    assert second.case.profile.full_name == "示例安宁"
    assert second.model.events[0].known_profile["_application_collection_states"]["travel"] == "unknown"
    assert len(second.case.application_records.declarations) == 1


def test_a_uk_contact_is_not_an_applicant_address_or_a_sponsor(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "My sister Example Chen lives at 1 Example Road, London, UK."
    result = dialogue.turn(body, patch(records=[record(body, {"name": "Example Chen", "relationship": "sister",
        "address": "1 Example Road, London, UK"}, kind="uk_contact")]))
    assert result.case.profile.current_address is None and result.case.profile.funding_source is None
    assert result.case.profile.sponsor_name is None and result.case.profile.full_name is None
    assert next(iter(result.case.application_records.current().values())).kind == "uk_contact"


def test_other_person_trip_does_not_create_a_client_record_or_receipt(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "My friend visited Japan in May 2023."
    result = dialogue.turn(body, patch(records=[record(body, {"country": "Japan", "period": "May 2023"})]))
    assert result.case.application_records is None
    assert "travel-history or UK-contact update" not in result.body


def test_new_record_cannot_fall_through_the_old_scalar_only_delivery_gate(tmp_path):
    from test_consultant_value import POLICY, TODAY

    dialogue = Conversation(tmp_path)
    body = "I have never travelled abroad."
    result = dialogue.turn(body, patch(assertions=[CollectionDeclarationProposal(
        kind="travel", state="none_declared", source_excerpt=body, confidence=1)]))
    gate = evaluate_gate(result.case, POLICY, TODAY)
    assert gate.checks["application_record_intake_release_checked"] is False and not gate.allowed
    store = SQLiteStore(dialogue.path)
    try:
        rows = store.list_outbox()
        assert all(row["message_type"] == "blocked" and row["status"] == "SENT" for row in rows)
        assert store.get_case(result.case.id).delivery_path is None
    finally:
        store.close()


def test_ambiguous_correction_retains_original_records_and_actual_review_reply(tmp_path):
    from datetime import timedelta

    from test_consultant_value import APPLICANT, POLICY, TODAY, Model

    from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
    from visa_agent.channels.outbound import OutboxDispatcher
    from visa_agent.workflow.service import WorkflowService

    dialogue = Conversation(tmp_path)
    one, two = "I visited Japan in May 2023.", "I visited Japan in June 2024."
    initial = dialogue.turn(one + " " + two, patch(records=[
        record(one, {"country": "Japan", "period": "May 2023"}),
        record(two, {"country": "Japan", "period": "June 2024"}),
    ]))
    body = "Please correct the Japan trip to July 2024."
    model = Model(patch(records=[record(body, {"period": "July 2024"}, action="amend", reference="Japan")]))
    event = initial.event.model_copy(update={"id": "ambiguous-record-correction", "body": body,
        "received_at": initial.event.received_at + timedelta(minutes=1)})
    store = SQLiteStore(dialogue.path)
    try:
        workflow = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked" and case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert case.application_records == initial.case.application_records
        assert case.latest_customer_message == body and case.delivery_path is None
        assert "unique existing target" in case.human_review_reason
        sender = AutomaticGmailReplySender(dialogue.gmail, store, APPLICANT)
        results = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",)).dispatch_due(event.received_at)
        assert len(results) == 1 and results[0].status == "SENT"
        row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
        assert row["payload"] == dialogue.gmail.calls[-1]["body"]
        assert "human adviser" in row["payload"] and "July 2024" not in row["payload"]
        before_calls = len(model.events)
        replay, duplicate, plan = workflow.process(event)
        assert duplicate and plan == "duplicate_ignored" and len(model.events) == before_calls
        assert replay.application_records == case.application_records
    finally:
        store.close()


def test_record_extraction_never_runs_before_a_configured_applicant_processing_grant(tmp_path):
    from test_processing_consent_boundaries import Journey

    journey = Journey(tmp_path)
    try:
        incoming = journey.event("I visited Japan in May 2023.")
        case, duplicate, plan = journey.workflow.process(incoming)
        assert not duplicate and plan == "processing_notice"
        assert not journey.model.extracted and not journey.reads
        assert case.application_records is None
        assert "Japan" not in case.model_dump_json()
    finally:
        journey.store.close()
