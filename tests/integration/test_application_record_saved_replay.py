"""Replay immutable failed-provider output; these tests make zero provider calls."""

import hashlib
import json
from pathlib import Path

import pytest
from test_consultant_value import Conversation

from visa_agent.llm.ports import CasePatch

REPORT = Path("eval_output/application_record_intake_2026-09-06-v1.json")
REPORT_SHA256 = "f1d18254a940428bb9c57ccbddf88589864cc2b5ef5bfc86cbeb3d21770a8ffb"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Saved record replay must not call a model or mailbox")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def test_failed_provider_record_is_immutable_and_does_not_claim_four_paid_turns():
    raw = REPORT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REPORT_SHA256
    report = json.loads(raw)
    assert report["completed"] and not report["all_passed"]
    assert report["git_head"] == "f131c4d08338daf24c160f0be50d3ab68614e92d"
    assert [row["passed"] for row in report["results"]] == [True, True, False, False]
    assert sum(len(row["usage"]) for row in report["results"]) == 3
    assert report["results"][3]["raw_model_content"] is None
    assert report["results"][3]["error_type"] == "StopIteration"
    assert any(path.endswith("NotoSansSC-Regular.ttf") for path in report["source_sha256"])


def test_original_three_provider_outputs_now_preserve_uncertainty_and_unchanged_field_source(tmp_path):
    report = json.loads(REPORT.read_text())
    dialogue = Conversation(tmp_path)
    results = []
    for row in report["results"][:3]:
        results.append(dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"])))
    first, corrected, uncertain = results
    japan = next(r for r in corrected.case.application_records.current().values() if r.fields.get("country") and r.fields["country"].value == "日本")
    assert japan.fields["country"].source_event_id == first.event.id
    assert japan.fields["period"].source_event_id == corrected.event.id
    assert japan.fields["period"].value == "2023年秋天"
    assert uncertain.case.application_records.collection_state("travel") == "unknown"
    assert len(uncertain.case.application_records.current()) == 3
    assert "记不清的部分先留待核实" in uncertain.body
    assert not uncertain.case.profile_confirmed and not uncertain.case.final_summary_confirmed
    assert uncertain.case.delivery_path is None


def test_second_failed_provider_output_uses_literal_reference_and_does_not_rewrite_old_fact_sources(tmp_path):
    path = Path("eval_output/application_record_intake_2026-09-06-v2.json")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "27a563296e3129cb1996ebd2b1c40da1d3c3f13619d865810a1061c5662db93d"
    report = json.loads(raw)
    assert not report["all_passed"] and sum(len(row["usage"]) for row in report["results"]) == 2
    assert report["results"][1]["checks"]["no_extraction_fallback"] is False
    dialogue = Conversation(tmp_path)
    first_row, second_row = report["results"][:2]
    first = dialogue.turn(first_row["input"], CasePatch.model_validate_json(first_row["raw_model_content"]))
    second = dialogue.turn(second_row["input"], CasePatch.model_validate_json(second_row["raw_model_content"]))
    japan = next(r for r in second.case.application_records.current().values() if r.fields.get("country") and r.fields["country"].value == "日本")
    assert japan.fields["period"].value == "2023年秋天"
    assert japan.fields["period"].source_event_id == second.event.id
    assert japan.fields["country"].source_event_id == japan.fields["purpose"].source_event_id == first.event.id
    assert "已按你的更正" in second.body
