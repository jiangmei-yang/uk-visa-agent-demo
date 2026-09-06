import pytest

from visa_agent.delivery.pack import _profile_rows
from visa_agent.domain.models import Case, CaseProfile


@pytest.mark.parametrize(("occupation", "expected"), [
    ("student", "Not applicable to the recorded occupation"),
    ("self_employed", "Not applicable to the recorded occupation"),
    (None, "Applicability not yet established"),
    ("employed", "Not provided"),
])
def test_employer_display_does_not_equate_missing_with_inapplicable(occupation, expected):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
                policy_version="synthetic", profile=CaseProfile(occupation_status=occupation))
    before = case.model_dump(mode="json")
    rows = _profile_rows(case)
    assert f"Current employer: {expected}" in rows
    assert f"Employer address: {expected}" in rows
    assert f"Employer contact number: {expected}" in rows
    assert case.model_dump(mode="json") == before


@pytest.mark.parametrize("owner", ["Northstar Ltd", "Old Company"])
def test_deferral_display_is_bound_to_current_employer(owner):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic", profile=CaseProfile(occupation_status="employed", employer_name="Northstar Ltd"),
        deferred_fields=["employer_phone"],
        employer_detail_deferrals=[{"field": "employer_phone", "employer_name": owner, "source_event_id": "fictional-event"}])
    expected = "Deferred for checking - not yet supplied" if owner == "Northstar Ltd" else "Not provided"
    assert f"Employer contact number: {expected}" in _profile_rows(case)


def test_inconsistent_supplied_legacy_detail_is_not_hidden():
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic", profile=CaseProfile(occupation_status="student", employer_name="Supplied Company"))
    assert "Current employer: Supplied Company" in _profile_rows(case)
