from __future__ import annotations

from datetime import date

import pytest

from visa_agent.domain.models import Case
from visa_agent.domain.rules import CRITICAL_FACTS
from visa_agent.workflow.conversation import (
    QUESTION_TEXT_EN,
    QUESTION_TEXT_ZH,
    SPONSOR_IDENTITY_QUESTION_EN,
    SPONSOR_IDENTITY_QUESTION_ZH,
    blocked_customer_message,
    next_fact_questions,
    reply_items,
)
from visa_agent.workflow.explicit_answer_scope import explicitly_answers_current_enum


def case(language: str = "en") -> Case:
    return Case(
        id="human-question-case",
        external_thread_id="human-question-thread",
        applicant_contact="applicant@example.test",
        policy_version="test",
        customer_language=language,
    )


def complete_except_route(language: str = "en") -> Case:
    item = case(language)
    item.profile.full_name = "Sample Applicant"
    item.profile.date_of_birth = date(1994, 6, 12)
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.planned_arrival_date = date(2026, 11, 10)
    item.profile.planned_departure_date = date(2026, 11, 18)
    item.profile.visit_purpose = "tourism"
    item.profile.uk_accommodation = "10 Sample Street, London"
    item.profile.estimated_trip_cost_gbp = 2_000
    item.profile.current_address = "Flat 8, 10 Sample Street, Hong Kong"
    item.profile.current_address_duration = "two years"  # explicit fictional completed-profile fixture
    item.profile.occupation_status = "student"
    item.profile.funding_source = "self"
    item.profile.has_serious_history = False
    return item


def test_every_delivery_critical_field_has_reviewed_chinese_and_english_text() -> None:
    assert QUESTION_TEXT_ZH.keys() >= CRITICAL_FACTS
    assert QUESTION_TEXT_EN.keys() >= CRITICAL_FACTS
    for field in CRITICAL_FACTS:
        assert field not in QUESTION_TEXT_ZH[field]
        assert field.replace("_", " ").title() not in QUESTION_TEXT_EN[field]
        assert QUESTION_TEXT_ZH[field].endswith(("？", "。", "visa")) or "https://" in QUESTION_TEXT_ZH[field]
        assert QUESTION_TEXT_EN[field].endswith(("?", ".", "visa")) or "https://" in QUESTION_TEXT_EN[field]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_personal_sponsor_identity_is_one_question_before_uk_location(language: str) -> None:
    item = case(language)
    item.profile.visit_purpose = "tourism"
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.occupation_status = "student"
    item.profile.funding_source = "personal_sponsor"

    assert next_fact_questions(item) == ["sponsor_relationship", "sponsor_name"]
    _, questions, _ = reply_items(item)
    assert questions == [
        SPONSOR_IDENTITY_QUESTION_ZH if language == "zh" else SPONSOR_IDENTITY_QUESTION_EN
    ]
    assert "sponsor_relationship" not in questions[0]
    assert "sponsor name" not in questions[0].lower()

    item.profile.sponsor_relationship = "mother"
    item.profile.sponsor_name = "Mei Example"
    assert next_fact_questions(item) == ["sponsor_is_in_uk"]
    _, location_question, _ = reply_items(item)
    assert location_question == [
        (QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN)["sponsor_is_in_uk"]
    ]


@pytest.mark.parametrize(
    ("language", "body", "expected", "not_expected"),
    [
        ("zh", "这次费用由我父母资助。", ("你说这次由父母资助", "父亲、母亲", "两位共同", "姓名"), "这次由谁资助"),
        ("zh", "我父亲会承担我这次旅行费用。", ("我理解", "父亲资助", "姓名"), "和资助人的关系"),
        ("zh", "我妈会支付我的旅费。", ("我理解", "母亲资助", "姓名"), "这次由谁资助"),
        ("en", "My parents will fund my trip.", ("your parents will fund", "father", "mother", "both", "full name"), "Who is sponsoring"),
        ("en", "My father will pay for my trip.", ("your father will fund", "full name"), "relationship to you"),
        ("en", "My mother is covering this trip.", ("your mother will fund", "full name"), "Who is sponsoring"),
    ],
)
def test_current_parent_funding_gets_a_specific_single_follow_up(
    language: str, body: str, expected: tuple[str, ...], not_expected: str,
) -> None:
    item = case(language)
    item.profile.visit_purpose = "tourism"
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.occupation_status = "student"
    item.profile.funding_source = "personal_sponsor"
    item.latest_customer_message = body

    assert next_fact_questions(item) == ["sponsor_relationship", "sponsor_name"]
    _, questions, _ = reply_items(item)

    assert len(questions) == 1
    assert all(value.casefold() in questions[0].casefold() for value in expected)
    assert not_expected.casefold() not in questions[0].casefold()


