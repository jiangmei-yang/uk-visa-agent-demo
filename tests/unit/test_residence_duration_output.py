import pytest

from visa_agent.delivery.pack import _profile_rows
from visa_agent.domain.models import Case, CaseProfile
from visa_agent.workflow.conversation import fact_label


@pytest.mark.parametrize("value,address,expected", [
    ("about two years", "1 Example Road", "about two years"),
    ("大概两年半", "1 Example Road", "大概两年半"),
    (None, "1 Example Road", "Deferred for checking - not yet supplied"),
    (None, "2 Example Road", "Not provided"),
])
def test_profile_rows_preserve_precision_and_current_home_deferral_binding(value, address, expected):
    case = Case(id="fictional-output", external_thread_id="fictional-output",
        applicant_contact="fictional@example.test", policy_version="test",
        profile=CaseProfile(current_address=address, current_address_duration=value),
        deferred_fields=["current_address_duration"],
        residence_duration_deferrals=[{"address": "1 Example Road", "source_event_id": "synthetic-source"}])
    rows = _profile_rows(case)
    assert f"Time living at current home: {expected}" in rows
    assert rows.index(f"Time living at current home: {expected}") == rows.index(f"Current home address: {address}") + 1
    assert not any("synthetic-source" in row for row in rows)
    assert fact_label(case, "current_address_duration") == "Time living at your current home"
    case.customer_language = "zh"
    assert fact_label(case, "current_address_duration") == "在现住址居住多久"
