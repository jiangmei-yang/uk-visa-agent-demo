"""Synthetic six-turn contracts for state checks and weak lexical proxies only.

No experiment corpus, report, model, credentials, or mailbox is read. Passing a
lexical proxy does not establish relevance, correctness, or naturalness.
"""

from __future__ import annotations

import importlib.util
import json
import socket
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from visa_agent.domain.models import Case, CaseProfile, NextStepAdvice


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No network")))
    path = Path(__file__).resolve().parents[2] / "scripts/cold_start_checks.py"
    spec = importlib.util.spec_from_file_location("synthetic_cold_start_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journey():
    """Invented, cumulative conversation; not copied from evaluation material."""
    descriptions = [
        ("Where is the official visitor application page?", {}, False, False, ["official_application_entry"]),
        ("I am Mara Linden, a Canadian applying from Singapore for tourism.",
         {"full_name": "Mara Linden", "nationality_country": "Canada",
          "application_country": "Singapore", "visit_purpose": "tourism"}, False, False, []),
        ("I am self-employed and paying myself, with a GBP 1700 budget. Dates are undecided. What do bank records show?",
         {"occupation_status": "self_employed", "funding_source": "self",
          "estimated_trip_cost_gbp": 1700}, True, False, ["bank_evidence_purpose"]),
        ("My date of birth is 6 April 1997.", {"date_of_birth": "1997-04-06"}, True, False, []),
        ("Please correct my birth date to 16 April 1997 and pause preparation.",
         {"date_of_birth": "1997-04-16"}, True, True, ["corrected_fact_acknowledged"]),
        ("Please resume preparation. That is all for this email.", {}, True, False,
         ["resume_acknowledged", "no_unsolicited_document_request"]),
    ]
    profile = {}
    turns = []
    for index, (body, updates, deferred, paused, tags) in enumerate(descriptions, 1):
        profile.update(updates)
        turns.append({
            "id": f"synthetic-turn-{index}", "body": body, "expected_profile": deepcopy(profile),
            "deferred_date_expected": deferred, "preparation_paused_expected": paused,
            "expected_information": tags, "forbidden_reasked_fields": [],
            "rationale": "Synthetic contract fixture, not a semantic or naturalness rating.",
        })
    return {"id": "synthetic-journey", "split": "development", "language": "en",
            "subject": "Fictional visitor preparation", "turns": turns}


def encoded(*journeys):
    return json.dumps(journeys).encode()


def case_for(turn, **overrides):
    return Case(
        id="fictional-case", external_thread_id="fictional-thread",
        applicant_contact="fictional@example.test", policy_version="synthetic-policy",
        profile=CaseProfile.model_validate(turn["expected_profile"]),
        deferred_fields=(["planned_arrival_date", "planned_departure_date"]
                         if turn["deferred_date_expected"] else []),
        preparation_paused=turn["preparation_paused_expected"], **overrides,
    )


def test_complete_synthetic_six_turn_state_sequence(checks):
    fixture = journey()
    before = deepcopy(fixture)
    assert checks.load_journeys(encoded(fixture), "development") == [fixture]
    replies = [
        "The official entry is https://www.gov.uk/standard-visitor/apply-standard-visitor-visa",
        "I have recorded your name, nationality, application location, and visit purpose.",
        "Bank statements help show the source of funds and whether they are available to you.",
        "Your date of birth is recorded as 6 April 1997.",
        "I have corrected your date of birth to 16 April 1997 and paused preparation.",
        "We can resume preparation. Your travel dates remain undecided.",
    ]
    for index, (turn, reply) in enumerate(zip(fixture["turns"], replies, strict=True)):
        case = case_for(turn)
        if index == 4:
            case.latest_changes = {"date_of_birth": "1997-04-16"}
        if index == 5:
            case.latest_preparation_action = "resume"
        before_case = case.model_dump(mode="json")
        result = checks.check_turn(fixture, turn, case, reply)
        assert all(result.values()), result
        assert case.model_dump(mode="json") == before_case
    assert fixture == before


@pytest.mark.parametrize("value", [None, {}, [], [None], [{"split": "unknown"}]])
def test_invalid_top_level_schema_rejected(checks, value):
    with pytest.raises(ValueError):
        checks.load_journeys(json.dumps(value).encode(), "development")


@pytest.mark.parametrize("split", ["all", "", "training"])
def test_split_must_be_explicitly_supported(checks, split):
    with pytest.raises(ValueError, match="supported split"):
        checks.load_journeys(encoded(journey()), split)


def test_unselected_split_is_not_expanded_or_label_validated(checks):
    selected = journey()
    opaque_other_split = {"split": "holdout", "turns": "not a valid selected journey"}
    assert checks.load_journeys(encoded(selected, opaque_other_split), "development") == [selected]
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(selected, opaque_other_split), "holdout")


