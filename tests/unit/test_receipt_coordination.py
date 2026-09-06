import pytest

from visa_agent.domain.models import Case
from visa_agent.workflow.conversation import received_context


def case_for(facts, language="en"):
    return Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", policy_version="synthetic",
                customer_language=language, latest_received_facts=facts)


@pytest.mark.parametrize(("facts", "expected"), [
    ({"full_name": "Kai Example", "date_of_birth": "1997-07-01"},
     "I've recorded the name in your passport and your date of birth."),
    ({"full_name": "Kai Example", "date_of_birth": "1997-07-01", "uk_accommodation": "London"},
     "I've recorded the name in your passport, your date of birth, and your proposed accommodation."),
    ({"estimated_trip_cost_gbp": "2000"}, "I've recorded your estimated budget."),
    ({}, ""),
])
def test_receipt_coordinates_supplied_items_without_new_claims(facts, expected):
    case = case_for(facts)
    before = case.model_dump(mode="json")
    assert received_context(case) == expected
    assert case.model_dump(mode="json") == before


@pytest.mark.parametrize(("facts", "expected"), [
    ({"planned_arrival_date": "2026-11-01"}, "your planned arrival date"),
    ({"planned_departure_date": "2026-11-08"}, "your planned departure date"),
    ({"planned_arrival_date": "2026-11-01", "planned_departure_date": "2026-11-08"},
     "your planned travel dates"),
])
def test_partial_date_receipt_does_not_claim_a_complete_or_changed_itinerary(facts, expected):
    case = case_for(facts)
    assert received_context(case) == f"I've recorded {expected}."
    assert not case.profile_confirmed and not case.final_summary_confirmed


@pytest.mark.parametrize(("facts", "expected"), [
    ({"planned_arrival_date": "2026-11-01"}, "计划抵达日期"),
    ({"planned_departure_date": "2026-11-08"}, "计划离开日期"),
    ({"planned_arrival_date": "2026-11-01", "planned_departure_date": "2026-11-08"}, "行程日期"),
])
def test_chinese_partial_date_receipt_is_specific(facts, expected):
    assert received_context(case_for(facts, "zh")) == f"你提供的{expected}已记下。"
