"""Tool-free paired runner contracts using only newly invented local fixtures."""

import hashlib
import importlib.util
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from visa_agent.llm.ports import CasePatch, CustomerQuestion, CustomerQuestionBatch


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/question_understanding_probe.py"
    spec = importlib.util.spec_from_file_location("synthetic_question_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No network in synthetic contracts")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No network in synthetic contracts")))
    monkeypatch.setattr(module, "read_secret", Mock(side_effect=AssertionError("No real secret in synthetic contracts")))
    monkeypatch.setattr(module, "DeepSeekStructuredLLM", Mock(side_effect=AssertionError("No real provider in synthetic contracts")))
    return module


def synthetic_item(**overrides):
    return {
        "id": "new-synthetic-case", "body": "Thanks.", "language": "en",
        "expected_topics": [], "rationale": "New synthetic runner fixture, not corpus data.",
        **overrides,
    }


def question(topic="off_topic", excerpt="How do I bake bread?", confidence=0.99):
    return CustomerQuestion(topic=topic, source_excerpt=excerpt, confidence=confidence)


def empty_patch(**overrides):
    return CasePatch.model_validate({"updates": [], "ambiguities": [], **overrides})


class SyntheticModel:
    """Exactly one controlled outcome for each operation, with observable calls."""

    def __init__(self, baseline=None, focused=None, *, neutral=None, mutate_event=False):
        self.baseline = baseline if baseline is not None else empty_patch()
        self.focused = focused if focused is not None else CustomerQuestionBatch(customer_questions=[])
        self.neutral = neutral if neutral is not None else empty_patch()
        self.mutate_event = mutate_event
        self.usage_history = []
        self.calls = []
        self.last_extraction_content = None
        self.last_question_content = None

    def _call(self, operation, event, output, content_field):
        self.calls.append((operation, event.model_dump(mode="json")))
        if self.mutate_event:
            event.body = "This mutation must never enter the other arm."
            event.known_profile["full_name"] = "Changed only in this local model copy"
        self.usage_history.append({"operation": operation, "prompt_tokens": 7,
                                   "completion_tokens": 3, "total_tokens": 10})
        if isinstance(output, Exception):
            setattr(self, content_field, "{malformed synthetic JSON")
            raise output
        setattr(self, content_field, output.model_dump_json())
        return output.model_copy(deep=True)

    def extract_case_patch(self, event):
        raise AssertionError("The probe baseline must use the explicit legacy input, not the production default")

    def extract_case_patch_legacy_input(self, event):
        return self._call("extract_case_patch_legacy_input", event, self.baseline, "last_extraction_content")

    def extract_customer_questions(self, event):
        return self._call("extract_customer_questions", event, self.focused, "last_question_content")

    def extract_case_patch_neutral_input(self, event):
        return self._call("extract_case_patch_neutral_input", event, self.neutral, "last_extraction_content")


def stub_workflow(*args, **kwargs):
    proposed = args[3]
    return {
        "checks": {"synthetic_workflow_guard": True},
        "provider_bound_body": "Captured fictional reply.",
        "accepted_update_fields": sorted(update.field for update in proposed.updates),
        "captured_sends": 1,
    }


def run_pair(probe, *, item=None, model=None, index=0, **kwargs):
    return probe.paired_case(
        item or synthetic_item(), index=index,
        policy_version=probe.adviser.load_policy(probe.POLICY).version,
        model=model or SyntheticModel(), development_checks=True, **kwargs,
    )


