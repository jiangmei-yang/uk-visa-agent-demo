import pytest

from visa_agent.workflow.conversation import clear_natural_confirmation


FULL = "我已逐项核对这封邮件里的资料摘要，姓名、生日、出行日期、住址、收入、资金来源和旅行记录都准确，没有遗漏或更改。请继续整理材料。"


def test_actual_gmail_review_confirmation_is_recognized():
    assert clear_natural_confirmation(FULL)
    assert clear_natural_confirmation(FULL + "\nOn Monday someone wrote:\n> 不是确认")


@pytest.mark.parametrize("body", [
    FULL.replace("都准确", "都不准确"),
    FULL.replace("我已逐项", "如果我已逐项"),
    FULL.replace("我已逐项", "我朋友已逐项"),
    FULL.replace("我已逐项", "我还没逐项"),
    FULL + "但是生日需要更正。",
    FULL + "？",
    FULL.replace("都准确", "可能都准确"),
    FULL.replace("没有遗漏或更改", "有遗漏"),
])
def test_long_summary_confirmation_does_not_drop_caveats(body):
    assert not clear_natural_confirmation(body)
