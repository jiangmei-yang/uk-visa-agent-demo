from datetime import date

import pytest

from visa_agent.domain.models import Case
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.privacy.public_consultation import public_consultation
from visa_agent.workflow.reception import reception_message


@pytest.mark.parametrize("body", ["你好", "您好！", "你是谁", "请问你是谁？", "您好，你们是做什么的？", "你能帮我什么", "你好\n\nOn Mon, Visa wrote:\n请告诉我出行目的"])
def test_reception_answers_customer_instead_of_starting_intake(body: str) -> None:
    case = Case(id="c", external_thread_id="t", applicant_contact="a@example.test",
                policy_version="v", customer_language="zh", latest_customer_message=body)
    reply = deterministic_fallback_message(case, "blocked")
    assert "您好，这里是英国签证咨询服务" in reply
    assert "您有什么想咨询的？" in reply
    assert "出行目的" not in reply
    assert "护照" not in reply
    assert public_consultation(body, date(2026, 9, 7)) == reply


@pytest.mark.parametrize("body", ["Hi", "Who are you?", "Hello, what can you help me with?"])
def test_english_reception(body: str) -> None:
    assert "What would you like help with?" in (reception_message(body, "en") or "")


@pytest.mark.parametrize("body", ["你好，我想知道申请网址", "你是谁？还有申请费多少钱？", "不是问你是谁，我问材料", "我的生日是1997.7.1", "> 你是谁", "", "？", "Thanks\n\nOn Mon, Visa wrote:\n你是谁"])
def test_reception_does_not_swallow_specific_questions_facts_or_quotes(body: str) -> None:
    assert reception_message(body, "zh") is None


@pytest.mark.parametrize("body", ["你是AI吗", "你是真人吗？", "你是人工客服吗", "你是真人还是机器人？"])
def test_direct_identity_question_is_answered_truthfully(body: str) -> None:
    assert "不是真人客服" in (reception_message(body, "zh") or "")


def test_reception_does_not_change_case_or_bypass_file_review() -> None:
    case = Case(id="c", external_thread_id="t", applicant_contact="a@example.test",
                policy_version="v", customer_language="zh", latest_customer_message="你是谁")
    before = case.model_dump()
    deterministic_fallback_message(case, "awaiting_confirmation")
    assert case.model_dump() == before
    case.latest_document_names = ["passport.pdf"]
    assert "您好，这里是英国签证咨询服务" not in deterministic_fallback_message(case, "blocked")