@pytest.mark.parametrize(("key", "value"), [
    ("id", " "), ("language", "fr"), ("subject", "line one\nline two"),
    ("subject", "line one\rline two"), ("turns", []), ("extra", "not allowed"),
])
def test_journey_schema_errors(checks, key, value):
    fixture = journey()
    fixture[key] = value
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(fixture), "development")


@pytest.mark.parametrize(("key", "value"), [
    ("id", ""), ("body", "\t"), ("rationale", None), ("extra", "not allowed"),
    ("deferred_date_expected", 1), ("preparation_paused_expected", "false"),
    ("expected_profile", []), ("expected_profile", {"invented_field": "value"}),
    ("expected_information", "document_purpose"), ("expected_information", ["unrecognised"]),
    ("expected_information", ["document_purpose", "document_purpose"]),
    ("forbidden_reasked_fields", ["unknown_field"]), ("forbidden_reasked_fields", [42]),
    ("forbidden_reasked_fields", ["full_name", "full_name"]),
])
def test_turn_schema_errors(checks, key, value):
    fixture = journey()
    fixture["turns"][0][key] = value
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(fixture), "development")


def test_missing_keys_duplicate_ids_and_wrong_turn_count_rejected(checks):
    fixtures = []
    missing_journey_key = journey()
    del missing_journey_key["subject"]
    fixtures.append(missing_journey_key)
    missing_turn_key = journey()
    del missing_turn_key["turns"][0]["rationale"]
    fixtures.append(missing_turn_key)
    duplicate_turn = journey()
    duplicate_turn["turns"][1]["id"] = duplicate_turn["turns"][0]["id"]
    fixtures.append(duplicate_turn)
    five_turns = journey()
    five_turns["turns"].pop()
    fixtures.append(five_turns)
    for fixture in fixtures:
        with pytest.raises(ValueError):
            checks.load_journeys(encoded(fixture), "development")
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(journey(), journey()), "development")
    unique = [dict(journey(), id=f"synthetic-{index}") for index in range(3)]
    assert len(checks.load_journeys(encoded(*unique[:2]), "development")) == 2
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(*unique), "development")


@pytest.mark.parametrize(("field", "value"), [
    ("estimated_trip_cost_gbp", True), ("estimated_trip_cost_gbp", -1),
    ("estimated_trip_cost_gbp", 1700.0), ("estimated_trip_cost_gbp", "1700"),
    ("full_name", 123), ("occupation_status", False), ("funding_source", " "),
    ("date_of_birth", "1997-02-30"), ("date_of_birth", "19970406"),
    ("planned_arrival_date", "2027-5-01"),
    ("planned_departure_date", "2027-05-12T00:00:00"),
])
def test_expected_facts_use_exact_types_and_calendar_dates(checks, field, value):
    fixture = journey()
    fixture["turns"][0]["expected_profile"][field] = value
    with pytest.raises(ValueError):
        checks.load_journeys(encoded(fixture), "development")


def test_profiles_are_cumulative_but_may_explicitly_correct_values(checks):
    fixture = journey()
    assert checks.load_journeys(encoded(fixture), "development") == [fixture]
    del fixture["turns"][3]["expected_profile"]["full_name"]
    with pytest.raises(ValueError, match="cumulative"):
        checks.load_journeys(encoded(fixture), "development")


