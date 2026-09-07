import json
from pathlib import Path

import pytest
from test_consultant_value import Conversation

from visa_agent.llm.ports import CasePatch
from visa_agent.workflow.explicit_visit_purpose import explicit_holiday_statement


def test_actual_omission_recovers_source_without_granting_route_or_confirmation(tmp_path):
    report = json.loads(Path("eval_output/sponsor_address_2026-09-07-v4.json").read_text())
    row = report["results"][0]
    patch = CasePatch.model_validate_json(row["raw_model_content"])
    assert not any(item.field == "visit_purpose" for item in patch.updates)
    result = Conversation(tmp_path).turn(row["input"], patch)
    assert result.case.profile.visit_purpose == "tourism"
    evidence = result.case.active_evidence("visit_purpose")
    assert len(evidence) == 1 and evidence[0].source_event_id == result.event.id
    assert evidence[0].source_excerpt == "I'm preparing for a UK holiday"
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.delivery_path is None


@pytest.mark.parametrize("body", [
    'Please translate "I am preparing for a UK holiday".',
    "If I am preparing for a UK holiday, what documents do I need?",
    "My friend is preparing for a UK holiday.",
    "I am not preparing for a UK holiday.",
    "I am preparing for a UK holiday and a business meeting.",
    "I am preparing for a UK holiday. I also have a conference.",
    "我朋友准备去英国旅游。", "如果我准备去英国旅游，需要什么？",
])
def test_other_people_conditions_negation_and_mixed_purposes_are_not_recovered(body):
    assert explicit_holiday_statement(body) is None


@pytest.mark.parametrize("body", ["I'm preparing for a UK holiday.", "I am planning a vacation in the UK.", "我打算去英国旅游。"])
def test_literal_first_person_statements_are_preserved(body):
    assert explicit_holiday_statement(body) in body
