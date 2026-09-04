"""Off-topic intent through the guarded workflow and exact captured Gmail body.

The model only proposes typed patches. Every applicant, excerpt and provider ID is
fictional; the capture adapter performs no network I/O. Restarts and duplicates use
the real SQLite store, workflow, automatic sender and outbox dispatcher.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate, QuestionDeferral
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import (
    QUESTION_TEXT_EN,
    QUESTION_TEXT_ZH,
    clear_natural_confirmation,
    customer_requests_next_step,
    reply_items,
)
from visa_agent.workflow.customer_questions import (
    ACTIVITIES_SOURCE,
    APPLICATION_SOURCE,
    MEDICAL_SOURCE,
    SOURCE,
)
from visa_agent.workflow.service import WorkflowService

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 4)


def question(topic: str, excerpt: str, confidence: float = 0.99) -> CustomerQuestion:
    return CustomerQuestion.model_validate({
        "topic": topic, "source_excerpt": excerpt, "confidence": confidence,
    })


def patch_for(*questions: CustomerQuestion, updates: list[FactUpdate] | None = None) -> CasePatch:
    return CasePatch(updates=updates or [], ambiguities=[], customer_questions=list(questions))


class TypedScopeModel:
    version = "fictional-off-topic-extraction"

    def __init__(self) -> None:
        self.patches: dict[str, CasePatch] = {}
        self.events: list[InboundEvent] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event)
        return self.patches[event.id].model_copy(deep=True)

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"fictional-scope-sent-{len(self.calls)}"}


class Conversation:
    def __init__(
        self, tmp_path: Path, language: str, *, only_dates_missing: bool = False,
        complete_profile: bool = False,
    ) -> None:
        self.path = tmp_path / "off-topic-dialogue.db"
        self.model = TypedScopeModel()
        self.gmail = CaptureGmail()
        self.sequence = 0
        self.initial = Case(
            id="fictional-scope-case", external_thread_id="fictional-scope-thread",
            applicant_contact="fictional-scope@example.test", primary_channel="gmail",
            customer_language=language, policy_version=load_policy(POLICY_PATH).version,
            deferred_fields=["planned_arrival_date", "planned_departure_date"],
        )
        self.initial.profile.visit_purpose = "tourism"
        self.initial.profile.nationality_country = "China"
        self.initial.profile.application_country = "Hong Kong"
        if only_dates_missing or complete_profile:
            self.initial.profile.full_name = "Fictional Applicant"
            self.initial.profile.date_of_birth = date(1992, 4, 16)
            self.initial.profile.occupation_status = "student"
            self.initial.profile.funding_source = "self"
            self.initial.profile.uk_accommodation = "Intended London stay; no booking made"
            self.initial.profile.estimated_trip_cost_gbp = 2300
            self.initial.profile.current_address = "Fictional address, Hong Kong"
            self.initial.profile.has_serious_history = False
            self.initial.profile.route_confirmed_standard_visitor = True
        if complete_profile:
            self.initial.profile.planned_arrival_date = date(2026, 11, 1)
            self.initial.profile.planned_departure_date = date(2026, 11, 8)
            self.initial.deferred_fields = []
        evaluate_gate(self.initial, load_policy(POLICY_PATH), TODAY)
        store = SQLiteStore(self.path)
        try:
            store.save_case(self.initial)
        finally:
            store.close()

    def turn(
        self, text: str, patch: CasePatch, *, expected_plan: str = "blocked", today: date = TODAY,
    ) -> tuple[Case, str]:
        self.sequence += 1
        event = InboundEvent(
            id=f"fictional-scope-turn-{self.sequence}", channel="gmail",
            external_thread_id=self.initial.external_thread_id,
            sender=self.initial.applicant_contact, subject="A question", body=text,
            received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=self.sequence),
            rfc_message_id=f"<fictional-scope-turn-{self.sequence}@example.test>",
        )
        self.model.patches[event.id] = patch
        store = SQLiteStore(self.path)
        try:
            guard = GuardedLLM(self.model)
            workflow = WorkflowService(
                store, load_policy(POLICY_PATH), guard, today_provider=lambda: today,
            )
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == expected_plan
            assert not guard.last_extraction_fallback
            sender = AutomaticGmailReplySender(self.gmail, store, event.sender)
            sender.withhold_obsolete_unsent()
            dispatcher = OutboxDispatcher(
                store, sender, channel="gmail", allowed_message_types=(expected_plan,),
            )
            state_before_send = store.get_case(case.id).model_dump_json()
            outcomes = dispatcher.dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["payload"] == self.gmail.calls[-1]["body"]
            assert row["reply_render_mode"] == "reviewed"
            assert store.get_case(case.id).model_dump_json() == state_before_send
            persisted = store.get_case(case.id)
            assert persisted is not None
            extraction_count = len(self.model.events)
            send_count = len(self.gmail.calls)
        finally:
            store.close()

        store = SQLiteStore(self.path)
        try:
            workflow = WorkflowService(
                store, load_policy(POLICY_PATH), self.model, today_provider=lambda: today,
            )
            assert workflow.process(event)[1]
            assert len(self.model.events) == extraction_count
            assert store.get_case(persisted.id).model_dump_json() == persisted.model_dump_json()
            dispatcher = OutboxDispatcher(
                store, AutomaticGmailReplySender(self.gmail, store, event.sender),
                channel="gmail", allowed_message_types=(expected_plan,),
            )
            assert dispatcher.dispatch_due(event.received_at) == []
            assert len(self.gmail.calls) == send_count
            return persisted, row["payload"]
        finally:
            store.close()


def assert_no_added_authority(before: Case, after: Case) -> None:
    assert after.profile == before.profile
    assert after.evidence == before.evidence and after.documents == before.documents
    assert after.profile_confirmed == before.profile_confirmed
    assert after.final_summary_confirmed == before.final_summary_confirmed
    assert after.delivery_path is None and after.delivery_revision == before.delivery_revision
    assert after.confirmation_kind is None and after.confirmation_fingerprint is None
    assert after.human_review_reason is None and after.status == CaseStatus.DRAFT
    policy = load_policy(POLICY_PATH)
    assert evaluate_gate(after, policy, TODAY).checks == evaluate_gate(before, policy, TODAY).checks
    assert not evaluate_gate(after, policy, TODAY).allowed


def assert_no_unrelated_requests(case: Case, body: str) -> None:
    assert case.last_requested_fields == []
    assert reply_items(case)[1:] == ([], [])
    assert all(text not in body for text in [*QUESTION_TEXT_ZH.values(), *QUESTION_TEXT_EN.values()])
    assert all(text.casefold() not in body.casefold() for text in [
        "接下来还需要这些材料", "We'll also need these documents", "日期确定后再告诉我",
        "具体日期补齐前", "Let me know when your dates are decided", "final check will remain on hold",
        "人工复核", "human review", "eligibility", "另行核实", "separate check",
    ])


@pytest.mark.parametrize("only_dates_missing", [False, True])
@pytest.mark.parametrize(("text", "language"), [
    ("健身房会员卡申请费是多少？", "zh"),
    ("这道几何题应该怎么做？", "zh"),
    ("What is the application fee for joining a chess club?", "en"),
    ("Can you explain the ending of my favourite novel?", "en"),
])
def test_pure_off_topic_reaches_sender_as_only_short_scope_without_starting_intake(
    tmp_path: Path, text: str, language: str, only_dates_missing: bool,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=only_dates_missing)
    case, body = conversation.turn(text, patch_for(question("off_topic", text)))
    assert case.customer_question_topics == ["off_topic"]
    assert len(case.customer_answers) == 1 and body == case.customer_answers[0]
    assert len(body) < 500
    assert ("英国" in body and "签证" in body) if language == "zh" else (
        "uk" in body.casefold() and "visa" in body.casefold()
    )
    assert "http" not in body and "GOV.UK" not in body and "£135" not in body
    assert case.latest_received_facts == {} and case.latest_changes == {}
    assert case.latest_document_names == [] and case.latest_deferred_fields == []
    assert case.question_event_ids == conversation.initial.question_event_ids == {}
    assert case.guidance_events == conversation.initial.guidance_events == {}
    assert case.deferred_fields == conversation.initial.deferred_fields
    assert_no_unrelated_requests(case, body)
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("text", "language"), [
    ("请解释一下这个棋局应该怎么走。", "zh"),
    ("Can you explain the best move in this chess position?", "en"),
])
def test_pure_off_topic_preserves_independent_sent_question_ledger(
    tmp_path: Path, text: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    before, _ = conversation.turn("I am a student and paying for the trip myself.", patch_for(updates=[
        FactUpdate(field="occupation_status", value="student",
                   source_excerpt="I am a student", confidence=0.99),
        FactUpdate(field="funding_source", value="self",
                   source_excerpt="paying for the trip myself", confidence=0.99),
    ]))
    assert before.last_requested_fields and before.question_event_ids
    case, body = conversation.turn(text, patch_for(question("off_topic", text)))
    assert case.question_event_ids == before.question_event_ids
    assert set(case.pending_question_fields) == set(before.last_requested_fields)
    assert case.guidance_events == before.guidance_events
    assert_no_unrelated_requests(case, body)
    assert_no_added_authority(before, case)


@pytest.mark.parametrize(("off_topic", "visa", "language"), [
    ("健身房会员卡申请费是多少？", "普通六个月英国访问签证的申请费是多少？", "zh"),
    ("What is the application fee for joining a chess club?",
     "What is the fee for a six-month UK visitor visa?", "en"),
])
def test_overlapping_faq_is_suppressed_but_independent_visa_clause_reaches_sender(
    tmp_path: Path, off_topic: str, visa: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    case, body = conversation.turn(off_topic, patch_for(
        question("off_topic", off_topic), question("fees", off_topic),
    ))
    assert len(case.customer_answers) == 1 and "£135" not in body and "http" not in body
    case, body = conversation.turn(off_topic + "\n" + visa, patch_for(
        question("off_topic", off_topic), question("fees", visa),
    ))
    assert len(case.customer_answers) == 2
    assert "£135" in body and APPLICATION_SOURCE in body
    assert_no_unrelated_requests(case, body)
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("text", "language"), [
    ("我的出生日期是1992.4.16，旅行预算是2300英镑。请解释这个几何题。", "zh"),
    ("My date of birth is 1992.4.16 and my trip budget is GBP 2300. "
     "Can you explain this geometry exercise?", "en"),
])
def test_off_topic_does_not_discard_valid_current_birthday_and_budget_updates(
    tmp_path: Path, text: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    excerpt = "请解释这个几何题。" if language == "zh" else "Can you explain this geometry exercise?"
    budget_excerpt = "旅行预算是2300英镑" if language == "zh" else "my trip budget is GBP 2300"
    case, body = conversation.turn(text, patch_for(question("off_topic", excerpt), updates=[
        FactUpdate(field="date_of_birth", value="1992.4.16", source_excerpt="1992.4.16", confidence=0.99),
        FactUpdate(field="estimated_trip_cost_gbp", value=2300,
                   source_excerpt=budget_excerpt, confidence=0.99),
    ]))
    assert case.customer_question_topics == ["off_topic"] and case.customer_answers[0] in body
    assert case.profile.date_of_birth == date(1992, 4, 16)
    assert case.profile.estimated_trip_cost_gbp == 2300
    assert case.latest_received_facts == {"date_of_birth": "1992-04-16", "estimated_trip_cost_gbp": "2300"}
    assert {item.fact_key for item in case.evidence} == {"date_of_birth", "estimated_trip_cost_gbp"}
    assert ("生日" in body and "旅行预算" in body) if language == "zh" else (
        "date of birth" in body and "estimated budget" in body
    )
    assert "date_of_birth" not in case.last_requested_fields
    assert "estimated_trip_cost_gbp" not in case.last_requested_fields
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert not case.profile.route_confirmed_standard_visitor and case.delivery_path is None
    assert case.status == CaseStatus.DRAFT and case.human_review_reason is None
    assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed


@pytest.mark.parametrize(("off_topic", "visa", "ack", "language"), [
    ("请解释这个几何题。", "英国访问签证的申请费是多少？", "我晚点回复。", "zh"),
    ("Can you explain this geometry exercise?", "What is the UK visitor visa application fee?",
     "Thanks, I will reply later.", "en"),
])
def test_off_topic_is_reset_after_restart_and_next_visa_request_is_not_suppressed(
    tmp_path: Path, off_topic: str, visa: str, ack: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    first, first_body = conversation.turn(off_topic, patch_for(question("off_topic", off_topic)))
    second, second_body = conversation.turn(visa, patch_for(question("fees", visa)))
    assert second.customer_question_topics == ["fees"] and "£135" in second_body
    assert first_body not in second_body
    third, third_body = conversation.turn(ack + "\n> " + off_topic, patch_for())
    assert third.customer_question_topics == [] and third.customer_answers == []
    assert first_body not in third_body and "£135" not in third_body and "http" not in third_body
    assert len(conversation.gmail.calls) == 3
    assert_no_added_authority(first, third)


@pytest.mark.parametrize(("text", "topic", "language", "expected"), [
    ("十年英国访问签证的申请费是多少？", "unsupported", "zh", "另行核实"),
    ("What does a ten-year UK visitor visa cost?", "unsupported", "en", "separate check"),
    ("学生签证的申请费是多少？", "fees", "zh", "路线"),
    ("What is the application fee for a student visa?", "fees", "en", "route"),
])
def test_existing_unsupported_and_other_visa_routes_are_not_relabelled_off_topic(
    tmp_path: Path, text: str, topic: str, language: str, expected: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(text, patch_for(question(topic, text)))
    assert case.customer_question_topics == [topic]
    assert expected in body and "£135" not in body
    assert "outside UK visa preparation" not in body and "不属于英国签证准备" not in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("conflicting_topic", [None, "fees", "document_checklist"])
@pytest.mark.parametrize(("off_topic", "resume", "language"), [
    ("请解释这个几何题。", "继续核对我的资料。", "zh"),
    ("Can you explain this geometry exercise?", "Please continue checking my details.", "en"),
])
def test_complete_profile_off_topic_does_not_start_a_new_summary_confirmation(
    tmp_path: Path, off_topic: str, resume: str, language: str, conflicting_topic: str | None,
) -> None:
    conversation = Conversation(tmp_path, language, complete_profile=True)
    gate = evaluate_gate(conversation.initial, load_policy(POLICY_PATH), TODAY)
    assert gate.checks["required_profile_facts_complete"] and gate.checks["route_in_scope"]
    proposals = [question("off_topic", off_topic)]
    if conflicting_topic:
        proposals.append(question(conflicting_topic, off_topic))
    case, body = conversation.turn(off_topic, patch_for(*proposals))
    assert body == case.customer_answers[0]
    assert conversation.initial.profile.full_name not in body
    assert case.question_event_ids == {}
    assert_no_unrelated_requests(case, body)
    assert_no_added_authority(conversation.initial, case)

    resumed, summary = conversation.turn(resume, patch_for(), expected_plan="awaiting_profile_confirmation")
    assert resumed.customer_question_topics == [] and resumed.customer_answers == []
    assert resumed.profile.full_name in summary and body not in summary
    assert resumed.confirmation_kind == "profile" and resumed.confirmation_fingerprint
    assert resumed.confirmation_request_event_id == conversation.model.events[-1].id
    assert not resumed.profile_confirmed and not resumed.final_summary_confirmed


@pytest.mark.parametrize(("off_topic", "resume", "confirmation", "language"), [
    ("请解释这个几何题。", "继续核对我的资料。", "资料都正确，可以继续。", "zh"),
    ("Can you explain this geometry exercise?", "Please continue checking my details.",
     "Everything is correct, please proceed.", "en"),
])
def test_off_topic_preserves_existing_sent_summary_context_without_repeating_it(
    tmp_path: Path, off_topic: str, resume: str, confirmation: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, complete_profile=True)
    before, summary = conversation.turn(resume, patch_for(), expected_plan="awaiting_profile_confirmation")
    assert before.confirmation_kind == "profile" and before.confirmation_fingerprint
    case, body = conversation.turn(off_topic, patch_for(question("off_topic", off_topic)))
    assert body == case.customer_answers[0] and body != summary
    assert before.profile.full_name not in body
    assert case.question_event_ids == before.question_event_ids
    assert case.profile == before.profile and case.evidence == before.evidence
    assert case.documents == before.documents and case.delivery_path is None
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.confirmation_kind == before.confirmation_kind
    assert case.confirmation_fingerprint == before.confirmation_fingerprint
    assert case.confirmation_request_event_id == before.confirmation_request_event_id
    assert_no_unrelated_requests(case, body)

    confirmed, next_body = conversation.turn(confirmation, patch_for())
    assert confirmed.profile_confirmed
    assert not confirmed.final_summary_confirmed and confirmed.delivery_path is None
    assert confirmed.customer_question_topics == [] and confirmed.customer_answers == []
    assert body not in next_body


@pytest.mark.parametrize(("scenario", "status", "plan", "reason"), [
    ("sender", CaseStatus.DRAFT, "sender_mismatch_rejected", "THREAD_SENDER_MISMATCH"),
    ("older", CaseStatus.DRAFT, "out_of_order_held", "OUT_OF_ORDER_EVENT"),
    ("ready", CaseStatus.READY_FOR_HUMAN_REVIEW, "finalized_case_held", "FINALIZED_CASE_NEW_EVENT"),
    ("delivered", CaseStatus.DELIVERED_AFTER_CONFIRMATION, "finalized_case_held", "FINALIZED_CASE_NEW_EVENT"),
    ("review", CaseStatus.HUMAN_REVIEW_REQUIRED, "human_review_case_held", "HUMAN_REVIEW_CASE_NEW_EVENT"),
])
def test_off_topic_cannot_bypass_inbound_identity_order_or_review_holds(
    tmp_path: Path, scenario: str, status: CaseStatus, plan: str, reason: str,
) -> None:
    conversation = Conversation(tmp_path, "en")
    before = conversation.initial.model_copy(deep=True)
    before.status = status
    before.last_inbound_received_at = datetime(2026, 9, 4, 12, tzinfo=UTC)
    event = InboundEvent(
        id=f"fictional-rejected-off-topic-{scenario}", channel="gmail",
        external_thread_id=before.external_thread_id,
        sender="unrelated-sender@example.test" if scenario == "sender" else before.applicant_contact,
        subject="A question", body="Can you explain this geometry exercise?",
        received_at=before.last_inbound_received_at + timedelta(minutes=-1 if scenario == "older" else 1),
    )
    conversation.model.patches[event.id] = patch_for(question("off_topic", event.body))
    store = SQLiteStore(conversation.path)
    try:
        store.save_case(before)
        persisted_before = store.get_case(before.id).model_dump_json()
        service = WorkflowService(store, load_policy(POLICY_PATH), conversation.model,
                                  today_provider=lambda: TODAY)
        case, duplicate, actual_plan = service.process(event)
        assert not duplicate and actual_plan == plan
        assert case.model_dump_json() == persisted_before
        assert store.get_case(case.id).model_dump_json() == persisted_before
        assert conversation.model.events == [] and store.list_outbox() == []
        assert store.list_inbound_failures()[0]["reason_code"] == reason
        assert service.process(event)[1]
        assert len(store.list_inbound_failures()) == 1
        dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(
            conversation.gmail, store, before.applicant_contact,
        ), channel="gmail")
        assert dispatcher.dispatch_due(event.received_at) == [] and conversation.gmail.calls == []
    finally:
        store.close()


@pytest.mark.parametrize("conflicting_semantic_checklist", [False, True])
@pytest.mark.parametrize(("text", "language"), [
    ("请把陶艺课程需要准备的材料清单发我。", "zh"),
    ("Please send me a document checklist for publishing a novel.", "en"),
])
def test_unrelated_checklist_request_cannot_trigger_visa_document_checklist(
    tmp_path: Path, text: str, language: str, conflicting_semantic_checklist: bool,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    proposals = [question("off_topic", text)]
    if conflicting_semantic_checklist:
        proposals.append(question("document_checklist", text))
    case, body = conversation.turn(text, patch_for(*proposals))
    assert len(case.customer_answers) == 1 and body == case.customer_answers[0]
    assert "http" not in body and "GOV.UK" not in body
    assert_no_unrelated_requests(case, body)
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("deferral", "off_topic", "language"), [
    ("旅行日期没定。", "请解释这个几何题。", "zh"),
    ("My travel dates are not set.", "Can you explain this geometry exercise?", "en"),
])
def test_off_topic_keeps_current_deterministic_date_deferral_without_typed_deferral(
    tmp_path: Path, deferral: str, off_topic: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    conversation.initial.deferred_fields = []
    store = SQLiteStore(conversation.path)
    try:
        store.save_case(conversation.initial)
    finally:
        store.close()
    case, body = conversation.turn(deferral + "\n" + off_topic, patch_for(question("off_topic", off_topic)))
    assert set(case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert set(case.latest_deferred_fields) == set(case.deferred_fields)
    assert not set(case.deferred_fields) & set(case.last_requested_fields)
    assert case.customer_answers[0] in body
    assert case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("semantic_checklist", [False, True])
@pytest.mark.parametrize(("off_topic", "visa_checklist", "language"), [
    ("请把陶艺课程需要准备的材料清单发我。", "另外请把英国签证材料清单发我。", "zh"),
    ("Please send me a document checklist for publishing a novel.",
     "Also, please send me a document checklist for my UK visa.", "en"),
])
def test_independent_visa_checklist_is_not_suppressed_with_unrelated_checklist(
    tmp_path: Path, off_topic: str, visa_checklist: str, language: str, semantic_checklist: bool,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    proposals = [question("off_topic", off_topic)]
    if semantic_checklist:
        proposals.append(question("document_checklist", visa_checklist))
    case, body = conversation.turn(off_topic + "\n" + visa_checklist, patch_for(*proposals))
    assert case.customer_answers and case.customer_answers[0] in body
    documents = reply_items(case)[2]
    assert len(documents) >= 3 and all(document in body for document in documents)
    assert "https://www.gov.uk/" in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("context", ["sent", "unsent", "changed"])
@pytest.mark.parametrize(("confirmation", "off_topic", "correction", "language"), [
    ("资料都正确，可以继续。", "请解释这个几何题。", "旅行预算是2700英镑。", "zh"),
    ("Everything is correct, please proceed.", "Please explain this geometry exercise.",
     "My trip budget is GBP 2700.", "en"),
])
def test_independent_natural_confirmation_with_off_topic_still_requires_sent_unchanged_summary(
    tmp_path: Path, context: str, confirmation: str, off_topic: str, correction: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, complete_profile=True)
    resume = "继续核对我的资料。" if language == "zh" else "Please continue checking my details."
    if context == "unsent":
        # Produce the real workflow summary without a provider dispatch. No invented
        # SENT row may supply authority for the subsequent natural confirmation.
        event = InboundEvent(
            id="fictional-unsent-profile-summary", channel="gmail",
            external_thread_id=conversation.initial.external_thread_id,
            sender=conversation.initial.applicant_contact, subject="My details", body=resume,
            received_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
        )
        conversation.model.patches[event.id] = patch_for()
        store = SQLiteStore(conversation.path)
        try:
            service = WorkflowService(store, load_policy(POLICY_PATH), conversation.model,
                                      today_provider=lambda: TODAY)
            before, duplicate, plan = service.process(event)
            assert not duplicate and plan == "awaiting_profile_confirmation"
            assert store.list_outbox()[0]["status"] == "PENDING" and conversation.gmail.calls == []
        finally:
            store.close()
    else:
        before, _ = conversation.turn(resume, patch_for(), expected_plan="awaiting_profile_confirmation")
    assert before.confirmation_kind == "profile" and before.confirmation_fingerprint
    parts = [confirmation, off_topic]
    updates = []
    if context == "changed":
        parts.insert(1, correction)
        updates.append(FactUpdate(field="estimated_trip_cost_gbp", value=2700,
                                  source_excerpt=correction, confidence=0.99))
    text = "\n".join(parts)
    assert clear_natural_confirmation(text)
    expected = "blocked" if context == "sent" else "awaiting_profile_confirmation"
    case, body = conversation.turn(text, patch_for(question("off_topic", off_topic), updates=updates),
                                   expected_plan=expected)
    assert case.profile_confirmed == (context == "sent")
    assert not case.final_summary_confirmed and case.delivery_path is None
    assert case.status == CaseStatus.DRAFT and case.customer_answers[0] in body
    if context == "changed":
        assert case.profile.estimated_trip_cost_gbp == 2700
        assert case.confirmation_fingerprint != before.confirmation_fingerprint
    else:
        assert case.profile == before.profile
    assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed


@pytest.mark.parametrize("existing_pending", [False, True])
@pytest.mark.parametrize(("resume", "off_topic", "language"), [
    ("我现在可以继续，请继续问。", "请解释这个几何题。", "zh"),
    ("I am ready to continue.", "Please explain this geometry exercise.", "en"),
])
def test_independent_continue_request_is_not_swallowed_by_off_topic_clause(
    tmp_path: Path, existing_pending: bool, resume: str, off_topic: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    before = conversation.initial
    if existing_pending:
        before, _ = conversation.turn("I am a student and paying for the trip myself.", patch_for(updates=[
            FactUpdate(field="occupation_status", value="student",
                       source_excerpt="I am a student", confidence=0.99),
            FactUpdate(field="funding_source", value="self",
                       source_excerpt="paying for the trip myself", confidence=0.99),
        ]))
        assert before.question_event_ids and before.last_requested_fields
    text = resume + "\n" + off_topic
    assert customer_requests_next_step(text)
    case, body = conversation.turn(text, patch_for(question("off_topic", off_topic)))
    assert case.customer_answers[0] in body
    assert case.last_requested_fields
    questions = QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN
    assert all(questions[field] in body for field in case.last_requested_fields)
    if existing_pending:
        assert len(case.last_requested_fields) == 1
        assert set(case.last_requested_fields) <= set(before.last_requested_fields)
    assert not set(case.deferred_fields) & set(case.last_requested_fields)
    assert_no_added_authority(before, case)


@pytest.mark.parametrize("review_signal", ["explicit_flag", "ambiguity", "conflicting_updates"])
def test_off_topic_only_after_guarding_cannot_swallow_human_review_signal(
    tmp_path: Path, review_signal: str,
) -> None:
    conversation = Conversation(tmp_path, "en")
    off_topic = "Please explain this geometry exercise."
    text = off_topic
    patch = patch_for(question("off_topic", off_topic))
    if review_signal == "explicit_flag":
        patch.requires_human_review = True
        reason = "Bounded extractor requested human review."
    elif review_signal == "ambiguity":
        reason = "Fictional conflicting applicant details require review."
        patch.ambiguities = [reason]
    else:
        text = "My date of birth is 1992.4.16 or 1993.5.17.\n" + off_topic
        patch.updates = [FactUpdate(field="date_of_birth", value=value,
                                   source_excerpt=value, confidence=0.99)
                         for value in ("1992.4.16", "1993.5.17")]
        reason = "Conflicting values proposed for date_of_birth."
    case, body = conversation.turn(text, patch)
    assert case.customer_question_topics == ["off_topic"]
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert case.stage == WorkflowStage.HUMAN_REVIEW_REQUIRED
    assert case.human_review_reason == reason
    assert case.profile == conversation.initial.profile and case.evidence == []
    assert case.latest_received_facts == {} and case.latest_changes == {}
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.delivery_path is None and case.customer_answers[0] in body
    assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed


@pytest.mark.parametrize(("topic", "first", "second", "language", "conflict"), [
    ("off_topic", "请解释这个几何题。", "健身房会员卡申请费是多少？", "zh", "fees"),
    ("off_topic", "Please explain this geometry exercise.",
     "What is the application fee for joining a chess club?", "en", "fees"),
    ("off_topic", "请解释这个几何题。", "请把陶艺课程需要准备的材料清单发我。", "zh", "document_checklist"),
    ("off_topic", "Please explain this geometry exercise.",
     "Please send me a document checklist for publishing a novel.", "en", "document_checklist"),
    ("unsupported", "以前的拒签应该怎样处理？", "十年英国访问签证的申请费是多少？", "zh", "fees"),
    ("unsupported", "How does a previous refusal affect my application?",
     "What is the application fee for a ten-year UK visitor visa?", "en", "fees"),
    ("unsupported", "以前的拒签应该怎样处理？", "请把工作签证的材料清单发我。", "zh", "document_checklist"),
    ("unsupported", "How does a previous refusal affect my application?",
     "Please send me a document checklist for a work visa.", "en", "document_checklist"),
])
def test_repeated_distinct_scope_questions_do_not_revive_fee_or_document_keywords(
    tmp_path: Path, topic: str, first: str, second: str, language: str, conflict: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(first + "\n" + second, patch_for(
        question(topic, first), question(topic, second), question(conflict, second),
    ))
    assert case.customer_question_topics == [topic, topic]
    assert case.customer_question_exclusions == [first, second]
    assert len(case.customer_answers) == 1 and body == case.customer_answers[0]
    assert "£135" not in body and "http" not in body and "GOV.UK" not in body
    assert case.last_requested_fields == [] and reply_items(case)[2] == []
    if topic == "off_topic":
        assert_no_unrelated_requests(case, body)
    else:
        assert ("另行核实" in body) if language == "zh" else ("separate check" in body)
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("text", "language"), [
    ("办理签证的银行流水要多久？银行App里能下载吗？", "zh"),
    ("How long should bank statements for my visa cover? Can I download them through online banking?", "en"),
])
def test_bank_record_scope_and_download_caveat_reach_exact_gmail_body(
    tmp_path: Path, text: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(text, patch_for(question("bank_period", text)))
    assert len(case.customer_answers) == 1 and SOURCE in body
    assert APPLICATION_SOURCE not in body and "3 周" not in body and "3 weeks" not in body
    if language == "zh":
        assert "网银或银行 App" in body and "不代表任何下载文件都会被接受" in body
    else:
        assert "online banking or bank app" in body and "not a guarantee" in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("decline_links", [False, True])
@pytest.mark.parametrize(("today", "reviewed"), [
    (date(2026, 9, 3), False), (TODAY, True), (date(2026, 10, 5), False),
])
@pytest.mark.parametrize(("work", "medical", "decline", "language"), [
    ("我能在英国旅行时做兼职吗？", "我能去英国接受私人医疗治疗吗？", "不用发链接。", "zh"),
    ("Can I do a paid job during a UK visit?", "Can I travel to the UK for private medical treatment?",
     "No links please.", "en"),
])
def test_two_unsupported_contextual_answers_reach_sender_without_source_expiry_or_link_optout_leaks(
    tmp_path: Path, work: str, medical: str, decline: str, language: str,
    today: date, reviewed: bool, decline_links: bool,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    text = work + "\n" + medical + ("\n" + decline if decline_links else "")
    case, body = conversation.turn(text, patch_for(
        question("unsupported", work), question("unsupported", work), question("unsupported", medical),
    ), today=today)
    assert case.customer_question_topics == ["unsupported", "unsupported"]
    assert len(case.customer_answers) == 1 and body == case.customer_answers[0]
    work_heading = "关于在英国工作" if language == "zh" else "On working in the UK"
    medical_heading = "医疗访问有专门" if language == "zh" else "Medical visits have specific"
    assert body.count(work_heading) == int(reviewed)
    assert body.count(medical_heading) == int(reviewed)
    if reviewed and not decline_links:
        assert "GOV.UK: " + ACTIVITIES_SOURCE + "\n" in body
        assert "GOV.UK: " + MEDICAL_SOURCE in body
    else:
        assert "http" not in body
    assert "£135" not in body and APPLICATION_SOURCE not in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("route", "medical", "language"), [
    ("我准备申请学生签证。", "我能接受私人医疗治疗吗？", "zh"),
    ("I am applying for a student visa.", "Can I receive private medical treatment?", "en"),
])
def test_current_other_route_blocks_visitor_contextual_source_even_outside_excerpt(
    tmp_path: Path, route: str, medical: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(route + "\n" + medical, patch_for(question("unsupported", medical)))
    assert len(case.customer_answers) == 1 and "http" not in body
    assert "医疗访问有专门" not in body and "Medical visits have specific" not in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("unrelated", "visa_question", "language"), [
    ("请讲讲小说中的工作和医疗设定。", "以前的拒签会如何影响申请？", "zh"),
    ("Please explain the work and medical treatment in this novel.",
     "How does a previous refusal affect my application?", "en"),
])
def test_unrelated_work_or_medical_terms_do_not_create_contextual_visa_reply(
    tmp_path: Path, unrelated: str, visa_question: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(unrelated + "\n" + visa_question, patch_for(
        question("off_topic", unrelated), question("unsupported", visa_question),
    ))
    assert len(case.customer_answers) == 2 and "http" not in body
    assert "关于在英国工作" not in body and "On working in the UK" not in body
    assert "医疗访问有专门" not in body and "Medical visits have specific" not in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_off_topic_work_medical_and_fee_remain_three_captured_answers_without_state_authority(
    tmp_path: Path, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    excerpts = (["请解释这个几何题。", "我能在英国旅行时做兼职吗？", "我能去英国接受私人医疗治疗吗？",
                 "普通六个月英国访问签证的申请费是多少？"] if language == "zh" else
                ["Please explain this geometry exercise.", "Can I do a paid job during a UK visit?",
                 "Can I travel to the UK for private medical treatment?", "What is the fee for a six-month UK visitor visa?"])
    proposals = [question(topic, text) for topic, text in zip(
        ("off_topic", "unsupported", "unsupported", "fees"), excerpts, strict=True,
    )]
    case, body = conversation.turn("\n".join(excerpts), patch_for(*proposals))
    assert len(case.customer_answers) == 3
    assert all(answer in body for answer in case.customer_answers)
    assert "£135" in body and APPLICATION_SOURCE in body
    assert MEDICAL_SOURCE in body and "GOV.UK: " + ACTIVITIES_SOURCE + "\n" in body
    assert case.last_requested_fields == [] and reply_items(case)[2] == []
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize("typed_date_deferral", [False, True])
@pytest.mark.parametrize(("resume", "date_update", "language"), [
    ("Let's carry on with the application preparation.", "I'll send the dates later.", "en"),
    ("Please resume the application preparation.", "My dates are not set.", "en"),
    ("请继续准备材料。", "日期我稍后告诉你。", "zh"),
    ("我们继续整理资料。", "日期还没确定。", "zh"),
])
def test_continue_preparation_with_separate_dates_later_gets_useful_reviewed_steps(
    tmp_path: Path, resume: str, date_update: str, language: str, typed_date_deferral: bool,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    patch = patch_for()
    if typed_date_deferral:
        patch.question_deferrals = [QuestionDeferral(field=field, source_excerpt=date_update, confidence=0.99)
                                    for field in ("planned_arrival_date", "planned_departure_date")]
    case, body = conversation.turn(resume + "\n" + date_update, patch)
    assert case.proactive_guidance_offered and case.customer_answers
    assert APPLICATION_SOURCE in body and "Apply now" in body
    assert SOURCE in body
    assert case.customer_question_topics == []
    assert case.deferred_fields == conversation.initial.deferred_fields
    assert case.last_requested_fields == []
    assert all(QUESTION_TEXT_ZH[field] not in body and QUESTION_TEXT_EN[field] not in body
               for field in case.deferred_fields)
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("text", "language"), [
    ("Thanks\n> Let's carry on with the application preparation.", "en"),
    ("Please don't continue preparing the application. I'll send the dates later.", "en"),
    ("If I have time, let's continue preparing the application.", "en"),
    ("朋友说“请继续准备材料。”日期还没定。", "zh"),
    ("不要继续整理材料，日期没定。", "zh"),
    ("如果日期确定了，请继续准备材料。", "zh"),
])
def test_noncurrent_or_conditional_continue_does_not_start_reviewed_preparation(
    tmp_path: Path, text: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language, only_dates_missing=True)
    case, body = conversation.turn(text, patch_for())
    assert not customer_requests_next_step(text)
    assert not case.proactive_guidance_offered and case.customer_answers == []
    assert case.guidance_events == conversation.initial.guidance_events == {}
    assert case.last_requested_fields == [] and case.question_event_ids == {}
    assert reply_items(case)[2] == []
    assert "http" not in body and "Apply now" not in body
    assert_no_added_authority(conversation.initial, case)


@pytest.mark.parametrize(("resume", "date_update", "off_topic", "language"), [
    ("Please continue preparing the documents.", "I'll send the dates later.",
     "Please explain this geometry exercise.", "en"),
    ("请继续准备材料。", "日期我稍后告诉你。", "请解释这个几何题。", "zh"),
])
def test_continue_preparation_dates_later_and_off_topic_keep_independent_question_progress(
    tmp_path: Path, resume: str, date_update: str, off_topic: str, language: str,
) -> None:
    conversation = Conversation(tmp_path, language)
    before, _ = conversation.turn("I am a student and paying for the trip myself.", patch_for(updates=[
        FactUpdate(field="occupation_status", value="student", source_excerpt="I am a student", confidence=0.99),
        FactUpdate(field="funding_source", value="self", source_excerpt="paying for the trip myself", confidence=0.99),
    ]))
    assert before.last_requested_fields
    text = "\n".join([resume, date_update, off_topic])
    case, body = conversation.turn(text, patch_for(question("off_topic", off_topic)))
    assert customer_requests_next_step(text)
    assert case.customer_question_topics == ["off_topic"] and case.customer_answers[0] in body
    assert len(case.last_requested_fields) == 1
    assert set(case.last_requested_fields) <= set(before.last_requested_fields)
    assert not set(case.deferred_fields) & set(case.last_requested_fields)
    questions = QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN
    assert all(questions[field] in body for field in case.last_requested_fields)
    assert_no_added_authority(before, case)