@pytest.mark.parametrize("field", ["planned_arrival_date", "planned_departure_date"])
def test_deferred_expectation_cannot_also_supply_either_exact_date(checks, field):
    fixture = journey()
    fixture["turns"][-1]["expected_profile"][field] = "2027-05-12"
    with pytest.raises(ValueError, match="Deferred-date"):
        checks.load_journeys(encoded(fixture), "development")


def test_exact_travel_dates_and_zero_budget_are_valid_when_not_deferred(checks):
    fixture = journey()
    last = fixture["turns"][-1]
    last["deferred_date_expected"] = False
    last["expected_profile"].update(planned_arrival_date="2027-05-12",
                                    planned_departure_date="2027-05-19", estimated_trip_cost_gbp=0)
    assert checks.load_journeys(encoded(fixture), "development") == [fixture]
    assert all(checks.check_turn(fixture, last, case_for(last, latest_preparation_action="resume"),
                                 "Preparation can resume.").values())


@pytest.mark.parametrize(("field", "invented"), [
    ("full_name", "Invented Person"), ("date_of_birth", "1993-01-17"),
    ("nationality_country", "Canada"), ("application_country", "Singapore"),
    ("visit_purpose", "tourism"), ("occupation_status", "employed"),
    ("funding_source", "self"), ("estimated_trip_cost_gbp", 1200),
    ("planned_arrival_date", "2027-05-12"), ("planned_departure_date", "2027-05-19"),
])
def test_every_omitted_expected_field_means_unknown_not_permission_to_invent(checks, field, invented):
    fixture = journey()
    turn = fixture["turns"][0]
    case = case_for(turn)
    result = checks.check_turn(fixture, turn, case, "Recorded.")
    assert all(result[f"profile:{name}"] for name in checks.PROFILE_FIELDS)
    case.profile = CaseProfile.model_validate({field: invented})
    result = checks.check_turn(fixture, turn, case, "Recorded.")
    assert not result[f"profile:{field}"]
    assert set(key.removeprefix("profile:") for key in result if key.startswith("profile:")) == checks.PROFILE_FIELDS


@pytest.mark.parametrize(("field", "expected", "actual"), [
    ("funding_source", "self-funded", "self"),
    ("occupation_status", "self-employed", "self_employed"),
    ("nationality_country", "中国", "China"),
    ("application_country", "新加坡", "Singapore"),
])
def test_only_explicit_label_or_location_equivalences_are_accepted(checks, field, expected, actual):
    fixture = journey()
    turn = fixture["turns"][0]
    turn["expected_profile"][field] = expected
    case = case_for(turn)
    setattr(case.profile, field, actual)
    assert checks.check_turn(fixture, turn, case, "Recorded.")[f"profile:{field}"]
    setattr(case.profile, field, "different unrecognised value")
    assert not checks.check_turn(fixture, turn, case, "Recorded.")[f"profile:{field}"]


@pytest.mark.parametrize(("field", "wanted", "actual"), [
    ("full_name", "self-funded", "self"), ("visit_purpose", "self-employed", "self_employed"),
    ("estimated_trip_cost_gbp", 1700, True), ("estimated_trip_cost_gbp", 1700, 1700.0),
])
def test_aliases_and_type_coercion_do_not_relax_unrelated_comparisons(checks, field, wanted, actual):
    fixture = journey()
    turn = fixture["turns"][0]
    turn["expected_profile"][field] = wanted
    case = case_for(turn)
    # Deliberately corrupt the observed type to exercise the checker, not Pydantic coercion.
    setattr(case.profile, field, actual)
    if isinstance(actual, float):
        with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
            result = checks.check_turn(fixture, turn, case, "Recorded.")
    else:
        result = checks.check_turn(fixture, turn, case, "Recorded.")
    assert not result[f"profile:{field}"]


