"""Reviewed Gmail content remains useful when the applicant declines URLs."""

import pytest
from test_consultant_value import Conversation, _context, _patch


@pytest.mark.parametrize("kind", ["student", "employed", "parents", "family"])
def test_current_no_links_keeps_relevant_action_in_actual_captured_sent_reply(tmp_path, kind):
    body, patch = _context(kind)
    result = Conversation(tmp_path).turn(body + "这次不用给我链接。", patch)
    assert "http" not in result.body and "GOV.UK:" not in result.body
    assert "下面" not in result.body
    if kind == "student":
        assert "在读证明" in result.body and "资金来源" in result.body
    elif kind == "employed":
        assert "公司人事" in result.body and "职位" in result.body
    elif kind == "parents":
        assert "资助人" in result.body and "怎样支付" in result.body
    else:
        assert "邀请" in result.body and "住宿安排" in result.body
    assert result.case.proactive_guidance_offered
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.profile.planned_arrival_date is None
    assert "planned_arrival_date" not in result.case.last_requested_fields


def test_link_decline_does_not_falsely_establish_an_earlier_sent_application_link(tmp_path):
    dialogue = Conversation(tmp_path)
    body, patch = _context("student")
    first = dialogue.turn(body + "这次不用给我链接。", patch)
    assert "http" not in first.body
    question = "请告诉我英国访问签证在哪里申请？"
    later = dialogue.turn(question, _patch(questions=[("application", question)]))
    assert "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" in later.body
    assert "之前的" not in later.body and "again" not in later.body
    assert later.case.last_requested_fields == []
