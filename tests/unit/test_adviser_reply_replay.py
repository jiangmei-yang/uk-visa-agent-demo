"""Offline replay contracts: only newly invented report/corpus fixtures are read."""

import hashlib
import importlib.util
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def replay(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/adviser_reply_replay.py"
    spec = importlib.util.spec_from_file_location("synthetic_adviser_reply_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.adviser, "read_secret", Mock(side_effect=AssertionError("Replay must never read a key")))
    monkeypatch.setattr(module.adviser, "DeepSeekStructuredLLM", Mock(side_effect=AssertionError("Replay must never create a provider")))
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No test network")))
    return module


def item(**overrides):
    return {"id": "new-synthetic-replay", "body": "Thanks.", "language": "en",
            "expected_topics": [], "rationale": "Synthetic replay-only contract.", **overrides}


def source_report(replay, cases, corpus_hash):
    rows = []
    for case in cases:
        rows.append({
            "id": case["id"], "body": case["body"], "language": case["language"],
            "expected_topics": case["expected_topics"],
            "expected_profile_updates": case.get("expected_profile_updates", {}),
            "profile_before": replay.seed_case(case, "synthetic-policy").profile.model_dump(mode="json"),
            "completed": True,
            # These deliberately invalid patches must never be parsed or reused.
            "baseline": {"raw_patch": {"not": "an allowed patch"}},
            "focused": {"raw_patch": {"not": "an allowed patch"}},
            "neutral_combined": {
                "operation": "extract_case_patch_neutral_input", "attempted": True,
                "completed": True, "extraction_available": True,
                "raw_patch": {"updates": [], "ambiguities": []},
                "usage": [{"operation": "extract_case_patch_neutral_input", "total_tokens": 12}],
                "checks": {"old_content_check": False},
                "provider_bound_body": "Old synthetic captured reply.",
            },
        })
    return {"split": "development", "completed": True, "new_provider_result": True,
            "source_sha256": {"old-synthetic-source.py": "old-hash"},
            "source_unchanged_during_run": True, "model": "synthetic-original-model",
            "corpus_sha256": corpus_hash, "expected_case_count": len(cases),
            "completed_case_count": len(cases), "results": rows}


def write_fixtures(replay, tmp_path, *, cases=None, unselected=None):
    cases = cases or [item()]
    corpus = tmp_path / "new-synthetic-corpus.json"
    corpus.write_text(json.dumps(cases + (unselected or [])), encoding="utf-8")
    corpus_hash = hashlib.sha256(corpus.read_bytes()).hexdigest()
    source = tmp_path / "new-synthetic-source.json"
    report = source_report(replay, cases, corpus_hash)
    source.write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "new-synthetic-replay.json"
    return corpus, source, output, report


def args(monkeypatch, corpus, source, output):
    monkeypatch.setattr(sys, "argv", ["replay", "--source-report", str(source),
                                     "--corpus", str(corpus), "--output", str(output)])


def assert_no_provider(replay):
    replay.adviser.read_secret.assert_not_called()
    replay.adviser.DeepSeekStructuredLLM.assert_not_called()


def stub_workflow(*args, **kwargs):
    return {"checks": {"current_synthetic_check": True},
            "provider_bound_body": "Current synthetic captured reply.", "captured_sends": 1}


def test_existing_output_rejected_before_reading_source_or_corpus(replay, tmp_path, monkeypatch, capsys):
    output = tmp_path / "retained.json"
    output.write_text("Do not change synthetic history", encoding="utf-8")
    read = Mock(side_effect=AssertionError("No input should be read on output conflict"))
    monkeypatch.setattr(Path, "read_bytes", read)
    args(monkeypatch, tmp_path / "unread-corpus.json", tmp_path / "unread-source.json", output)
    with pytest.raises(SystemExit) as raised:
        replay.main()
    assert raised.value.code == 2
    assert "retained results cannot be overwritten" in capsys.readouterr().err
    read.assert_not_called()
    assert output.read_text(encoding="utf-8") == "Do not change synthetic history"
    assert_no_provider(replay)


def test_exclusive_create_rejects_file_appearing_after_cli_preflight(replay, tmp_path):
    output = tmp_path / "retained.json"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        replay.write_report(output, {"replacement": True}, create=True)
    assert output.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(("change", "message"), [
    ("holdout", "holdout is forbidden"),
    ("incomplete", "must be completed"),
    ("replay", "original provider report"),
    ("no_source_hashes", "original source fingerprints"),
])
def test_invalid_source_header_rejected_without_opening_corpus(replay, tmp_path, monkeypatch, capsys, change, message):
    corpus, source, output, report = write_fixtures(replay, tmp_path)
    if change == "holdout":
        report["split"] = "holdout"
    elif change == "incomplete":
        report["completed"] = False
    elif change == "replay":
        report["new_provider_result"] = False
    else:
        del report["source_sha256"]
    source.write_text(json.dumps(report), encoding="utf-8")
    real_read = Path.read_bytes
    reads = []

    def read(path):
        reads.append(path)
        if path == corpus:
            raise AssertionError("Invalid source header must not open corpus")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    args(monkeypatch, corpus, source, output)
    with pytest.raises(SystemExit) as raised:
        replay.main()
    assert raised.value.code == 2 and message in capsys.readouterr().err
    assert reads == [source] and not output.exists()
    assert_no_provider(replay)


@pytest.mark.parametrize(("change", "message"), [
    ("corpus_hash", "corpus hash mismatch"),
    ("row_missing", "case count"),
    ("case_count", "case count"),
    ("row_id", "IDs must uniquely and exactly match"),
    ("row_incomplete", "case is incomplete"),
    ("body", "body, language or expected topics mismatch"),
    ("language", "body, language or expected topics mismatch"),
    ("topics", "body, language or expected topics mismatch"),
    ("expected_profile_updates", "expected profile updates mismatch"),
    ("profile", "seed profile mismatch"),
    ("missing_profile", "seed profile mismatch"),
    ("neutral_missing", "three-arm report"),
    ("other_arm_missing", "three-arm report"),
    ("neutral_operation", "neutral extraction attempt"),
    ("neutral_incomplete", "neutral extraction attempt"),
    ("neutral_unattempted", "neutral extraction attempt"),
    ("neutral_patch_missing", "Missing neutral raw patch"),
    ("neutral_unavailable_without_error", "Missing neutral raw patch"),
    ("neutral_bad_patch", "validation error"),
    ("neutral_error_with_patch", "contradictory raw patch"),
])
def test_mismatched_cases_rejected_before_output_or_workflow(replay, tmp_path, monkeypatch, capsys, change, message):
    corpus, source, output, report = write_fixtures(replay, tmp_path)
    row = report["results"][0]
    neutral = row["neutral_combined"]
    if change == "corpus_hash":
        report["corpus_sha256"] = "different"
    elif change == "row_missing":
        report["results"] = []
    elif change == "case_count":
        report["completed_case_count"] = 0
    elif change == "row_id":
        row["id"] = "other"
    elif change == "row_incomplete":
        row["completed"] = False
    elif change == "body":
        row["body"] = "Different synthetic request."
    elif change == "language":
        row["language"] = "zh"
    elif change == "topics":
        row["expected_topics"] = ["off_topic"]
    elif change == "expected_profile_updates":
        row["expected_profile_updates"] = {"estimated_trip_cost_gbp": 999}
    elif change == "profile":
        row["profile_before"]["estimated_trip_cost_gbp"] = 999
    elif change == "missing_profile":
        del row["profile_before"]
    elif change == "neutral_missing":
        del row["neutral_combined"]
    elif change == "other_arm_missing":
        del row["focused"]
    elif change == "neutral_operation":
        neutral["operation"] = "extract_case_patch"
    elif change == "neutral_incomplete":
        neutral["completed"] = False
    elif change == "neutral_unattempted":
        neutral["attempted"] = False
    elif change == "neutral_patch_missing":
        neutral["raw_output"] = neutral.pop("raw_patch")
    elif change == "neutral_unavailable_without_error":
        neutral["extraction_available"] = False
        del neutral["raw_patch"]
    elif change == "neutral_bad_patch":
        neutral["raw_patch"] = {"this": "must not parse"}
    elif change == "neutral_error_with_patch":
        neutral["extraction_available"] = False
        neutral["error"] = {"type": "ValueError", "message": "synthetic failure"}
    source.write_text(json.dumps(report), encoding="utf-8")
    workflow = Mock(side_effect=AssertionError("Preflight failure must not start workflow"))
    monkeypatch.setattr(replay, "exercise_workflow", workflow)
    args(monkeypatch, corpus, source, output)
    with pytest.raises(SystemExit) as raised:
        replay.main()
    assert raised.value.code == 2 and message in capsys.readouterr().err
    workflow.assert_not_called()
    assert not output.exists()
    assert_no_provider(replay)


def test_duplicate_source_ids_and_empty_development_split_are_rejected(replay):
    cases = [item(id="synthetic-one"), item(id="synthetic-two")]
    encoded = json.dumps(cases).encode()
    source = source_report(replay, cases, hashlib.sha256(encoded).hexdigest())
    source["results"][1]["id"] = source["results"][0]["id"]
    with pytest.raises(ValueError, match="IDs must uniquely and exactly match"):
        replay.verify_source_cases(source, encoded, "synthetic-policy")
    encoded = json.dumps([{"holdout": True}]).encode()
    source["corpus_sha256"] = hashlib.sha256(encoded).hexdigest()
    with pytest.raises(ValueError, match="development split is empty"):
        replay.verify_source_cases(source, encoded, "synthetic-policy")


def test_completed_replay_uses_only_saved_neutral_patch_and_retains_provenance(replay, tmp_path, monkeypatch):
    corpus, source, output, _ = write_fixtures(replay, tmp_path, unselected=[{"holdout": True}])
    source_before, corpus_before = source.read_bytes(), corpus.read_bytes()
    seen = []

    def workflow(*args, **kwargs):
        seen.append((args[3].model_dump(mode="json"), kwargs))
        return stub_workflow(*args, **kwargs)

    monkeypatch.setattr(replay, "exercise_workflow", workflow)
    monkeypatch.setattr(replay, "source_fingerprints", lambda: {"current-source.py": "new-hash"})
    real_write = replay.write_report
    checkpoints = []

    def write(path, report, **kwargs):
        checkpoints.append(deepcopy(report))
        real_write(path, report, **kwargs)

    monkeypatch.setattr(replay, "write_report", write)
    args(monkeypatch, corpus, source, output)
    replay.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] and report["all_passed"]
    assert report["exposed_deterministic_replay"]
    assert not report["new_provider_result"] and not report["new_classifier_result"]
    assert report["new_model_calls"] == 0
    assert report["source_report_sha256"] == hashlib.sha256(source_before).hexdigest()
    assert report["corpus_sha256"] == hashlib.sha256(corpus_before).hexdigest()
    assert report["original_source_sha256"] == {"old-synthetic-source.py": "old-hash"}
    assert report["current_source_sha256"] == {"current-source.py": "new-hash"}
    assert report["source_unchanged_during_replay"]
    assert report["expected_case_count"] == report["completed_case_count"] == 1
    assert report["available_replay_cases"] == report["deterministic_reply_checks_passed_cases"] == 1
    row = report["results"][0]
    assert row["usage"] == [] and row["new_model_calls"] == 0
    assert row["original_neutral_usage"] == [{"operation": "extract_case_patch_neutral_input", "total_tokens": 12}]
    assert row["original_provider_bound_body"] == "Old synthetic captured reply."
    assert row["provider_bound_body"] == "Current synthetic captured reply."
    assert row["checks"] == {"current_synthetic_check": True}
    assert row["original_neutral_checks"] == {"old_content_check": False}
    assert len(seen) == 1 and seen[0][0]["updates"] == []
    assert seen[0][1] == {"development_checks": True}
    assert not checkpoints[0]["completed"] and checkpoints[0]["completed_case_count"] == 0
    assert any(not point["completed"] and point["completed_case_count"] == 1 for point in checkpoints)
    assert source.read_bytes() == source_before and corpus.read_bytes() == corpus_before
    assert "raw_classification" not in report and "validated_classification" not in report
    assert_no_provider(replay)


def test_original_neutral_error_stays_unavailable_without_fabricated_patch(replay, tmp_path, monkeypatch):
    corpus, source, output, report = write_fixtures(replay, tmp_path)
    neutral = report["results"][0]["neutral_combined"]
    neutral.update({"extraction_available": False,
                    "error": {"type": "TimeoutError", "message": "Synthetic timeout"}, "usage": []})
    del neutral["raw_patch"]
    source.write_text(json.dumps(report), encoding="utf-8")
    workflow = Mock(side_effect=AssertionError("No workflow without neutral patch"))
    monkeypatch.setattr(replay, "exercise_workflow", workflow)
    monkeypatch.setattr(replay, "source_fingerprints", lambda: {"source": "hash"})
    args(monkeypatch, corpus, source, output)
    with pytest.raises(SystemExit) as raised:
        replay.main()
    assert raised.value.code == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    row = result["results"][0]
    assert result["completed"] and not result["all_passed"]
    assert result["available_replay_cases"] == 0 and result["unavailable_replay_cases"] == 1
    assert row["unavailable_reason"] == "original_neutral_extraction_failed"
    assert row["original_neutral_error"] == neutral["error"]
    assert "raw_patch" not in row and "validated_patch" not in row and "provider_bound_body" not in row
    assert not row["available"] and row["new_model_calls"] == 0
    workflow.assert_not_called()
    assert_no_provider(replay)


def test_current_workflow_failure_is_retained_and_does_not_mutate_saved_patch(replay, monkeypatch):
    case = item()
    source = source_report(replay, [case], "unused")
    original = source["results"][0]
    before = deepcopy(original)
    monkeypatch.setattr(replay, "exercise_workflow", Mock(side_effect=RuntimeError("Synthetic rendering failure")))
    row = replay.replay_case(case, original, "synthetic-policy")
    assert row["completed"] and not row["available"] and not row["passed"]
    assert row["error"]["message"] == "Synthetic rendering failure"
    assert row["checks"]["replay_completed_without_error"] is False
    assert row["raw_patch"] == original["neutral_combined"]["raw_patch"]
    assert original == before
    assert_no_provider(replay)


def test_actual_guard_and_captured_workflow_replay_known_facts_without_model(replay):
    body = "My trip budget is 2750 GBP."
    case = item(body=body, expected_profile_updates={"estimated_trip_cost_gbp": 2750})
    original = source_report(replay, [case], "unused")["results"][0]
    original["neutral_combined"]["raw_patch"]["updates"] = [{
        "field": "estimated_trip_cost_gbp", "value": 2750,
        "source_excerpt": body, "confidence": 0.99,
    }]
    row = replay.replay_case(case, original, replay.adviser.load_policy(replay.POLICY).version)
    assert row["available"] and row["passed"]
    assert row["profile_after"]["estimated_trip_cost_gbp"] == 2750
    assert row["checks"]["simulation_network_disabled"]
    assert row["checks"]["one_captured_send"]
    assert row["checks"]["no_pack_or_release"]
    assert row["checks"]["replay_no_second_extraction"]
    assert row["new_model_calls"] == 0 and row["usage"] == []
    assert_no_provider(replay)
