"""Editorial checks are not a naturalness score or field-extraction authority."""

import pytest

from visa_agent.domain.models import Case, CaseProfile
from visa_agent.workflow.conversation import change_acknowledgement


def example(language="en", **changes):
    return Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
                policy_version="2026-02-25", customer_language=language, latest_changes=changes,
                profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Jian Example",
                    sponsor_relationship="father", sponsor_address="34 Another Road, Hong Kong", sponsor_is_in_uk=False))


@pytest.mark.parametrize("language", ["en", "zh"])
def test_replacement_is_a_sentence_with_actual_identity_not_field_labels(language):
    case = example(language, sponsor_name="Jian Example", sponsor_relationship="father")
    reply = change_acknowledgement(case)
    assert "Jian Example" in reply
    assert ("father" if language == "en" else "父亲") in reply
    assert "Sponsor Name:" not in reply and "Sponsor Relationship:" not in reply
    assert "资助人姓名：" not in reply
    assert "34 Another Road" not in reply  # unchanged details do not fill the receipt


@pytest.mark.parametrize("language", ["en", "zh"])
def test_address_and_location_changes_are_not_dropped_by_identity_sentence(language):
    case = example(language, sponsor_name="Jian Example", sponsor_relationship="father",
                   sponsor_address="34 Another Road, Hong Kong", sponsor_is_in_uk="False")
    reply = change_acknowledgement(case)
    assert "34 Another Road, Hong Kong" in reply
    assert ("answer about your sponsor's location" if language == "en" else "资助人是否在英国的回答") in reply
    assert ("do not live" if language == "en" else "不住在") not in reply


def test_name_spelling_correction_does_not_claim_the_payer_changed():
    reply = change_acknowledgement(example(sponsor_name="Jian Example"))
    assert "corrected your sponsor's name" in reply
    assert "will now" not in reply


def test_mixed_non_sponsor_correction_remains_visible():
    reply = change_acknowledgement(example(sponsor_name="Jian Example", estimated_trip_cost_gbp="2000"))
    assert "Jian Example" in reply and "2000" in reply


def test_cleared_identity_is_not_reinvented_for_fluent_copy():
    case = example(sponsor_relationship="father")
    case.profile.sponsor_name = None
    assert "Jian Example" not in change_acknowledgement(case)
