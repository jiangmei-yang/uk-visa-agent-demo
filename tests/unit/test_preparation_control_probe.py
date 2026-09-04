"""Offline probe contracts using invented fixtures, never the frozen corpus."""

import hashlib
import importlib.util
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from visa_agent.llm.ports import CasePatch


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/preparation_control_probe.py"
    spec = importlib.util.spec_from_file_location("synthetic_preparation_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(module, "read_secret", Mock(side_effect=AssertionError("No real test secret")))
    monkeypatch.setattr(module, "DeepSeekStructuredLLM", Mock(side_effect=AssertionError("No real provider")))
    return module


def item(**overrides):
    return {
        "id": "new-local-fixture", "language": "en", "body": "Thanks for the update.",
        "initially_paused": False, "expected_action": None,
        "expected_profile_updates": {}, "rationale": "Invented offline contract fixture.",
        "holdout": False, **overrides,
    }


def case_patch(**overrides):
    return CasePatch.model_validate({"updates": [], "ambiguities": [], **overrides})


def intent(action, excerpt, confidence=0.99):
    return {"action": action, "source_excerpt": excerpt, "confidence": confidence}


class SyntheticModel:
    model = "offline-fictional-model"

    def __init__(self, output=None, *, mutate_event=False, fail_before_content=False):
        self.output = case_patch() if output is None else output
        self.mutate_event = mutate_event
        self.fail_before_content = fail_before_content
        self.calls = []
        self.usage_history = []
        self.last_extraction_content = None
        self.client = SimpleNamespace(max_retries=0)

    def extract_case_patch(self, event):
        self.calls.append(event.model_dump(mode="json"))
        if self.mutate_event:
            event.body = "This local mutation must not reach the workflow."
            event.known_profile["full_name"] = "Mutated local model input"
        if self.fail_before_content:
            raise ValueError("Synthetic transport failure")
        self.usage_history.append({"operation": "extract_case_patch", "prompt_tokens": 11,
                                   "completion_tokens": 7, "total_tokens": 18})
        if isinstance(self.output, Exception):
            self.last_extraction_content = "{malformed synthetic response"
            raise self.output
        self.last_extraction_content = self.output.model_dump_json()
        return self.output.model_copy(deep=True)

    def extract_case_patch_legacy_input(self, event):
        raise AssertionError("Must use the production default, not legacy input")

    def extract_case_patch_neutral_input(self, event):
        raise AssertionError("Must use the production default, not a named alternative arm")

    def extract_customer_questions(self, event):
        raise AssertionError("No second extraction arm")

    def render_message(self, case, plan):
        raise AssertionError("The real model must never render probe workflow messages")


def stub_workflow(*args):
    return {"checks": {"offline_workflow_contract": True}, "external_sends": 0}


def run_case(probe, *, fixture=None, model=None, **kwargs):
    return probe.single_case(fixture or item(), policy_version=probe.load_policy(probe.POLICY).version,
                             model=model or SyntheticModel(), **kwargs)


@pytest.mark.parametrize(("flags", "existing", "message"), [
    (["--split", "holdout"], False, "requires --allow-holdout"),
    (["--allow-holdout"], False, "only meaningful"),
    ([], True, "cannot be overwritten"),
])
def test_invalid_cli_stops_before_corpus_credentials_or_provider(
    probe, monkeypatch, tmp_path, capsys, flags, existing, message,
):
    output = tmp_path / "report.json"
    if existing:
        output.write_text("retained fictional report", encoding="utf-8")
    read = Mock(side_effect=AssertionError("Invalid CLI cannot read corpus"))
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", "unread.json", "--output", str(output), *flags])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    read.assert_not_called()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()
    if existing:
        assert output.read_text(encoding="utf-8") == "retained fictional report"
    else:
        assert not output.exists()


def test_load_selects_split_without_validating_unselected_labels(probe):
    selected = item()
    unselected = {"holdout": True, "body": None, "expected_action": "deliberately-not-a-label"}
    assert probe.load_items(json.dumps([selected, unselected]).encode(), "development") == [selected]
    with pytest.raises(ValueError, match="nonempty id"):
        probe.load_items(json.dumps([selected, unselected]).encode(), "holdout")


@pytest.mark.parametrize("corpus", [
    {}, [], [item(holdout="false")], [item(initially_paused="false")],
    [item(expected_action="confirm")], [item(language="unknown")], [item(body="")],
    [item(expected_profile_updates={"estimated_trip_cost_gbp": True})],
    [item(expected_profile_updates={"date_of_birth": "21 May 1998"})],
    [item(expected_profile_updates={"unapproved_field": 1})],
    [item(expected_human_review="true")], [item(), item()],
])
def test_invalid_selected_shapes_are_rejected_offline(probe, corpus):
    with pytest.raises((ValueError, TypeError)):
        probe.load_items(json.dumps(corpus).encode(), "development")
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_single_default_call_checkpoints_raw_guarded_and_immutable_event(probe, monkeypatch):
    observed = []

    def capture_workflow(*args):
        observed.append(args[2].model_dump(mode="json"))
        return stub_workflow(*args)

    monkeypatch.setattr(probe, "exercise_workflow", capture_workflow)
    model = SyntheticModel(mutate_event=True)
    checkpoints = []
    row = run_case(probe, model=model, checkpoint=lambda row: checkpoints.append(deepcopy(row)))
    assert len(model.calls) == 1 and row["model_calls"] == 1
    assert model.calls[0] == observed[0] == row["input_event"]
    assert row["body"] == "Thanks for the update."
    assert row["raw_patch"] == row["guarded_patch"]
    assert row["raw_checks"]["action_exact"]
    assert row["guarded_checks"]["action_exact"]
    assert row["usage"] == model.usage_history
    assert row["model"] == "offline-fictional-model"
    assert row["extraction_latency_seconds"] >= 0
    assert checkpoints[0]["model_calls"] == 1 and not checkpoints[0]["completed"]
    assert any(point["extraction_available"] and not point["guarded_available"] for point in checkpoints)
    assert checkpoints[-1]["completed"] and row["passed"]


def test_extraction_error_keeps_usage_raw_and_error_without_retry_or_workflow(probe, monkeypatch):
    workflow = Mock(side_effect=AssertionError("No invented patch after extraction failure"))
    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    model = SyntheticModel(ValueError("Bad JSON echoed fictional-secret"))
    row = run_case(probe, model=model, key="fictional-secret")
    assert len(model.calls) == 1 and row["model_calls"] == 1
    assert row["extraction_error"]["message"] == "Bad JSON echoed [REDACTED]"
    assert row["raw_response_content"] == "{malformed synthetic response"
    assert row["usage"] == model.usage_history
    assert "raw_patch" not in row and "guarded_patch" not in row
    assert not row["extraction_available"] and not row["passed"] and row["completed"]
    workflow.assert_not_called()


def test_failed_new_call_cannot_reuse_previous_raw_or_usage(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    model = SyntheticModel(fail_before_content=True)
    model.last_extraction_content = "a previous case response"
    model.usage_history.append({"total_tokens": 999})
    row = run_case(probe, model=model)
    assert row["raw_response_content"] is None
    assert row["usage"] == []
    assert len(model.calls) == 1
    metrics = probe.aggregate([row])
    assert metrics["extraction_errors"] == 1
    assert metrics["usage_unavailable_cases"] == 1
    assert metrics["guarded_action_accuracy_including_errors"] == 0


def test_guard_rejection_is_retained_without_injecting_expected_action(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    body = "Please pause all my UK visa preparation."
    output = case_patch(preparation_intent=intent("pause", body, confidence=0.1))
    row = run_case(probe, fixture=item(body=body, expected_action="pause"), model=SyntheticModel(output))
    assert row["raw_patch"]["preparation_intent"]["action"] == "pause"
    assert row["guarded_patch"]["preparation_intent"] is None
    assert row["raw_checks"]["action_exact"]
    assert not row["guarded_checks"]["action_exact"]
    assert not row["passed"]


def test_guard_and_workflow_exceptions_are_separate_and_never_retry(probe, monkeypatch):
    validator = Mock(side_effect=ValueError("Synthetic guard error"))
    monkeypatch.setattr(probe, "validate_case_patch", validator)
    model = SyntheticModel()
    row = run_case(probe, model=model)
    assert "guard_error" in row and "workflow_error" not in row
    assert len(model.calls) == 1 and not row["guarded_available"]
    monkeypatch.setattr(probe, "validate_case_patch", lambda event, proposed: proposed)
    monkeypatch.setattr(probe, "exercise_workflow", Mock(side_effect=ValueError("Synthetic workflow error")))
    model = SyntheticModel()
    row = run_case(probe, model=model)
    assert "workflow_error" in row and "guard_error" not in row
    assert len(model.calls) == 1 and row["guarded_available"] and not row["workflow_available"]


def test_redaction_covers_every_checkpoint_and_nested_model_value(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    output = case_patch(ambiguities=["fictional-secret echoed in model output"])
    checkpoints = []
    row = run_case(probe, model=SyntheticModel(output), key="fictional-secret", checkpoint=checkpoints.append)
    assert "fictional-secret" not in json.dumps([row, *checkpoints])
    assert "[REDACTED]" in row["raw_response_content"]


@pytest.mark.parametrize(("paused", "action"), [
    (False, "pause"), (True, "pause"), (True, "resume"),
    (False, "resume"), (True, None), (False, None),
])
def test_real_local_workflow_control_transitions_and_idempotent_replay(probe, paused, action):
    body = {"pause": "Please pause all my UK visa preparation now.",
            "resume": "Please resume preparing all my UK visa materials now.",
            None: "Thanks for sending that information."}[action]
    output = case_patch(preparation_intent=intent(action, body) if action else None)
    row = run_case(probe, fixture=item(body=body, initially_paused=paused, expected_action=action),
                   model=SyntheticModel(output))
    assert row["passed"], row
    workflow = row["workflow"]
    expected_paused = paused if action is None else action == "pause"
    changed = expected_paused != paused
    assert workflow["control_after"]["preparation_paused"] == expected_paused
    assert workflow["control_after"]["preparation_control_epoch"] == int(paused) + int(changed)
    assert workflow["control_after"]["latest_preparation_action"] == (action if changed else None)
    assert workflow["captured_extraction_calls"] == 1
    assert workflow["simulation_network_disabled"] and workflow["external_sends"] == 0
    if expected_paused:
        assert workflow["question_plan"] == []
        assert workflow["checks"]["paused_no_confirmation_metadata"]


def test_pause_does_not_swallow_facts_question_or_human_review(probe):
    pause = "Please pause all UK visa preparation now."
    birthday = "Please correct my date of birth to 21 May 1998."
    budget = "Please change my total trip budget to GBP 2250."
    history = "I previously had a UK visitor visa application refused."
    question = "What is the ordinary six-month visitor visa application fee?"
    body = " ".join([pause, birthday, budget, history, question])
    expected = {"date_of_birth": "1998-05-21", "estimated_trip_cost_gbp": 2250, "has_serious_history": True}
    output = case_patch(
        preparation_intent=intent("pause", pause),
        updates=[{"field": field, "value": expected[field], "source_excerpt": excerpt, "confidence": 0.99}
                 for field, excerpt in (("date_of_birth", birthday), ("estimated_trip_cost_gbp", budget),
                                        ("has_serious_history", history))],
        customer_questions=[{"topic": "fees", "source_excerpt": question, "confidence": 0.99}],
        requires_human_review=False,
    )
    row = run_case(probe, fixture=item(body=body, expected_action="pause", expected_profile_updates=expected,
                                       expected_human_review=True), model=SyntheticModel(output))
    assert not row["raw_checks"]["expected_human_review"]
    assert row["guarded_checks"]["expected_human_review"]
    assert row["passed"], row
    assert row["workflow"]["workflow_topics"] == ["fees"]
    assert row["workflow"]["customer_answers"]
    for field, value in expected.items():
        assert row["workflow"]["profile_after"][field] == value


def test_missing_expected_fact_fails_independently_of_correct_control(probe):
    body = "Please pause all my visa preparation. My trip budget should be GBP 2450."
    output = case_patch(preparation_intent=intent("pause", "Please pause all my visa preparation."))
    row = run_case(probe, fixture=item(body=body, expected_action="pause",
                                       expected_profile_updates={"estimated_trip_cost_gbp": 2450}),
                   model=SyntheticModel(output))
    assert row["guarded_checks"]["action_exact"]
    assert not row["guarded_checks"]["expected_profile_update:estimated_trip_cost_gbp"]
    assert not row["workflow"]["checks"]["expected_profile_update:estimated_trip_cost_gbp"]
    assert not row["passed"]


@pytest.mark.parametrize("holdout", [False, True])
def test_cli_synthetic_run_records_hashes_one_call_and_selected_split_only(
    probe, monkeypatch, tmp_path, holdout,
):
    corpus = tmp_path / "synthetic-corpus.json"
    fixtures = [item(id="local-development", body="Thanks for the development note."),
                item(id="local-reserved", holdout=True, body="Thanks for the separately reserved note.")]
    corpus.write_text(json.dumps(fixtures), encoding="utf-8")
    output = tmp_path / "fresh-report.json"
    model = SyntheticModel()
    monkeypatch.setattr(probe, "read_secret", Mock(return_value="offline-key"))
    constructor = Mock(return_value=model)
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", constructor)
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic.py": "offline-hash"})
    flags = ["--split", "holdout", "--allow-holdout"] if holdout else []
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--output", str(output), *flags])
    probe.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    selected = fixtures[int(holdout)]
    assert report["split"] == ("holdout" if holdout else "development")
    assert report["corpus_sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert report["source_sha256"] == {"synthetic.py": "offline-hash"}
    assert report["source_unchanged_during_run"] and report["corpus_unchanged_during_run"]
    assert report["model_calls"] == report["expected_case_count"] == report["completed_case_count"] == 1
    assert report["results"][0]["id"] == selected["id"]
    assert len(model.calls) == 1
    assert report["metrics"]["tokens"]["total_tokens"] == 18
    assert report["completed"] and report["all_passed"]
    constructor.assert_called_once_with(probe.MODEL, api_key="offline-key", capture_raw_responses=True)
    assert fixtures[int(not holdout)]["body"] not in output.read_text(encoding="utf-8")


def test_startup_rejects_retries_before_any_provider_call(probe, monkeypatch, tmp_path, capsys):
    corpus = tmp_path / "synthetic.json"
    corpus.write_text(json.dumps([item()]), encoding="utf-8")
    output = tmp_path / "fresh-report.json"
    model = SyntheticModel()
    model.client.max_retries = 2
    monkeypatch.setattr(probe, "read_secret", Mock(return_value="offline-key"))
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", Mock(return_value=model))
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "retries must be disabled" in capsys.readouterr().err
    assert not model.calls
    assert json.loads(output.read_text(encoding="utf-8"))["model_calls"] == 0


def test_report_reservation_refuses_overwrite(probe, tmp_path):
    path = tmp_path / "preserved.json"
    path.write_text("original synthetic result", encoding="utf-8")
    with pytest.raises(FileExistsError):
        probe.write_report(path, {"replacement": True}, create=True)
    assert path.read_text(encoding="utf-8") == "original synthetic result"


def test_captured_model_rejects_second_extraction(probe):
    captured = probe.CapturedLLM(case_patch())
    assert captured.extract_case_patch(None) == case_patch()
    with pytest.raises(AssertionError, match="second captured extraction"):
        captured.extract_case_patch(None)


def saved_provider_report(probe, *, output=None, fixture=None):
    """Produce an offline original-format report without opening a saved corpus."""
    fixture = fixture or item()
    corpus_bytes = json.dumps([fixture]).encode()
    row = run_case(probe, fixture=fixture, model=SyntheticModel(output))
    sources = {"fictional_original_source.py": "a" * 64}
    report = {
        "split": "development", "holdout_authorized": False, "completed": True,
        "source_unchanged_during_run": True, "corpus_unchanged_during_run": True,
        "no_provider_retries": True, "operation": "extract_case_patch", "calls_per_case": 1,
        "model": row["model"], "expected_case_count": 1, "completed_case_count": 1, "model_calls": 1,
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "source_sha256": sources,
        "source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        "results": [row], "metrics": probe.aggregate([row]),
    }
    return report, corpus_bytes, [fixture]


def load_saved(probe, report, corpus_bytes, fixtures):
    return probe.load_replay(json.dumps(report).encode(), corpus_bytes=corpus_bytes, items=fixtures,
                             policy_version=probe.load_policy(probe.POLICY).version)


@pytest.mark.parametrize("change", [
    "incomplete", "source_changed", "corpus_changed", "wrong_corpus_hash", "holdout",
    "holdout_authorized", "wrong_ids", "body", "label", "profile_label", "seed_profile",
    "seed_control", "raw_repair", "source_hash", "source_bundle", "calls", "replayed_report",
])
def test_replay_rejects_nonmatching_or_nonimmutable_originals(probe, change):
    report, corpus_bytes, fixtures = saved_provider_report(probe)
    row = report["results"][0]
    if change == "incomplete":
        report["completed"] = False
    elif change == "source_changed":
        report["source_unchanged_during_run"] = False
    elif change == "corpus_changed":
        report["corpus_unchanged_during_run"] = False
    elif change == "wrong_corpus_hash":
        report["corpus_sha256"] = "0" * 64
    elif change == "holdout":
        report["split"] = "holdout"
    elif change == "holdout_authorized":
        report["holdout_authorized"] = True
    elif change == "wrong_ids":
        row["id"] = "not-the-original-id"
    elif change == "body":
        row["body"] = "Changed fictional mail text."
    elif change == "label":
        row["expected_action"] = "resume"
    elif change == "profile_label":
        row["expected_profile_updates"] = {"estimated_trip_cost_gbp": 9999}
    elif change == "seed_profile":
        row["input_event"]["known_profile"]["estimated_trip_cost_gbp"] = 9999
    elif change == "seed_control":
        row["workflow"]["control_before"]["preparation_control_epoch"] = 99
    elif change == "raw_repair":
        del row["raw_patch"]["preparation_intent"]
    elif change == "source_hash":
        report["source_sha256"]["fictional_original_source.py"] = "invalid"
    elif change == "source_bundle":
        report["source_bundle_sha256"] = "0" * 64
    elif change == "calls":
        report["model_calls"] = 2
    elif change == "replayed_report":
        report["evaluation_mode"] = "saved_patch_replay"
    with pytest.raises(ValueError):
        load_saved(probe, report, corpus_bytes, fixtures)
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


@pytest.mark.parametrize("flags", [["--split", "holdout"], ["--split", "holdout", "--allow-holdout"],
                                  ["--allow-holdout"]])
def test_replay_holdout_forbidden_before_any_file_or_secret_read(probe, monkeypatch, tmp_path, capsys, flags):
    read = Mock(side_effect=AssertionError("Forbidden replay cannot read any report or corpus"))
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", "unread.json", "--replay-from", "unread-report.json",
                                      "--output", str(tmp_path / "new.json"), *flags])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "development-only" in capsys.readouterr().err
    read.assert_not_called()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_replay_never_reconstructs_original_failed_extraction(probe, monkeypatch):
    report, corpus_bytes, fixtures = saved_provider_report(probe, output=ValueError("Original schema failure"))
    original = report["results"][0]
    original["raw_response_content"] = case_patch().model_dump_json()
    load_saved(probe, report, corpus_bytes, fixtures)
    validator = Mock(side_effect=AssertionError("No repairing failed original extraction"))
    workflow = Mock(side_effect=AssertionError("No invented patch can reach workflow"))
    monkeypatch.setattr(probe, "validate_case_patch", validator)
    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    row = probe.replay_case(fixtures[0], original, policy_version="offline-policy")
    assert row["original_result"] == original
    assert row["extraction_error"] == original["extraction_error"]
    assert row["raw_response_content"] == case_patch().model_dump_json()
    assert "raw_patch" not in row and not row["passed"]
    assert row["model_calls"] == 0 and row["usage"] == []
    validator.assert_not_called()
    workflow.assert_not_called()
    metrics = probe.aggregate([row], replay=True)
    assert metrics["original_extraction_errors_retained"] == 1
    assert metrics["new_provider_calls"] == 0 and metrics["tokens"] == {}
    assert "raw_accuracy_including_errors" not in metrics


def test_replay_preserves_original_guard_failure_but_evaluates_current_guard(probe):
    body = "Please pause all my UK visa preparation now."
    fixture = item(body=body, expected_action="pause")
    output = case_patch(preparation_intent=intent("pause", body))
    report, corpus_bytes, fixtures = saved_provider_report(probe, fixture=fixture, output=output)
    original = report["results"][0]
    original["guarded_patch"]["preparation_intent"] = None
    original["guarded_checks"]["action_exact"] = False
    original["guarded_passed"] = False
    original["passed"] = False
    load_saved(probe, report, corpus_bytes, fixtures)
    before = deepcopy(original)
    row = probe.replay_case(fixture, original, policy_version=probe.load_policy(probe.POLICY).version)
    assert original == before and row["original_result"] == before
    assert row["raw_patch"] == original["raw_patch"]
    assert row["guarded_patch"]["preparation_intent"]["action"] == "pause"
    assert row["passed"] and row["model_calls"] == 0
    assert not row["original_result"]["guarded_passed"]
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_usage_metrics_sum_only_actual_observed_numeric_keys(probe):
    metrics = probe.usage_metrics([
        {"usage": [{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "operation": "extract"}]},
        {"usage": [{"input_tokens": 7, "total_tokens": 7}]},
        {"usage": []},
    ])
    assert metrics["tokens"] == {"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}
    assert metrics["token_key_record_counts"] == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 2}
    assert metrics["usage_unavailable_cases"] == 1
    assert "prompt_tokens" not in metrics["tokens"] and "completion_tokens" not in metrics["tokens"]
    assert probe.usage_metrics([{"usage": []}])["tokens"] == {}


def test_cli_replay_is_zero_api_preserves_original_and_separates_historical_cost(
    probe, monkeypatch, tmp_path,
):
    original, corpus_bytes, fixtures = saved_provider_report(probe)
    original["results"][0]["usage"] = [
        {"operation": "extract_case_patch", "input_tokens": 21, "output_tokens": 9, "total_tokens": 30},
    ]
    original["metrics"]["tokens"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 30}
    corpus = tmp_path / "synthetic-corpus.json"
    corpus.write_bytes(corpus_bytes)
    saved = tmp_path / "original-report.json"
    saved.write_text(json.dumps(original), encoding="utf-8")
    original_bytes = saved.read_bytes()
    output = tmp_path / "replayed-report.json"
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"new_guard.py": "b" * 64})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--replay-from", str(saved),
                                      "--output", str(output)])
    probe.main()
    replayed = json.loads(output.read_text(encoding="utf-8"))
    assert saved.read_bytes() == original_bytes
    assert replayed["original_report_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert replayed["original_source_sha256"] == original["source_sha256"]
    assert replayed["source_sha256"] == {"new_guard.py": "b" * 64}
    assert replayed["original_report_unchanged_during_replay"]
    assert replayed["not_new_model_accuracy"] and not replayed["new_provider_result"]
    assert replayed["model_calls"] == replayed["new_provider_calls"] == 0
    assert replayed["historical_provider"]["model_calls"] == 1
    assert replayed["historical_provider"]["metrics_as_recorded"]["tokens"]["prompt_tokens"] == 0
    assert replayed["historical_provider"]["corrected_usage_metrics"]["tokens"] == {
        "input_tokens": 21, "output_tokens": 9, "total_tokens": 30,
    }
    assert replayed["metrics"]["tokens"] == {} and replayed["metrics"]["model_calls"] == 0
    assert "raw_accuracy_including_errors" not in replayed["metrics"]
    assert replayed["results"][0]["original_result"] == original["results"][0]
    assert replayed["results"][0]["raw_patch"] == original["results"][0]["raw_patch"]
    assert replayed["corrected_usage_metrics"] and replayed["all_passed"]
    assert replayed["results"][0]["id"] == fixtures[0]["id"]
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_replay_cli_mismatch_rejected_before_output_or_credentials(probe, monkeypatch, tmp_path, capsys):
    original, corpus_bytes, _ = saved_provider_report(probe)
    original["results"][0]["body"] = "Edited after measurement."
    corpus = tmp_path / "synthetic-corpus.json"
    corpus.write_bytes(corpus_bytes)
    saved = tmp_path / "original-report.json"
    saved.write_text(json.dumps(original), encoding="utf-8")
    output = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--replay-from", str(saved),
                                      "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "body or evaluator labels changed" in capsys.readouterr().err
    assert not output.exists()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()