@pytest.mark.parametrize("field", ["full_name", "date_of_birth", "planned_arrival_date", "planned_departure_date"])
def test_known_or_deferred_facts_cannot_reappear_in_requested_fields(checks, field):
    fixture = journey()
    turn = fixture["turns"][3]
    case = case_for(turn)
    case.last_requested_fields = [field]
    assert not checks.check_turn(fixture, turn, case, "Recorded.")["known_or_deferred_fields_not_planned"]


def test_explicit_forbidden_unknown_field_is_also_protected(checks):
    fixture = journey()
    turn = fixture["turns"][0]
    turn["forbidden_reasked_fields"] = ["full_name"]
    case = case_for(turn, last_requested_fields=["full_name"])
    result = checks.check_turn(fixture, turn, case, "Please provide your full name.")
    assert not result["known_or_deferred_fields_not_planned"]
    assert not result["no_reask_text_proxy:full_name"]


@pytest.mark.parametrize(("field", "value", "body"), [
    ("full_name", "Mara Linden", "Please provide your full name."),
    ("date_of_birth", "1997-04-16", "What is your date of birth?"),
    ("nationality_country", "Canada", "Which country issued your passport?"),
    ("application_country", "Singapore", "Which country will you apply from?"),
    ("visit_purpose", "tourism", "What is the main purpose of your visit?"),
    ("occupation_status", "self_employed", "Are you currently employed or studying?"),
    ("funding_source", "self", "Who will pay for the trip?"),
    ("estimated_trip_cost_gbp", 1700, "How much do you plan to spend?"),
    ("planned_arrival_date", "2027-05-12", "When will you arrive?"),
    ("planned_departure_date", "2027-05-19", "When will you return?"),
])
def test_each_tracked_field_has_an_independent_text_reask_alarm(checks, field, value, body):
    fixture = journey()
    turn = fixture["turns"][0]
    turn["expected_profile"][field] = value
    result = checks.check_turn(fixture, turn, case_for(turn), body)
    assert result["known_or_deferred_fields_not_planned"]
    assert not result[f"no_reask_text_proxy:{field}"]


@pytest.mark.parametrize("actual_deferred", [[], ["planned_arrival_date"], ["planned_departure_date"]])
def test_both_dates_must_be_deferred_when_label_requires_it(checks, actual_deferred):
    fixture = journey()
    turn = fixture["turns"][3]
    case = case_for(turn)
    case.deferred_fields = actual_deferred
    assert not checks.check_turn(fixture, turn, case, "Recorded.")["dates_deferred"]


@pytest.mark.parametrize("actual_deferred", [["planned_arrival_date"], ["planned_departure_date"]])
def test_partial_date_deferral_cannot_pass_a_no_deferral_expectation(checks, actual_deferred):
    fixture = journey()
    turn = fixture["turns"][0]
    case = case_for(turn)
    case.deferred_fields = actual_deferred
    assert not checks.check_turn(fixture, turn, case, "Recorded.")["dates_deferred"]


@pytest.mark.parametrize("body", [
    "已记录你的出生日期是1997年4月6日。", "你的出生日期为1997年4月6日，已记录。",
    "Your date of birth is recorded as 6 April 1997.",
])
def test_dob_statement_acknowledgement_is_not_a_fresh_question(checks, body):
    fixture = journey()
    turn = fixture["turns"][3]
    assert checks.check_turn(fixture, turn, case_for(turn), body)["no_reask_text_proxy:date_of_birth"]


@pytest.mark.parametrize("body", [
    "你的出生日期是什么？", "你的出生日期是哪一天？", "请提供出生日期。",
    "What is your date of birth?", "When were you born?",
])
def test_dob_question_is_detected_even_if_no_structured_field_is_planned(checks, body):
    fixture = journey()
    turn = fixture["turns"][3]
    result = checks.check_turn(fixture, turn, case_for(turn), body)
    assert result["known_or_deferred_fields_not_planned"]
    assert not result["no_reask_text_proxy:date_of_birth"]


