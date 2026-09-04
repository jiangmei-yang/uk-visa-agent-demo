"""Ordinary consecutive emails must not lose advice in a coalesced Gmail reply.

Real Gmail runner, guard, SQLite and automatic reviewed sender; only provider
I/O and source-grounded extraction proposals are synthetic. No customer test
markers or special continuation phrase is needed to receive an unanswered FAQ.
"""

from __future__ import annotations

import re

import pytest
from test_gmail_processing_consent import SENDER
from test_gmail_processing_consent import harness as harness

from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate
from visa_agent.privacy.consent import ConsentLedger

APPLICATION = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
DOCUMENTS = ("https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/"
             "guide-to-supporting-documents-visiting-the-uk")
QUESTIONS = {
    "en": [
        ("application", "Where do I apply for my UK visitor visa?"),
        ("translation", "How should I translate my Chinese supporting documents?"),
    ],
    "zh": [("application", "英国访问签证在哪里申请？"), ("translation", "中文的证明材料要怎么翻译？")],
}
FACTS = {
    "en": [("full_name", "Alex Chen", "My full name is Alex Chen."),
           ("date_of_birth", "1993-08-19", "My date of birth is 19 August 1993.")],
    "zh": [("full_name", "陈伟", "我的姓名是陈伟。"),
           ("date_of_birth", "1993-08-19", "我的出生日期是1993年8月19日。")],
}
LINK_REQUESTS = {"en": "Send me that link again.", "zh": "网址发我一下。"}
EXTRA_QUESTIONS = {
    "en": [("timing", "How early can I apply for a UK visitor visa?"),
           ("booking", "Do I need to buy flights before applying?")],
    "zh": [("timing", "英国访问签证最早什么时候能申请？"), ("booking", "申请前需要先买机票吗？")],
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Batched consultation regressions cannot access a network")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def conversation(harness, monkeypatch):
    class Model:
        def extract_case_patch(self, event):
            harness.extracted.append((event.id, event.body, event.received_at))
            questions = [CustomerQuestion(topic=topic, source_excerpt=question, confidence=1)
                         for entries in [*QUESTIONS.values(), *EXTRA_QUESTIONS.values(),
                             [("application", question) for question in LINK_REQUESTS.values()]]
                         for topic, question in entries
                         if question in event.body]
            facts = [FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=1)
                     for entries in FACTS.values() for field, value, excerpt in entries
                     if excerpt in event.body]
            return CasePatch(updates=facts, ambiguities=[], customer_questions=questions)

        render_message = staticmethod(deterministic_fallback_message)

    monkeypatch.setattr(harness.runner, "DeepSeekStructuredLLM", lambda *args, **kwargs: Model())
    return harness


def _activate(conversation):
    conversation.add("greeting", "Hello.")
    conversation.run()
    notice = conversation.sent[-1]
    reference = re.search(r"PC-[A-F0-9]{12}", notice["body"]).group()
    conversation.add("processing-grant", "I consent to the processing described in this notice "
                     f"(consent reference {reference}).", references=notice["message_id"])
    conversation.run()
    conversation.run()
    assert [item[0] for item in conversation.extracted] == ["greeting"]
    store = conversation.open_store()
    try:
        assert ConsentLedger(store).allowed(store.list_cases()[0])
        notice_row = next(row for row in store.list_outbox() if row["message_type"] == "processing_notice")
        assert notice_row["status"] == "SENT"
    finally:
        store.close()


def _sent_business(conversation, event_id):
    store = conversation.open_store()
    try:
        row = next(row for row in store.list_outbox()
                   if row["event_id"] == event_id and row["message_type"] == "blocked")
        assert row["status"] == "SENT", row
        sent = next(payload for index, payload in enumerate(conversation.sent, 1)
                    if row["provider_message_id"] == f"capture-{index}")
        assert row["payload"] == sent["body"] and sent["recipient"] == SENDER
        assert sent.get("attachment") is None
        assert row["reply_render_mode"] == "reviewed"
        return sent["body"]
    finally:
        store.close()


