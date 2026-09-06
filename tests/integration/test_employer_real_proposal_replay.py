"""Replay real provider omissions and corrections without network or live mail."""

import hashlib
import json
import runpy
import sys
from pathlib import Path


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
    assert report["source_manifest_version"] == 2
    policy_path = report["policy"]["path"]
    assert report["source_sha256"][policy_path] == hashlib.sha256(Path(policy_path).read_bytes()).hexdigest()
    assert {"uv.lock", "pyproject.toml"} <= report["source_sha256"].keys()
    assert report["workflow_configuration"]["transport"] == "capture_only"
    address = report["results"][3]["extraction_event"]
    assert address["requested_fields"] == ["employer_address"]
    assert address["known_profile"]["_employer_question_verified"] == "employer_address"
    assert all(not row["usage"] for row in report["results"])
    rows = report["results"]
    assert "12 Example Road, Hong Kong" in rows[3]["reply"]
    assert "Hello" not in rows[3]["reply"]
    assert "Hello" not in rows[4]["reply"]
    assert "I'll leave that employer detail for checking" in rows[4]["reply"]
    assert "+852 2000 1234" in rows[5]["reply"]
    assert "Employer Name:" not in rows[6]["reply"]
    assert "Southstar Ltd" in rows[6]["reply"]
    assert "Next, I need your home address" not in rows[7]["reply"]
    assert "+852 2000 9012" in rows[8]["reply"]