@pytest.mark.parametrize("tag", [
    "official_application_entry", "document_purpose", "translation_requirements",
    "bank_evidence_purpose", "corrected_fact_acknowledged", "no_unsolicited_document_request",
    "resume_acknowledged", "next_step_action",
])
def test_whitespace_reply_cannot_satisfy_any_information_proxy(checks, tag):
    assert not checks.information_proxies(tag, case_for(journey()["turns"][0]), " \n\t")


def test_polite_empty_information_does_not_satisfy_a_requested_explanation(checks):
    fixture = journey()
    turn = fixture["turns"][2]
    result = checks.check_turn(fixture, turn, case_for(turn), "Thank you. Noted.")
    assert result["body_not_empty"]
    assert not result["information_proxy:bank_evidence_purpose"]


@pytest.mark.parametrize("body", [
    "For reference: a passport identifies you; bank statements can show available funds. This is not a request to send documents.",
    "一般清单可包括护照、资金及来源证明；适用时还包括翻译。这是参考，不是要求现在补交材料。",
    "You do not need to send a passport today.", "无需现在上传银行流水。",
    "If you resume later, please send the passport PDF.",
])
def test_reference_checklist_and_negated_or_future_request_do_not_alarm(checks, body):
    assert not checks.unsolicited_document_request(body)


@pytest.mark.parametrize("body", [
    "Please upload your passport PDF now.", "Please send your bank statements.",
    "We'll also need these documents: your passport and bank statements.",
    "请现在上传护照 PDF。", "请回复这封邮件并附上银行流水。",
])
def test_direct_document_collection_requests_alarm(checks, body):
    assert checks.unsolicited_document_request(body)


@pytest.mark.parametrize("body", [
    "Please provide your date of birth.", "Please provide your full name.", "请提供你的姓名。",
])
def test_fact_question_is_not_itself_a_document_collection_request(checks, body):
    assert not checks.unsolicited_document_request(body)


@pytest.mark.parametrize("body", [
    "You do not need to send a passport, but please upload your bank statements now.",
    "无需现在提供护照，但请上传银行流水。",
])
def test_negating_one_document_request_does_not_hide_another_positive_request(checks, body):
    assert checks.unsolicited_document_request(body)


def test_next_step_proxy_requires_persisted_actionable_advice_in_the_actual_reply(checks):
    case = case_for(journey()["turns"][0])
    message = "Start with a clear PDF of the passport identity page so we can check your identity."
    assert not checks.information_proxies("next_step_action", case, message)
    case.next_step_advice = NextStepAdvice(kind="document", message=message, requirement_id="passport")
    assert checks.information_proxies("next_step_action", case, message)
    assert not checks.information_proxies("next_step_action", case, "We can discuss the next step.")
    case.next_step_advice = NextStepAdvice(kind="paused", message=message)
    assert not checks.information_proxies("next_step_action", case, message)


def test_correction_and_resume_proxies_need_state_not_just_keywords(checks):
    case = case_for(journey()["turns"][0])
    assert not checks.information_proxies("corrected_fact_acknowledged", case, "Updated.")
    case.latest_changes = {"date_of_birth": "1997-04-16"}
    assert checks.information_proxies("corrected_fact_acknowledged", case, "Updated.")
    assert not checks.information_proxies("resume_acknowledged", case, "We can resume.")
    case.latest_preparation_action = "resume"
    assert checks.information_proxies("resume_acknowledged", case, "We can resume.")
    case.preparation_paused = True
    assert not checks.information_proxies("resume_acknowledged", case, "We can resume.")


def test_keyword_presence_is_intentionally_not_a_semantic_or_naturalness_score(checks):
    case = case_for(journey()["turns"][0])
    # These disconnected words pass the documented lexical presence check; a
    # human reviewer must still reject them as an explanation to a customer.
    assert checks.information_proxies(
        "translation_requirements", case, "Translation full accuracy name signature date contact.",
    )
    with pytest.raises(ValueError, match="Unknown information obligation"):
        checks.information_proxies("invented-naturalness-rating", case, "Hello.")