def _both_answers(body, language):
    assert APPLICATION in body, "The earlier application answer was not sent:\n" + body
    assert DOCUMENTS in body, "The translation answer was not sent:\n" + body
    for required in (("完整翻译", "翻译日期", "联系方式") if language == "zh"
                     else ("full translation", "translation date", "contact details")):
        assert required.casefold() in body.casefold(), "Missing substantive translation advice:\n" + body


@pytest.mark.parametrize("language", ["en", "zh"])
def test_two_ordinary_faq_emails_in_one_cycle_both_reach_latest_actual_sent_reply(conversation, language):
    _activate(conversation)
    conversation.add("application-question", QUESTIONS[language][0][1])
    conversation.add("translation-question", QUESTIONS[language][1][1])
    before = len(conversation.sent)
    conversation.run()
    assert len(conversation.sent) == before + 1
    assert [item[0] for item in conversation.extracted][-2:] == ["application-question", "translation-question"]
    _both_answers(_sent_business(conversation, "translation-question"), language)
    store = conversation.open_store()
    try:
        old = next(row for row in store.list_outbox() if row["event_id"] == "application-question")
        assert old["status"] == "FAILED" and old["attempt_count"] == 0
    finally:
        store.close()
    snapshot = (list(conversation.extracted), list(conversation.raw_reads), len(conversation.sent))
    conversation.run()
    assert (conversation.extracted, conversation.raw_reads, len(conversation.sent)) == snapshot


@pytest.mark.parametrize("language", ["en", "zh"])
def test_pending_faqs_survive_restart_and_a_plain_fact_without_repeating_them_after_sent(conversation, language):
    _activate(conversation)
    conversation.args.action = "prepare"
    conversation.add("application-question", QUESTIONS[language][0][1])
    conversation.add("translation-question", QUESTIONS[language][1][1])
    before = len(conversation.sent)
    conversation.run()
    assert len(conversation.sent) == before  # Actual prepared drafts, not a claimed send.
    conversation.args.action = "serve"
    conversation.add("name-update", FACTS[language][0][2])
    conversation.run()  # A new runner/store is constructed by the shared harness.
    _both_answers(_sent_business(conversation, "name-update"), language)
    store = conversation.open_store()
    try:
        assert store.list_cases()[0].profile.full_name == FACTS[language][0][1]
        assert all(store.event_processed(identifier) for identifier in
                   ["application-question", "translation-question", "name-update"])
    finally:
        store.close()
    conversation.add("birthday-update", FACTS[language][1][2])
    conversation.run()
    later = _sent_business(conversation, "birthday-update")
    assert APPLICATION not in later and DOCUMENTS not in later, "Already SENT answers were repeated:\n" + later


@pytest.mark.parametrize("language", ["en", "zh"])
def test_an_earlier_actually_sent_answer_is_not_repeated_with_a_different_new_faq(conversation, language):
    _activate(conversation)
    conversation.add("application-question", QUESTIONS[language][0][1])
    conversation.run()
    assert APPLICATION in _sent_business(conversation, "application-question")
    conversation.add("translation-question", QUESTIONS[language][1][1])
    conversation.run()
    latest = _sent_business(conversation, "translation-question")
    assert DOCUMENTS in latest and APPLICATION not in latest


