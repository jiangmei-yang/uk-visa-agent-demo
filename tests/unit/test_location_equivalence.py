from pathlib import Path

import pytest

from visa_agent.domain.locations import location_key
from visa_agent.domain.models import Case
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import build_requirements


@pytest.mark.parametrize("passport,location,expected", [
    ("中国", "China", False), ("China", "中國", False),
    ("新加坡", "Singapore", False), ("China", "香港", True),
    ("中国", "Hong Kong", True), ("Unknown A", "Unknown B", True),
])
def test_location_comparison_preserves_meaning_and_original_values(passport, location, expected):
    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test",
                policy_version="v")
    case.profile.nationality_country = passport
    case.profile.application_country = location
    before = case.profile.model_dump_json()
    requirements = build_requirements(case, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")))
    assert next(item for item in requirements if item.id == "legal_residence").applicable is expected
    assert case.profile.model_dump_json() == before


def test_missing_location_or_work_city_is_not_inferred_as_a_country():
    assert location_key(None) is None
    assert location_key("深圳") != location_key("China")
    assert location_key("香港") != location_key("China")
