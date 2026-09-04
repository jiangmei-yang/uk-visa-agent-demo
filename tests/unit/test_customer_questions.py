from datetime import date

import pytest

from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    SOURCE,
    grounded_customer_answers,
)


def test_booking_answer_has_reviewed_source_and_transit_exception() -> None:
    answers = grounded_customer_answers("我必须先买机票、订酒店吗？", "zh", date(2026, 9, 4))
    assert len(answers) == 1
    assert SOURCE in answers[0]
    assert "过境除外" in answers[0]
    assert "尚未确定" in answers[0]


def test_stale_advice_and_transit_are_not_given_standard_visitor_answer() -> None:
    assert "复核" in grounded_customer_answers("必须买机票吗？", "zh", date(2026, 11, 1))[0]
    assert "路线" in grounded_customer_answers("我过境需要买机票吗？", "zh", date(2026, 9, 4))[0]
    assert grounded_customer_answers("Here is my student letter.", "en", date(2026, 9, 4)) == []


def test_unbooked_hotel_mention_is_not_a_booking_policy_question() -> None:
    assert grounded_customer_answers(
        "现在还没订酒店，也没把材料都准备好，可以先聊一下该怎么准备吗？", "zh", date(2026, 9, 4)
    ) == []
    assert grounded_customer_answers(
        "I haven't booked a hotel yet. Where do I start?", "en", date(2026, 9, 4)
    ) == []
    assert grounded_customer_answers(
        "我没买机票、没订酒店，必须先买吗？", "zh", date(2026, 9, 4)
    )


@pytest.mark.parametrize(("body", "language"), [
    ("英国签证在哪申请？申请官网发我一下，可以说说办理流程吗？", "zh"),
    ("请给我签证申请官网链接", "zh"),
    ("How do I apply for a visitor visa? Please send the official website.", "en"),
    ("What is the visa application process?", "en"),
])
def test_application_question_links_to_official_entry_with_conditional_route(body, language) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert APPLICATION_SOURCE in answer
    assert "Apply now" in answer
    if language == "zh":
        assert all(part in answer for part in ["如果", "在线", "签证中心", "保存", "不代表"])
    else:
        assert all(part in answer for part in ["If you need", "online", "visa application centre", "save", "does not yet confirm"])


@pytest.mark.parametrize(("body", "language"), [
    ("最早什么时候申请，签证通常多久下来？", "zh"),
    ("多久能出结果？", "zh"),
    ("When can I apply? How long does a visa decision take?", "en"),
    ("How early can I apply for my visa?", "en"),
])
def test_timing_question_gives_full_start_condition_not_promise(body, language) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert APPLICATION_SOURCE in answer
    if language == "zh":
        assert all(part in answer for part in ["3 个月", "3 周", "在线申请", "身份核验", "材料提交", "不保证"])
    else:
        assert all(part in answer for part in ["3 months", "3 weeks", "applied online", "proved your identity", "provided your documents", "not a guaranteed"])


@pytest.mark.parametrize(("body", "language"), [
    ("中文材料要翻译吗？译文需要什么？", "zh"),
    ("翻译有什么要求？", "zh"),
    ("Do my Chinese documents need translation? What must the translation contain?", "en"),
])
def test_translation_question_covers_complete_reviewed_requirements(body, language) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert SOURCE in answer
    if language == "zh":
        assert all(part in answer for part in ["英语或威尔士语", "完整翻译", "独立核验", "准确性声明", "日期", "全名和签名", "联系方式"])
    else:
        assert all(part in answer for part in ["English or Welsh", "full translation", "independently verify", "accuracy", "date", "name and signature", "contact"])


@pytest.mark.parametrize("body", [
    "我已经看过官网和申请流程，中文材料也翻译好了。",
    "我不想知道怎么申请。",
    "不用发申请官网链接。",
    "无需申请链接，先不用解释办理流程。",
    "Don't send me the official website. Don't explain the application process.",
    "I have read the official website and translated my documents.",
    "不用告诉我机票必须买吗？",
    "My friend asked ‘how to apply’ but I have no question.",
    "收到\n> 英国签证如何申请？\n> 中文材料需要翻译吗？",
    "谢谢\nOn Friday, someone wrote:\nHow do I apply?",
    "你之前说“申请官网在哪？”，我现在不问这个。",
    "存款要多少才保证过签？",
])
def test_declined_quoted_unknown_or_plain_mentions_do_not_trigger(body) -> None:
    assert grounded_customer_answers(body, "zh", date(2026, 9, 4)) == []


@pytest.mark.parametrize("today", [date(2026, 9, 3), date(2026, 10, 5)])
@pytest.mark.parametrize("body", ["怎么申请签证？", "签证多久出结果？", "中文材料需要翻译吗？"])
def test_all_new_guidance_is_withheld_outside_review_window(body, today) -> None:
    answers = grounded_customer_answers(body, "zh", today)
    assert len(answers) == 1
    assert "复核" in answers[0]
    assert "3 个月" not in answers[0] and "完整翻译" not in answers[0]


def test_multi_question_answer_cap_dedup_and_link_preference() -> None:
    answers = grounded_customer_answers(
        "必须先买机票吗？怎么申请签证？申请官网在哪里？签证多久出结果？中文材料需要翻译吗？",
        "zh", date(2026, 10, 4),
    )
    assert len(answers) == 3
    assert len(set(answers)) == 3
    without_links = grounded_customer_answers(
        "不用发链接，告诉我申请流程可以吗？材料需要翻译吗？", "zh", date(2026, 9, 4),
    )
    assert len(without_links) == 2
    assert not any("https://" in answer for answer in without_links)


@pytest.mark.parametrize("body", [
    "我办学生签证，怎么申请？多久出签？",
    "How do I apply for a transit visa? How long does a decision take?",
])
def test_explicit_other_route_does_not_receive_standard_visitor_timing(body) -> None:
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4))
    assert len(answers) == 1
    assert "路线" in answers[0]
    assert "3 周" not in answers[0]


@pytest.mark.parametrize(("body", "language"), [
    ("不用官网链接了。签证多久出结果？", "zh"),
    ("I don't need links. How long does a visa decision take?", "en"),
    ("No need for the website. How long does a visa decision take?", "en"),
    ("No links please. How long does a visa decision take?", "en"),
])
def test_declining_links_preserves_other_explicit_answer_without_links(body, language) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    assert "3" in answers[0]
    assert "https://" not in answers[0]


def test_quoted_or_declined_booking_does_not_leak_into_another_answer() -> None:
    for body in [
        'My friend asked “must I buy flights?” How do I apply?',
        "Don't explain whether I need to buy flights. Where do I apply?",
    ]:
        answers = grounded_customer_answers(body, "en", date(2026, 9, 4))
        assert len(answers) == 1
        assert APPLICATION_SOURCE in answers[0]
        assert "buy flights" not in answers[0]


def test_quoted_route_does_not_override_current_application_question() -> None:
    answers = grounded_customer_answers(
        'The old email says “student visa”. How do I apply?', "en", date(2026, 9, 4)
    )
    assert len(answers) == 1
    assert APPLICATION_SOURCE in answers[0]
