import json

import pytest
from test_sponsor_location_summary import example

from visa_agent.delivery.pack import _add_sponsor_location_answers
from visa_agent.workflow.sponsor_location_summary import sponsor_location_snapshot


@pytest.mark.parametrize("state", ["complete", "unknown", "conflicting", "replacement", "self"])
def test_export_keeps_current_source_dimensions_without_competing_legacy_claim(state):
    case = example("zh")
    if state == "unknown":
        case.sponsor_location_statements.pop()
    elif state == "conflicting":
        case.sponsor_location_statements.append(case.sponsor_location_statements[0].model_copy(update={"value": True}))
    elif state == "replacement":
        case.sponsor_location_epoch += 1
    elif state == "self":
        case.profile.funding_source = "self"
    before = case.model_dump_json()
    answers = {"profile": case.profile.model_dump(mode="json"), "facts": [
        {"key": "sponsor_is_in_uk", "value": False}, {"key": "full_name", "value": "Example"}]}
    _add_sponsor_location_answers(answers, case)
    exported = json.loads(json.dumps(answers))
    assert "sponsor_is_in_uk" not in exported["profile"]
    assert exported["facts"] == [{"key": "full_name", "value": "Example"}]
    if state == "self":
        assert "sponsor_location" not in exported
    else:
        location = exported["sponsor_location"]
        assert location["basis"] == "applicant_reported_not_legal_status_verification"
        residence = location["dimensions"]["residence"]
        presence = location["dimensions"]["current_presence"]
        if state == "complete":
            assert residence["value"] is False and presence["value"] is True
            assert residence["state"] == presence["state"] == "reported"
            assert residence["sources"][0]["source_event_id"] == "private-source-id"
        elif state == "unknown":
            assert presence == {"state": "unknown", "value": None, "sources": []}
        elif state == "replacement":
            assert residence == presence == {"state": "unknown", "value": None, "sources": []}
        elif state == "conflicting":
            assert residence["state"] == "conflicting" and residence["value"] is None
            assert {item["reported_value"] for item in residence["sources"]} == {False, True}
        assert "review" not in location and "actor" not in location
    assert case.model_dump_json() == before


def test_legacy_export_is_not_silently_migrated_into_new_facts():
    case = example("en")
    case.sponsor_location_statements = []
    answers = {"profile": case.profile.model_dump(mode="json"), "facts": []}
    before = json.dumps(answers)
    _add_sponsor_location_answers(answers, case)
    assert json.dumps(answers) == before
    assert sponsor_location_snapshot(case) is None
