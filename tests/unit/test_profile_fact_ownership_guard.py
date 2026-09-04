"""Model-extracted facts must belong to the current applicant before persistence."""

from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


def event(body: str) -> InboundEvent:
    return InboundEvent(
        id="fictional-fact-owner",
        external_thread_id="fictional-fact-owner",
        sender="fictional@example.test",
        subject="UK visa question",
        body=body,
        received_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def update(field: str, value: str | int | bool, excerpt: str) -> FactUpdate:
    return FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=1)


@pytest.mark.parametrize(
    ("body", "candidate"),
    [
        (
            "My friend Ada is applying for a UK visa. She is a student.",
            update("occupation_status", "student", "She is a student"),
        ),
        (
            "My aunt's date of birth is 2 January 2000.",
            update("date_of_birth", "2000-01-02", "2 January 2000"),
        ),
        (
            "My spouse has a previous visa refusal.",
            update("has_serious_history", True, "previous visa refusal"),
        ),
        (
            "Her passport is Chinese and she lives in Singapore.",
            update("nationality_country", "China", "Her passport is Chinese"),
        ),
        (
            "My friend told me I should apply under the Standard Visitor route.",
            update(
                "route_confirmed_standard_visitor",
                True,
                "I should apply under the Standard Visitor route",
            ),
        ),
        (
            "我的朋友正在申请英国签证。她在读大学。",
            update("occupation_status", "student", "她在读大学"),
        ),
        (
            "我姑姑的护照是中国护照。",
            update("nationality_country", "China", "我姑姑的护照是中国护照"),
        ),
    ],
)
def test_other_person_facts_are_silently_discarded(body: str, candidate: FactUpdate) -> None:
    result = validate_case_patch(
        event(body),
        CasePatch(updates=[candidate], ambiguities=[], requires_human_review=True),
    )

    assert result.updates == []
    assert result.ambiguities == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "candidate"),
    [
        (
            "If I had a criminal conviction, would it affect an application?",
            update("has_serious_history", True, "I had a criminal conviction"),
        ),
        (
            "Hypothetically, I am a student and would apply next year.",
            update("occupation_status", "student", "I am a student"),
        ),
        (
            "如果我曾经被拒签，会有影响吗？",
            update("has_serious_history", True, "我曾经被拒签"),
        ),
    ],
)
def test_hypothetical_facts_are_silently_discarded(body: str, candidate: FactUpdate) -> None:
    result = validate_case_patch(
        event(body),
        CasePatch(
            updates=[candidate],
            ambiguities=["The imagined case needs review."],
            requires_human_review=True,
        ),
    )

    assert result.updates == []
    assert result.ambiguities == []
    assert not result.requires_human_review


def test_third_party_risk_does_not_hide_independent_applicant_fact() -> None:
    body = "My friend has a criminal conviction, but I am a student."
    result = validate_case_patch(
        event(body),
        CasePatch(
            updates=[
                update("has_serious_history", True, "My friend has a criminal conviction"),
                update("occupation_status", "student", "I am a student"),
            ],
            ambiguities=[],
            requires_human_review=True,
        ),
    )

    assert [(item.field, item.value) for item in result.updates] == [
        ("occupation_status", "student")
    ]
    assert result.ambiguities == []
    assert not result.requires_human_review


def test_later_hypothetical_risk_does_not_pollute_an_independent_current_fact() -> None:
    body = "I am a student, but if I had a criminal conviction it would be different."
    result = validate_case_patch(
        event(body),
        CasePatch(
            updates=[
                update("occupation_status", "student", "I am a student"),
                update("has_serious_history", True, "I had a criminal conviction"),
            ],
            ambiguities=[],
            requires_human_review=True,
        ),
    )

    assert [(item.field, item.value) for item in result.updates] == [
        ("occupation_status", "student")
    ]
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "relationship", "relationship_excerpt", "funding_excerpt"),
    [
        (
            "My father sponsors me and will pay for my trip.",
            "father",
            "My father sponsors me",
            "will pay for my trip",
        ),
        (
            "My spouse will pay for my travel costs.",
            "spouse",
            "My spouse will pay for my travel costs",
            "My spouse will pay for my travel costs",
        ),
        (
            "My father is paying for my accommodation.",
            "father",
            "My father is paying for my accommodation",
            "My father is paying for my accommodation",
        ),
        (
            "My father is my sponsor.",
            "father",
            "My father is my sponsor",
            "My father is my sponsor",
        ),
        (
            "My father Jian Chen will sponsor this trip.",
            "father",
            "My father Jian Chen will sponsor this trip",
            "My father Jian Chen will sponsor this trip",
        ),
        ("我的父亲资助我的旅行。", "father", "我的父亲资助我", "资助我的旅行"),
    ],
)
def test_explicit_support_for_applicant_remains_valid(
    body: str,
    relationship: str,
    relationship_excerpt: str,
    funding_excerpt: str,
) -> None:
    result = validate_case_patch(
        event(body),
        CasePatch(
            updates=[
                update("funding_source", "personal_sponsor", funding_excerpt),
                update("sponsor_relationship", relationship, relationship_excerpt),
            ],
            ambiguities=[],
        ),
    )

    assert {item.field for item in result.updates} == {
        "funding_source",
        "sponsor_relationship",
    }
    assert not result.requires_human_review


