"""Retained failed real extractions through reopened state and captured SENT mail."""

import json
import runpy
from pathlib import Path

from test_consultant_value import Conversation

from visa_agent.llm.ports import CasePatch


def test_real_v5_supplement_and_correction_no_longer_disappear(tmp_path):
    report = json.loads(Path("eval_output/application_record_intake_2026-09-07-v5.json").read_text())
    probe = runpy.run_path("scripts/consultant_journey_probe.py")
    dialogue = Conversation(tmp_path)
    original_declaration = None
    for row in report["results"]:
        turn = dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
        spec = dict(report["scenarios"][row["journey"]][row["turn"] - 1])
        if "passport_source_event" in spec:
            spec["passport_source_event"] = turn.event.id
        checks = probe["check_turn"](spec, turn.case, turn.body)
        assert all(checks.values()), (row["turn"], checks, turn.body)
        if row["turn"] == 5:
            original_declaration = turn.case.application_records.latest_declarations()["uk_contact"]
        if row["turn"] >= 6:
            ledger = turn.case.application_records
            assert ledger.latest_declarations()["uk_contact"] == original_declaration
            contact = next(record for record in ledger.current().values() if record.kind == "uk_contact")
            assert contact.fields["passport_number"].source_event_id == turn.event.id
            assert turn.body.strip() != "好的，已有资料会保留。有新安排或材料时，直接接着回复就好。"
            assert not turn.case.profile_confirmed and not turn.case.final_summary_confirmed
            assert turn.case.delivery_path is None
