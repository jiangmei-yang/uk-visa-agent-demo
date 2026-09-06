import pytest

from visa_agent.delivery.pack import _profile_rows
from visa_agent.domain.models import Case, CaseProfile


@pytest.mark.parametrize(("funding", "expected"), [
    (None, "Applicability not yet established"),
    ("unrecognized_legacy_value", "Applicability not yet established"),
    ("self", "Not applicable to the recorded funding source"),
    ("employer_or_school", "Not applicable to the recorded funding source"),
    ("personal_sponsor", "Not provided"),
])
def test_missing_sponsor_display_depends_on_known_funding(funding, expected):
    case = Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", policy_version="synthetic",
                profile=CaseProfile(funding_source=funding))
    before = case.model_dump(mode="json")
    rows = _profile_rows(case)
    for label in ("Sponsor name", "Sponsor address", "Sponsor relationship", "Sponsor is in the UK"):
        assert f"{label}: {expected}" in rows
    assert case.model_dump(mode="json") == before


@pytest.mark.parametrize("funding", [None, "self", "employer_or_school", "personal_sponsor"])
@pytest.mark.parametrize("in_uk", [False, True])
def test_supplied_sponsor_details_are_never_hidden_by_display(funding, in_uk):
    case = Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", policy_version="synthetic",
                profile=CaseProfile(funding_source=funding, sponsor_name="Mina Example",
                    sponsor_address="Unit_B, Example Road", sponsor_relationship="parent",
                    sponsor_is_in_uk=in_uk))
    before = case.model_dump(mode="json")
    rows = _profile_rows(case)
    assert "Sponsor name: Mina Example" in rows
    assert "Sponsor address: Unit_B, Example Road" in rows
    assert "Sponsor relationship: parent" in rows
    assert f"Sponsor is in the UK: {'Yes' if in_uk else 'No'}" in rows
    assert case.model_dump(mode="json") == before