@pytest.mark.parametrize(("flags", "existing_output", "error"), [
    (["--split", "holdout"], False, "requires --allow-holdout"),
    (["--split", "development", "--allow-holdout"], False, "only meaningful"),
    (["--split", "development"], True, "retained results cannot be overwritten"),
])
def test_invalid_cli_rejected_before_corpus_secret_or_provider(
    probe, tmp_path, monkeypatch, capsys, flags, existing_output, error,
):
    output = tmp_path / "new-report.json"
    if existing_output:
        output.write_text("retained synthetic history", encoding="utf-8")
    corpus_read = Mock(side_effect=AssertionError("Bad CLI must not inspect corpus"))
    monkeypatch.setattr(Path, "read_bytes", corpus_read)
    monkeypatch.setattr(sys, "argv", ["probe", *flags, "--corpus", str(tmp_path / "unread.json"),
                                      "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert error in capsys.readouterr().err
    corpus_read.assert_not_called()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()
    if existing_output:
        assert output.read_text(encoding="utf-8") == "retained synthetic history"
    else:
        assert not output.exists()


def test_corpus_is_required_and_no_replay_flag_exists(probe, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["probe", "--split", "development", "--output", str(tmp_path / "absent.json")])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "--corpus" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["probe", "--split", "development", "--corpus", "unused.json",
                                      "--output", str(tmp_path / "absent.json"), "--replay-report", "old.json"])
    with pytest.raises(SystemExit):
        probe.main()
    assert "unrecognized arguments" in capsys.readouterr().err
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


@pytest.mark.parametrize("cases", [[], [{"holdout": True}]])
def test_empty_split_rejected_before_secret_provider_or_output(probe, monkeypatch, tmp_path, capsys, cases):
    corpus = tmp_path / "synthetic-empty.json"
    corpus.write_text(json.dumps(cases), encoding="utf-8")
    output = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--split", "development", "--corpus", str(corpus), "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "Selected split is empty" in capsys.readouterr().err
    assert not output.exists()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_exclusive_output_create_preserves_existing_file(probe, tmp_path):
    output = tmp_path / "retained.json"
    output.write_text("keep this synthetic history", encoding="utf-8")
    with pytest.raises(FileExistsError):
        probe.write_report(output, {"replacement": True}, create=True)
    assert output.read_text(encoding="utf-8") == "keep this synthetic history"


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_one_call_per_arm_alternates_order_with_identical_unmutated_input(probe, monkeypatch, index):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    model = SyntheticModel(mutate_event=True)
    checkpoints = []
    row = run_pair(probe, model=model, index=index,
                   checkpoint=lambda row: checkpoints.append(deepcopy(row)))
    operations = [call[0] for call in model.calls]
    expected_order = ["extract_case_patch_legacy_input", "extract_case_patch_neutral_input", "extract_customer_questions"]
    shift = index % 3
    assert operations == expected_order[shift:] + expected_order[:shift]
    assert model.calls[0][1] == model.calls[1][1] == model.calls[2][1]
    assert model.calls[0][1]["body"] == "Thanks."
    assert row["model_calls"] == 3 and row["completed"]
    assert row["baseline"]["usage"] == [model.usage_history[operations.index("extract_case_patch_legacy_input")]]
    assert row["focused"]["usage"] == [model.usage_history[operations.index("extract_customer_questions")]]
    assert row["neutral_combined"]["usage"] == [model.usage_history[operations.index("extract_case_patch_neutral_input")]]
    first, second, third = row["call_order"]
    assert any(point[first].get("completed") and not point[second].get("attempted") for point in checkpoints)
    assert any(point[second].get("attempted") and not point[second].get("completed") for point in checkpoints)
    assert any(point[third].get("attempted") and not point[third].get("completed") for point in checkpoints)
    assert all(point["model_calls"] <= 3 for point in checkpoints)
    assert checkpoints[-1]["completed"]


def test_focused_replaces_only_questions_and_preserves_all_shared_fields(probe, monkeypatch):
    body = "My trip budget is 2750 GBP. I do not know my travel dates. How do I bake bread?"
    baseline = empty_patch(
        updates=[{"field": "estimated_trip_cost_gbp", "value": 2750,
                  "source_excerpt": "My trip budget is 2750 GBP.", "confidence": 0.99}],
        ambiguities=["Synthetic unchanged ambiguity."], requires_human_review=True,
        question_deferrals=[{"field": "planned_arrival_date", "source_excerpt": "I do not know my travel dates.", "confidence": 0.99}],
    )
    batch = CustomerQuestionBatch(customer_questions=[question()])
    baseline_before, focused_before = baseline.model_dump(), batch.model_dump()
    observed = []

    def workflow(*args, **kwargs):
        observed.append(args[3].model_dump(mode="json"))
        return stub_workflow(*args, **kwargs)

    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    composer = Mock(wraps=probe.with_customer_questions)
    monkeypatch.setattr(probe, "with_customer_questions", composer)
    row = run_pair(probe, item=synthetic_item(body=body, expected_topics=["off_topic"]),
                   model=SyntheticModel(baseline, batch))
    composer.assert_called_once()
    assert len(observed) == 3
    assert observed[1]["customer_questions"] == []
    assert observed[2]["customer_questions"] == [question().model_dump(mode="json")]
    assert {k: v for k, v in observed[1].items() if k != "customer_questions"} == {
        k: v for k, v in observed[2].items() if k != "customer_questions"
    }
    assert baseline.model_dump() == baseline_before and batch.model_dump() == focused_before
    assert row["focused"]["checks"]["shared_baseline_fields_preserved"]
    assert row["focused"]["checks"]["validated_shared_baseline_fields_preserved"]
    assert row["focused"]["accepted_update_fields"] == ["estimated_trip_cost_gbp"]


@pytest.mark.parametrize("failed_arm", ["baseline", "neutral_combined", "focused", "all"])
def test_api_failures_keep_errors_raw_usage_and_never_retry_or_invent_baseline(probe, monkeypatch, failed_arm):
    workflow = Mock(side_effect=stub_workflow)
    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    failure = ValueError("Malformed response containing synthetic-secret")
    model = SyntheticModel(
        baseline=failure if failed_arm in {"baseline", "all"} else empty_patch(),
        focused=failure if failed_arm in {"focused", "all"} else CustomerQuestionBatch(customer_questions=[question()]),
        neutral=failure if failed_arm in {"neutral_combined", "all"} else empty_patch(),
    )
    row = run_pair(probe, item=synthetic_item(body="How do I bake bread?", expected_topics=["off_topic"]),
                   model=model, key="synthetic-secret")
    assert len(model.calls) == 3 and row["model_calls"] == 3
    for name in probe.ARMS:
        arm = row[name]
        assert len(arm["usage"]) == 1
        assert arm["extraction_latency_seconds"] >= 0
        if failed_arm in {name, "all"}:
            assert not arm["extraction_available"]
            assert arm["error"]["message"] == "Malformed response containing [REDACTED]"
            assert arm["raw_response_content"] == "{malformed synthetic JSON"
            assert "raw_topic_metrics" not in arm
    if failed_arm in {"baseline", "all"}:
        assert workflow.call_count == (1 if failed_arm == "baseline" else 0)
        assert row["focused"]["workflow_unavailable_reason"] == "combined_baseline_extraction_failed"
        assert "raw_patch" not in row["focused"] and "validated_patch" not in row["focused"]
        assert row["focused"]["question_passed"] is None
        if failed_arm == "baseline":
            assert row["focused"]["raw_topic_metrics"]["exact"]
    else:
        assert workflow.call_count == 2
        assert row["baseline"]["workflow_available"]
    assert row["paired_workflow_available"] is (failed_arm == "neutral_combined")
    assert row["completed"]


def test_failed_workflow_does_not_discard_either_api_result_or_skip_other_arm(probe, monkeypatch):
    workflow = Mock(side_effect=[stub_workflow(None, None, None, empty_patch()),
                                RuntimeError("synthetic workflow fault"),
                                stub_workflow(None, None, None, empty_patch())])
    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    model = SyntheticModel()
    row = run_pair(probe, model=model)
    assert len(model.calls) == 3 and workflow.call_count == 3
    assert row["baseline"]["raw_output"] == row["focused"]["raw_patch"]
    assert row["baseline"]["workflow_error"]["type"] == "RuntimeError"
    assert not row["baseline"]["workflow_available"]
    assert row["focused"]["workflow_available"]


def test_real_guard_and_captured_workflow_preserve_corrected_facts(probe):
    body = "My trip budget is 2750 GBP. How do I bake bread?"
    baseline = empty_patch(updates=[{
        "field": "estimated_trip_cost_gbp", "value": 2750,
        "source_excerpt": "My trip budget is 2750 GBP.", "confidence": 0.99,
    }])
    row = run_pair(probe, item=synthetic_item(body=body, expected_topics=["off_topic"],
                                            expected_profile_updates={"estimated_trip_cost_gbp": 2750}),
                   model=SyntheticModel(baseline, CustomerQuestionBatch(customer_questions=[question()])))
    for name in ("baseline", "focused"):
        arm = row[name]
        assert arm["workflow_available"]
        assert arm["profile_after"]["estimated_trip_cost_gbp"] == 2750
        assert arm["checks"]["simulation_network_disabled"]
        assert arm["checks"]["replay_no_second_extraction"]
        assert arm["checks"]["one_captured_send"]
        assert arm["checks"]["no_pack_or_release"]
    assert row["baseline"]["profile_after"] == row["focused"]["profile_after"]
    assert row["focused"]["actual_topics"] == ["off_topic"]
    assert row["shared_baseline_fact_evaluation"]["missed_fields"] == []


def test_missing_shared_fact_correction_is_not_a_focused_question_error(probe):
    item = synthetic_item(body="My trip budget is 2750 GBP.",
                          expected_profile_updates={"estimated_trip_cost_gbp": 2750})
    row = run_pair(probe, item=item)
    for name in ("baseline", "focused"):
        arm = row[name]
        assert arm["workflow_available"]
        assert not arm["checks"]["expected_profile_update:estimated_trip_cost_gbp"]
        assert "expected_profile_update:estimated_trip_cost_gbp" not in arm["question_checks"]
        assert not arm["passed"]
        assert arm["profile_after"]["estimated_trip_cost_gbp"] == 1500
    facts = row["shared_baseline_fact_evaluation"]
    assert facts["missed_fields"] == ["estimated_trip_cost_gbp"]
    assert facts["not_attributable_to_focused_question_pass"]
    assert row["baseline"]["question_passed"] == row["focused"]["question_passed"]


def test_neutral_combined_uses_own_facts_without_repairing_shared_baseline(probe):
    body = "My trip budget is 3100 GBP."
    item = synthetic_item(body=body, expected_profile_updates={"estimated_trip_cost_gbp": 3100})
    neutral = empty_patch(updates=[{
        "field": "estimated_trip_cost_gbp", "value": 3100,
        "source_excerpt": body, "confidence": 0.99,
    }])
    row = run_pair(probe, item=item, model=SyntheticModel(neutral=neutral))
    assert row["neutral_combined"]["workflow_available"]
    assert row["neutral_combined"]["profile_after"]["estimated_trip_cost_gbp"] == 3100
    assert row["neutral_combined"]["checks"]["expected_profile_update:estimated_trip_cost_gbp"]
    for name in ("baseline", "focused"):
        assert row[name]["profile_after"]["estimated_trip_cost_gbp"] == 1500
        assert row[name]["raw_patch"]["updates"] == []
        assert not row[name]["checks"]["expected_profile_update:estimated_trip_cost_gbp"]
    assert row["shared_baseline_fact_evaluation"]["missed_fields"] == ["estimated_trip_cost_gbp"]
    assert row["neutral_combined_fact_evaluation"]["missed_fields"] == []
    assert row["neutral_combined_fact_evaluation"]["uses_own_fact_extraction"]


def test_aggregate_distinguishes_wins_regressions_unavailable_and_fact_misses(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    item = synthetic_item(body="How do I bake bread?", expected_topics=["off_topic"])
    yes_patch = empty_patch(customer_questions=[question()])
    yes_batch = CustomerQuestionBatch(customer_questions=[question()])
    rows = [
        run_pair(probe, item=item, model=SyntheticModel(empty_patch(), yes_batch)),
        run_pair(probe, item=item, model=SyntheticModel(yes_patch, CustomerQuestionBatch(customer_questions=[]))),
        run_pair(probe, item=item, model=SyntheticModel(yes_patch, yes_batch)),
        run_pair(probe, item=item, model=SyntheticModel(ValueError("synthetic failure"), yes_batch)),
    ]
    rows[0]["shared_baseline_fact_evaluation"]["missed_fields"] = ["estimated_trip_cost_gbp"]
    metrics = probe.aggregate_pairs(rows)
    paired = metrics["paired"]["baseline_to_focused"]["raw_classification"]
    assert paired == {"both_pass": 1, "baseline_only_pass": 1, "candidate_only_pass": 1,
                      "both_fail": 0, "unavailable_pairs": 1, "evaluated_pairs": 3,
                      "candidate_minus_baseline_pass_rate": 0.0}
    assert metrics["paired"]["baseline_to_neutral_combined"]["raw_classification"]["evaluated_pairs"] == 3
    assert metrics["baseline"]["extraction_errors"] == 1
    assert metrics["focused"]["raw_classification"]["evaluated"] == 4
    assert metrics["focused"]["validated_classification"]["evaluated"] == 3
    assert metrics["baseline"]["usage_totals"]["total_tokens"] == 40
    assert metrics["focused"]["usage_totals"]["total_tokens"] == 40
    assert metrics["fact_evaluation"]["shared_baseline_fact_miss_cases"] == 1
    assert metrics["fact_evaluation"]["shared_baseline_fact_missed_fields"] == 1
    cost = metrics["architecture_cost"]
    assert cost["focused_pipeline"]["calls_per_case"] == 2
    assert cost["focused_pipeline"]["usage_totals"]["total_tokens"] == 80
    assert cost["neutral_combined_pipeline"]["calls_per_case"] == 1
    assert cost["neutral_combined_pipeline"]["usage_totals"]["total_tokens"] == 40


@pytest.mark.parametrize("split", ["development", "holdout"])
def test_complete_cli_checkpoints_exact_selected_synthetic_split(probe, monkeypatch, tmp_path, split):
    selected = synthetic_item(id="synthetic-selected", holdout=split == "holdout")
    unselected = {"holdout": split != "holdout", "not_a_valid_case": "must not be evaluated"}
    corpus = tmp_path / "new-synthetic-corpus.json"
    corpus.write_text(json.dumps([selected, unselected]), encoding="utf-8")
    corpus_hash = hashlib.sha256(corpus.read_bytes()).hexdigest()
    output = tmp_path / "new-paired-report.json"
    fake_key = Mock(return_value="fictional-test-key")
    model = SyntheticModel()
    provider = Mock(return_value=model)
    monkeypatch.setattr(probe, "read_secret", fake_key)
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", provider)
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic-source.py": "synthetic-hash"})
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    real_write = probe.write_report
    checkpoints = []

    def write(path, report, **kwargs):
        checkpoints.append(deepcopy(report))
        real_write(path, report, **kwargs)

    monkeypatch.setattr(probe, "write_report", write)
    args = ["probe", "--corpus", str(corpus), "--split", split, "--output", str(output)]
    if split == "holdout":
        args.append("--allow-holdout")
    monkeypatch.setattr(sys, "argv", args)
    probe.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["corpus_id"] == str(corpus.resolve())
    assert report["corpus_sha256"] == corpus_hash
    assert report["source_sha256"] == {"synthetic-source.py": "synthetic-hash"}
    assert report["source_unchanged_during_run"]
    assert report["completed"] and report["all_passed"]
    assert report["expected_case_count"] == report["completed_case_count"] == 1
    assert report["model_calls"] == 3
    assert report["holdout_authorized"] is (split == "holdout")
    assert report["development_content_checks"] is (split == "development")
    assert [row["id"] for row in report["results"]] == [selected["id"]]
    assert not checkpoints[0]["completed"] and checkpoints[0]["model_calls"] == 0
    assert any(point["model_calls"] == 1 for point in checkpoints)
    fake_key.assert_called_once()
    provider.assert_called_once_with(probe.MODEL, api_key="fictional-test-key", capture_raw_responses=True)


def test_missing_key_retains_startup_checkpoint_with_zero_calls(probe, monkeypatch, tmp_path):
    corpus = tmp_path / "synthetic-corpus.json"
    corpus.write_text(json.dumps([synthetic_item()]), encoding="utf-8")
    output = tmp_path / "new-report.json"
    monkeypatch.setattr(probe, "read_secret", Mock(return_value=None))
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"source": "hash"})
    monkeypatch.setattr(sys, "argv", ["probe", "--split", "development", "--corpus", str(corpus), "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert raised.value.code == 2
    assert not report["completed"] and report["model_calls"] == 0
    assert "no API request made" in report["startup_error"]
    probe.DeepSeekStructuredLLM.assert_not_called()
