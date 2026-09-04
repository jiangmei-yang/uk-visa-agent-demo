from datetime import date

import pytest

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.workflow.adviser_guidance import (
    APPLICATION_URL,
    DOCUMENTS_URL,
    ROUTE_CHECK_URL,
    preparation_guidance,
)
from visa_agent.workflow.conversation import confirmation_message, received_context


def student_case(language: str = "zh") -> Case:
    case = Case(id="advice", external_thread_id="thread", applicant_contact="fictional@example.test",
                policy_version="v", customer_language=language)
    case.profile.visit_purpose = "tourism"
    case.profile.route_confirmed_standard_visitor = True
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.latest_customer_message = "我可以先准备材料。"
    return case


@pytest.mark.parametrize("language", ["zh", "en"])
def test_guidance_is_contextual_sourced_and_does_not_change_case(language):
    case = student_case(language)
    before = case.model_dump_json()
    guidance = preparation_guidance(case, date(2026, 9, 4), set())
    assert [topic for topic, _ in guidance] == ["application_overview_v1", "student_self_preparation_v1"]
    assert APPLICATION_URL in guidance[0][1] and DOCUMENTS_URL in guidance[1][1]
    assert case.model_dump_json() == before
    assert preparation_guidance(case, date(2026, 9, 4), {key for key, _ in guidance}) == []


@pytest.mark.parametrize("today", [date(2026, 9, 3), date(2026, 10, 5)])
def test_expired_or_future_guidance_is_not_issued(today):
    assert preparation_guidance(student_case(), today, set()) == []


@pytest.mark.parametrize("body", ["先不用链接。", "请不要发官网", "Don't send links yet.",
                                  "谢谢", "Thank you!", "不用解释流程。"])
def test_decline_and_acknowledgement_do_not_trigger_proactive_lecture(body):
    case = student_case()
    case.latest_customer_message = body
    assert preparation_guidance(case, date(2026, 9, 4), set()) == []


def test_explicit_answers_and_review_have_priority_over_proactive_guidance():
    case = student_case()
    case.customer_answers = ["An answer to the customer's actual question."]
    assert preparation_guidance(case, date(2026, 9, 4), set()) == []
    case.customer_answers = []
    case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    assert preparation_guidance(case, date(2026, 9, 4), set()) == []


def test_unknown_route_gets_checker_not_a_visa_requirement_decision():
    case = student_case()
    case.profile.visit_purpose = None
    case.profile.route_confirmed_standard_visitor = False
    before = case.model_dump_json()
    guidance = preparation_guidance(case, date(2026, 9, 4), set())
    assert len(guidance) == 1 and ROUTE_CHECK_URL in guidance[0][1]
    assert APPLICATION_URL not in guidance[0][1]
    assert case.model_dump_json() == before


def test_conditional_guidance_does_not_wait_for_or_grant_route_confirmation():
    case = student_case()
    case.profile.route_confirmed_standard_visitor = False
    guidance = preparation_guidance(case, date(2026, 9, 4), set())
    assert APPLICATION_URL in guidance[0][1]
    assert "如果需要" in guidance[0][1]
    assert not case.profile.route_confirmed_standard_visitor


def test_changed_circumstances_do_not_repeat_self_funding_advice():
    case = student_case()
    case.profile.visit_purpose = "conference"
    case.profile.funding_source = "employer_or_school"
    guidance = preparation_guidance(case, date(2026, 9, 4), {"application_overview_v1", "student_self_preparation_v1"})
    assert [topic for topic, _ in guidance] == ["conference_preparation_v1"]
    assert "主办方" in guidance[0][1]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_confirmation_does_not_drop_direct_questions_or_grant_consent(language):
    case = student_case(language)
    case.customer_answers = ["Verified answer\nGOV.UK: " + APPLICATION_URL]
    result = confirmation_message(case, profile_only=True)
    assert result.startswith(case.customer_answers[0])
    assert not case.profile_confirmed and not case.final_summary_confirmed


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_birthday_accommodation_and_budget_are_acknowledged_without_echoing_pii(language):
    case = student_case(language)
    case.latest_received_facts = {"date_of_birth": "1998-05-12", "uk_accommodation": "London",
                                  "estimated_trip_cost_gbp": "3000"}
    message = received_context(case)
    assert ("生日" if language == "zh" else "date of birth") in message
    assert ("预算" if language == "zh" else "budget") in message
    assert "1998-05-12" not in message


@pytest.mark.parametrize("language", ["zh", "en"])
def test_received_travel_dates_are_acknowledged_as_new_information(language):
    case = student_case(language)
    case.latest_received_facts = {"planned_arrival_date": "2026-11-10", "planned_departure_date": "2026-11-17"}
    assert ("行程日期" if language == "zh" else "travel dates") in received_context(case)
