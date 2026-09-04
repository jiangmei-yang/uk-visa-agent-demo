"""Special-duration visitor fees stay scoped through workflow and captured Gmail."""

from datetime import date

import pytest
from test_advice_continuation import Conversation, proposal

from visa_agent.workflow.customer_questions import ACADEMIC_SOURCE, MEDICAL_SOURCE


@pytest.mark.parametrize(("body", "excerpt", "source", "wording"), [
    (
        "I need private medical treatment in the UK for 11 months. What is the visa fee?",
        "What is the visa fee?",
        MEDICAL_SOURCE,
        "up to 11 months",
    ),
    (
        "我是研究人员，计划进行12个月的学术访问。申请费是多少？",
        "申请费是多少？",
        ACADEMIC_SOURCE,
        "最长 12 个月",
    ),
])
def test_special_duration_fee_is_the_reviewed_sent_answer(
    tmp_path,
    body: str,
    excerpt: str,
    source: str,
    wording: str,
) -> None:
    dialogue = Conversation(tmp_path)

    result = dialogue.turn(
        body,
        proposal(questions=[("fees", excerpt)]),
        today=date(2026, 9, 5),
    )

    assert result.row["status"] == "SENT"
    assert result.row["reply_render_mode"] == "reviewed"
    assert dialogue.gmail.delivered[-1]["body"] == result.body
    assert all(term in result.body for term in ("£234", wording, source))
    assert "£135" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert not result.case.profile_confirmed
    assert not result.case.final_summary_confirmed
    assert result.case.delivery_path is None


def test_academic_conference_duration_cannot_borrow_the_academic_fee_row(tmp_path) -> None:
    body = "I will attend an academic conference for 12 months. What is the visitor visa fee?"
    dialogue = Conversation(tmp_path)

    result = dialogue.turn(
        body,
        proposal(questions=[("fees", "What is the visitor visa fee?")]),
        today=date(2026, 9, 5),
    )

    assert result.row["status"] == "SENT"
    assert "£135" not in result.body
    assert "£234" not in result.body
    assert ACADEMIC_SOURCE not in result.body
    assert "longer than the ordinary 6-month period" in result.body
