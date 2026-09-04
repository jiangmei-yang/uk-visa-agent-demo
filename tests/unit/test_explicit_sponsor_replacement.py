"""Sponsor replacement signals retire stale identity; they never infer a new one."""

import pytest

from visa_agent.workflow.explicit_answer_scope import explicit_personal_sponsor_replacement


@pytest.mark.parametrize(
    "body",
    [
        "Plans changed: someone else will sponsor my trip instead.",
        "I have switched to a different sponsor.",
        "My sponsor has changed.",
        "计划有变，改由别人承担这次旅费。",
        "我的资助人现在换了其他人。",
    ],
)
def test_explicit_current_sponsor_replacement_is_detected(body: str) -> None:
    assert explicit_personal_sponsor_replacement(body)


@pytest.mark.parametrize(
    "body",
    [
        "Could someone else sponsor my trip instead?",
        "My sponsor has changed?",
        "My sponsor is now someone else?",
        "If someone else sponsors me instead, what should they provide?",
        "My friend said that someone else will sponsor my trip instead.",
        "Maybe I will switch to a different sponsor.",
        "可以改由别人资助吗？",
        "我的资助人现在换了其他人吗？",
        "如果改由别人资助，要什么材料？",
        "我朋友说我的资助人换了其他人。",
        "我可能会改成由其他人资助。",
    ],
)
def test_question_hypothesis_report_or_uncertainty_is_not_a_replacement(body: str) -> None:
    assert not explicit_personal_sponsor_replacement(body)
