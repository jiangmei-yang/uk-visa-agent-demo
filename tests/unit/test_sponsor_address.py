"""Literal personal-sponsor address foundation, not postal/identity verification."""

import pytest

from visa_agent.domain.sponsor_evidence import sponsor_address_is_grounded


@pytest.mark.parametrize(("body", "value"), [
    ("My sponsor's address is 12 Example Road, Hong Kong.", "12 Example Road, Hong Kong"),
    ("My sponsor’s current address is Willow Cottage, Example Village.", "Willow Cottage, Example Village"),
    ("我的资助人的住址是深圳市示例路12号。", "深圳市示例路12号"),
    ("我资助人的地址：深圳市示例路12号。", "深圳市示例路12号"),
])
def test_explicit_current_owner_and_literal_value(body, value):
    assert sponsor_address_is_grounded(value, body, body)


@pytest.mark.parametrize("body", [
    "My home address is 12 Example Road, Hong Kong.",
    "My host's address is 12 Example Road, Hong Kong.",
    "My mother's address is 12 Example Road, Hong Kong.",
    "My sponsor's address is 10 Other Road, London, and my home address is 12 Example Road, Hong Kong.",
    "If my sponsor's address is 12 Example Road, Hong Kong, would that help?",
    "My sponsor's address is not 12 Example Road, Hong Kong.",
    'Please translate "My sponsor\'s address is 12 Example Road, Hong Kong."',
    "My friend said: My sponsor's address is 12 Example Road, Hong Kong.",
    "My sponsor's address is 12 Example Road, Hong Kong, but I am not sure.",
    "12 Example Road, Hong Kong",
])
def test_wrong_owner_uncertainty_or_clipped_source_cannot_supply_address(body):
    assert not sponsor_address_is_grounded("12 Example Road, Hong Kong", "12 Example Road, Hong Kong", body)


def test_no_invented_address_components():
    body = "My sponsor's address is Hong Kong."
    assert not sponsor_address_is_grounded("12 Example Road, Hong Kong", body, body)


def test_independent_current_statement_can_follow_old_address():
    body = "My sponsor used to live elsewhere. My sponsor's address is 12 Example Road, Hong Kong."
    excerpt = "My sponsor's address is 12 Example Road, Hong Kong."
    assert sponsor_address_is_grounded("12 Example Road, Hong Kong", excerpt, body)


def test_source_must_retain_the_owner_not_only_the_address():
    body = "My sponsor's address is 12 Example Road, Hong Kong."
    assert not sponsor_address_is_grounded("12 Example Road, Hong Kong", "12 Example Road, Hong Kong", body)


def test_quoted_cjk_statement_is_not_fresh_applicant_evidence():
    body = "「我的资助人的地址是深圳市示例路12号。」"
    assert not sponsor_address_is_grounded("深圳市示例路12号", "我的资助人的地址是深圳市示例路12号", body)


def test_customer_summary_keeps_home_separate_and_hides_inapplicable_sponsor():
    from visa_agent.delivery.pack import _profile_rows
    from visa_agent.domain.models import Case, CaseProfile

    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
                policy_version="2026-02-25", profile=CaseProfile(
                    funding_source="personal_sponsor", sponsor_address="12 Example Road, Hong Kong"))
    rows = _profile_rows(case)
    assert "Sponsor address: 12 Example Road, Hong Kong" in rows
    assert "Current home address: Not provided" in rows
    case.profile.funding_source = "self"
    assert "Sponsor address: Not applicable" in _profile_rows(case)
    assert all("12 Example Road" not in row for row in _profile_rows(case))
