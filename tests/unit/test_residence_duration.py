import pytest

from visa_agent.domain.residence_duration import residence_duration_is_grounded


@pytest.mark.parametrize("body,value", [
    ("I've lived at my current address for about two years.", "about two years"),
    ("I have lived at my current home for 3 years and 4 months.", "3 years and 4 months"),
    ("我在现在的住址住了大概两年。", "大概两年"),
    ("我在这个地址已经住了两年半。", "两年半"),
])
def test_preserves_exact_customer_precision(body, value):
    assert residence_duration_is_grounded(value, body, body)


@pytest.mark.parametrize("body,value", [
    ("I have lived in Hong Kong for two years.", "two years"),
    ("My mother has lived at her current address for two years.", "two years"),
    ("If I've lived at my current address for two years, is that enough?", "two years"),
    ("I've lived at my current address for two years?", "two years"),
    ('Please translate "I have lived at my current address for two years".', "two years"),
    ("I've lived at my current address for one year, my mother for two years.", "two years"),
    ("I have lived at my previous address for two years.", "two years"),
    ("我在现在的住址没住两年。", "两年"),
    ("我在现在的住址住了大概两年。", "24 months"),
    ("我在现在的住址住了大概两年。", "2024-09-07"),
])
def test_wrong_role_hypothetical_questions_and_inferred_values_are_not_facts(body, value):
    assert not residence_duration_is_grounded(value, body, body)


@pytest.mark.parametrize("value,expected", [("about three years", True), ("two", False), ("two years", False)])
def test_trailing_rejected_old_duration_does_not_negate_the_affirmative_correction(value, expected):
    body = "Correction: I've lived at my current address for about three years, not two years."
    assert residence_duration_is_grounded(value, value, body) is expected


@pytest.mark.parametrize("body", [
    "I have not lived at my current address for about three years, not two.",
    "If I've lived at my current address for about three years, not two.",
    "I've lived at my current address for about three years, not two?",
])
def test_contrast_does_not_bypass_negation_hypothetical_or_question(body):
    assert not residence_duration_is_grounded("about three years", "about three years", body)
