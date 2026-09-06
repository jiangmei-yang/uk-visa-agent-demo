import pytest

from visa_agent.domain.models import Case, CaseProfile
from visa_agent.workflow.conversation import change_acknowledgement, received_context


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("relationship", ["parent", "parents"])
def test_boolean_location_receipt_does_not_assert_residence(language, value, relationship):
    case = Case(id="fictional", external_thread_id="fictional",
        applicant_contact="fictional@example.test", policy_version="synthetic",
        customer_language=language, profile=CaseProfile(funding_source="personal_sponsor",
            sponsor_relationship=relationship, sponsor_is_in_uk=value),
        latest_received_facts={"sponsor_is_in_uk": str(value)})
    before = case.model_dump(mode="json")
    reply = received_context(case)
    assert ("answer about your sponsor's location" in reply if language == "en" else "是否在英国的回答" in reply)
    assert not any(word in reply for word in ("lives", "live in", "resident", "住在", "居住"))
    assert case.model_dump(mode="json") == before


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("value", [True, False, None])
@pytest.mark.parametrize("relationship", [None, "mother"])
def test_sponsor_name_correction_does_not_strengthen_location_into_residence(language, value, relationship):
    case = Case(id="fictional", external_thread_id="fictional",
        applicant_contact="fictional@example.test", policy_version="synthetic",
        customer_language=language, profile=CaseProfile(funding_source="personal_sponsor",
            sponsor_name="Mina Example", sponsor_is_in_uk=value, sponsor_relationship=relationship),
        latest_changes={"sponsor_name": "Mina Example", "sponsor_is_in_uk": str(value)})
    reply = change_acknowledgement(case)
    assert "Mina Example" in reply
    assert not any(word in reply for word in ("lives", "live in", "resident", "住在", "居住"))
