"""Real saved proposals, local reopened workflow and captured sends; no live calls."""

import json
from pathlib import Path

from test_consultant_value import Conversation

from visa_agent.domain.models import CaseStatus
from visa_agent.llm.ports import CasePatch


def test_real_residence_corrections_move_and_chinese_supplement_retain_facts_and_receipts(tmp_path):
    report = json.loads(Path("eval_output/residence_duration_2026-09-07-v2.json").read_text())
    dialogue = Conversation(tmp_path)
    expected = ["about two years", "about three years", None, "大概两个月"]
    for index, row in enumerate(report["results"]):
        result = dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
        assert result.case.profile.current_address_duration == expected[index]
        assert result.case.status != CaseStatus.HUMAN_REVIEW_REQUIRED
        assert not result.case.delivery_path and not result.case.final_summary_confirmed
        if expected[index] is not None:
            assert expected[index] in result.body
            assert result.case.active_evidence("current_address_duration")[0].source_event_id == result.event.id
        else:
            assert not result.case.active_evidence("current_address_duration")
        assert {"planned_arrival_date", "planned_departure_date"} <= set(result.case.deferred_fields)
