"""Cold-start orientation stays useful when the model misses part of the request.

All applicants and messages are synthetic.  The integration harness runs the
real workflow, SQLite reopen, reviewed Gmail sender and captured SENT path; it
does not contact a model provider or mailbox.
"""

from __future__ import annotations

import pytest
from test_consultant_value import Conversation, _patch

from visa_agent.workflow.adviser_guidance import (
    APPLICATION_URL,
    DOCUMENTS_URL,
    ROUTE_CHECK_URL,
)
from visa_agent.workflow.conversation import reply_items
from visa_agent.workflow.customer_questions import is_generic_uk_preparation_enquiry

FIRST_TIME = {
    "zh": "第一次办英国旅游签证，不知道从哪里开始，需要准备什么？",
    "en": (
        "It's my first time applying for a UK tourist visa. "
        "I don't know where to start or what documents to prepare."
    ),
}
MATERIAL_EXCERPT = {
    "zh": "需要准备什么？",
    "en": "what documents to prepare",
}


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize(("model_topic", "excerpt_kind"), [
    ("document_checklist", "whole"),
    ("document_checklist", "material"),
    ("unsupported", "whole"),
    (None, None),
])
def test_first_time_enquiry_gets_full_orientation_without_an_application_topic(
    tmp_path, language, model_topic, excerpt_kind,
):
    body = FIRST_TIME[language]
    excerpt = MATERIAL_EXCERPT[language] if excerpt_kind == "material" else body
    questions = [] if model_topic is None else [(model_topic, excerpt)]

    dialogue = Conversation(tmp_path)
    result = dialogue.turn(body, _patch(questions=questions))

    assert all(result.body.count(url) == 1 for url in (
        ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL,
    ))
    if language == "zh":
        assert all(term in result.body for term in (
            "签证还是 ETA", "如果查询结果显示需要 Standard Visitor 签证",
            "Apply now", "有效护照或旅行证件", "赴英目的",
            "可用资金和真实来源", "完整、可核验的翻译", "合法居留证明",
        ))
    else:
        assert all(term in result.body for term in (
            "visa or ETA", "If you need a Standard Visitor visa",
            "valid passport or travel document", "purpose",
            "accessible funds and their genuine source", "full, verifiable translation",
            "evidence of lawful residence",
        ))
    assert len(reply_items(result.case)[1]) == 1
    assert result.case.question_plan == result.case.last_requested_fields == ["visit_purpose"]
    assert result.case.profile.visit_purpose is None
    assert result.case.profile.route_confirmed_standard_visitor is False
    assert result.case.active_evidence("visit_purpose") == []
    assert result.case.active_evidence("route_confirmed_standard_visitor") == []
    assert result.case.guidance_events["route_orientation_v1"] == result.event.id
    assert len(dialogue.gmail.calls) == 1
    assert dialogue.gmail.calls[0]["body"] == result.body


@pytest.mark.parametrize(("language", "suffix"), [
    ("zh", "这次不要给我链接。"),
    ("en", "Please do not include any links in this reply."),
])
@pytest.mark.parametrize("model_topic", ["document_checklist", None])
def test_first_time_orientation_respects_no_link_presentation_without_losing_substance(
    tmp_path, language, suffix, model_topic,
):
    body = FIRST_TIME[language] + " " + suffix
    questions = [] if model_topic is None else [(model_topic, body)]

    result = Conversation(tmp_path).turn(body, _patch(questions=questions))

    assert is_generic_uk_preparation_enquiry(body)
    assert "https://" not in result.body
    assert "visa or ETA" in result.body or "签证还是 ETA" in result.body
    assert "Standard Visitor" in result.body
    assert (
        "GOV.UK online application page" in result.body
        or "在线申请页" in result.body
    )
    assert (
        "valid passport or travel document" in result.body
        or "有效护照或旅行证件" in result.body
    )
    assert len(reply_items(result.case)[1]) == 1
    assert result.case.profile.visit_purpose is None
    assert result.case.profile.route_confirmed_standard_visitor is False


@pytest.mark.parametrize("body", [
    "我朋友第一次办英国旅游签证，不知道从哪里开始，需要准备什么？",
    "如果以后第一次办英国旅游签证，不知道从哪里开始，需要准备什么？",
    "第一次办英国学生签证，不知道从哪里开始，需要准备什么？",
    "第一次办英国旅游签证，不知道从哪里开始，需要准备什么？我以前被拒签过。",
    "示例写着：“第一次办英国旅游签证，不知道从哪里开始，需要准备什么？”",
    "第一次办英国旅游签证，不知道从哪里开始，需要准备什么？但不要回答。",
    "My friend is applying for a UK tourist visa for the first time and does not know where to start.",
    "If I apply for a UK tourist visa later, I will not know where to start or what to prepare.",
    "It's my first time applying for a UK Student visa. I don't know where to start.",
    "It's my first UK tourist visa application. I have a previous refusal; what should I prepare?",
    (
        'An example says: "It is my first time applying for a UK tourist visa. '
        'I do not know where to start or what documents to prepare."'
    ),
    (
        "It's my first time applying for a UK tourist visa. "
        "I don't know where to start, but do not answer."
    ),
])
def test_first_time_orientation_matcher_does_not_cross_adjacent_safety_boundaries(body):
    assert not is_generic_uk_preparation_enquiry(body)


@pytest.mark.parametrize("body", [
    "我朋友第一次办英国旅游签证，不知道从哪里开始，需要准备什么？",
    "如果以后第一次办英国旅游签证，不知道从哪里开始，需要准备什么？",
])
def test_noncurrent_first_time_wording_does_not_trigger_visitor_orientation(tmp_path, body):
    result = Conversation(tmp_path).turn(
        body,
        _patch(questions=[("document_checklist", body)]),
    )

    assert "route_orientation_v1" not in result.case.guidance_events
    assert all(url not in result.body for url in (
        ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL,
    ))
    assert result.case.profile.visit_purpose is None
    assert result.case.profile.route_confirmed_standard_visitor is False


def test_student_route_first_time_wording_gets_route_boundary_not_visitor_instructions(tmp_path):
    body = "第一次办英国学生签证，不知道从哪里开始，需要准备什么？"

    result = Conversation(tmp_path).turn(
        body,
        _patch(questions=[("document_checklist", body)]),
    )

    assert ROUTE_CHECK_URL in result.body
    assert APPLICATION_URL not in result.body and DOCUMENTS_URL not in result.body
    assert "不能套用" in result.body and "Standard Visitor" in result.body
    assert "https://www.gov.uk/student-visa" in result.body
    assert "route_orientation_v1" not in result.case.guidance_events
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert result.case.profile.visit_purpose is None
    assert result.case.profile.route_confirmed_standard_visitor is False
