import pytest

from visa_agent.delivery.pack import _cover_letter_context
from visa_agent.domain.models import Case, CaseProfile


def make_case(**values):
    return Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", policy_version="synthetic",
                profile=CaseProfile(**values))


@pytest.mark.parametrize(("occupation", "expected"), [
    ("student", "I am currently a student."),
    ("self_employed", "I am self-employed."),
    ("employed", "I am currently employed."),
])
def test_occupation_is_readable_without_adding_job_details(occupation, expected):
    case = make_case(occupation_status=occupation, funding_source="self")
    before = case.model_dump(mode="json")
    text = _cover_letter_context(case)
    assert expected in text
    assert "I plan to pay for this visit myself." in text
    assert "recorded" not in text
    assert "return to work" not in text
    assert case.model_dump(mode="json") == before


def test_employer_name_is_literal_and_not_assumed_to_be_payer():
    text = _cover_letter_context(make_case(occupation_status="employed",
        employer_name="eBay_RESEARCH Ltd", funding_source="self"))
    assert "I work for eBay_RESEARCH Ltd." in text
    assert "I plan to pay for this visit myself." in text


@pytest.mark.parametrize("occupation", ["student", "employed", "self_employed", None])
def test_organization_category_does_not_invent_which_organization_pays(occupation):
    text = _cover_letter_context(make_case(occupation_status=occupation,
        employer_name="Unverified payer association", funding_source="employer_or_school"))
    assert "Adviser note: confirm whether the employer or educational institution" in text
    assert "will pay" not in text
    assert "will cover" not in text


def test_personal_sponsor_does_not_invent_relationship_or_support_terms():
    text = _cover_letter_context(make_case(occupation_status="student",
        funding_source="personal_sponsor", sponsor_name="Mina Example"))
    assert "Mina Example is the person I have identified to sponsor this visit." in text
    assert "all expenses" not in text
    assert "mother" not in text
    assert "guarantee" not in text


@pytest.mark.parametrize("value", [None, "unsupported_legacy_value"])
def test_unknown_fields_are_review_notes_not_applicant_assertions(value):
    text = _cover_letter_context(make_case(occupation_status=value, funding_source=value))
    assert "Adviser note: confirm the applicant's current occupation" in text
    assert "Adviser note: confirm who will fund the visit" in text
    assert "None" not in text
    assert "unsupported_legacy_value" not in text


def test_missing_sponsor_name_stays_unresolved():
    text = _cover_letter_context(make_case(funding_source="personal_sponsor"))
    assert "Adviser note: confirm the personal sponsor's name" in text
