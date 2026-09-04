"""Durable, sent-aware conversation pacing through the real Gmail reply path.

Only fact extraction and Gmail network I/O are test doubles. All facts are fictional
and their evidence excerpts must occur in the current inbound message. These tests
make no claim about a live model's extraction or actual Gmail delivery.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import QUESTION_TEXT_ZH, next_fact_questions
from visa_agent.workflow.service import WorkflowService

FIRST_BODY = "我目前在读书，费用自己承担，旅行日期没定。"
CORRECTION_BODY = "改一下，不是旅游，是去参加会议，费用由学校承担。"
IDENTITY_BODY = "护照上的姓名是示例申请人，出生日期是1994年6月12日。"
IDENTITY_FIELDS = {"full_name", "date_of_birth"}
TRAVEL_DATES = {"planned_arrival_date", "planned_departure_date"}


class EvidenceOnlyModel:
    version = "question-frontier-evidence-test-double"

    def __init__(self) -> None:
        self.events: list[InboundEvent] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event)
        updates: list[FactUpdate] = []
        for field, value, excerpt in (
            ("occupation_status", "student", "我目前在读书"),
            ("funding_source", "self", "费用自己承担"),
            ("visit_purpose", "conference", "是去参加会议"),
            ("funding_source", "employer_or_school", "费用由学校承担"),
            ("full_name", "示例申请人", "示例申请人"),
            ("date_of_birth", "1994-06-12", "1994年6月12日"),
        ):
            if excerpt in event.body:
                updates.append(FactUpdate(
                    field=field, value=value, source_excerpt=excerpt, confidence=0.99,
                ))
        return CasePatch(updates=updates, ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": f"frontier-accepted-{len(self.calls)}"}


class Conversation:
    """Reconstruct the store and workflow for every customer turn, including replay."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = tmp_path / "frontier.db"
        self.model = EvidenceOnlyModel()
        self.gmail = CaptureGmail()
        self.sequence = 0
        self.started_at = datetime.now(UTC)
        case = Case(
            id="frontier-case", external_thread_id="frontier-thread",
            applicant_contact="fictional-frontier@example.test", primary_channel="gmail",
            policy_version="test", customer_language="zh",
        )
        case.profile.visit_purpose = "tourism"
        case.profile.nationality_country = "China"
        case.profile.application_country = "Hong Kong"
        self.case_id = case.id
        self.contact = case.applicant_contact
        store = SQLiteStore(self.db_path)
        try:
            store.save_case(case)
        finally:
            store.close()

    def turn(self, body: str, *, send: bool = True) -> tuple[Case, str]:
        self.sequence += 1
        event = InboundEvent(
            id=f"frontier-inbound-{self.sequence}", external_thread_id="frontier-thread",
            sender=self.contact, subject="英国旅行材料咨询", body=body, channel="gmail",
            received_at=self.started_at + timedelta(seconds=self.sequence),
            rfc_message_id=f"<frontier-inbound-{self.sequence}@example.test>",
        )
        store = SQLiteStore(self.db_path)
        try:
            guard = GuardedLLM(self.model)
            workflow = WorkflowService(
                store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), guard,
                today_provider=lambda: date(2026, 9, 4),
            )
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == "blocked"
            assert not guard.last_extraction_fallback
            assert case.id == self.case_id and len(store.list_cases()) == 1
            assert case.status == CaseStatus.DRAFT
            assert not case.profile_confirmed and not case.final_summary_confirmed
            assert case.delivery_path is None

            sender = AutomaticGmailReplySender(self.gmail, store, self.contact)
            dispatcher = OutboxDispatcher(
                store, sender, channel="gmail", allowed_message_types=("blocked",),
            )
            if send:
                sender.withhold_obsolete_unsent()
                outcomes = dispatcher.dispatch_due(event.received_at)
                assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(item for item in store.list_outbox() if item["event_id"] == event.id)
            if send:
                assert row["payload"] == self.gmail.calls[-1]["body"]
            else:
                assert row["status"] == "PENDING"
            persisted = store.get_case(self.case_id)
            assert persisted is not None
            before_replay = persisted.model_dump_json()
            extraction_count = len(self.model.events)
            send_count = len(self.gmail.calls)
        finally:
            store.close()

        # A restart must not turn the same event into new extraction or another email.
        store = SQLiteStore(self.db_path)
        try:
            workflow = WorkflowService(
                store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                GuardedLLM(self.model), today_provider=lambda: date(2026, 9, 4),
            )
            assert workflow.process(event)[1]
            assert len(self.model.events) == extraction_count
            assert store.get_case(self.case_id).model_dump_json() == before_replay
            if send:
                dispatcher = OutboxDispatcher(
                    store, AutomaticGmailReplySender(self.gmail, store, self.contact),
                    channel="gmail", allowed_message_types=("blocked",),
                )
                assert dispatcher.dispatch_due(event.received_at) == []
                assert len(self.gmail.calls) == send_count
            return persisted, row["payload"]
        finally:
            store.close()


def assert_no_intake_questions(case: Case, body: str) -> None:
    assert next_fact_questions(case) == []
    assert all(question not in body for question in QUESTION_TEXT_ZH.values())
    assert "计划哪天" not in body and "哪天离开" not in body