@pytest.mark.parametrize(
    ("language", "relationship", "expected"),
    [
        ("zh", "father", ("资助说明", "父亲", "姓名")),
        ("zh", "parents", ("共同资助", "两位", "姓名")),
        ("en", "mother", ("support letter", "mother's full name", "ID")),
        ("en", "parents", ("both parents", "both names", "financial evidence")),
    ],
)
def test_known_parent_relationship_asks_only_for_the_missing_name(
    language: str, relationship: str, expected: tuple[str, ...],
) -> None:
    item = case(language)
    item.profile.visit_purpose = "tourism"
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.occupation_status = "student"
    item.profile.funding_source = "personal_sponsor"
    item.profile.sponsor_relationship = relationship

    assert next_fact_questions(item) == ["sponsor_name"]
    _, questions, _ = reply_items(item)

    assert len(questions) == 1
    assert all(value.casefold() in questions[0].casefold() for value in expected)
    assert "relationship" not in questions[0].casefold()
    assert "关系" not in questions[0]


@pytest.mark.parametrize(
    ("language", "body"),
    [
        ("zh", "如果我父亲资助我的旅行，他要准备什么？"),
        ("zh", "我的朋友说：‘我父母会资助这次旅行’。"),
        ("en", "If my mother pays for my trip, what would she need?"),
        ("en", "My friend's trip will be paid for by my father."),
    ],
)
def test_hypothetical_quoted_or_third_party_parent_does_not_tailor_identity_question(
    language: str, body: str,
) -> None:
    item = case(language)
    item.profile.visit_purpose = "tourism"
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.occupation_status = "student"
    item.profile.funding_source = "personal_sponsor"
    item.latest_customer_message = body

    _, questions, _ = reply_items(item)

    assert questions == [
        SPONSOR_IDENTITY_QUESTION_ZH if language == "zh" else SPONSOR_IDENTITY_QUESTION_EN
    ]


@pytest.mark.parametrize(
    ("language", "body", "facts", "expected"),
    [
        (
            "zh",
            "中国护照，在香港申请，去英国旅游。我目前在读书，费用由我父母资助。",
            {
                "nationality_country": "China", "application_country": "Hong Kong",
                "visit_purpose": "tourism", "occupation_status": "student",
                "funding_source": "personal_sponsor",
            },
            ("明白", "中国护照", "香港递交", "去英国旅游", "目前在读书", "父母资助"),
        ),
        (
            "en",
            "I have a Chinese passport and will apply in Hong Kong. I am a student travelling for tourism, "
            "and my father will pay for my trip.",
            {
                "nationality_country": "China", "application_country": "Hong Kong",
                "visit_purpose": "tourism", "occupation_status": "student",
                "funding_source": "personal_sponsor",
            },
            ("starting point", "Chinese passport", "apply from Hong Kong", "holiday", "studying", "father"),
        ),
    ],
)
def test_reply_acknowledges_known_case_context_before_one_tailored_question(
    language: str, body: str, facts: dict[str, str], expected: tuple[str, ...],
) -> None:
    item = case(language)
    item.profile.nationality_country = "China"
    item.profile.application_country = "Hong Kong"
    item.profile.visit_purpose = "tourism"
    item.profile.occupation_status = "student"
    item.profile.funding_source = "personal_sponsor"
    item.latest_customer_message = body
    item.latest_received_facts = facts

    reply = blocked_customer_message(item)

    assert all(value.casefold() in reply.casefold() for value in expected)
    assert reply.count("？") + reply.count("?") <= 1
    assert ("你说这次由父母资助" in reply if language == "zh"
            else "I understand that your father will fund the trip" in reply)
    assert ("这次由谁资助" if language == "zh" else "Who is sponsoring") not in reply


