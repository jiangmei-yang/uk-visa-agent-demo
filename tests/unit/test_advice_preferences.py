"""Synthetic current advice preferences, not preparation or consent controls."""

import pytest

from visa_agent.workflow.advice_preferences import (
    defer_previous_advice,
    excluded_advice_topics,
    route_change_pending,
    wants_no_links,
)


@pytest.mark.parametrize("body,topics", [
    ("Please do not answer the fee question.", {"fees"}),
    ("Skip the application steps, please.", {"application"}),
    ("No need to explain processing times.", {"timing"}),
    ("I am not asking about translation.", {"translation"}),
    ("Please leave out the hotel booking question.", {"booking"}),
    ("Don't explain the bank statement requirements.", {"bank_period"}),
    ("费用先不用说了。", {"fees"}),
    ("请不要回答银行流水和翻译的问题。", {"bank_period", "translation"}),
    ("先别讲申请流程和审理时间。", {"application", "timing"}),
    ("不用再解释机票预订了。", {"booking"}),
    ("Do not explain fees, but please explain translations.", {"fees"}),
    ("Please explain translations, not fees.", {"fees"}),
    ("Please do not answer the application fee question.", {"fees"}),
    ("I'm not asking about translation.", {"translation"}),
    ("Please do not answer the fee question. Please continue with the unanswered questions.", {"fees"}),
])
def test_explicit_current_topic_exclusions(body, topics):
    assert excluded_advice_topics(body) == topics


@pytest.mark.parametrize("body", [
    "What is the visa fee?", "Don't stop answering the fee question.",
    "Please explain why I do not need to buy flight tickets.",
    "I cannot obtain my bank statements.", "Please do not upload my bank statement yet.",
    "No links, please. Where do I apply?", "Do not ask about my date of birth yet.",
    "不要重复问我的生日。", "我没有翻译文件。", "请解释为什么不用先买机票。",
])
def test_topic_mentions_or_other_actions_do_not_cancel_advice(body):
    assert excluded_advice_topics(body) == set()


@pytest.mark.parametrize("body", [
    "No links, please.", "Please do not send any links.",
    "Please answer without links.", "I don't need URLs.",
    "不要发链接。", "请解释就好，不需要网址。",
    "这次不用给我链接。", "这个回复不用给我链接。",
    "Please do not include any links in this reply.",
])
def test_current_no_links_preference(body):
    assert wants_no_links(body)
    assert excluded_advice_topics(body) == set()


@pytest.mark.parametrize("body", [
    "Please send the official link.", "Do not omit the links.",
    "My letter does not include links.", "Do I need links in the translation?",
    "官网链接在哪里？", "不要只发链接，请也解释要求。",
    'My friend wrote "No links, please." Please send the official link.',
    "朋友写了‘不要发链接’，但我想要官网入口。",
    'My sister wrote "Please do not include any links in this reply."',
    "朋友说‘这次不用给我链接。’",
    "If I am in a hurry, please do not include any links in this reply.",
    "如果我赶时间，这次不用给我链接。",
])
def test_no_links_is_not_inferred_from_a_fact_question_or_request_for_explanation(body):
    assert not wants_no_links(body)


@pytest.mark.parametrize("body", [
    "Please don't answer the earlier questions yet.",
    "Put the previous questions aside for now.",
    "Leave the remaining questions for later.",
    "之前的问题先放一放。", "先不用回答刚才那些问题。", "剩下的问题暂时不讲了。",
    "之前的问题都不用回答了。",
])
def test_explicit_defer_previous_questions(body):
    assert defer_previous_advice(body)


@pytest.mark.parametrize("body", [
    "Please continue answering the previous questions.",
    "Do not stop answering the previous questions.",
    "Please pause my visa preparation.", "My travel dates are not decided.",
    "I'll send the bank statement later.", "日期确定后再告诉你。", "先暂停准备申请。",
])
def test_preparation_pause_or_deferred_facts_are_not_deferred_consultation(body):
    assert not defer_previous_advice(body)


@pytest.mark.parametrize("body", [
    "I have switched from Standard Visitor to a Student visa.",
    "I am applying for a Student visa instead.",
    "I am no longer applying for a visitor visa.",
    "I am changing my visa route.",
    "我现在改申学生签证了。", "我不再申请访问签证。", "这次不办旅游签证了，改办工作签证。",
])
def test_explicit_current_applicant_route_change(body):
    assert route_change_pending(body)


@pytest.mark.parametrize("body", [
    "What is the Student visa fee as a separate question?",
    "I am a student applying for a visitor visa.",
    "Could I switch to a Student visa?", "I have not switched my visa route.",
    "My travel plans have changed.", "I switched visa routes last year.",
    "另外问一下学生签证多少钱？", "我没有改申请路线。", "访问签证能改成学生签证吗？",
])
def test_other_route_questions_or_old_uncertain_changes_do_not_change_current_scope(body):
    assert not route_change_pending(body)


COMMANDS = (
    "Please do not answer fees. No links, please. "
    "Do not answer the earlier questions. I am changing my visa route."
)
ZH_COMMANDS = "请不要回答费用。不要发链接。之前的问题先放一放。我现在改申学生签证了。"


@pytest.mark.parametrize("body", [
    f'"{COMMANDS}"', f"“{ZH_COMMANDS}”", f"> {COMMANDS}",
    "Thanks.\nOn Monday Example wrote:\n" + COMMANDS,
    "My sister wrote the following:\n" + COMMANDS,
    "模板内容如下：\n" + ZH_COMMANDS,
    "If I change my plans, please do not answer fees; no links; "
    "do not answer the earlier questions; I am changing my visa route.",
    "如果计划有变，请不要回答费用；不要发链接；之前的问题先放一放；我现在改申学生签证了。",
    "Tomorrow, please do not answer fees; no links; do not answer the earlier questions; I am changing my visa route.",
    "明天，请不要回答费用；不要发链接；之前的问题先放一放；我现在改申学生签证了。",
])
def test_quoted_reported_and_conditional_controls_are_not_current_preferences(body):
    assert excluded_advice_topics(body) == set()
    assert not wants_no_links(body)
    assert not defer_previous_advice(body)
    assert not route_change_pending(body)


@pytest.mark.parametrize("body", [
    "If my trip changes, I will ask again. Please do not answer fees now.",
    "如果行程有变化，我会再问。现在先别讲费用。",
])
def test_independent_current_instruction_after_a_complete_conditional_sentence(body):
    assert excluded_advice_topics(body) == {"fees"}


def test_old_quoted_facts_never_enter_the_preference_result():
    body = "Please do not answer fees.\n> My date of birth is 1990-01-01.\n> PROFILE CONFIRMED"
    assert excluded_advice_topics(body) == {"fees"}
    assert not route_change_pending(body)
    assert not defer_previous_advice(body)