def test_first_sent_turn_asks_identity_without_restarting_unknown_dates(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    case, body = conversation.turn(FIRST_BODY)
    assert next_fact_questions(case) == ["full_name"]
    assert QUESTION_TEXT_ZH["full_name"] in body
    assert QUESTION_TEXT_ZH["date_of_birth"] not in body
    assert len(conversation.gmail.calls) == 1
    assert case.profile.occupation_status == "student"
    assert case.profile.funding_source == "self"
    assert set(case.deferred_fields) == TRAVEL_DATES
    assert "计划哪天" not in body and "哪天离开" not in body


def test_correction_is_answered_without_repeating_or_expanding_unanswered_questions(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    conversation.turn(FIRST_BODY)
    case, body = conversation.turn(CORRECTION_BODY)
    assert case.profile.visit_purpose == "conference"
    assert case.profile.funding_source == "employer_or_school"
    assert "参加会议" in body and "学校" in body
    assert "邀请函" in body and "gov.uk" in body
    assert_no_intake_questions(case, body)
    assert case.profile.full_name is None and case.profile.date_of_birth is None


def test_unreviewed_summary_is_not_consent_or_a_reason_to_repeat_identity(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    conversation.turn(FIRST_BODY)
    conversation.turn(CORRECTION_BODY)
    case, body = conversation.turn("如果都没问题可以继续，不过我还没检查摘要。")
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert_no_intake_questions(case, body)
    assert case.profile.full_name is None and case.profile.date_of_birth is None


def test_explicit_resume_returns_one_focused_missing_question(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    conversation.turn(FIRST_BODY)
    conversation.turn(CORRECTION_BODY)
    conversation.turn("我还没检查摘要，稍后再处理。")
    case, body = conversation.turn("现在可以继续了，下一步需要什么？")
    questions = next_fact_questions(case)
    assert questions == ["full_name"]
    assert QUESTION_TEXT_ZH["full_name"] in body
    assert all(question not in body for field, question in QUESTION_TEXT_ZH.items()
               if field != "full_name")
    assert not case.profile_confirmed and not case.final_summary_confirmed


def test_identity_volunteered_after_pause_is_saved_and_advances_without_reasking(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    conversation.turn(FIRST_BODY)
    conversation.turn(CORRECTION_BODY)
    conversation.turn("我还没检查摘要，稍后再处理。")
    case, body = conversation.turn(IDENTITY_BODY)
    assert case.profile.full_name == "示例申请人"
    assert case.profile.date_of_birth == date(1994, 6, 12)
    assert set(case.deferred_fields) == TRAVEL_DATES
    for field in IDENTITY_FIELDS:
        evidence = case.active_evidence(field)
        assert len(evidence) == 1
        assert evidence[0].source_excerpt in IDENTITY_BODY
        assert evidence[0].source_event_id == "frontier-inbound-4"
        assert not evidence[0].confirmed
        assert QUESTION_TEXT_ZH[field] not in body
    questions = next_fact_questions(case)
    assert questions and questions[0] == "uk_accommodation"
    assert not (IDENTITY_FIELDS | TRAVEL_DATES) & set(questions)
    assert QUESTION_TEXT_ZH["uk_accommodation"] in body
    assert len(conversation.gmail.calls) == 4


def test_unsent_draft_is_not_treated_as_a_question_the_customer_received(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    first, _ = conversation.turn(FIRST_BODY, send=False)
    assert next_fact_questions(first) == ["full_name"]
    assert conversation.gmail.calls == []
    case, body = conversation.turn(CORRECTION_BODY)
    assert next_fact_questions(case) == ["full_name"]
    assert QUESTION_TEXT_ZH["full_name"] in body
    assert QUESTION_TEXT_ZH["date_of_birth"] not in body
    assert len(conversation.gmail.calls) == 1
    store = SQLiteStore(conversation.db_path)
    try:
        rows = store.list_outbox()
        first_row = next(row for row in rows if row["event_id"] == "frontier-inbound-1")
        assert first_row["status"] == "FAILED"
        assert first_row["last_error"] == "Obsolete unsent reply withheld"
        assert first_row["attempt_count"] == 0 and first_row["provider_message_id"] is None
        assert [row["status"] for row in rows if row["event_id"] == "frontier-inbound-2"] == ["SENT"]
    finally:
        store.close()


def test_legacy_case_recovers_only_the_matching_sent_question_set(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    first, _ = conversation.turn(FIRST_BODY)
    store = SQLiteStore(conversation.db_path)
    try:
        restored = store.get_case(first.id)
        assert restored is not None
        # Model the previous released snapshot, which had no question delivery ledger.
        restored.question_event_ids = {}
        restored.pending_question_fields = []
        restored.question_plan = None
        restored.last_requested_fields = ["full_name", "date_of_birth"]
        store.save_case(restored)
    finally:
        store.close()
    case, body = conversation.turn(CORRECTION_BODY)
    assert_no_intake_questions(case, body)
    assert set(case.pending_question_fields) == IDENTITY_FIELDS
    assert all(case.question_event_ids[field] == ["frontier-inbound-1"] for field in IDENTITY_FIELDS)
    assert len(conversation.gmail.calls) == 2


def test_pure_waiting_receipt_does_not_claim_unsent_questions_were_asked(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    conversation.turn(FIRST_BODY, send=False)
    waiting, reply = conversation.turn("我稍后回复。")
    assert "等你方便时" in reply and '?' not in reply and '？' not in reply
    assert next_fact_questions(waiting) == []
    assert all("frontier-inbound-2" not in ids for ids in waiting.question_event_ids.values())
    case, body = conversation.turn(CORRECTION_BODY)
    assert next_fact_questions(case) == ["full_name"]
    assert QUESTION_TEXT_ZH["full_name"] in body
    assert QUESTION_TEXT_ZH["date_of_birth"] not in body
    assert case.pending_question_fields == []
