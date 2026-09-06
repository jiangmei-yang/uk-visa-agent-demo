"""Replay real provider omissions and corrections without network or live mail."""

import json
import runpy
import sys


def test_saved_employer_journey_preserves_deferral_identity_and_sources(tmp_path, monkeypatch):
    output = tmp_path / "replayed.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("Saved employer regression cannot use network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(sys, "argv", ["consultant_journey_probe.py", "--scenario-set", "employer",
        "--replay", "eval_output/employer_intake_2026-09-07-v2.json", "--output", str(output)])
    runpy.run_path("scripts/consultant_journey_probe.py", run_name="__main__")
    report = json.loads(output.read_text())
    assert report["all_passed"] and len(report["results"]) == 9
    assert report["maximum_model_calls"] == report["mailbox_calls"] == 0
    address = report["results"][3]["extraction_event"]
    assert address["requested_fields"] == ["employer_address"]
    assert address["known_profile"]["_employer_question_verified"] == "employer_address"
    assert all(not row["usage"] for row in report["results"])
