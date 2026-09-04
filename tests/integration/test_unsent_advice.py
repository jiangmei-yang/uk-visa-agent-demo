"""Actual inbound questions survive unsent replies without inventing SENT context.

These isolated conversations use the real workflow, reviewed automatic sender
and dispatcher. Only model extraction and the Gmail provider are substituted.
"""

import json
import re
from datetime import timedelta

import pytest
from test_advice_continuation import (
    APPLICANT,
    APPLICATION_SOURCE,
    CONTINUE_EN,
    CONTINUE_ZH,
    REVIEW_AFTER,
    SOURCE,
    Conversation,
    assert_fee_answer,
    assert_fee_not_answered,
    assert_no_intake_or_consent,
    proposal,
)
from test_mixed_processing_consent import Journey as ConsentJourney

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.outbound import OutboxDispatcher, UncertainDeliveryError
from visa_agent.storage.sqlite import SQLiteStore

QUESTION = {
    "zh": {
        "fees": "普通访客签证申请费是多少？",
        "application": "普通访客签证在哪里申请？",
        "translation": "中文材料需要怎样翻译？",
    },
    "en": {
        "fees": "What is the visitor visa application fee?",
        "application": "Where do I apply for my UK visitor visa?",
        "translation": "How should I translate my Chinese supporting documents?",
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unsent advice tests must not use a model or Gmail network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def ask(dialogue, topic, *, language="zh", **kwargs):
    text = QUESTION[language][topic]
    return dialogue.turn(text, proposal(questions=[(topic, text)]), **kwargs)


def assert_application_answer(body):
    assert APPLICATION_SOURCE in body and "apply now" in body.casefold(), body


def assert_translation_answer(body):
    assert SOURCE in body
    assert "翻译日期" in body or "translation date" in body.casefold(), body


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("delivery", ["PENDING", "FAILED"])
def test_latest_sent_reply_includes_the_earlier_truly_unsent_question(tmp_path, language, delivery):
    dialogue = Conversation(tmp_path)
    first = ask(dialogue, "fees", language=language, delivery=delivery)
    assert_fee_answer(first)  # It was drafted, not delivered to the applicant.
    assert dialogue.gmail.delivered == []
    latest = ask(dialogue, "application", language=language)
    assert latest.row["status"] == "SENT" and len(dialogue.gmail.delivered) == 1
    assert_application_answer(latest.body)
    assert_fee_answer(latest)
    assert_no_intake_or_consent(latest)
    assert latest.case.id == first.case.id
    continued = dialogue.turn(CONTINUE_ZH if language == "zh" else CONTINUE_EN)
    assert_fee_not_answered(continued)
    assert_no_intake_or_consent(continued)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_pure_continue_can_answer_an_actual_question_without_a_sent_omission_invitation(tmp_path, language):
    dialogue = Conversation(tmp_path)
    ask(dialogue, "fees", language=language, delivery="PENDING")
    continued = dialogue.turn(CONTINUE_ZH if language == "zh" else CONTINUE_EN)
    assert_fee_answer(continued)
    assert_no_intake_or_consent(continued)
    assert len(dialogue.gmail.delivered) == 1
    assert not re.search(r"上一封已发|之前已经发|(?:as|in) (?:my|the) (?:previous|last) sent reply",
                         continued.body, re.I)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_overflow_from_an_unsent_four_question_request_is_eventually_answered_once(tmp_path, language):
    dialogue = Conversation(tmp_path)
    dialogue.opening(language, delivery="PENDING")
    continuation = CONTINUE_ZH if language == "zh" else CONTINUE_EN
    first = dialogue.turn(continuation)
    second = dialogue.turn(continuation)
    assert 1 <= len(first.case.customer_answers) <= 3
    assert 1 <= len(second.case.customer_answers) <= 3
    combined = first.body + "\n" + second.body
    assert_application_answer(combined)
    assert_translation_answer(combined)
    assert re.search(r"£\s*\d", combined)
    assert re.search(r"3\s*(?:个月|周|months|weeks)", combined, re.I)
    assert sum(bool(re.search(r"£\s*\d", result.body)) for result in (first, second)) == 1
    finished = dialogue.turn(continuation)
    assert_fee_not_answered(finished)
    assert_no_intake_or_consent(finished)
    assert "apply now" not in finished.body.casefold()


def test_an_unsent_request_never_crosses_into_another_case_for_the_same_sender(tmp_path):
    dialogue = Conversation(tmp_path)
    # A definitely rejected delivery cannot be picked up by the shared sender
    # while dispatching the other case's otherwise independent reply.
    original = ask(dialogue, "fees", delivery="FAILED", thread="original-case")
    other = ask(dialogue, "application", thread="other-case")
    assert original.case.id != other.case.id
    assert_application_answer(other.body)
    assert_fee_not_answered(other)
    result = dialogue.turn(CONTINUE_ZH, thread="original-case")
    assert_fee_answer(result)
    assert_no_intake_or_consent(result)


def test_expired_unsent_fee_is_not_replayed_as_a_current_price(tmp_path):
    dialogue = Conversation(tmp_path)
    original = ask(dialogue, "fees", delivery="PENDING")
    result = ask(dialogue, "application", today=REVIEW_AFTER + timedelta(days=1))
    assert_fee_not_answered(result)
    assert any(word in result.body for word in ("核验", "复核", "核实", "查证")), result.body
    assert any(item.source_event_id == original.event.id for item in result.case.unsent_advice)
    repeated = dialogue.turn(CONTINUE_ZH, today=REVIEW_AFTER + timedelta(days=2))
    assert_fee_not_answered(repeated)
    assert_no_intake_or_consent(repeated)


@pytest.mark.parametrize("status", ["SENDING", "AMBIGUOUS"])
def test_uncertain_delivery_is_not_assumed_unsent_and_repeated_in_the_new_reply(tmp_path, status):
    dialogue = Conversation(tmp_path)
    original = ask(dialogue, "fees", delivery="PENDING")
    store = SQLiteStore(dialogue.path)
    try:
        dialogue.gmail.failure = UncertainDeliveryError("Provider acceptance is not known")
        sender = AutomaticGmailReplySender(dialogue.gmail, store, APPLICANT)
        dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",))
        dispatcher.dispatch_due(original.event.received_at)
        if status == "AMBIGUOUS":
            dispatcher.reconcile_sending(sender, original.event.received_at)
        row = next(row for row in store.list_outbox() if row["event_id"] == original.event.id)
        assert row["status"] == status and not row["provider_message_id"]
    finally:
        store.close()
    assert len(dialogue.gmail.calls) == 1 and dialogue.gmail.delivered == []
    latest = ask(dialogue, "application", delivery="PENDING")
    assert_application_answer(latest.body)
    assert_fee_not_answered(latest)
    continued = dialogue.turn(CONTINUE_ZH, delivery="PENDING")
    assert_fee_not_answered(continued)
    assert_no_intake_or_consent(continued)
    assert not re.search(r"之前(?:还|从)没(?:有)?发|从未(?:发|回复)|has not been sent|never sent",
                         continued.body, re.I)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_latest_no_links_request_applies_to_both_new_and_carried_advice(tmp_path, language):
    dialogue = Conversation(tmp_path)
    ask(dialogue, "application", language=language, delivery="PENDING")
    question = QUESTION[language]["fees"]
    constraint = "这次不用给我链接。" if language == "zh" else "Please do not include any links in this reply."
    result = dialogue.turn(constraint + " " + question, proposal(questions=[("fees", question)]))
    assert re.search(r"£\s*\d", result.body), result.body
    assert not re.search(r"https?://|www\.gov\.uk", result.body, re.I), result.body
    assert_no_intake_or_consent(result)
    continued = dialogue.turn(CONTINUE_ZH if language == "zh" else CONTINUE_EN)
    assert_fee_not_answered(continued)  # Removing requested URLs must not make a delivered fee look unanswered.


@pytest.mark.parametrize("language", ["zh", "en"])
def test_cancelled_earlier_questions_are_not_carried_or_revived_by_a_generic_continue(tmp_path, language):
    dialogue = Conversation(tmp_path)
    ask(dialogue, "fees", language=language, delivery="PENDING")
    question = QUESTION[language]["translation"]
    cancel = ("之前的问题都不用回答了。" if language == "zh"
              else "Please stop answering my earlier questions; I no longer need those answers.")
    result = dialogue.turn(cancel + " " + question, proposal(questions=[("translation", question)]))
    assert_translation_answer(result.body)
    assert_fee_not_answered(result)
    continued = dialogue.turn(CONTINUE_ZH if language == "zh" else CONTINUE_EN)
    assert_fee_not_answered(continued)
    assert_no_intake_or_consent(continued)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_same_topic_keeps_the_original_document_qualification_not_only_the_latest_keyword(tmp_path, language):
    dialogue = Conversation(tmp_path)
    original = ("存款余额证明能不能代替银行流水？" if language == "zh"
                else "Can a balance certificate replace my bank statements?")
    latest = ("两个账户的钱分开放，流水怎么整理？" if language == "zh"
              else "My funds are split between two accounts. How should I organize the bank statements?")
    first = dialogue.turn(original, proposal(questions=[("bank_period", original)]), delivery="PENDING")
    distinction = "不一定能互相替代" if language == "zh" else "not necessarily interchangeable"
    accounts = "避免把同一笔钱重复计算" if language == "zh" else "so the same money is not counted twice"
    assert distinction in first.body
    result = dialogue.turn(latest, proposal(questions=[("bank_period", latest)]))
    assert accounts in result.body
    assert distinction in result.body, result.body
    assert SOURCE in result.body
    assert_no_intake_or_consent(result)


@pytest.mark.parametrize("body", [
    'My friend asked "What is the visitor visa application fee?"',
    "If I apply later, I might ask what the visitor visa application fee is.",
    "Please do not answer what the visitor visa application fee is.",
])
def test_reported_conditional_or_negated_question_is_not_an_actual_unsent_request(tmp_path, body):
    dialogue = Conversation(tmp_path)
    dialogue.turn(body, proposal(questions=[("fees", body)]), delivery="PENDING")
    result = ask(dialogue, "application", language="en")
    assert_application_answer(result.body)
    assert_fee_not_answered(result)
    continued = dialogue.turn(CONTINUE_EN)
    assert_fee_not_answered(continued)


def test_original_faqs_are_not_processed_or_carried_before_actual_sent_processing_consent(tmp_path):
    journey = ConsentJourney(tmp_path)
    try:
        fee = journey.event(QUESTION["en"]["fees"])
        application = journey.event(QUESTION["en"]["application"])
        journey.process(fee, proposal(questions=[("fees", fee.body)]))
        journey.process(application, proposal(questions=[("application", application.body)]))
        assert journey.model.extracted == journey.model.rendered == journey.reads == []
        assert journey.case().customer_answers == [] and journey.case().unsent_advice == []
        exported = json.dumps(journey.store.export_case_data(journey.case().id), ensure_ascii=False)
        assert fee.body not in exported and application.body not in exported
        assert not journey.ledger.allowed(journey.case())
        journey.pure_grant()  # Existing helper captures the current notice through the real sender.
        assert journey.model.extracted == []
        journey.reopen()
        journey.process(fee)
        journey.process(application)
        journey.dispatch("blocked")
        row = next(row for row in journey.rows(application) if row["message_type"] == "blocked")
        assert row["status"] == "SENT" and row["payload"] == journey.gmail.sent[-1]["body"]
        assert_application_answer(row["payload"])
        assert re.search(r"£\s*\d", row["payload"]), row["payload"]
        assert [item.id for item in journey.model.extracted] == [fee.id, application.id]
    finally:
        journey.store.close()
