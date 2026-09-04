from datetime import date

import pytest

from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    SOURCE,
    grounded_customer_answers,
)

TODAY = date(2026, 9, 4)


def answer(body, language="zh", topic="unsupported", *, today=TODAY):
    return "\n\n".join(grounded_customer_answers(body, language, today, semantic_questions=[
        CustomerQuestion(topic=topic, source_excerpt=body, confidence=.99),
    ]))


@pytest.mark.parametrize(("body", "language", "parts"), [
    ("在职证明是为了说明我有收入，还是也要说明我的工作和假期安排？", "zh",
     ["收入", "职位", "任职", "联系方式", "假期", "不"]),
    ("What is the purpose of an employer letter: earnings, my job or leave arrangements?", "en",
     ["income", "role", "employment", "contact", "leave", "not"]),
])
def test_employment_evidence_is_not_mistaken_for_working_in_uk(body, language, parts):
    text = answer(body, language)
    assert all(part in text for part in parts)
    assert SOURCE in text
    assert "关于在英国工作" not in text and "On working in the UK" not in text
    assert "没有核验过的依据" not in text and "don't currently have verified guidance" not in text


@pytest.mark.parametrize(("body", "language", "parts"), [
    ("请解释一下旅行行程概要有什么作用？我说的是活动计划，不是订票。", "zh",
     ["计划", "活动", "住宿", "预算", "预订", "不"]),
    ("Could you explain the purpose of a short travel itinerary? I mean a summary of what I plan to do, not bookings.", "en",
     ["planned", "activities", "accommodation", "budget", "book", "not"]),
])
def test_itinerary_purpose_is_explained_without_inventing_a_required_format(body, language, parts):
    text = answer(body, language)
    assert all(part in text for part in parts)
    assert APPLICATION_SOURCE in text
    assert "没有核验过的依据" not in text and "don't currently have verified guidance" not in text


@pytest.mark.parametrize(("body", "language"), [
    ("我的在职证明够不够，一定能过吗？", "zh"),
    ("Will this employer letter guarantee that my visa is approved?", "en"),
    ("能帮我编造一份在职证明说明工作收入吗？", "zh"),
    ("Can you invent an employer letter to show a salary I do not earn?", "en"),
    ("我的工作签证在职证明有什么作用？", "zh"),
    ("What is the purpose of an employer letter for a work visa?", "en"),
])
def test_purpose_help_does_not_claim_to_resolve_sufficiency_fabrication_or_other_routes(body, language):
    text = answer(body, language)
    assert "没有核验过的依据" in text or "don't currently have verified guidance" in text


@pytest.mark.parametrize(("body", "language"), [
    ("在职证明有什么作用？", "zh"),
    ("What is the purpose of a travel itinerary?", "en"),
])
def test_document_purpose_respects_source_expiry(body, language):
    text = answer(body, language, today=date(2026, 11, 4))
    assert "没有核验过的依据" in text or "don't currently have verified guidance" in text


@pytest.mark.parametrize(("body", "language"), [
    ("请问可以在英国兼职工作吗？", "zh"),
    ("Can I work part-time in the UK as a Standard Visitor?", "en"),
])
def test_real_uk_work_question_keeps_reviewed_boundary(body, language):
    text = answer(body, language)
    assert "关于在英国工作" in text or "On working in the UK" in text


@pytest.mark.parametrize("body", [
    "这里是我的在职证明。", "不用解释在职证明有什么作用。",
    "收到\n> 在职证明有什么作用？",
    "Here is my employer letter.", "Do not explain the purpose of an employer letter.",
])
def test_non_current_purpose_mentions_do_not_get_an_answer(body):
    assert grounded_customer_answers(body, "zh", TODAY) == []


@pytest.mark.parametrize(("body", "language"), [
    ("银行流水和余额证明分别有什么用途？", "zh"),
    ("What is the difference between a bank statement and a balance certificate?", "en"),
])
def test_bank_comparison_answers_both_sides(body, language):
    text = answer(body, language, "bank_period")
    assert ("余额证明" if language == "zh" else "balance certificate") in text
    assert ("某个时点" if language == "zh" else "point in time") in text
    assert ("进出" if language == "zh" else "transactions") in text
    assert SOURCE in text


@pytest.mark.parametrize("topic", ["unsupported", "document_checklist"])
def test_narrow_document_purpose_survives_either_existing_model_label(topic):
    text = answer("在职证明有什么作用？", topic=topic)
    assert "任职时间" in text and "联系方式" in text
    assert "关于在英国工作" not in text


def test_independent_document_and_uk_work_questions_are_both_answered():
    document = "在职证明有什么作用？"
    work = "我能在英国做兼职吗？"
    text = "\n\n".join(grounded_customer_answers(document + "\n" + work, "zh", TODAY,
        semantic_questions=[CustomerQuestion(topic="unsupported", source_excerpt=part, confidence=.99)
                            for part in (document, work)]))
    assert "任职时间" in text and "关于在英国工作" in text


def test_off_topic_scope_still_wins_over_document_purpose():
    body = "What is the purpose of an employer letter for my mortgage application?"
    text = answer(body, "en", "off_topic")
    assert "outside UK visa preparation" in text
    assert "company-headed" not in text and "http" not in text


def test_document_advice_does_not_add_links_when_customer_declines_them():
    question = "在职证明有什么作用？"
    text = "\n\n".join(grounded_customer_answers(question + "不用发链接。", "zh", TODAY,
        semantic_questions=[CustomerQuestion(topic="unsupported", source_excerpt=question, confidence=.99)]))
    assert "任职时间" in text and "http" not in text


@pytest.mark.parametrize(("body", "language", "required"), [
    ("What is an employer's letter intended to show in a visitor application?", "en", "income"),
    ("行程说明主要是解释旅行目的和安排吗？它和已经购买的机票不是一回事吧？", "zh", "还没有预订"),
    ("请解释一下雇主信。", "zh", "任职时间"),
])
def test_exposed_purpose_paraphrases_are_not_an_unseen_success(body, language, required):
    text = answer(body, language)
    assert required in text
    assert "没有核验过的依据" not in text and "don't currently have verified guidance" not in text


def test_unknown_answer_does_not_imply_permission_to_continue_preparation():
    for body, language in (("以前拒签会影响这次申请吗？", "zh"),
                           ("How would an earlier refusal affect this application?", "en")):
        text = answer(body, language)
        assert "已确定的信息可以继续整理" not in text
        assert "we can still organise" not in text


def test_one_excerpt_keeps_work_boundary_alongside_document_purpose():
    text = answer("What is the purpose of an employer letter, and can I work in the UK as a visitor?", "en")
    assert "current employment" in text and "On working in the UK" in text
    assert "cannot reliably confirm" in text


def test_specific_medical_itinerary_is_not_treated_as_ordinary_holiday_planning():
    text = answer("What is the purpose of an itinerary for my UK medical treatment?", "en")
    assert "Medical visits have specific requirements" in text
    assert "medical" in text and "GOV.UK:" in text
    assert "a separate itinerary is an organising suggestion" not in text
