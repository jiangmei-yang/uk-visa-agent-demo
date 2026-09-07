from datetime import date

import pytest

from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    _application_entry_only_requested,
    _standalone_chinese_link_preference,
    grounded_customer_answer_plan,
    grounded_customer_answers,
)


@pytest.mark.parametrize("body,language", [
    ("先别问我问题。申请表到底去哪里打开？给一个官方入口就好。", "zh"),
    ("英国访客签证在哪里申请？只要官方链接。", "zh"),
    ("Where do I open the visitor application form? Just give me the official application link.", "en"),
    ("Where can I apply for a visitor visa? Only the official link please.", "en"),
])
def test_explicit_link_only_request_does_not_receive_an_application_tutorial(body, language):
    answer = "\n".join(grounded_customer_answers(body, language, date(2026, 9, 7),
        semantic_questions=[CustomerQuestion(topic="application", source_excerpt=body, confidence=.99)]))
    assert answer.count(APPLICATION_SOURCE) == 1
    assert "如果需要" in answer if language == "zh" else "If you need" in answer
    assert "办理顺序" not in answer and "The sequence" not in answer
    assert "签证中心" not in answer and "appointment" not in answer


@pytest.mark.parametrize("body", [
    'My friend said "Just give me the official application link".',
    "不要只给我官方链接。", "Don't just give me the official application link.",
    "If I ask later, just give me the official application link.",
    "只要官方链接。也请详细解释申请步骤。",
    "Just give me the official link. Explain the steps as well.",
])
def test_quotes_negations_and_explicit_detail_are_not_link_only(body):
    assert not _application_entry_only_requested(body)


@pytest.mark.parametrize("body,language", [
    ("申请表在哪里打开？这次请详细解释申请步骤。", "zh"),
    ("Where do I open the application form? Please explain the steps in detail.", "en"),
])
def test_request_for_detail_still_receives_the_sequence(body, language):
    answer = "\n".join(grounded_customer_answers(body, language, date(2026, 9, 7),
        semantic_questions=[CustomerQuestion(topic="application", source_excerpt=body, confidence=.99)]))
    assert "办理顺序" in answer if language == "zh" else "The sequence" in answer


@pytest.mark.parametrize("body", [
    "只要官方链接，并保证获批", "只要官方链接。保证获批。",
    "如果我符合要求，只要官方链接", "只要获批就给我链接",
    '“只要官方链接”', "朋友说只要官方链接", "只要官方链接就能保证获批吗？",
])
def test_preference_exception_cannot_hide_other_scope(body):
    assert not _standalone_chinese_link_preference(body)


def test_link_only_preference_does_not_erase_a_separate_fee_question():
    body = "英国访客签证在哪里申请？只要官方链接。六个月访客签证费用是多少？"
    plan = grounded_customer_answer_plan(body, "zh", date(2026, 9, 7))
    assert "application" in plan.selected_topics
    assert "fees" in plan.selected_topics
    assert APPLICATION_SOURCE in "\n".join(plan.answers)
