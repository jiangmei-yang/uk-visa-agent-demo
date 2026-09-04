"""Unanswered advice is continued from actual SENT context, never from a draft.

Only the extractor and Gmail transport are substituted. Every positive delivery
uses WorkflowService, the real automatic reviewed sender and OutboxDispatcher.
Each turn reopens its isolated database; no provider network is available.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import (
    OutboxDispatcher,
    PermanentChannelError,
    UncertainDeliveryError,
)
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.customer_questions import APPLICATION_SOURCE, REVIEW_AFTER, SOURCE
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 4)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
APPLICANT = "fictional-advice-continuation@example.test"
CONTINUE_ZH = "好，那接着讲刚才没说的吧。"
CONTINUE_EN = "Please continue with the unanswered questions."
QUESTIONS = {
    "zh": [
        ("application", "访问签证在哪里申请？"),
        ("timing", "最早什么时候能申请？"),
        ("fees", "申请费是多少？"),
        ("translation", "中文的存款证明要怎么翻译？"),
    ],
    "en": [
        ("application", "Where do I apply for my UK visitor visa?"),
        ("timing", "How early can I apply?"),
        ("fees", "What is the visitor visa application fee?"),
        ("translation", "How should I translate my Chinese supporting documents?"),
    ],
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Advice continuation tests must not access a model or Gmail network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def proposal(*, questions=None, facts=(), control=None):
    value = {
        "updates": [{"field": field, "value": item, "source_excerpt": excerpt, "confidence": 1}
                    for field, item, excerpt in facts],
        "ambiguities": [],
    }
    if questions is not None:
        value["customer_questions"] = [
            {"topic": topic, "source_excerpt": excerpt, "confidence": 1}
            for topic, excerpt in questions
        ]
    if control:
        value["preparation_intent"] = {
            "action": control[0], "source_excerpt": control[1], "confidence": 1,
        }
    return CasePatch.model_validate(value)


class Model:
    def __init__(self, patch, *, lowercase_draft=False):
        self.patch = patch
        self.events = []
        self.lowercase_draft = lowercase_draft

    def extract_case_patch(self, event):
        self.events.append(event.model_copy(deep=True))
        return self.patch.model_copy(deep=True)

    def render_message(self, case, plan):
        body = deterministic_fallback_message(case, plan)
        return body.lower() if self.lowercase_draft else body


class CaptureGmail(GmailAdapter):
    def __init__(self):
        self.calls = []
        self.delivered = []
        self.failure = None

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == APPLICANT and kwargs.get("attachment") is None
        self.calls.append(kwargs)
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        self.delivered.append(kwargs)
        return {"id": f"captured-advice-{len(self.delivered)}"}

    def find_sent_message(self, rfc_message_id):
        return None  # No provider acceptance evidence for the injected uncertain send.


class Conversation:
    def __init__(self, tmp_path, *, lowercase_guarded_drafts=False):
        self.path = tmp_path / "advice-continuation.db"
        self.gmail = CaptureGmail()
        self.sequence = 0
        self.reads = []
        self.lowercase_guarded_drafts = lowercase_guarded_drafts

    def turn(self, body, patch=None, *, delivery="SENT", today=TODAY,
             thread="advice-thread", attachment_paths=()):
        self.sequence += 1
        event = InboundEvent(
            id=f"advice-{self.sequence}", external_thread_id=thread, sender=APPLICANT,
            channel="gmail", subject="Fictional visitor questions", body=body,
            received_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC)
                        + timedelta(hours=12, minutes=self.sequence),
            rfc_message_id=f"<advice-{self.sequence}@example.test>",
            attachment_paths=list(attachment_paths),
        )
        model = Model(patch if patch is not None else proposal(),
                      lowercase_draft=self.lowercase_guarded_drafts)
        store = SQLiteStore(self.path)
        try:
            guard = GuardedLLM(model)

            def reader(path):
                self.reads.append(path)
                return DocumentReadResult("student_letter", "en", 1, {}, method="test_reader")

            workflow = WorkflowService(store, POLICY, guard, today_provider=lambda: today,
                                       document_reader=reader)
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == "blocked" and not guard.last_extraction_fallback
            assert case.delivery_path is None
            sender = AutomaticGmailReplySender(
                self.gmail, store, APPLICANT,
                allow_guarded_drafts=self.lowercase_guarded_drafts,
            )
            sender.withhold_obsolete_unsent()
            dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",))
            current_row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            if delivery != "PENDING":
                if delivery == "FAILED":
                    self.gmail.failure = PermanentChannelError("Synthetic send rejected before acceptance")
                elif delivery == "AMBIGUOUS":
                    self.gmail.failure = UncertainDeliveryError("Synthetic send result was lost")
                outcomes = dispatcher.dispatch_due(event.received_at)
                assert any(item.outbox_id == current_row["id"] for item in outcomes)
                if delivery == "AMBIGUOUS":
                    dispatcher.reconcile_sending(sender, event.received_at)
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["status"] == delivery
            if delivery == "SENT":
                assert row["provider_message_id"]
                expected_mode = "guarded_draft" if self.lowercase_guarded_drafts else "reviewed"
                assert row["reply_render_mode"] == expected_mode
                assert self.gmail.delivered[-1]["body"] == row["payload"]
                assert self.gmail.delivered[-1]["thread_id"] == thread
            assert store.get_case(case.id).model_dump() == case.model_dump()
            return SimpleNamespace(case=case, event=event, body=row["payload"], row=row, model=model)
        finally:
            store.close()

    def opening(self, language="zh", **kwargs):
        questions = QUESTIONS[language]
        return self.turn(" ".join(text for _, text in questions), proposal(questions=questions), **kwargs)

    def saved_case(self, case_id):
        store = SQLiteStore(self.path)
        try:
            return store.get_case(case_id)
        finally:
            store.close()


def assert_no_intake_or_consent(result, *, paused=False, full_name=None, preparation_action=None):
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert result.case.profile.visit_purpose is None
    assert result.case.profile.full_name == full_name and result.case.profile.date_of_birth is None
    assert result.case.profile.current_address is None
    assert result.case.preparation_paused is paused
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.confirmation_kind is None and result.case.confirmation_fingerprint is None
    assert result.case.latest_preparation_action == preparation_action
    assert result.case.delivery_path is None


def assert_fee_answer(result):
    assert re.search(r"£\s*\d", result.body), result.body
    assert "Standard Visitor" in result.body and APPLICATION_SOURCE in result.body
    assert any(word in result.body.lower() for word in ("申请费", "application fee")), result.body
    assert not any(word in result.body for word in ("护照上的姓名", "这次去英国主要是", "date of birth"))


def assert_fee_not_answered(result):
    assert re.search(r"£\s*\d", result.body) is None, result.body


def pending_fee(result):
    entries = [item for item in result.case.pending_advice if item.topic == "fees"]
    assert len(entries) == 1
    return entries[0]


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("model_label", ["default", "empty", "unsupported", "next_step"])
def test_actual_sent_omission_reopens_as_fee_advice_independently_of_classifier(tmp_path, language, model_label):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(language)
    pending = pending_fee(first)
    assert pending.source_event_id == first.event.id and pending.source_body == first.event.body
    assert pending.offered_notice in first.body and pending.source_checked_at == TODAY
    assert len(pending.source_questions) == 4 and pending.answer_attempts == []
    assert_fee_not_answered(first)
    body = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    questions = None if model_label == "default" else [] if model_label == "empty" else [(model_label, body)]
    result = dialogue.turn(body, proposal(questions=questions))
    assert result.case.id == first.case.id
    assert result.model.events == []  # Context continuation must precede classifier extraction.
    assert result.case.profile == first.case.profile
    assert_fee_answer(result)
    assert_no_intake_or_consent(result)
    attempted = pending_fee(result)
    assert any(item.event_id == result.event.id and item.answer in result.body for item in attempted.answer_attempts)
    assert len(dialogue.gmail.delivered) == 2


@pytest.mark.parametrize("delivery", ["PENDING", "FAILED", "AMBIGUOUS"])
def test_an_unsent_omission_is_not_received_context(tmp_path, delivery):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(delivery=delivery)
    pending_fee(first)
    result = dialogue.turn(CONTINUE_ZH)
    assert_fee_not_answered(result)
    assert_no_intake_or_consent(result)
    assert any(word in result.body for word in ("没有", "未", "请", "哪")), result.body
    assert pending_fee(result).answer_attempts == []


def test_expired_source_is_rechecked_not_replayed_or_consumed(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening()
    result = dialogue.turn(CONTINUE_ZH, today=REVIEW_AFTER + timedelta(days=1))
    assert_fee_not_answered(result)
    assert_no_intake_or_consent(result)
    assert any(word in result.body for word in ("复核", "核实", "最新")), result.body
    assert pending_fee(result).source_event_id == first.event.id
    again = dialogue.turn(CONTINUE_ZH, today=REVIEW_AFTER + timedelta(days=2))
    assert_fee_not_answered(again)
    assert pending_fee(again).source_event_id == first.event.id


def test_pending_advice_is_isolated_by_case_even_for_the_same_sender(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(thread="first-advice-case")
    other = dialogue.turn(CONTINUE_ZH, thread="unrelated-advice-case")
    assert other.case.id != first.case.id and not other.case.pending_advice
    assert_fee_not_answered(other)
    assert_no_intake_or_consent(other)
    result = dialogue.turn(CONTINUE_ZH, thread="first-advice-case")
    assert_fee_answer(result)


def test_sent_answer_is_consumed_on_next_event_and_repeated_continue_is_finite(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.opening()
    answer = dialogue.turn(CONTINUE_ZH)
    assert_fee_answer(answer)
    delivered = len(dialogue.gmail.delivered)
    store = SQLiteStore(dialogue.path)
    try:
        model = Model(proposal())
        duplicate = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY).process(answer.event)
        assert duplicate[1] and model.events == []
    finally:
        store.close()
    assert len(dialogue.gmail.delivered) == delivered
    for _ in range(2):
        result = dialogue.turn(CONTINUE_ZH)
        assert_fee_not_answered(result)
        assert result.case.pending_advice == []
        assert_no_intake_or_consent(result)


@pytest.mark.parametrize("delivery", ["PENDING", "FAILED", "AMBIGUOUS"])
def test_unsent_continuation_is_not_consumed_and_only_current_draft_is_delivered(tmp_path, delivery):
    dialogue = Conversation(tmp_path)
    dialogue.opening()
    unsent = dialogue.turn(CONTINUE_ZH, delivery=delivery)
    assert_fee_answer(unsent)
    assert any(item.event_id == unsent.event.id for item in pending_fee(unsent).answer_attempts)
    sent = dialogue.turn(CONTINUE_ZH)
    assert_fee_answer(sent)
    assert any(item.event_id == unsent.event.id for item in pending_fee(sent).answer_attempts)
    assert len(dialogue.gmail.delivered) == 2
    if delivery == "PENDING":
        store = SQLiteStore(dialogue.path)
        try:
            old = next(row for row in store.list_outbox() if row["event_id"] == unsent.event.id)
            assert old["status"] == "FAILED" and old["attempt_count"] == 0
        finally:
            store.close()
    finished = dialogue.turn(CONTINUE_ZH)
    assert_fee_not_answered(finished)
    assert finished.case.pending_advice == []


def test_an_independent_new_question_takes_priority_without_losing_earlier_unanswered_advice(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening()
    question = "先说一个新问题：银行流水主要用来说明什么？"
    fresh = dialogue.turn(question, proposal(questions=[("bank_period", question)]))
    assert SOURCE in fresh.body and "资金来源" in fresh.body
    assert_fee_not_answered(fresh)
    assert_no_intake_or_consent(fresh)
    assert pending_fee(fresh).source_event_id == first.event.id
    assert pending_fee(fresh).answer_attempts == []
    continued = dialogue.turn(CONTINUE_ZH)
    assert_fee_answer(continued)


@pytest.mark.parametrize("body", [
    "先不要接着讲刚才没说的。",
    "如果以后决定继续，再接着讲刚才没说的吧。",
    "朋友说‘接着讲刚才没说的’，这不是我的请求。",
    "谢谢。\n\nOn Friday, Applicant wrote:\n好，那接着讲刚才没说的吧。",
    "Do not continue with the unanswered questions.",
    "If I decide to proceed later, please continue with the unanswered questions.",
    'My friend wrote "Please continue with the unanswered questions."',
])
def test_quoted_conditional_negative_or_third_party_text_does_not_trigger_or_consume(tmp_path, body):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening()
    result = dialogue.turn(body)
    assert_fee_not_answered(result)
    assert pending_fee(result).source_event_id == first.event.id
    assert pending_fee(result).answer_attempts == []
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    continued = dialogue.turn(CONTINUE_ZH)
    assert_fee_answer(continued)


def test_continuing_information_does_not_resume_preparation_or_confirm_any_summary(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening()
    pause = "请先暂停我的英国签证材料准备。"
    paused = dialogue.turn(pause, proposal(control=("pause", pause)))
    assert paused.case.preparation_paused
    assert pending_fee(paused).source_event_id == first.event.id
    epoch = paused.case.preparation_control_epoch
    result = dialogue.turn(CONTINUE_ZH, proposal(questions=[("next_step", CONTINUE_ZH)]))
    assert_fee_answer(result)
    assert_no_intake_or_consent(result, paused=True)
    assert result.case.preparation_control_epoch == epoch
    assert result.model.events == []


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("model_label", ["empty", "unsupported", "next_step"])
@pytest.mark.parametrize("sentence_separator", [True, False])
def test_mixed_new_fact_is_extracted_then_unanswered_advice_is_continued(
    tmp_path, language, model_label, sentence_separator,
):
    dialogue = Conversation(tmp_path)
    dialogue.opening(language)
    name = "示例安宁" if language == "zh" else "Rowan Example"
    statement = f"我的姓名是{name}" if language == "zh" else f"My full name is {name}"
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    separator = ("。" if sentence_separator else "，") if language == "zh" else (
        ". " if sentence_separator else ", "
    )
    questions = [] if model_label == "empty" else [(model_label, continuation)]
    result = dialogue.turn(statement + separator + continuation,
                           proposal(facts=[("full_name", name, statement)], questions=questions))
    assert len(result.model.events) == 1 and result.case.profile.full_name == name
    assert_fee_answer(result)
    assert_no_intake_or_consent(result, full_name=name)
    assert any(item.event_id == result.event.id for item in pending_fee(result).answer_attempts)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_an_attachment_is_read_then_unanswered_advice_is_continued(tmp_path, language):
    dialogue = Conversation(tmp_path)
    dialogue.opening(language)
    attachment = tmp_path / "ordinary.pdf"
    attachment.write_bytes(b"Isolated synthetic attachment")
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    result = dialogue.turn(continuation, proposal(questions=[("unsupported", continuation)]),
                           attachment_paths=[str(attachment)])
    assert len(result.model.events) == 1 and dialogue.reads == [attachment]
    assert len(result.case.documents) == 1
    assert_fee_answer(result)
    assert_no_intake_or_consent(result)
    assert any(item.event_id == result.event.id for item in pending_fee(result).answer_attempts)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_current_pause_is_applied_while_independent_advice_continues(tmp_path, language):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(language)
    pause = ("请先暂停我的英国签证材料准备。" if language == "zh"
             else "Please pause all my UK visa preparation.")
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    result = dialogue.turn(pause + " " + continuation,
                           proposal(control=("pause", pause), questions=[("next_step", continuation)]))
    assert len(result.model.events) == 1
    assert result.case.preparation_control_epoch == first.case.preparation_control_epoch + 1
    assert_fee_answer(result)
    assert_no_intake_or_consent(result, paused=True, preparation_action="pause")
    assert any(item.event_id == result.event.id for item in pending_fee(result).answer_attempts)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_mixed_fact_and_advice_does_not_resume_an_existing_pause(tmp_path, language):
    dialogue = Conversation(tmp_path)
    dialogue.opening(language)
    pause = ("请先暂停我的英国签证材料准备。" if language == "zh"
             else "Please pause all my UK visa preparation.")
    paused = dialogue.turn(pause, proposal(control=("pause", pause)))
    name = "示例安宁" if language == "zh" else "Rowan Example"
    fact = f"我的姓名是{name}。" if language == "zh" else f"My full name is {name}."
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    result = dialogue.turn(fact + " " + continuation,
                           proposal(facts=[("full_name", name, fact)],
                                    questions=[("unsupported", continuation)]))
    assert len(result.model.events) == 1
    assert result.case.preparation_control_epoch == paused.case.preparation_control_epoch
    assert_fee_answer(result)
    assert_no_intake_or_consent(result, paused=True, full_name=name)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_independent_faq_takes_priority_over_mixed_continuation_and_keeps_pending(tmp_path, language):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(language)
    name = "示例安宁" if language == "zh" else "Rowan Example"
    fact = f"我的姓名是{name}。" if language == "zh" else f"My full name is {name}."
    question = ("银行流水主要用来说明什么？" if language == "zh"
                else "What are my bank statements supposed to show?")
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    result = dialogue.turn(" ".join((fact, question, continuation)),
                           proposal(facts=[("full_name", name, fact)],
                                    questions=[("bank_period", question), ("unsupported", continuation)]))
    assert len(result.model.events) == 1
    assert SOURCE in result.body
    assert ("资金来源" if language == "zh" else "where the funds come from") in result.body
    assert_fee_not_answered(result)
    assert_no_intake_or_consent(result, full_name=name)
    assert pending_fee(result).source_event_id == first.event.id
    assert pending_fee(result).answer_attempts == []
    later = dialogue.turn(continuation)
    assert_fee_answer(later)
    assert_no_intake_or_consent(later, full_name=name)


@pytest.mark.parametrize("language,tail", [
    ("zh", "如果以后决定继续，再接着讲刚才没说的吧。"),
    ("zh", "朋友写了‘好，那接着讲刚才没说的吧’，这不是我的请求。"),
    ("zh", "先不要接着讲刚才没说的。"),
    ("en", "If I decide to proceed later, please continue with the unanswered questions."),
    ("en", 'My friend wrote "Please continue with the unanswered questions."'),
    ("en", "Do not continue with the unanswered questions."),
])
def test_a_real_new_fact_does_not_promote_conditional_quoted_or_negative_advice_to_a_request(
    tmp_path, language, tail,
):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening(language)
    name = "示例安宁" if language == "zh" else "Rowan Example"
    fact = f"我的姓名是{name}。" if language == "zh" else f"My full name is {name}."
    result = dialogue.turn(fact + " " + tail, proposal(facts=[("full_name", name, fact)]))
    assert len(result.model.events) == 1 and result.case.profile.full_name == name
    assert_fee_not_answered(result)
    assert pending_fee(result).source_event_id == first.event.id
    assert pending_fee(result).answer_attempts == []
    assert not result.case.preparation_paused and not result.case.profile_confirmed
    assert not result.case.final_summary_confirmed


def test_sent_status_without_the_exact_offered_notice_does_not_authorize_continuation(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.opening()
    pending = pending_fee(first)
    store = SQLiteStore(dialogue.path)
    try:
        with store.connection:
            store.connection.execute("UPDATE outbox SET payload=? WHERE event_id=?",
                                     (first.body.replace(pending.offered_notice, ""), first.event.id))
    finally:
        store.close()
    result = dialogue.turn(CONTINUE_ZH)
    assert_fee_not_answered(result)
    assert pending_fee(result).answer_attempts == []


def test_sent_status_without_the_exact_answer_does_not_consume_the_pending_question(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.opening()
    first = dialogue.turn(CONTINUE_ZH)
    answer = next(item.answer for item in pending_fee(first).answer_attempts if item.event_id == first.event.id)
    assert answer in first.body
    store = SQLiteStore(dialogue.path)
    try:
        with store.connection:
            store.connection.execute("UPDATE outbox SET payload=? WHERE event_id=?",
                                     (first.body.replace(answer, ""), first.event.id))
    finally:
        store.close()
    result = dialogue.turn(CONTINUE_ZH)
    assert_fee_answer(result)
    assert len(pending_fee(result).answer_attempts) >= 2


def test_casefold_equivalent_guarded_sent_notice_and_answer_authorize_then_consume(tmp_path):
    dialogue = Conversation(tmp_path, lowercase_guarded_drafts=True)
    first = dialogue.opening("en")
    notice = pending_fee(first).offered_notice
    assert notice not in first.body and notice.casefold() in first.body.casefold()
    assert first.body == first.body.lower()
    assert first.row["reply_render_mode"] == "guarded_draft"

    answered = dialogue.turn(CONTINUE_EN)
    assert answered.model.events == []
    assert re.search(r"£\s*\d", answered.body), answered.body
    assert "standard visitor" in answered.body and APPLICATION_SOURCE in answered.body
    attempt = next(item for item in pending_fee(answered).answer_attempts
                   if item.event_id == answered.event.id)
    assert attempt.answer not in answered.body
    assert attempt.answer.casefold() in answered.body.casefold()
    assert answered.body == answered.body.lower()
    assert answered.row["reply_render_mode"] == "guarded_draft"
    assert_no_intake_or_consent(answered)

    finished = dialogue.turn(CONTINUE_EN)
    assert finished.case.pending_advice == []
    assert_fee_not_answered(finished)
    assert_no_intake_or_consent(finished)
    assert len(dialogue.gmail.delivered) == 3


def test_casefold_matching_does_not_treat_a_changed_fee_as_the_grounded_answer(tmp_path):
    dialogue = Conversation(tmp_path, lowercase_guarded_drafts=True)
    dialogue.opening("en")
    first = dialogue.turn(CONTINUE_EN)
    pending = pending_fee(first)
    answer = next(item.answer for item in pending.answer_attempts if item.event_id == first.event.id)
    changed = re.sub(r"£\s*\d+", "£999999", first.body, count=1)
    assert changed != first.body and answer.casefold() not in changed.casefold()
    store = SQLiteStore(dialogue.path)
    try:
        with store.connection:
            store.connection.execute("UPDATE outbox SET payload=? WHERE event_id=?",
                                     (changed, first.event.id))
    finally:
        store.close()

    result = dialogue.turn(CONTINUE_EN)
    assert re.search(r"£\s*\d", result.body), result.body
    assert "£999999" not in result.body
    assert len(pending_fee(result).answer_attempts) == 2
    assert_no_intake_or_consent(result)
