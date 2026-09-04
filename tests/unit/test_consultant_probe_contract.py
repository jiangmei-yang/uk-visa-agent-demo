"""Probe controls must hold even on failed extraction; never invokes a provider."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from visa_agent.domain.models import Case

SPEC = importlib.util.spec_from_file_location("consultant_probe", "scripts/consultant_value_probe.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_no_model_authorization_exits_before_key_access(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(tmp_path / "no.json")])
    monkeypatch.setattr(probe, "read_secret", lambda *a, **k: pytest.fail("must not read a key"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2 and not (tmp_path / "no.json").exists()


def test_existing_evidence_is_not_overwritten_or_retried(monkeypatch, tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("retained evidence")
    monkeypatch.setattr(sys, "argv", ["probe", "--allow-model-calls", "--output", str(output)])
    monkeypatch.setattr(probe, "read_secret", lambda *a, **k: pytest.fail("must not read a key"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2 and output.read_text() == "retained evidence"


@pytest.mark.parametrize("kind,extra", [
    ("mixed-request", ""),
    ("mixed-request", "我的存款两万元是不是一定能获批？"),
    ("mixed-request", "存款达到两万元，签证一定获批。"),
    ("separate-routes", ""),
    ("separate-routes", "What is the student visa fee?"),
    ("separate-routes", "The Student visa fee is £135."),
])
def test_two_question_check_cannot_pass_with_missing_or_unsafe_second_answer(kind, extra):
    case = Case(id="probe-case", external_thread_id="probe-thread", policy_version="test",
                applicant_contact="fictional@example.test", question_plan=[])
    checks = probe.checks_for(kind, case, probe.APPLICATION_URL + "\n" + extra, {})
    key = "savings_outcome_question_answered" if kind == "mixed-request" else "student_fee_question_answered"
    assert not checks[key]
    assert not all(checks.values())


@pytest.mark.parametrize("suite,expected_calls", [("consultant", 7), ("application-entry", 3)])
def test_every_failure_is_retained_and_guard_does_not_double_the_attempt_budget(monkeypatch, tmp_path, suite, expected_calls):
    instances = []

    class FailingModel:
        def __init__(self, *args, **kwargs):
            assert kwargs["capture_raw_responses"] is True
            self.extraction_attempts = 0
            self.usage_history = []
            self.last_extraction_content = "fictional malformed result"
            self.proposed_patch = None
            self.extraction_error_type = "ValueError"
            instances.append(self)

        def extract_case_patch(self, event):
            self.extraction_attempts += 1
            raise ValueError("fictional provider failure with SECRET-DO-NOT-LOG")

        render_message = staticmethod(probe.deterministic_fallback_message)

    def no_network(*args, **kwargs):
        pytest.fail("No network belongs in this contract test")

    monkeypatch.setattr("socket.socket.connect", no_network)
    monkeypatch.setattr("socket.create_connection", no_network)
    monkeypatch.setattr(probe, "ExtractionOnly", FailingModel)
    monkeypatch.setattr(probe, "read_secret", lambda *a, **k: "fictional-local-placeholder")
    output = tmp_path / "failures.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--allow-model-calls", "--suite", suite, "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 1
    report = json.loads(output.read_text())
    assert report["completed"] and not report["all_passed"]
    assert len(report["results"]) == len(instances) == expected_calls == report["maximum_model_calls"]
    assert sum(instance.extraction_attempts for instance in instances) == expected_calls
    assert all(not row["checks"]["no_extraction_fallback"] for row in report["results"])
    assert all(row["raw_model_content"] == "fictional malformed result" for row in report["results"])
    assert "SECRET-DO-NOT-LOG" not in output.read_text()
    assert report["mailbox_calls"] == 0
    assert not list(Path(tmp_path).glob("*.db"))


@pytest.mark.parametrize("missing,key", [
    (probe.APPLICATION_URL, "official_entry_and_action"),
    ("Apply now", "official_entry_and_action"),
    ("online", "form_save_and_appointment_steps"),
    ("save", "form_save_and_appointment_steps"),
    ("appointment", "form_save_and_appointment_steps"),
])
def test_application_probe_requires_the_link_and_each_operational_step(missing, key):
    case = Case(id="app-probe-case", external_thread_id="app-probe-thread", policy_version="test",
                applicant_contact="fictional@example.test", question_plan=[])
    reply = (probe.APPLICATION_URL + " Apply now online save appointment").replace(missing, "")
    checks = probe.checks_for("application-en", case, reply, {})
    assert not checks[key] and not all(checks.values())
