"""Fictional generated journeys and fake models only; never reads the experiment corpus."""

from __future__ import annotations

import importlib.util
import json
import socket
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from visa_agent.llm.ports import CasePatch, FactUpdate


@pytest.fixture
def probe(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("synthetic_cold_probe", scripts / "cold_start_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(module, "read_secret", Mock(side_effect=AssertionError("No test credentials")))
    monkeypatch.setattr(module, "source_fingerprints", lambda: {
        "scripts/cold_start_probe.py": "1" * 64, "scripts/cold_start_checks.py": "2" * 64,
        "src/visa_agent/workflow/service.py": "3" * 64})
    return module


def fictional_journeys(split="development"):
    return [{"id": f"fictional-journey-{number}", "split": split, "language": "en", "subject": "UK visa questions",
             "turns": [{"id": f"turn-{index}", "body": f"Hello, I would like help with a UK visit. Message {index}.",
                        "expected_profile": {}, "deferred_date_expected": False,
                        "preparation_paused_expected": False, "expected_information": [],
                        "forbidden_reasked_fields": [], "rationale": "SYNTHETIC_LABEL_MUST_NOT_REACH_MODEL"}
                       for index in range(6)]} for number in range(2)]


class FakeModel:
    model = version = "fictional-extraction-only"

    def __init__(self, state, mode="normal"):
        self.state, self.mode = state, mode
        self.usage_history = []
        self.last_extraction_content = None
        self.calls = 0
        self.client = SimpleNamespace(max_retries=0, close=self.close)

    def close(self):
        self.state.closed += 1

    def extract_case_patch(self, event):
        self.calls += 1
        self.state.calls.append(event.model_dump(mode="json"))
        self.usage_history.append({"operation": "extract_case_patch", "input_tokens": 10,
                                   "output_tokens": 2, "total_tokens": 12})
        if self.mode == "timeout":
            self.usage_history.clear()
            raise TimeoutError("Fictional provider timeout")
        if self.mode in {"malformed_once", "always_malformed"} and (
                self.calls == 1 or self.mode == "always_malformed"):
            self.last_extraction_content = "{fictional invalid response"
            raise ValueError("Fictional schema failure after billed response")
        value = CasePatch(updates=[], ambiguities=[])
        if self.mode == "guard_rejected_once" and self.calls == 1:
            value = CasePatch(updates=[FactUpdate(field="full_name", value="Invented Person",
                                                source_excerpt="not present in message", confidence=1)], ambiguities=[])
        self.last_extraction_content = value.model_dump_json()
        return value

    def render_message(self, case, plan):
        raise AssertionError("Do not spend on unused provider prose")


@pytest.fixture
def experiment(tmp_path):
    corpus = tmp_path / "fictional-corpus.json"
    # Invalid unselected labels intentionally prove the driver does not expand them.
    corpus.write_text(json.dumps([*fictional_journeys(), {"split": "holdout", "not_a_journey": True}]))
    return SimpleNamespace(corpus=corpus, output=tmp_path / "report.json", calls=[], models=[], closed=0)


def factory(experiment, mode="normal"):
    def create():
        model = FakeModel(experiment, mode)
        experiment.models.append(model)
        return model
    return create


def run(probe, experiment, mode="normal", **kwargs):
    return probe.run_probe(corpus_path=experiment.corpus, split="development", output=experiment.output,
                           model_factory=factory(experiment, mode), **kwargs)


def test_cold_instances_real_mime_guard_dispatch_and_reopened_duplicate(probe, experiment):
    report = run(probe, experiment)
    assert report["all_passed"] and report["completed"]
    assert len(report["turns"]) == len(experiment.models) == experiment.closed == 12
    assert report["provider_attempts"] == len(experiment.calls) == 12
    assert report["metrics"]["tokens"] == {"input_tokens": 120, "output_tokens": 24, "total_tokens": 144}
    assert report["metrics"]["usage_unavailable_attempts"] == 0
    assert report["gmail_network_calls"] == 0 and not report["naturalness_scored"]
    assert report["manual_information_review_required"]
    assert stat.S_IMODE(experiment.output.stat().st_mode) == 0o600
    assert json.loads(experiment.output.read_bytes()) == report
    for index, row in enumerate(report["turns"]):
        assert row["input_event"]["channel"] == "gmail"
        assert row["input_event"]["body"].endswith("\n")
        assert row["input_event"]["id"].startswith("event-")
        assert "fictional-journey" not in row["input_event"]["external_thread_id"]
        assert row["guard"]["fallback"] is False
        assert row["duplicate_reopen"]["passed"] and row["duplicate_reopen"]["zero_provider_calls"]
        assert len(row["captured_requests"]) == 1
        current = [item for item in row["after"]["outbox"] if item["event_id"] == row["input_event"]["id"]]
        assert len(current) == 1 and current[0]["status"] == "SENT"
        assert current[0]["payload"] == row["body"]
        assert row["before"]["counts"]["cases"] == (0 if index in {0, 6} else 1)
        assert len(row["attempts"]) == 1 and row["attempts"][0]["guard_validation"]["patch"] == row["guard"]["patch"]
    assert len({request["message_id"] for row in report["turns"] for request in row["captured_requests"]}) == 12
    assert "SYNTHETIC_LABEL" not in json.dumps(experiment.calls)
    assert "expected_profile" not in json.dumps(experiment.calls)


@pytest.mark.parametrize("mode", ["malformed_once", "guard_rejected_once"])
def test_every_failed_first_attempt_is_retained_even_when_production_retry_recovers(probe, experiment, mode):
    report = run(probe, experiment, mode)
    assert report["all_passed"] and report["provider_attempts"] == 24
    assert len(experiment.calls) == 24
    assert report["metrics"]["tokens"]["total_tokens"] == 288
    for row in report["turns"]:
        first, second = row["attempts"]
        assert first["guard_retry_followed"] and not first["selected_by_guard"]
        assert second["selected_by_guard"] and not second["guard_retry_followed"]
        assert row["guard"]["fallback"] is False
        if mode == "malformed_once":
            assert first["extraction_available"] is False and first["error"]["type"] == "ValueError"
            assert first["raw_response_content"] == "{fictional invalid response"
        else:
            assert first["extraction_available"]
            assert first["guard_validation"]["patch"]["requires_human_review"]


@pytest.mark.parametrize("mode", ["timeout", "always_malformed"])
def test_failures_and_following_cascade_remain_visible_without_resetting_case(probe, experiment, mode):
    report = run(probe, experiment, mode)
    assert report["completed"] and not report["all_passed"]
    assert len(report["turns"]) == 12 and report["provider_attempts"] == 4
    for row in (report["turns"][0], report["turns"][6]):
        assert row["guard"]["fallback"] and len(row["attempts"]) == 2
        assert all(not attempt["extraction_available"] for attempt in row["attempts"])
    assert report["turns"][1]["attempts"] == []
    assert report["turns"][1]["after"]["held_events"]
    assert report["metrics"]["usage_unavailable_attempts"] == (4 if mode == "timeout" else 0)
    assert report["metrics"]["tokens"] == ({} if mode == "timeout" else {
        "input_tokens": 40, "output_tokens": 8, "total_tokens": 48})


def test_failed_capture_does_not_hide_next_turns_actual_body(probe, experiment, monkeypatch):
    original = probe.CaptureGmail.send_reply
    counter = [0]

    def fail_once(self, **request):
        counter[0] += 1
        if counter[0] == 1:
            raise RuntimeError("Fictional uncertain send before capture")
        return original(self, **request)

    monkeypatch.setattr(probe.CaptureGmail, "send_reply", fail_once)
    report = run(probe, experiment)
    assert not report["all_passed"] and report["turns"][0]["body"] == ""
    assert report["turns"][1]["body"]
    assert report["turns"][1]["flow_checks"]["exact_persisted_body"]


def test_checkpoint_exists_before_call_and_contains_failed_response_usage_immediately(probe, experiment, monkeypatch):
    original = probe.EvidenceFile.checkpoint
    snapshots = []

    def capture(self, report):
        snapshots.append(deepcopy(report))
        return original(self, report)

    monkeypatch.setattr(probe.EvidenceFile, "checkpoint", capture)
    report = run(probe, experiment, "malformed_once")
    assert report["all_passed"]
    assert any(snapshot["turns"] and snapshot["turns"][0]["attempts"]
               and not snapshot["turns"][0]["attempts"][0]["completed"] for snapshot in snapshots)
    assert any(snapshot["turns"] and len(snapshot["turns"][0]["attempts"]) == 1
               and snapshot["turns"][0]["attempts"][0].get("usage")
               and snapshot["turns"][0]["attempts"][0].get("error") for snapshot in snapshots)


def test_existing_report_is_refused_without_credentials_or_provider(probe, experiment):
    experiment.output.write_text("prior failed experiment")
    with pytest.raises(FileExistsError):
        run(probe, experiment)
    assert experiment.output.read_text() == "prior failed experiment" and experiment.calls == []


@pytest.mark.parametrize("split,flag,replay", [("holdout", False, None), ("holdout", True, "original.json")])
def test_holdout_execution_and_replay_boundaries(probe, experiment, split, flag, replay):
    with pytest.raises(ValueError):
        probe.run_probe(corpus_path=experiment.corpus, split=split, allow_holdout=flag,
                        replay_from=Path(replay) if replay else None, output=experiment.output,
                        model_factory=factory(experiment))
    assert not experiment.output.exists() and not experiment.calls


def test_selected_holdout_requires_explicit_flag_and_never_becomes_development(probe, experiment):
    experiment.corpus.write_text(json.dumps(fictional_journeys("holdout")))
    report = probe.run_probe(corpus_path=experiment.corpus, split="holdout", allow_holdout=True,
                            output=experiment.output, model_factory=factory(experiment))
    assert report["all_passed"] and report["holdout_authorized"] and report["split"] == "holdout"


def test_saved_development_replay_uses_no_credentials_or_new_calls(probe, experiment):
    original = run(probe, experiment, "malformed_once")
    original_bytes = experiment.output.read_bytes()
    destination = experiment.output.with_name("replay.json")
    report = probe.run_probe(corpus_path=experiment.corpus, split="development", output=destination,
                            replay_from=experiment.output)
    assert report["all_passed"] and report["provider_attempts"] == 0
    assert report["metrics"]["tokens"] == {} and report["metrics"]["attempts"] == 0
    assert report["historical_metrics"] == original["metrics"]
    assert report["original_report_sha256"] == probe.digest(original_bytes)
    assert experiment.output.read_bytes() == original_bytes and len(experiment.calls) == 24
    assert report["turns"][0]["historical_attempts"][0]["error"]["type"] == "ValueError"


@pytest.mark.parametrize("alter", ["source_bundle", "event", "raw", "missing_attempt", "mode", "split"])
def test_replay_rejects_incomplete_or_altered_evidence_without_repair(probe, experiment, alter):
    report = run(probe, experiment)
    if alter == "source_bundle":
        report["source_bundle_sha256"] = "0" * 64
    elif alter == "event":
        report["turns"][0]["input_event"]["body"] = "changed input"
    elif alter == "raw":
        report["turns"][0]["attempts"][0]["raw_response_content"] = "{}"
    elif alter == "missing_attempt":
        report["turns"][0]["attempts"] = []
    else:
        report[alter] = "replay" if alter == "mode" else "holdout"
    experiment.output.write_text(json.dumps(report))
    replay = probe.run_probe(corpus_path=experiment.corpus, split="development",
                            output=experiment.output.with_name("replay.json"), replay_from=experiment.output)
    assert not replay["all_passed"] and replay["provider_attempts"] == 0 and replay["errors"]
    assert len(experiment.calls) == 12


def test_replay_detects_changed_prepared_context_instead_of_borrowing_saved_patch(probe, experiment):
    report = run(probe, experiment)
    report["turns"][0]["attempts"][0]["prepared_event"]["known_profile"]["full_name"] = "wrong context"
    report["turns"][0]["attempts"][0]["prepared_input_sha256"] = probe.digest(
        report["turns"][0]["attempts"][0]["prepared_event"])
    experiment.output.write_text(json.dumps(report))
    replay = probe.run_probe(corpus_path=experiment.corpus, split="development",
                            output=experiment.output.with_name("replay.json"), replay_from=experiment.output)
    assert not replay["all_passed"] and replay["provider_attempts"] == 0
    assert replay["turns"][0]["replay_integrity_errors"]


def test_proxy_error_after_send_keeps_usage_capture_and_all_later_turns(probe, experiment, monkeypatch):
    def fail(*args):
        raise ValueError("fictional evaluator bug")

    monkeypatch.setattr(probe, "check_turn", fail)
    report = run(probe, experiment)
    assert report["completed"] and not report["all_passed"] and len(report["turns"]) == 12
    assert report["provider_attempts"] == 12
    assert all(row["captured_requests"] and row["attempts"][0]["usage"] and row["error"] for row in report["turns"])


def test_secret_echo_is_removed_from_checkpoints_and_final_return(probe, experiment, monkeypatch):
    secret = "FICTIONAL_SECRET_71a87901_NOT_A_REAL_KEY"
    monkeypatch.setattr(probe, "read_secret", lambda *a, **kw: secret)

    class EchoModel(FakeModel):
        def extract_case_patch(self, event):
            self.last_extraction_content = secret
            raise ValueError("fictional provider echoed " + secret)

    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", lambda *a, **kw: EchoModel(experiment))
    report = probe.run_probe(corpus_path=experiment.corpus, split="development", output=experiment.output)
    assert not report["all_passed"]
    assert secret not in json.dumps(report) and secret not in experiment.output.read_text()
    assert "[REDACTED]" in experiment.output.read_text()


def test_atomic_checkpoint_failure_keeps_previous_complete_report(probe, tmp_path, monkeypatch):
    path = tmp_path / "evidence.json"
    writer = probe.EvidenceFile(path, {"stage": "before"})
    monkeypatch.setattr(probe.os, "replace", Mock(side_effect=OSError("fictional disk failure")))
    with pytest.raises(probe.EvidenceWriteError):
        writer.checkpoint({"stage": "after"})
    assert json.loads(path.read_bytes()) == {"stage": "before"}
    assert list(tmp_path.iterdir()) == [path]


def test_external_report_change_is_not_overwritten(probe, tmp_path):
    path = tmp_path / "evidence.json"
    writer = probe.EvidenceFile(path, {"stage": "before"})
    path.write_text("operator correction")
    with pytest.raises(probe.EvidenceWriteError):
        writer.checkpoint({"stage": "after"})
    assert path.read_text() == "operator correction"
