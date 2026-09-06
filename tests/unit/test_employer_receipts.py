"""Receipts must reflect supplied facts without changing their authority."""

import pytest

from visa_agent.domain.models import Case
from visa_agent.workflow.conversation import change_acknowledgement, received_context


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(("field", "value"), [
    ("employer_name", "Northstar Ltd"),
    ("employer_address", "12 Example Road, Hong Kong"),
    ("employer_phone", "+852 2000 1234"),
])
def test_new_and_corrected_employer_receipts_keep_exact_values(language, field, value):
    case = Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", customer_language=language,
                policy_version="synthetic-receipt-test")
    case.latest_received_facts = {field: value}
    received = received_context(case)
    assert value in received
    assert "starting point" not in received
    case.latest_received_facts = {}
    case.latest_changes = {field: value}
    correction = change_acknowledgement(case)
    assert value in correction
    assert "Employer Name:" not in correction
    assert not case.profile_confirmed and not case.final_summary_confirmed
