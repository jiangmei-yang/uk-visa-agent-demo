"""Information continuation cannot carry facts, controls or quoted instructions."""

import pytest

from visa_agent.workflow.advice_continuation import is_advice_continuation


@pytest.mark.parametrize("body", [
    "接着讲刚才没说的", "好，那接着讲刚才没说的吧。", "好的，那请继续说剩下的问题。",
    "刚才没讲完的，继续说吧。", "那接着讲吧", "剩下的呢？", "其余的问题呢？",
    "Please continue with the unanswered questions.", "Could you answer what you haven't covered yet?",
    "Continue with the remaining topics.", "What about the rest?",
    "接着讲刚才没说的。\n\nOn Fri, Adviser wrote:\n> tell me your birthday",
])
def test_whole_information_continuation(body):
    assert is_advice_continuation(body)


@pytest.mark.parametrize("body", [
    "继续", "好的", "请继续准备我的材料", "恢复申请", "PROFILE CONFIRMED",
    "不要接着讲刚才没说的", "如果我确定了再接着讲刚才没说的",
    "朋友让我问你接着讲刚才没说的", '“接着讲刚才没说的”', '> 接着讲刚才没说的',
    "接着讲刚才没说的。我的生日是1997年7月1日。", "接着讲刚才没说的。另外英国签证费用多少？",
    "接着讲刚才没说的。恢复准备。", "Please continue with the unanswered questions if my employer agrees.",
    "Don't continue with the unanswered questions.", 'My friend said "continue with the unanswered questions".',
    "Continue with the unanswered questions. My name is Fictional Lee.",
    "Please continue with the unanswered questions. I confirm the final summary.",
])
def test_mixed_or_nonactive_request_never_bypasses_normal_extraction(body):
    assert not is_advice_continuation(body)