def test_natural_employer_funding_phrase_remains_valid() -> None:
    body = "My company will cover flights and the hotel."
    candidate = update("funding_source", "employer_or_school", body)

    result = validate_case_patch(event(body), CasePatch(updates=[candidate], ambiguities=[]))

    assert result.updates == [candidate]
    assert not result.requires_human_review


@pytest.mark.parametrize(
    "body",
    [
        "My father Jian Chen will not sponsor this trip.",
        "If my father Jian Chen sponsors this trip, I will ask him for a letter.",
        "My father Jian Chen will host me but does not pay for this trip.",
        "My friend said my father Jian Chen will sponsor this trip.",
    ],
)
def test_natural_sponsor_wording_does_not_weaken_negative_or_reported_boundaries(
    body: str,
) -> None:
    candidate = update("funding_source", "personal_sponsor", body)

    result = validate_case_patch(event(body), CasePatch(updates=[candidate], ambiguities=[]))

    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "relationship", "name", "location_excerpt", "in_uk"),
    [
        (
            "My father Jian Chen will sponsor this trip and he does not live in the UK.",
            "father",
            "Jian Chen",
            "he does not live in the UK",
            False,
        ),
        (
            "My father Jian Chen will sponsor this trip and he doesn't live in the UK.",
            "father",
            "Jian Chen",
            "he doesn't live in the UK",
            False,
        ),
        (
            "My mother Mei Chen will sponsor this trip and she lives in the UK.",
            "mother",
            "Mei Chen",
            "she lives in the UK",
            True,
        ),
        (
            "我父亲陈建国资助这次旅行，他不住在英国。",
            "father",
            "陈建国",
            "他不住在英国",
            False,
        ),
        (
            "我母亲陈美丽资助这次旅行，她住在英国。",
            "mother",
            "陈美丽",
            "她住在英国",
            True,
        ),
    ],
)
def test_same_sentence_pronoun_location_is_linked_to_one_explicit_sponsor(
    body: str,
    relationship: str,
    name: str,
    location_excerpt: str,
    in_uk: bool,
) -> None:
    result = validate_case_patch(
        event(body),
        CasePatch(
            updates=[
                update("sponsor_relationship", relationship, body.split(" and ")[0].split("，")[0]),
                update("sponsor_name", name, name),
                update("sponsor_is_in_uk", in_uk, location_excerpt),
            ],
            ambiguities=[],
        ),
    )

    assert {(item.field, item.value) for item in result.updates} == {
        ("sponsor_relationship", relationship),
        ("sponsor_name", name),
        ("sponsor_is_in_uk", in_uk),
    }
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "location_excerpt"),
    [
        (
            "My father Jian Chen will sponsor this trip, and my brother Wei Chen will travel with us, "
            "and he does not live in the UK.",
            "he does not live in the UK",
        ),
        (
            'My friend said, "My father Jian Chen will sponsor this trip and he does not live in the UK."',
            "he does not live in the UK",
        ),
        (
            "If my father Jian Chen sponsors this trip, he does not live in the UK.",
            "he does not live in the UK",
        ),
        (
            "My father Jian Chen will not sponsor this trip and he doesn't live in the UK.",
            "he doesn't live in the UK",
        ),
        (
            "My father Jian Chen will sponsor this trip. He doesn't live in the UK.",
            "He doesn't live in the UK",
        ),
        (
            "My father Jian Chen will host me but will not pay for this trip, and he doesn't live in the UK.",
            "he doesn't live in the UK",
        ),
        (
            "我父亲陈建国资助这次旅行，我哥哥也同行，他不住在英国。",
            "他不住在英国",
        ),
    ],
)
def test_pronoun_location_is_not_borrowed_from_ambiguous_or_unsafe_context(
    body: str,
    location_excerpt: str,
) -> None:
    candidate = update("sponsor_is_in_uk", False, location_excerpt)

    result = validate_case_patch(event(body), CasePatch(updates=[candidate], ambiguities=[]))

    assert result.updates == []
    assert not result.requires_human_review


def test_joint_applicant_statement_remains_current_applicant_evidence() -> None:
    body = "My wife and I are students and will travel together."
    candidate = update("occupation_status", "student", "My wife and I are students")

    result = validate_case_patch(event(body), CasePatch(updates=[candidate], ambiguities=[]))

    assert result.updates == [candidate]
    assert not result.requires_human_review


def test_current_applicant_serious_history_still_requires_review() -> None:
    body = "I have a criminal conviction."
    candidate = update("has_serious_history", True, body)

    result = validate_case_patch(event(body), CasePatch(updates=[candidate], ambiguities=[]))

    assert result.updates == [candidate]
    assert result.requires_human_review