@pytest.mark.parametrize("language", ["en", "zh"])
def test_unsent_short_link_reply_keeps_its_original_actually_sent_application_context(conversation, language):
    _activate(conversation)
    conversation.add("application-question", QUESTIONS[language][0][1])
    conversation.run()
    assert APPLICATION in _sent_business(conversation, "application-question")
    conversation.args.action = "prepare"
    conversation.add("link-request", LINK_REQUESTS[language])
    conversation.run()
    store = conversation.open_store()
    try:
        case = store.list_cases()[0]
        item = next(item for item in case.unsent_advice if item.source_event_id == "link-request")
        assert item.source_application_guidance_event_id == "application-question"
        context = next(row for row in store.list_outbox() if row["event_id"] == "application-question")
        assert context["status"] == "SENT" and APPLICATION in context["payload"]
        link = next(row for row in store.list_outbox() if row["event_id"] == "link-request")
        assert link["status"] == "PENDING"
    finally:
        store.close()
    conversation.args.action = "serve"
    conversation.add("translation-question", QUESTIONS[language][1][1])
    conversation.run()
    latest = _sent_business(conversation, "translation-question")
    _both_answers(latest, language)
    assert ("这是之前的 GOV.UK 申请入口" if language == "zh" else
            "Here's the GOV.UK application link again") in latest
    assert ("流程是出发前在线填写申请" if language == "zh" else
            "Apply online before travelling") not in latest  # Keep the requested short link, not a fresh full explainer.
    assert [value[1] for value in conversation.extracted][-2:] == [
        LINK_REQUESTS[language], QUESTIONS[language][1][1]]
    conversation.add("name-update", FACTS[language][0][2])
    conversation.run()
    later = _sent_business(conversation, "name-update")
    assert "application link again" not in later and "这是之前的 GOV.UK 申请入口" not in later


@pytest.mark.parametrize("language", ["en", "zh"])
def test_a_short_link_without_prior_sent_context_cannot_claim_a_previous_delivered_link(conversation, language):
    _activate(conversation)
    conversation.args.action = "prepare"
    conversation.add("link-request", LINK_REQUESTS[language])
    conversation.run()
    store = conversation.open_store()
    try:
        items = [item for item in store.list_cases()[0].unsent_advice if item.source_event_id == "link-request"]
        assert all(item.source_application_guidance_event_id is None for item in items)
    finally:
        store.close()
    conversation.args.action = "serve"
    conversation.add("translation-question", QUESTIONS[language][1][1])
    conversation.run()
    latest = _sent_business(conversation, "translation-question")
    assert DOCUMENTS in latest
    assert "application link again" not in latest and "这是之前的 GOV.UK 申请入口" not in latest


@pytest.mark.parametrize("language", ["en", "zh"])
def test_more_than_three_batched_topics_remain_answerable_by_an_ordinary_followup(conversation, language):
    _activate(conversation)
    questions = QUESTIONS[language] + EXTRA_QUESTIONS[language]
    for topic, question in questions:
        conversation.add("batch-" + topic, question)
    before = len(conversation.sent)
    conversation.run()
    assert len(conversation.sent) == before + 1
    first = _sent_business(conversation, "batch-booking")
    markers = ({"application": "Apply now", "translation": "完整翻译",
                "timing": "出发前 3 个月", "booking": "不需要为了提供这些预订证明而先购买"} if language == "zh" else
               {"application": "Apply now", "translation": "full translation",
                "timing": "3 months before travel", "booking": "do not need to buy flights"})
    covered = {topic for topic, marker in markers.items() if marker in first}
    assert len(covered) == 3, first
    assert ("还没有展开" if language == "zh" else "I have not covered") in first
    remaining = set(markers) - covered
    count = len(conversation.extracted)
    conversation.add("remaining-question", "剩下的呢？" if language == "zh" else "What about the rest?")
    conversation.run()
    second = _sent_business(conversation, "remaining-question")
    assert len(conversation.extracted) == count  # No re-extraction of any old source body.
    assert all(markers[topic] in second for topic in remaining), second
    assert all(markers[topic] not in second for topic in covered), second
    assert "GOV.UK:" in second and (APPLICATION in second or DOCUMENTS in second)
    conversation.add("name-update", FACTS[language][0][2])
    conversation.run()
    later = _sent_business(conversation, "name-update")
    assert all(marker not in later for marker in markers.values()), later
    assert conversation.extracted[-1][1] == FACTS[language][0][2]
