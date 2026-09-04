"""Current reply pacing preferences, not preparation or processing consent.

Pure synthetic text checks only. Workflow/SENT behaviour has separate integration
coverage; this boolean never selects a field or alters a case.
"""

import pytest

from visa_agent.workflow.conversation import consultation_only_requested, current_no_intake_clause

ZH = "先别一直问我个人信息，告诉我英国旅游签证在哪个网页申请、怎么开始。"
EN = ("Please stop asking me for personal details for now. Tell me which webpage to use "
      "to apply for a UK tourist visa and how to get started.")


@pytest.mark.parametrize("body", [
    ZH, EN,
    "先别一直问我个人信息。那下一步怎么开始？",
    "请先不要再问我的个人资料，先解释流程。",
    "暂时不用收集我的个人信息。",
    "Please do not ask me for personal information yet.",
    "Don't keep asking me for personal details.",
    "I only want to understand the application process for now.",
    "我先了解签证准备流程。",
    "I haven't decided whether to apply.",
    "我还没决定要不要申请。",
    "Stop asking me for personal details for now. My date of birth is 12 May 1995.",
    "Don't ask me for personal details now. Which document should I prepare first?",
    "我不是说要暂停准备。先别问我个人信息，先告诉我官网。",
    "If I later ask you to stop asking, respect that. For now, please stop asking me for personal details.",
])
def test_current_information_first_request_suppresses_only_this_reply_intake(body):
    assert consultation_only_requested(body)


@pytest.mark.parametrize("body", [
    "那下一步怎么办？", "What is the next step?",
    "我该先补哪项个人信息？", "Which personal detail should I provide first?",
    "请继续问我个人信息。", "Please ask me for my personal details.",
    "你已经问过我的个人信息了。", "You have already asked for my personal details.",
    "‘先别一直问我个人信息。’", '"Please stop asking me for personal details for now."',
    "> 先别一直问我个人信息。\n谢谢。",
    "My friend said: please stop asking me for personal details for now.",
    "朋友说先别一直问我个人信息。",
    "The example says:\nPlease stop asking me for personal details for now.",
    "如果我还没准备好，先别一直问我个人信息。",
    "If I am not ready, please stop asking me for personal details for now.",
    "If I ask later; please stop asking me for personal details.",
    "假如下周我不方便；先别一直问我个人信息。",
    "明天先别问我个人信息。", "Tomorrow, stop asking me for personal details.",
    "以后，先别问我个人信息。", "Later, please stop asking me for personal details.",
    "以后我会说先别一直问我个人信息。",
    "I will ask you to stop asking me for personal details next week.",
    "不要停止问我个人信息。", "我不是让你别问我个人信息。",
    "Please don't stop asking me for personal details.",
    "I am not asking you to stop asking me for personal details.",
    "先别问她的个人信息。", "Please stop asking my sister for personal details.",
    "如果我只想了解签证流程，我会告诉你。",
    "If I only want to understand the visa process, I will tell you.",
    "我不只是想了解签证流程。", "I do not only want to understand the application process.",
])
def test_noncurrent_declined_or_ordinary_next_step_does_not_suppress_intake(body):
    assert not consultation_only_requested(body)


@pytest.mark.parametrize("body", [
    "先别一直问我个人信息。不过现在请先问我的出生日期。",
    "先解释申请流程，然后请问我护照姓名。",
    "Stop asking me for personal details for now. But please ask me for my date of birth now.",
    "I only want to understand the application process first. Please ask me for my passport name next.",
])
def test_independent_explicit_personal_question_remains_allowed(body):
    assert not consultation_only_requested(body)


@pytest.mark.parametrize("body", [
    "先别问我个人信息。‘请问我出生日期’是旧邮件的内容。",
    "先别问我个人信息。如果以后继续，再问我出生日期。",
    "Stop asking me for personal details for now. 'Please ask me for my date of birth' is an old quote.",
    "Stop asking me for personal details for now. If I continue later, ask me for my date of birth.",
    "先别问我个人信息。稍后请问我出生日期。",
    "Stop asking me for personal details for now. Please ask me for my date of birth later.",
])
def test_quoted_or_conditional_personal_question_does_not_override_current_no_intake(body):
    assert consultation_only_requested(body)


@pytest.mark.parametrize(("body", "expected"), [
    ("先别一直问我个人信息。", True),
    ("Please stop asking me for personal details for now.", True),
    ("For now, please stop asking me for personal details.", True),
    (ZH, False), (EN, False),
    ("先别问我个人信息，另外申请费是多少？", False),
    ("Please stop asking me for personal details; where do I apply?", False),
    ('"Please stop asking me for personal details."', False),
    ("If I need time, please stop asking me for personal details.", False),
    ("Please don't stop asking me for personal details.", False),
    ("Please ask me for my personal details.", False),
])
def test_answer_compiler_may_skip_only_a_complete_current_preference_clause(body, expected):
    assert current_no_intake_clause(body) is expected
