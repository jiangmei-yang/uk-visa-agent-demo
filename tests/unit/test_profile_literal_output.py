"""Formatting must not silently rewrite applicant-provided strings."""

import pytest

from visa_agent.delivery.pack import _profile_rows
from visa_agent.domain.models import Case, CaseProfile


@pytest.mark.parametrize(("field", "value"), [
    ("employer_name", "ACME_Labs LTD"),
    ("employer_address", "Unit A_B, 12 Sample Road"),
    ("current_address", "Building A_B, 34 Sample Road"),
    ("uk_accommodation", "Hotel A_B London"),
    ("full_name", "Alex_SAMPLE"),
    ("sponsor_name", "Mei_SAMPLE"),
    ("sponsor_address", "Unit C_D, 56 Sample Road"),
])
def test_literal_strings_survive_summary_without_case_or_separator_rewrite(field, value):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic", profile=CaseProfile(funding_source="personal_sponsor", **{field: value}))
    before = case.model_dump(mode="json")
    assert any(row.endswith(": " + value) for row in _profile_rows(case))
    assert case.model_dump(mode="json") == before


def test_known_enum_labels_still_render_readably():
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="synthetic", profile=CaseProfile(occupation_status="self_employed", funding_source="employer_or_school"))
    rows = _profile_rows(case)
    assert "Occupation status: Self employed" in rows
    assert "Funding source: Employer or school" in rows
