"""Actual captured replies: trim duplicate receipts, not facts or release gates."""

import json
from pathlib import Path

import pytest
from test_consultant_value import Conversation

from visa_agent.llm.ports import CasePatch
from visa_agent.workflow.conversation import received_context


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Editorial replay cannot call Gmail or a model")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.socket.connect", deny)


@pytest.mark.parametrize("journey", [
    "student-obstacle-and-change", "self-employed-host-and-funds",
    "zh-durable-pacing", "en-durable-pacing",
])
def test_initial_receipt_does_not_repeat_occupation_explained_by_advice(tmp_path, journey):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v6.json").read_text())
    row = next(r for r in report["results"] if r["journey"] == journey and r["turn"] == 1)
    result = Conversation(tmp_path).turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
    assert result.case.proactive_guidance_offered
    # Historical scenarios supplied no current-home duration. Keep the original
    # report immutable and require the new field to remain explicitly empty.
    expected = {"employer_name": None, "employer_address": None, "employer_phone": None, "current_address_duration": None, "sponsor_address": None, **row["profile"]}
    assert result.case.profile.model_dump(mode="json") == expected
    snapshot = result.case.model_dump(mode="json")
    receipt = received_context(result.case)
    assert result.case.model_dump(mode="json") == snapshot
    assert receipt in result.body
    assert len(receipt) <= (40 if result.case.customer_language == "zh" else 90)
    if result.case.customer_language == "zh":
        assert "中国护照" in receipt and "香港" in receipt
        assert "目前在读书" not in receipt
        assert "你还在读书" in result.body and "在读证明" in result.body
    else:
        assert "Chinese passport" in receipt and "Hong Kong" in receipt
        assert "you're self-employed" not in receipt
        assert "For your own business" in result.body and "invoices" in result.body
    assert len(result.body) < len(row["reply"])
    # Removing a repeated sentence cannot remove its source from the case.
    assert result.case.latest_received_facts["occupation_status"] == expected["occupation_status"]
    assert result.case.active_evidence("occupation_status")
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.delivery_path is None


@pytest.mark.parametrize("correction", [False, True])
def test_receipt_is_not_trimmed_when_advice_is_unselected_or_facts_are_corrected(tmp_path, correction):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v6.json").read_text())
    row = report["results"][0]
    result = Conversation(tmp_path).turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
    case = result.case.model_copy(deep=True)
    if correction:
        case.latest_changes = {"occupation_status": {"old": "employed", "new": "student"}}
    else:
        case.proactive_guidance_offered = False
    assert "目前在读书" in received_context(case)


def test_saved_name_and_birthday_turn_has_coordinated_actual_reply(tmp_path):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-07-v9.json").read_text())
    conversation = Conversation(tmp_path)
    rows = [row for row in report["results"]
            if row["journey"] == "en-durable-pacing" and row["turn"] <= 2]
    assert len(rows) == 2
    for row in rows:
        result = conversation.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
    assert "I've recorded the name in your passport and your date of birth." in result.body
    assert "passport, your date of birth" not in result.body
    assert result.case.profile.full_name == rows[-1]["profile"]["full_name"]
    assert result.case.profile.date_of_birth.isoformat() == rows[-1]["profile"]["date_of_birth"]
    assert result.case.delivery_path is None
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
