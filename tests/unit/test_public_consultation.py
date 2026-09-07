from datetime import date

import pytest

from visa_agent.privacy.public_consultation import public_consultation

TODAY = date(2026, 9, 7)


@pytest.mark.parametrize("body", [
    "您好，我想办理英国旅游签证，需要准备什么材料？",
    "英国旅游签证需要哪些资料？",
    "What documents do I need for a UK tourist visa?",
    "旅游",
])
def test_explicit_holiday_is_not_asked_again(body):
    answer = public_consultation(body, TODAY)
    assert answer and "gov.uk" in answer
    assert "这次主要是旅游" not in answer and "Is the visit mainly" not in answer
    assert "大致行程" in answer or "rough itinerary" in answer


def test_unspecified_visit_still_asks_purpose():
    assert "这次主要是旅游" in public_consultation("英国签证需要哪些资料？", TODAY)


def test_public_followup_respects_link_only_without_intake():
    answer = public_consultation("先给我一个官方申请入口就好，其他的之后再说。", TODAY)
    assert answer and "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" in answer
    assert "办理顺序" not in answer and "授权" not in answer


@pytest.mark.parametrize("body", [
    '朋友问“英国旅游签证需要哪些资料？”',
    "不是旅游，英国签证需要哪些资料？",
    "我叫林晓，英国旅游签证需要哪些资料？",
    "我有附件，英国旅游签证需要哪些资料？",
])
def test_purpose_keyword_does_not_bypass_scope(body):
    assert public_consultation(body, TODAY) is None
