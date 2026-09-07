"""Saved real-model proposals through the reopened workflow; never live mail/API."""

import json
import runpy
import sys

import pytest


def test_saved_sponsor_journey_preserves_latest_question_context_and_source(tmp_path, monkeypatch):
    output = tmp_path / "replayed.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("Saved proposal regression cannot use network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(sys, "argv", ["consultant_journey_probe.py", "--scenario-set", "sponsor",
        "--replay", "eval_output/sponsor_address_2026-09-07-v5.json", "--output", str(output)])
    runpy.run_path("scripts/consultant_journey_probe.py", run_name="__main__")
    report = json.loads(output.read_text())
    assert report["all_passed"] and len(report["results"]) == 7
    assert report["maximum_model_calls"] == report["mailbox_calls"] == 0
    sixth = report["results"][5]
    assert sixth["extraction_event"]["requested_fields"] == ["sponsor_address"]
    assert sixth["extraction_event"]["known_profile"]["_sponsor_address_question_verified"] is True
    assert sixth["checks"]["current_sponsor_address_source"]
    assert all(not row["usage"] for row in report["results"])
    assert report["results"][0]["checks"]["current_sponsor_residence_source"]
    assert report["results"][4]["checks"]["current_sponsor_residence_source"]
    assert report["results"][4]["checks"]["presence_not_invented_or_waived"]


def test_replay_refuses_old_proposals_for_changed_scenario_messages(tmp_path, monkeypatch):
    output = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(sys, "argv", ["consultant_journey_probe.py", "--scenario-set", "sponsor",
        "--replay", "eval_output/sponsor_address_2026-09-07-v1.json", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        runpy.run_path("scripts/consultant_journey_probe.py", run_name="__main__")
    assert error.value.code == 2
    assert not output.exists()