@pytest.mark.parametrize("language", ["zh", "en"])
def test_route_confirmation_gives_safe_official_check_action(language: str) -> None:
    item = complete_except_route(language)
    _, questions, _ = reply_items(item)
    assert len(questions) == 1
    assert "Standard Visitor" in questions[0]
    assert "https://www.gov.uk/check-uk-visa" in questions[0]
    assert ("ETA" in questions[0]) and ("核对" in questions[0] if language == "zh" else "confirm" in questions[0])

    item.latest_customer_message = "这次不要链接。" if language == "zh" else "Please do not send links."
    _, no_link_questions, _ = reply_items(item)
    assert len(no_link_questions) == 1
    assert "https://" not in no_link_questions[0]
    assert "GOV.UK" in no_link_questions[0]


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("I am travelling to the UK for tourism.", "visit_purpose"),
        ("Tourism", "visit_purpose"),
        ("这次去英国旅游。", "visit_purpose"),
        ("這次去英國旅遊。", "visit_purpose"),
        ("I am a student.", "occupation_status"),
        ("我目前在讀書。", "occupation_status"),
        ("I will pay for the trip myself.", "funding_source"),
        ("這次費用由我自己承擔。", "funding_source"),
        ("My mother will pay for my trip.", "funding_source"),
    ],
)
def test_current_applicant_explicit_enum_answer_is_recognised(text: str, field: str) -> None:
    assert explicitly_answers_current_enum(text, field)


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("My friend is visiting the UK for tourism.", "visit_purpose"),
        ("我的朋友这次去英国旅游。", "visit_purpose"),
        ("我朋友这次去英國旅遊。", "visit_purpose"),
        ("If I travel for tourism, what would I need?", "visit_purpose"),
        ("如果我去旅游，需要什么？", "visit_purpose"),
        ("It is not tourism.", "visit_purpose"),
        ("不是旅遊。", "visit_purpose"),
        ("I am not sure whether it is tourism or business.", "visit_purpose"),
        ("I want to know what documents tourists usually need.", "visit_purpose"),
        ("我想知道旅游一般要什么材料。", "visit_purpose"),
        ("My friend is a student.", "occupation_status"),
        ("I am asking what students normally need.", "occupation_status"),
        ("我在问学生一般要什么材料。", "occupation_status"),
        ("My mother pays for my friend's trip.", "funding_source"),
    ],
)
def test_noncurrent_uncertain_or_negated_enum_does_not_suppress_question(
    text: str, field: str,
) -> None:
    assert not explicitly_answers_current_enum(text, field)


@pytest.mark.parametrize(
    ("language", "message"),
    [
        ("zh", "这次去英国旅游。"),
        ("en", "I am travelling to the UK for tourism."),
    ],
)
def test_model_omission_does_not_trigger_same_turn_enum_repeat(
    language: str, message: str,
) -> None:
    item = case(language)
    item.latest_customer_message = message

    assert item.profile.visit_purpose is None
    assert next_fact_questions(item) == ["nationality_country"]
    assert item.profile.visit_purpose is None  # pacing recognition never persists inferred evidence


@pytest.mark.parametrize(
    ("language", "guidance", "canned"),
    [
        (
            "zh",
            "既然这次是旅游，可以先做一页简洁的预计行程。",
            "明白了，我记下的是",
        ),
        (
            "en",
            "As this is a holiday, start with a one-page intended itinerary.",
            "Thanks, I've got the starting point",
        ),
    ],
)
def test_selected_guidance_replaces_duplicate_single_fact_receipt(
    language: str, guidance: str, canned: str,
) -> None:
    item = case(language)
    item.profile.visit_purpose = "tourism"
    item.latest_customer_message = "这次是旅游。" if language == "zh" else "This is a holiday."
    item.latest_received_facts = {"visit_purpose": "tourism"}
    item.customer_answers = [guidance]
    item.proactive_guidance_offered = True
    item.question_plan = []

    reply = blocked_customer_message(item)
    assert guidance in reply
    assert canned not in reply


def test_multi_fact_receipt_is_retained_even_when_guidance_mentions_one_fact() -> None:
    item = case("en")
    item.profile.visit_purpose = "tourism"
    item.profile.occupation_status = "student"
    item.latest_received_facts = {
        "visit_purpose": "tourism",
        "occupation_status": "student",
    }
    item.customer_answers = ["As this is a holiday, start with a one-page intended itinerary."]
    item.proactive_guidance_offered = True
    item.question_plan = []

    reply = blocked_customer_message(item)
    assert "Thanks, I've got the starting point" in reply
