"""A presentation preference must not erase practical preparation guidance."""

from datetime import date

import pytest

from visa_agent.domain.models import Case
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, preparation_guidance


def example(body: str, language: str) -> Case:
    case = Case(id="format", external_thread_id="format", applicant_contact="fictional@example.test",
                customer_language=language, policy_version="test")
    case.latest_customer_message = body
    case.profile.visit_purpose = "tourism"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.latest_received_facts = {"occupation_status": "student", "funding_source": "self"}
    return case


@pytest.mark.parametrize(("body", "language"), [
    ("这次不用给我链接。我现在读书，旅行自费，想准备材料。", "zh"),
    ("先不用链接，我现在想准备材料。", "zh"),
    ("Don't send links; I want to prepare my documents.", "en"),
    ("Please explain without links. I want to prepare my documents.", "en"),
])
def test_no_links_keeps_current_action_explanation_and_no_dangling_link_reference(body, language):
    case = example(body, language)
    before = case.model_dump_json()
    result = preparation_guidance(case, date(2026, 9, 5), set())
    text = "\n".join(answer for _, answer in result)
    assert "http" not in text and "GOV.UK:" not in text
    assert "下面" not in text and "below" not in text
    if language == "zh":
        assert "在读证明" in text and "资金来源" in text and "可以先准备" in text
    else:
        assert "enrolment" in text and "bank statements" in text and "start" in text
    assert case.model_dump_json() == before


@pytest.mark.parametrize(("body", "language"), [
    ("不需要材料建议，我只是告诉你情况。", "zh"),
    ("Don't give me preparation advice; I am only updating you.", "en"),
])
def test_actual_advice_opt_out_still_suppresses_proactive_guidance(body, language):
    assert preparation_guidance(example(body, language), date(2026, 9, 5), set()) == []


@pytest.mark.parametrize(("body", "language"), [
    ("我朋友说‘不用链接’，我现在想准备材料。", "zh"),
    ('My friend said "no links". I want to prepare my documents.', "en"),
    ("如果明天我说不用链接再说。我现在想准备材料。", "zh"),
])
def test_quoted_reported_or_conditional_link_decline_is_not_current(body, language):
    result = preparation_guidance(example(body, language), date(2026, 9, 5), set())
    assert APPLICATION_URL in "\n".join(answer for _, answer in result)


def test_expired_sources_still_withheld_when_links_declined():
    case = example("No links. I want to prepare my documents.", "en")
    assert preparation_guidance(case, date(2026, 10, 5), set()) == []
