"""Critical profile enums require field- and value-bound customer evidence."""

from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


def event(body: str, requested: list[str] | None = None) -> InboundEvent:
    return InboundEvent(
        id="semantic-enum-evidence",
        external_thread_id="semantic-enum-evidence",
        sender="fictional@example.test",
        subject="UK visa enquiry",
        body=body,
        received_at=datetime(2026, 9, 5, tzinfo=UTC),
        requested_fields=requested or [],
    )


def candidate(
    field: str,
    value: str | int | bool,
    excerpt: str,
) -> FactUpdate:
    return FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=1)


def checked(
    body: str,
    field: str,
    value: str | int | bool,
    excerpt: str,
    *,
    requested: list[str] | None = None,
    model_review: bool = False,
) -> CasePatch:
    return validate_case_patch(
        event(body, requested),
        CasePatch(
            updates=[candidate(field, value, excerpt)],
            ambiguities=[],
            requires_human_review=model_review,
        ),
    )


@pytest.mark.parametrize(
    ("body", "field", "value", "excerpt"),
    [
        (
            "I am a student. My trip is for tourism.",
            "visit_purpose",
            "tourism",
            "I am a student",
        ),
        (
            "I am a student. My trip is for tourism.",
            "occupation_status",
            "student",
            "My trip is for tourism",
        ),
        (
            "What documents do tourists normally need?",
            "visit_purpose",
            "tourism",
            "tourists",
        ),
        (
            "Tourism is not my purpose; I will attend an industry conference.",
            "visit_purpose",
            "tourism",
            "Tourism is not my purpose",
        ),
        (
            "I am not sure whether this is tourism or business.",
            "visit_purpose",
            "tourism",
            "tourism",
        ),
        (
            "I will attend an academic conference in London.",
            "visit_purpose",
            "business",
            "academic conference",
        ),
        (
            "My sister will host me in London.",
            "visit_purpose",
            "family_or_friends",
            "will host me",
        ),
        (
            "I am employed, not a student.",
            "occupation_status",
            "student",
            "not a student",
        ),
        (
            "I am a student.",
            "occupation_status",
            "employed",
            "I am a student",
        ),
        (
            "I might be a student or employed next year.",
            "occupation_status",
            "student",
            "student",
        ),
        (
            "I will work in the UK if the visa permits it.",
            "occupation_status",
            "employed",
            "work in the UK",
        ),
        (
            "I am self-employed.",
            "occupation_status",
            "employed",
            "self-employed",
        ),
        (
            "Where can I read about the Standard Visitor route?",
            "route_confirmed_standard_visitor",
            True,
            "Standard Visitor route",
        ),
        (
            "I am not sure whether the Standard Visitor route is right for me.",
            "route_confirmed_standard_visitor",
            True,
            "Standard Visitor route",
        ),
        (
            "I am not applying under the Standard Visitor route.",
            "route_confirmed_standard_visitor",
            True,
            "not applying under the Standard Visitor route",
        ),
        (
            "I confirm I am applying for a UK Standard Visitor visa.",
            "route_confirmed_standard_visitor",
            False,
            "confirm I am applying for a UK Standard Visitor visa",
        ),
        (
            "How would a previous visa refusal affect an application?",
            "has_serious_history",
            True,
            "previous visa refusal",
        ),
        (
            "I have never had any serious immigration or criminal history.",
            "has_serious_history",
            True,
            "serious immigration or criminal history",
        ),
        (
            "I had a visa refusal in 2022.",
            "has_serious_history",
            False,
            "visa refusal in 2022",
        ),
        (
            "I have never had a visa refusal.",
            "has_serious_history",
            False,
            "never had a visa refusal",
        ),
        (
            "I am going for a holiday.",
            "visit_purpose",
            "leisure",
            "going for a holiday",
        ),
    ],
)
def test_opposite_uncertain_unrelated_or_unsupported_values_fail_closed(
    body: str,
    field: str,
    value: str | int | bool,
    excerpt: str,
) -> None:
    result = checked(body, field, value, excerpt, model_review=True)

    assert result.updates == []
    assert result.ambiguities == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "field", "value", "excerpt"),
    [
        (
            "I have confirmed the Standard Visitor route.",
            "route_confirmed_standard_visitor",
            True,
            "confirmed the Standard Visitor route",
        ),
        (
            "我已确认按标准访客签证申请。",
            "route_confirmed_standard_visitor",
            True,
            "已确认按标准访客签证申请",
        ),
        (
            "I have not chosen the Standard Visitor route.",
            "route_confirmed_standard_visitor",
            False,
            "I have not chosen the Standard Visitor route",
        ),
        (
            "我不按标准访客签证申请。",
            "route_confirmed_standard_visitor",
            False,
            "我不按标准访客签证申请",
        ),
        (
            "I had a visa refusal in 2022.",
            "has_serious_history",
            True,
            "I had a visa refusal in 2022",
        ),
        (
            "我在2022年有一次加拿大签证拒签。",
            "has_serious_history",
            True,
            "我在2022年有一次加拿大签证拒签",
        ),
        (
            "I have no visa refusals, immigration breaches, criminal history or civil judgments.",
            "has_serious_history",
            False,
            "I have no visa refusals, immigration breaches, criminal history or civil judgments",
        ),
        ("I am a university student.", "occupation_status", "student", "I am a university student"),
        ("I work for a bank in Hong Kong.", "occupation_status", "employed", "I work for a bank"),
        (
            "Actually, I am employed now; that corrects my earlier student status.",
            "occupation_status",
            "employed",
            "I am employed now",
        ),
        ("I run my own business.", "occupation_status", "self_employed", "I run my own business"),
        ("我目前在读大学。", "occupation_status", "student", "我目前在读大学"),
        ("我在深圳上班。", "occupation_status", "employed", "我在深圳上班"),
        ("我自己经营一家工作室。", "occupation_status", "self_employed", "我自己经营"),
        ("I plan a holiday in the UK.", "visit_purpose", "tourism", "I plan a holiday in the UK"),
        ("I will visit my sister in London.", "visit_purpose", "family_or_friends", "visit my sister"),
        ("I am planning a business trip.", "visit_purpose", "business", "planning a business trip"),
        (
            "I will attend an academic conference in London.",
            "visit_purpose",
            "conference",
            "attend an academic conference",
        ),
        ("这次去英国旅游。", "visit_purpose", "tourism", "去英国旅游"),
        ("这次去伦敦探望姐姐。", "visit_purpose", "family_or_friends", "探望姐姐"),
        ("这次去英国出差。", "visit_purpose", "business", "这次去英国出差"),
        ("这次去伦敦参加学术会议。", "visit_purpose", "conference", "参加学术会议"),
    ],
)
def test_explicit_current_facts_and_corrections_remain_valid(
    body: str,
    field: str,
    value: str | int | bool,
    excerpt: str,
) -> None:
    result = checked(body, field, value, excerpt)

    assert [(item.field, item.value) for item in result.updates] == [(field, value)]
    assert result.requires_human_review is (
        (field == "has_serious_history" and value is True)
        or (field == "route_confirmed_standard_visitor" and value is False)
    )


@pytest.mark.parametrize(
    ("field", "answer", "value"),
    [
        ("occupation_status", "student", "student"),
        ("occupation_status", "self-employed", "self_employed"),
        ("visit_purpose", "holiday", "tourism"),
        ("visit_purpose", "conference", "conference"),
        ("route_confirmed_standard_visitor", "Yes", True),
        ("route_confirmed_standard_visitor", "No", False),
        ("has_serious_history", "Yes", True),
        ("has_serious_history", "No", False),
    ],
)
def test_short_answers_only_bind_to_the_field_actually_requested(
    field: str,
    answer: str,
    value: str | bool,
) -> None:
    accepted = checked(answer, field, value, answer, requested=[field])
    unscoped = checked(answer, field, value, answer)

    assert [(item.field, item.value) for item in accepted.updates] == [(field, value)]
    if field in {"route_confirmed_standard_visitor", "has_serious_history"}:
        assert unscoped.updates == []


def test_repeated_ambiguous_tiny_excerpt_cannot_choose_the_convenient_occurrence() -> None:
    body = "I am not a student. The form asks whether I am a student."
    result = checked(body, "occupation_status", "student", "student")

    assert result.updates == []
    assert not result.requires_human_review
