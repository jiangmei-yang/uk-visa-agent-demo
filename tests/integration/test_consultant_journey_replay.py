"""Replay the retained failed provider run; no fresh model calls or mailbox I/O."""

import json
import runpy
from pathlib import Path

import pytest
from test_consultant_value import Conversation

from visa_agent.llm.ports import CasePatch

REPORT = json.loads(Path("eval_output/consultant_journey_2026-09-06-v1.json").read_text())
PROBE = runpy.run_path("scripts/consultant_journey_probe.py")


@pytest.mark.parametrize("journey", REPORT["scenarios"])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_saved_provider_facts_and_controls_survive_reopening(tmp_path, journey, version):
    report = json.loads(Path(f"eval_output/consultant_journey_2026-09-06-{version}.json").read_text())
    dialogue = Conversation(tmp_path)
    for row in report["results"]:
        if row["journey"] != journey:
            continue
        result = dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
        spec = PROBE["SCENARIOS"][journey][row["turn"] - 1]
        checks = PROBE["check_turn"](spec, result.case, result.body)
        assert all(checks.values()), (row["turn"], checks, result.body)
        actual = result.case.profile.model_dump(mode="json")
        for field, expected in spec.get("profile", {}).items():
            assert actual[field] == expected, (row["turn"], field, actual[field])
        if spec.get("deferred_dates"):
            fields = {"planned_arrival_date", "planned_departure_date"}
            assert fields <= set(result.case.deferred_fields)
            assert not fields.intersection(result.case.last_requested_fields)
        if "paused" in spec:
            assert result.case.preparation_paused == spec["paused"]
        if journey == "student-obstacle-and-change" and row["turn"] == 6:
            assert "这次由谁资助" not in result.body
            assert "母亲" in result.body and "姓名" in result.body
            assert "具体费用" in result.body and "怎样支付" in result.body


@pytest.mark.parametrize(("field", "value", "statement"), [
    ("occupation_status", "student", "在香港念硕士"),
    ("visit_purpose", "tourism", "去伦敦玩几天"),
    ("visit_purpose", "tourism", "applying here for a short UK holiday"),
    ("funding_source", "self", "钱自己出"),
    ("funding_source", "self", "I'm paying from my savings"),
    ("application_country", "Hong Kong", "准备在香港办"),
])
@pytest.mark.parametrize("wrapper", [
    "如果我{statement}，要怎么准备？",
    "朋友说：{statement}。这是朋友的情况，不是我的。",
    'Please translate "{statement}"; this is not my situation.',
])
def test_new_wording_cannot_escape_hypothetical_report_or_quote(tmp_path, field, value, statement, wrapper):
    body = wrapper.format(statement=statement)
    patch = CasePatch.model_validate({"updates": [{"field": field, "value": value,
        "source_excerpt": statement, "confidence": 1}], "ambiguities": []})
    result = Conversation(tmp_path).turn(body, patch)
    assert getattr(result.case.profile, field) is None


@pytest.mark.parametrize("body", [
    "If I'm busy, pause this for now.",
    'My friend said: "Pause this for now."',
    "Do not pause this for now.",
    "Pause this document for now.",
    "Can you explain pause this for now?",
    "Tomorrow, pause this for now.",
    "Pause this for now for my friend's visa application.",
])
def test_deictic_pause_does_not_bypass_scope(tmp_path, body):
    patch = CasePatch.model_validate({"updates": [], "ambiguities": [],
        "preparation_intent": {"action": "pause", "source_excerpt": body, "confidence": 1}})
    result = Conversation(tmp_path).turn(body, patch)
    assert not result.case.preparation_paused


@pytest.mark.parametrize("body", [
    "I am not paying from my savings.", "钱不是自己出。",
    "我不是在香港念硕士。", "我不打算去伦敦玩几天。",
    "I am not applying here for a short UK holiday.",
])
def test_negative_natural_facts_are_not_positive_updates(tmp_path, body):
    fields = {"occupation_status": "student", "visit_purpose": "tourism", "funding_source": "self"}
    patch = CasePatch.model_validate({"updates": [{"field": f, "value": v,
        "source_excerpt": body, "confidence": 1} for f, v in fields.items()], "ambiguities": []})
    case = Conversation(tmp_path).turn(body, patch).case
    assert all(getattr(case.profile, field) is None for field in fields)
