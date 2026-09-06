import pytest
from pydantic import ValidationError

from visa_agent.domain.sponsor_location import (
    SponsorLocationStatement,
    conflicting_location_dimensions,
    parse_sponsor_location_statements,
)


@pytest.mark.parametrize(("body", "expected"), [
    ("My sponsor does not live in the UK but is in the UK now.", [("residence", False), ("current_presence", True)]),
    ("My sponsor lives in the UK but is not in the UK now.", [("residence", True), ("current_presence", False)]),
    ("我的资助人不住在英国，但现在在英国。", [("residence", False), ("current_presence", True)]),
    ("我的资助人住在英国，但现在不在英国。", [("residence", True), ("current_presence", False)]),
    ("My sponsor is in the UK.", [("current_presence", True)]),
    ("My sponsor doesn't live in the UK.", [("residence", False)]),
    ("My sponsor doesn’t live in the UK.", [("residence", False)]),
    ("我的资助人不在英国。", [("current_presence", False)]),
])
def test_distinct_dimensions_keep_the_original_source_and_identity(body, expected):
    result = parse_sponsor_location_statements(body, source_event_id="original-7",
        sponsor_name="Mina Example", sponsor_relationship="mother")
    assert [(item.dimension, item.value) for item in result] == expected
    assert all(item.source_event_id == "original-7" and item.source_excerpt in body
               and item.sponsor_name == "Mina Example" and item.sponsor_relationship == "mother"
               for item in result)
    assert not conflicting_location_dimensions(result)


@pytest.mark.parametrize("body", [
    "My sponsor used to live in the UK.", "If my sponsor is in the UK.",
    "My friend said my sponsor lives in the UK.", '"My sponsor is in the UK."',
    "Does my sponsor live in the UK?", "My sister is in the UK.",
    "My sponsors are in the UK.", "My sponsor will be in the UK.",
    "My sponsor is in the UK but my sister is not in the UK.",
    "My sponsor is in the UK but I am not sure.",
    "如果我的资助人住在英国。", "朋友说我的资助人在英国。",
    "我的资助人在英国，但我妹妹不在英国。", "我的资助人以前住在英国。",
    "我的资助人在英国，但可能已经离开。", "我的资助人是英国公民。",
])
def test_unknown_other_person_historical_or_legal_status_is_not_location(body):
    assert parse_sponsor_location_statements(body, source_event_id="original") == []


def test_same_dimension_conflict_is_retained_not_overwritten():
    rows = parse_sponsor_location_statements(
        "My sponsor lives in the UK. My sponsor does not live in the UK.", source_event_id="original")
    assert len(rows) == 2
    assert conflicting_location_dimensions(rows) == {"residence"}


def test_legacy_boolean_cannot_deserialize_into_typed_location_evidence():
    with pytest.raises(ValidationError):
        SponsorLocationStatement.model_validate({"sponsor_is_in_uk": False})
    with pytest.raises(ValueError):
        parse_sponsor_location_statements("My sponsor is in the UK.", source_event_id=" ")
