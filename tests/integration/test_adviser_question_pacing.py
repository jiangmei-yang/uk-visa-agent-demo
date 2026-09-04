"""Fictional, sent-aware FAQ pacing through reopened workflow and Gmail outbox.

Only extraction and provider I/O are fakes. Every sent turn passes through the
real reviewed sender/dispatcher and reaches SENT in an isolated SQLite database.
No evaluation corpus, live mailbox, secrets, or API is accessed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import QUESTION_TEXT_EN, QUESTION_TEXT_ZH

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 4)
CONTACT = "fictional-pacing@example.test"
TRAVEL_FIELDS = {"planned_arrival_date", "planned_departure_date"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pacing tests cannot use a real network or mailbox")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def _patch(*questions, updates=(), deferrals=(), control=None):
    return CasePatch.model_validate({
        "updates": [{"field": field, "value": value, "source_excerpt": excerpt, "confidence": 1.0}
                    for field, value, excerpt in updates],
        "ambiguities": [],
        "customer_questions": [{"topic": topic, "source_excerpt": excerpt, "confidence": 1.0}
                               for topic, excerpt in questions],
        "question_deferrals": [{"field": field, "source_excerpt": excerpt, "confidence": 1.0}
                               for field, excerpt in deferrals],
        "preparation_intent": ({"action": control[0], "source_excerpt": control[1], "confidence": 1.0}
                               if control else None),
    })


class FixedModel:
    def __init__(self, patch):
        self.patch = patch
        self.events = []

    def extract_case_patch(self, event):
        self.events.append(event.model_copy(deep=True))
        return self.patch.model_copy(deep=True)

    def render_message(self, case, plan):
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self):
        self.calls = []

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == CONTACT and kwargs.get("attachment") is None
        self.calls.append(kwargs)
        return {"id": f"fictional-pacing-sent-{len(self.calls)}"}

    def find_sent_message(self, rfc_message_id):
        raise AssertionError("No provider reconciliation is needed in this isolated experiment")


class Conversation:
    def __init__(self, path, language):
        self.path = path / "adviser-pacing.db"
        self.language = language
        self.gmail = CaptureGmail()
        self.sequence = 0
        self.rows = []
        self.models = []

    def turn(self, body, patch, *, send=True, expected_plan="blocked"):
        # No case snapshot is seeded: all profile and question state comes from
        # earlier processed messages, and each message gets a new runtime/store.
        from visa_agent.workflow.service import WorkflowService

        self.sequence += 1
        event = InboundEvent(id=f"pacing-inbound-{self.sequence}",
            external_thread_id="fictional-pacing-thread", sender=CONTACT,
            subject="UK visitor preparation", channel="gmail", body=body,
            received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=self.sequence),
            rfc_message_id=f"<pacing-inbound-{self.sequence}@example.test>")
        model = FixedModel(patch)
        self.models.append(model)
        store = SQLiteStore(self.path)
        try:
            guard = GuardedLLM(model)
            workflow = WorkflowService(store, POLICY, guard, today_provider=lambda: TODAY)
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == expected_plan and len(model.events) == 1
            assert not guard.last_extraction_fallback and case.status == CaseStatus.DRAFT
            assert not case.profile_confirmed and not case.final_summary_confirmed
            assert case.delivery_path is None
            assert case.confirmation_kind == ("profile" if expected_plan == "awaiting_profile_confirmation" else None)
            sender = AutomaticGmailReplySender(self.gmail, store, CONTACT)
            if send:
                sender.withhold_obsolete_unsent()
                outcomes = OutboxDispatcher(store, sender, channel="gmail",
                    allowed_message_types=("blocked", "awaiting_profile_confirmation")).dispatch_due(event.received_at)
                assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["status"] == ("SENT" if send else "PENDING")
            if send:
                assert row["payload"] == self.gmail.calls[-1]["body"]
                assert row["provider_message_id"] == f"fictional-pacing-sent-{len(self.gmail.calls)}"
            persisted = store.get_case(case.id)
            assert persisted.model_dump() == case.model_dump()
            snapshot = persisted.model_dump_json()
            rows = store.list_outbox()
            send_count = len(self.gmail.calls)
            self.rows.append(row)
        finally:
            store.close()

        # Reopening and replaying the exact event must not extract or send again.
        store = SQLiteStore(self.path)
        try:
            reopened = WorkflowService(store, POLICY, GuardedLLM(model), today_provider=lambda: TODAY)
            assert reopened.process(event)[1] and len(model.events) == 1
            assert store.get_case(case.id).model_dump_json() == snapshot
            assert store.list_outbox() == rows
            if send:
                assert OutboxDispatcher(store, AutomaticGmailReplySender(self.gmail, store, CONTACT),
                    channel="gmail", allowed_message_types=("blocked", "awaiting_profile_confirmation")).dispatch_due(event.received_at) == []
                assert len(self.gmail.calls) == send_count
            return persisted, row["payload"]
        finally:
            store.close()


def _initial(language, *, with_step=True, personal_checklist=False):
    data = {
        "en": [
            ("full_name", "Sample Rowan", "My full name is Sample Rowan."),
            ("date_of_birth", "1993-08-19", "My date of birth is 1993-08-19."),
            ("nationality_country", "China", "My nationality is China."),
            ("application_country", "China", "I will apply from China."),
            ("occupation_status", "employed", "I am employed."),
            ("visit_purpose", "tourism", "I plan a holiday in the UK."),
        ],
        "zh": [
            ("full_name", "示例洛文", "我的姓名是示例洛文。"),
            ("date_of_birth", "1993-08-19", "我的出生日期是1993-08-19。"),
            ("nationality_country", "China", "我的国籍是中国。"),
            ("application_country", "China", "我在中国递交申请。"),
            ("occupation_status", "employed", "我目前受雇工作。"),
            ("visit_purpose", "tourism", "我计划去英国旅游。"),
        ],
    }[language]
    deferred = "My travel dates are not decided yet." if language == "en" else "旅行日期还没有确定。"
    step = "What should I prepare next for my UK visitor application?" if language == "en" else "我下一步应该先准备哪一项？"
    personal = "Which documents do I need for my application?" if language == "en" else "我这次申请还需要哪些材料？"
    question = personal if personal_checklist else step
    questions = [("document_checklist" if personal_checklist else "next_step", question)] if with_step or personal_checklist else []
    body = " ".join([*(excerpt for _, _, excerpt in data), deferred,
                     question if questions else "Please help me prepare the application."])
    return body, _patch(*questions, updates=data, deferrals=[(field, deferred) for field in sorted(TRAVEL_FIELDS)])


def _funding_faq(language):
    statement = "I will pay from my own savings." if language == "en" else "旅行费用全部由我自己的存款承担。"
    question = "Why are bank statements useful for a visitor application?" if language == "en" else "银行流水在访问签证申请里有什么作用？"
    return statement + " " + question, _patch(("bank_period", question),
        updates=[("funding_source", "self", statement)])


def _employment_faq(language):
    question = "What is the purpose of an employer letter?" if language == "en" else "在职证明有什么作用？"
    return question, _patch(("document_checklist", question))


def _step_with_faq(language):
    question, _ = _employment_faq(language)
    step = "Separately, what should I prepare next for my UK visitor application?" if language == "en" else "另外，按我的情况下一步应该先补哪一项？"
    return question + " " + step, _patch(("document_checklist", question), ("next_step", step))


def _accommodation(language):
    statement = ("My planned accommodation is Harbour Example Hotel in Bristol, not booked."
                 if language == "en" else "计划住宿是布里斯托的示例港湾酒店，尚未预订。")
    value = "Harbour Example Hotel in Bristol, not booked" if language == "en" else "布里斯托的示例港湾酒店，尚未预订"
    return statement, _patch(updates=[("uk_accommodation", value, statement)])


def _assert_no_intake(case, reply):
    assert case.question_plan == case.last_requested_fields == []
    questions = QUESTION_TEXT_ZH if case.customer_language == "zh" else QUESTION_TEXT_EN
    assert all(question not in reply for question in questions.values())


@pytest.mark.parametrize("language", ["en", "zh"])
def test_answer_then_consecutive_faqs_wait_but_explicit_next_step_and_plain_answer_advance(tmp_path, language):
    dialogue = Conversation(tmp_path, language)
    first, _ = dialogue.turn(*_initial(language))
    assert first.last_requested_fields == ["funding_source"]
    funding, bank_reply = dialogue.turn(*_funding_faq(language))
    employment, employment_reply = dialogue.turn(*_employment_faq(language))
    next_step, next_reply = dialogue.turn(*_step_with_faq(language))
    advanced, advanced_reply = dialogue.turn(*_accommodation(language))

    assert funding.profile.funding_source == employment.profile.funding_source == "self"
    assert ("资金来源" in bank_reply if language == "zh" else "funds come from" in bank_reply)
    assert ("在职证明" in employment_reply if language == "zh" else "employer letter" in employment_reply.lower())
    _assert_no_intake(funding, bank_reply)
    _assert_no_intake(employment, employment_reply)
    assert next_step.last_requested_fields == ["uk_accommodation"]
    assert next_step.next_step_advice.question_field == "uk_accommodation"
    assert (QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN)["uk_accommodation"] in next_reply
    assert advanced.profile.uk_accommodation is not None
    assert advanced.last_requested_fields and advanced.last_requested_fields[0] == "estimated_trip_cost_gbp"
    assert "uk_accommodation" not in advanced.last_requested_fields
    assert (QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN)["estimated_trip_cost_gbp"] in advanced_reply
    assert set(advanced.deferred_fields) == TRAVEL_FIELDS and not advanced.preparation_paused
    assert len(dialogue.gmail.calls) == 5 and all(row["status"] == "SENT" for row in dialogue.rows)
    assert all("pacing-inbound-2" not in ids and "pacing-inbound-3" not in ids
               for ids in advanced.question_event_ids.values())


@pytest.mark.parametrize("language", ["en", "zh"])
def test_partial_answer_with_faq_does_not_repeat_other_sent_question_or_add_income(tmp_path, language):
    dialogue = Conversation(tmp_path, language)
    first, _ = dialogue.turn(*_initial(language, with_step=False))
    assert "funding_source" in first.last_requested_fields and "uk_accommodation" in first.last_requested_fields
    case, reply = dialogue.turn(*_funding_faq(language))
    assert case.profile.funding_source == "self" and case.profile.uk_accommodation is None
    _assert_no_intake(case, reply)
    assert "uk_accommodation" in case.pending_question_fields
    assert case.question_event_ids["uk_accommodation"] == ["pacing-inbound-1"]
    assert "annual_income_gbp" not in case.question_event_ids
    assert len(dialogue.gmail.calls) == 2


def test_unsent_question_draft_does_not_become_pending_but_answered_faq_can_still_be_quiet(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    first, _ = dialogue.turn(*_initial("en"), send=False)
    assert first.last_requested_fields == ["funding_source"] and dialogue.gmail.calls == []
    faq, reply = dialogue.turn(*_funding_faq("en"))
    _assert_no_intake(faq, reply)
    assert faq.pending_question_fields == []
    next_step, _ = dialogue.turn(*_step_with_faq("en"))
    assert next_step.last_requested_fields == ["uk_accommodation"]
    assert next_step.question_event_ids["uk_accommodation"] == ["pacing-inbound-3"]
    store = SQLiteStore(dialogue.path)
    try:
        rows = store.list_outbox()
        assert [row["status"] for row in rows] == ["FAILED", "SENT", "SENT"]
        assert rows[0]["provider_message_id"] is None and rows[0]["attempt_count"] == 0
        assert rows[0]["last_error"] == "Obsolete unsent reply withheld"
    finally:
        store.close()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_unanswered_personal_checklist_label_alone_does_not_silently_stall_missing_context(tmp_path, language):
    dialogue = Conversation(tmp_path, language)
    case, reply = dialogue.turn(*_initial(language, with_step=False, personal_checklist=True))
    assert case.customer_question_topics == ["document_checklist"]
    assert case.customer_answers == [] and case.profile.funding_source is None
    assert case.last_requested_fields and case.last_requested_fields[0] == "funding_source"
    assert (QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN)["funding_source"] in reply


@pytest.mark.parametrize("language", ["en", "zh"])
def test_pause_retains_facts_and_faq_then_explicit_resume_and_next_step_asks_only_one(tmp_path, language):
    dialogue = Conversation(tmp_path, language)
    dialogue.turn(*_initial(language))
    pause = "Please pause my UK visa preparation for now." if language == "en" else "请先暂停我的英国签证材料准备。"
    paused, pause_reply = dialogue.turn(pause, _patch(control=("pause", pause)))
    info, info_reply = dialogue.turn(*_funding_faq(language))
    _, step_patch = _step_with_faq(language)
    step = step_patch.customer_questions[-1].source_excerpt
    preview, preview_reply = dialogue.turn(step, _patch(("next_step", step)))
    resume = "Please resume my UK visa preparation now." if language == "en" else "现在请恢复我的英国签证材料准备。"
    resumed, resumed_reply = dialogue.turn(resume + " " + step,
        _patch(("next_step", step), control=("resume", resume)))

    assert paused.preparation_paused and info.preparation_paused and preview.preparation_paused
    assert info.profile.funding_source == "self" and info.preparation_control_epoch == 1
    _assert_no_intake(paused, pause_reply)
    _assert_no_intake(info, info_reply)
    _assert_no_intake(preview, preview_reply)
    assert preview.next_step_advice.kind == "paused"
    assert not resumed.preparation_paused and resumed.preparation_control_epoch == 2
    assert resumed.last_requested_fields == ["uk_accommodation"]
    assert (QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN)["uk_accommodation"] in resumed_reply
    assert not resumed.profile_confirmed and not resumed.final_summary_confirmed and resumed.delivery_path is None
    assert len(dialogue.gmail.calls) == 5


def _application_country_question():
    body, patch = _initial("en")
    missing = next(update for update in patch.updates if update.field == "application_country")
    return body.replace(missing.source_excerpt, ""), patch.model_copy(update={
        "updates": [update for update in patch.updates if update.field != "application_country"],
    })


def test_unsent_draft_question_is_not_given_to_extractor_as_an_answer_context(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    draft, _ = dialogue.turn(*_initial("en"), send=False)
    assert draft.last_requested_fields == ["funding_source"]
    statement = "My estimated total budget is GBP 1900."
    case, _ = dialogue.turn(statement, _patch(updates=[("estimated_trip_cost_gbp", 1900, statement)]))
    prepared = dialogue.models[-1].events[0]
    assert prepared.body == statement and prepared.requested_fields == []
    assert prepared.known_profile["funding_source"] is None
    assert case.profile.estimated_trip_cost_gbp == 1900 and case.profile.funding_source is None
    assert case.pending_question_fields == []
    assert len(dialogue.gmail.calls) == 1
    store = SQLiteStore(dialogue.path)
    try:
        rows = store.list_outbox()
        assert [row["status"] for row in rows] == ["FAILED", "SENT"]
        assert rows[0]["provider_message_id"] is None
    finally:
        store.close()


def test_real_sent_question_provides_exact_context_for_a_bare_country_answer(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    question, reply = dialogue.turn(*_application_country_question())
    assert question.last_requested_fields == ["application_country"]
    assert QUESTION_TEXT_EN["application_country"] in reply and dialogue.rows[-1]["status"] == "SENT"
    answered, _ = dialogue.turn("China", _patch(updates=[("application_country", "China", "China")]))
    prepared = dialogue.models[-1].events[0]
    assert prepared.body == "China" and prepared.requested_fields == ["application_country"]
    assert prepared.known_profile["application_country"] is None
    assert answered.profile.application_country == "China"
    assert answered.active_evidence("application_country")[0].source_event_id == "pacing-inbound-2"
    assert "application_country" not in answered.pending_question_fields
    assert len(dialogue.gmail.calls) == 2


def test_faq_clears_latest_plan_but_older_sent_unanswered_question_remains_bare_answer_context(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    first, _ = dialogue.turn(*_application_country_question())
    assert first.last_requested_fields == ["application_country"]
    faq, reply = dialogue.turn(*_employment_faq("en"))
    _assert_no_intake(faq, reply)
    assert faq.pending_question_fields == ["application_country"]
    assert faq.question_event_ids["application_country"] == ["pacing-inbound-1"]
    assert dialogue.models[-1].events[0].requested_fields == ["application_country"]

    answered, _ = dialogue.turn("China", _patch(updates=[("application_country", "China", "China")]))
    prepared = dialogue.models[-1].events[0]
    assert prepared.requested_fields == ["application_country"]
    assert answered.profile.application_country == "China"
    assert "application_country" not in answered.pending_question_fields
    assert "pacing-inbound-2" not in answered.question_event_ids["application_country"]
    assert len(dialogue.gmail.calls) == 3


def test_completed_sent_answer_is_not_reused_as_context_for_an_independent_fact(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    first, _ = dialogue.turn(*_initial("en"))
    assert first.last_requested_fields == ["funding_source"]
    funded, reply = dialogue.turn(*_funding_faq("en"))
    _assert_no_intake(funded, reply)
    assert funded.profile.funding_source == "self"
    assert dialogue.models[-1].events[0].requested_fields == ["funding_source"]
    assert funded.question_event_ids["funding_source"] == ["pacing-inbound-1"]
    assert funded.pending_question_fields == []

    statement = "My estimated total budget is GBP 1900."
    case, _ = dialogue.turn(statement, _patch(updates=[("estimated_trip_cost_gbp", 1900, statement)]))
    prepared = dialogue.models[-1].events[0]
    assert prepared.body == statement and prepared.requested_fields == []
    assert prepared.known_profile["funding_source"] == "self"
    assert case.profile.estimated_trip_cost_gbp == 1900 and case.profile.funding_source == "self"
    assert "funding_source" not in case.pending_question_fields
    assert len(dialogue.gmail.calls) == 3


def _all_facts_with_partial_home_address():
    # Every value, including the coarse address, first enters through a real
    # inbound event. There is no preconstructed case or fake SENT ledger.
    data = [
        ("full_name", "Sample Rowan", "My full name is Sample Rowan."),
        ("date_of_birth", "1993-08-19", "My date of birth is 1993-08-19."),
        ("nationality_country", "India", "My nationality is India."),
        ("application_country", "India", "I will apply from India."),
        ("occupation_status", "student", "I am a student."),
        ("visit_purpose", "tourism", "I plan a holiday in the UK."),
        ("planned_arrival_date", "2026-11-05", "My planned UK arrival date is 2026-11-05."),
        ("planned_departure_date", "2026-11-12", "My planned UK departure date is 2026-11-12."),
        ("uk_accommodation", "Planned hotel in London", "My accommodation is Planned hotel in London."),
        ("estimated_trip_cost_gbp", 2400, "My estimated total trip budget is GBP 2400."),
        ("funding_source", "self", "I will pay from my own savings."),
        ("current_address", "Mumbai, India", "My home address is Mumbai, India."),
        ("has_serious_history", False,
         "I have no visa refusals, immigration breaches, criminal history or civil judgments."),
        ("route_confirmed_standard_visitor", True,
         "I confirm I am applying for a UK Standard Visitor visa."),
    ]
    question = "Why are bank statements useful for a visitor application?"
    return " ".join([*(item[2] for item in data), question]), _patch(("bank_period", question), updates=data)


def _assert_home_detail_clarification(case, reply):
    assert case.profile.current_address == "Mumbai, India"
    assert case.question_plan == case.last_requested_fields == ["current_address"]
    assert case.pending_question_fields == ["current_address"]
    assert QUESTION_TEXT_EN["current_address"] not in reply
    assert "details that identify your home" in reply
    assert "street" in reply and "residence name" in reply and "applicable" in reply
    assert "Just reply when you have new plans" not in reply


def test_same_partial_home_answer_gets_specific_clarification_then_complete_answer_advances(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    initial, initial_reply = dialogue.turn(*_all_facts_with_partial_home_address())
    assert initial.profile.current_address == "Mumbai, India"
    assert initial.active_evidence("current_address")[0].source_event_id == "pacing-inbound-1"
    _assert_no_intake(initial, initial_reply)

    step = "What should I prepare next for my UK visitor application?"
    asked, asked_reply = dialogue.turn(step, _patch(("next_step", step)))
    assert asked.last_requested_fields == ["current_address"]
    assert "details that identify your home" in asked_reply

    repeated = "My home address is Mumbai, India."
    clarified, clarification = dialogue.turn(repeated,
        _patch(updates=[("current_address", "Mumbai, India", repeated)]))
    assert dialogue.models[-1].events[0].requested_fields == ["current_address"]
    assert "current_address" not in clarified.latest_changes
    assert "current_address" not in clarified.latest_received_facts
    _assert_home_detail_clarification(clarified, clarification)

    # Named residences are valid locally without imposing a universal street
    # number/postcode requirement, but the customer must actually supply it.
    full_address = "Room 4, Building W, Mumbai, India"
    complete_statement = f"My home address is {full_address}."
    complete, summary = dialogue.turn(complete_statement,
        _patch(updates=[("current_address", full_address, complete_statement)]),
        expected_plan="awaiting_profile_confirmation")
    assert complete.profile.current_address == full_address
    assert "current_address" not in complete.pending_question_fields
    assert "current_address" not in complete.last_requested_fields
    assert full_address in summary and "details that identify your home" not in summary
    assert complete.confirmation_request_event_id == "pacing-inbound-4"
    assert not complete.profile_confirmed and not complete.final_summary_confirmed
    assert len(dialogue.gmail.calls) == 4
    assert all(row["status"] == "SENT" for row in dialogue.rows)


def test_same_partial_home_answer_with_faq_answers_faq_without_repeating_address_question(tmp_path):
    dialogue = Conversation(tmp_path, "en")
    dialogue.turn(*_all_facts_with_partial_home_address())
    step = "What should I prepare next for my UK visitor application?"
    dialogue.turn(step, _patch(("next_step", step)))
    repeated = "My home address is Mumbai, India."
    faq, _ = _employment_faq("en")
    case, reply = dialogue.turn(repeated + " " + faq,
        _patch(("document_checklist", faq), updates=[("current_address", "Mumbai, India", repeated)]))
    assert dialogue.models[-1].events[0].requested_fields == ["current_address"]
    assert case.profile.current_address == "Mumbai, India"
    assert case.pending_question_fields == ["current_address"]
    assert "employer letter" in reply.lower()
    assert "details that identify your home" not in reply
    _assert_no_intake(case, reply)
    assert case.question_event_ids["current_address"] == ["pacing-inbound-2"]

    # Quiet FAQ pacing keeps a recoverable pending question; it is not a pause
    # or a permanent stall when the customer explicitly asks to proceed.
    resumed, resumed_reply = dialogue.turn(step, _patch(("next_step", step)))
    _assert_home_detail_clarification(resumed, resumed_reply)
    assert not resumed.preparation_paused and len(dialogue.gmail.calls) == 4
