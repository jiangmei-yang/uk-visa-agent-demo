"""Current employer source ownership, not corporate/telephone verification."""

import pytest

from visa_agent.domain.employer_evidence import employer_detail_is_grounded


@pytest.mark.parametrize(("field", "value", "body"), [
    ("employer_name", "Northstar Ltd", "My employer is Northstar Ltd."),
    ("employer_name", "Maybe Ltd", "My employer is Maybe Ltd."),
    ("employer_address", "12 Example Road, Hong Kong", "My employer's address is 12 Example Road, Hong Kong."),
    ("employer_phone", "+852 2000 1234", "My employer's telephone number is +852 2000 1234."),
    ("employer_name", "虚构公司", "我的雇主是虚构公司。"),
    ("employer_address", "深圳市示例路12号", "我的雇主的地址是深圳市示例路12号。"),
    ("employer_phone", "020-12345678", "我的雇主的电话是020-12345678。"),
])
def test_literal_current_employer_details(field, value, body):
    assert employer_detail_is_grounded(field, value, body, body)


@pytest.mark.parametrize("body", [
    "My friend's employer is Northstar Ltd.",
    "My former employer is Northstar Ltd.",
    "If my employer is Northstar Ltd, what would I need?",
    "Previously, my employer is Northstar Ltd.",
    "My employer is not Northstar Ltd.",
    'Please translate "My employer is Northstar Ltd."',
    "My friend said my employer is Northstar Ltd.",
    "Northstar Ltd",
    "我的朋友说：My employer is Northstar Ltd.",
])
def test_other_or_noncurrent_employers_do_not_supply_current_fact(body):
    assert not employer_detail_is_grounded("employer_name", "Northstar Ltd", body, body)


def test_uncertainty_cannot_be_hidden_in_the_proposed_company_name():
    value = "Northstar Ltd, but I am not sure"
    body = f"My employer is {value}."
    assert not employer_detail_is_grounded("employer_name", value, body, body)


def test_source_must_keep_owner_and_phone_cannot_gain_country_code():
    body = "My employer's phone number is 2000 1234."
    assert not employer_detail_is_grounded("employer_phone", "+852 2000 1234", body, body)
    assert not employer_detail_is_grounded("employer_phone", "2000 1234", "2000 1234", body)


def test_home_address_is_not_employer_address():
    body = "My home address is 12 Example Road, Hong Kong."
    assert not employer_detail_is_grounded("employer_address", "12 Example Road, Hong Kong", body, body)


@pytest.mark.parametrize("body", ["My friend's employer is Northstar Ltd.",
                                  "If my employer is Northstar Ltd, what would I need?",
                                  "My former employer is Northstar Ltd."])
def test_full_model_guard_still_rejects_other_hypothetical_and_former_employers(body):
    from visa_agent.domain.models import InboundEvent
    from visa_agent.llm.guarded import validate_case_patch
    from visa_agent.llm.ports import CasePatch, FactUpdate

    event = InboundEvent(id="fictional", external_thread_id="fictional", channel="gmail",
                         sender="fictional@example.test", subject="Fictional preparation", body=body,
                         received_at="2026-09-07T00:00:00+00:00")
    patch = CasePatch(updates=[FactUpdate(field="employer_name", value="Northstar Ltd",
                     source_excerpt=body, confidence=1)], ambiguities=[])
    assert not validate_case_patch(event, patch).updates
