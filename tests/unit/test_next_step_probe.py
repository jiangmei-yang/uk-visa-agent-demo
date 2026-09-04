"""Synthetic-only evaluator contracts; no frozen corpus, secrets, provider or mailbox."""

from __future__ import annotations

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

from visa_agent.domain.models import CaseStatus, Requirement
from visa_agent.llm.ports import CasePatch


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/next_step_probe.py"
    spec = importlib.util.spec_from_file_location("synthetic_next_step_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No test network")))
    monkeypatch.setattr(module, "read_secret", Mock(side_effect=AssertionError("No real test secret")))
    monkeypatch.setattr(module, "DeepSeekStructuredLLM", Mock(side_effect=AssertionError("No real provider")))
    return module


def item(**overrides):
    return {
        "id": "offline-next-step", "language": "en", "body": "Thanks for the update.",
        "initially_paused": False, "expected_topics": [], "expected_preparation_action": None,
        "expected_profile_updates": {}, "rationale": "New synthetic contract fixture.",
        "holdout": False, **overrides,
    }


def case_patch(**overrides):
    return CasePatch.model_validate({"updates": [], "ambiguities": [], **overrides})


def question(topic, excerpt, confidence=0.99):
    return {"topic": topic, "source_excerpt": excerpt, "confidence": confidence}


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
            event.body = "Model must not mutate the saved input event."
            event.known_profile["full_name"] = "Wrong seed"
        if self.fail_before_content:
            raise ValueError("Synthetic transport failure")
        self.usage_history.append({"operation": "extract_case_patch", "input_tokens": 17,
                                   "output_tokens": 9, "total_tokens": 26})
        if isinstance(self.output, Exception):
            self.last_extraction_content = "{malformed fictional response"
            raise self.output
        self.last_extraction_content = self.output.model_dump_json()
        return self.output.model_copy(deep=True)

    def render_message(self, *args):
        raise AssertionError("No provider render call")

    def extract_customer_questions(self, *args):
        raise AssertionError("No extra question call")


def run_case(probe, fixture=None, model=None, **kwargs):
    return probe.single_case(fixture or item(), model=model or SyntheticModel(),
                             policy_version=probe.load_policy(probe.POLICY).version, **kwargs)


def stub_workflow(*args):
    return {"checks": {"offline_capture": True}, "external_sends": 0}


@pytest.mark.parametrize(("flags", "existing", "message"), [
    (["--split", "holdout"], False, "requires --allow-holdout"),
    (["--allow-holdout"], False, "only meaningful"),
    (["--split", "holdout", "--allow-holdout", "--replay-from", "source.json"], False, "development-only"),
    ([], True, "cannot be overwritten"),
])
def test_cli_rejects_unsafe_scope_before_reading_corpus_or_credentials(
    probe, monkeypatch, tmp_path, capsys, flags, existing, message,
):
    output = tmp_path / "result.json"
    if existing:
        output.write_text("Retained synthetic report", encoding="utf-8")
    read = Mock(side_effect=AssertionError("No invalid CLI corpus read"))
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", "unread.json", "--output", str(output), *flags])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert message in capsys.readouterr().err
    read.assert_not_called()
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_selected_split_only_validates_its_own_labels(probe):
    selected = item()
    unseen = {"holdout": True, "body": None, "expected_topics": "not expanded"}
    data = json.dumps([selected, unseen]).encode()
    assert probe.load_items(data, "development") == [selected]
    with pytest.raises(ValueError):
        probe.load_items(data, "holdout")


def test_omitted_optional_fact_updates_mean_no_profile_changes(probe):
    fixture = item()
    del fixture["expected_profile_updates"]
    assert probe.load_items(json.dumps([fixture]).encode(), "development") == [fixture]
    assert all(probe.patch_checks(fixture, case_patch()).values())
    result = run_case(probe, fixture)
    assert result["workflow"]["checks"]["profile_matches_expected"]


@pytest.mark.parametrize("corpus", [
    {}, [], [item(holdout="false")], [item(initially_paused=1)], [item(body="")],
    [item(expected_topics=None)], [item(expected_topics=["made_up"])],
    [item(expected_topics=[1])], [item(expected_preparation_action="confirm")],
    [item(expected_profile_updates={"estimated_trip_cost_gbp": True})],
    [item(expected_profile_updates={"date_of_birth": "May 2 1998"})],
    [item(extra_instruction="not a corpus label")], [item(), item()],
])
def test_selected_invalid_shapes_rejected_without_provider(probe, corpus):
    with pytest.raises((ValueError, TypeError)):
        probe.load_items(json.dumps(corpus).encode(), "development")
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_exact_topics_count_extras_duplicates_and_missing_requests(probe):
    fixture = item(expected_topics=["next_step", "application"])
    complete = case_patch(customer_questions=[question("next_step", "What next?"), question("application", "Which form?")])
    assert probe.patch_checks(fixture, complete)["topics_exact"]
    for questions in (
        [], [question("application", "Which form?")],
        [*complete.customer_questions, question("fees", "What fee?")],
        [*complete.customer_questions, question("next_step", "What next?")],
    ):
        assert not probe.patch_checks(fixture, case_patch(customer_questions=questions))["topics_exact"]


def test_one_default_provider_call_checkpoints_stages_and_protects_event(probe, monkeypatch):
    events = []

    def workflow(*args):
        events.append(args[2].model_dump(mode="json"))
        return stub_workflow(*args)

    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    model = SyntheticModel(mutate_event=True)
    snapshots = []
    row = run_case(probe, model=model, checkpoint=lambda row: snapshots.append(deepcopy(row)))
    assert model.calls == events == [row["input_event"]]
    assert row["model_calls"] == 1 and row["raw_patch"] == row["guarded_patch"]
    assert row["raw_checks"]["topics_exact"] and row["guarded_checks"]["topics_exact"]
    assert snapshots[0]["model_calls"] == 1 and not snapshots[0]["completed"]
    assert any(snapshot["extraction_available"] and not snapshot["guarded_available"] for snapshot in snapshots)
    assert snapshots[-1]["completed"] and row["passed"]
    assert row["usage"] == model.usage_history


def test_provider_error_retains_raw_usage_without_retry_or_repaired_patch(probe, monkeypatch):
    workflow = Mock(side_effect=AssertionError("No fallback after provider failure"))
    monkeypatch.setattr(probe, "exercise_workflow", workflow)
    model = SyntheticModel(ValueError("Invalid JSON with fictional-secret"))
    snapshots = []
    row = run_case(probe, model=model, key="fictional-secret", checkpoint=snapshots.append)
    assert len(model.calls) == 1 and row["completed"] and not row["passed"]
    assert row["raw_response_content"] == "{malformed fictional response"
    assert row["usage"] == model.usage_history
    assert "raw_patch" not in row and "guarded_patch" not in row
    assert "fictional-secret" not in json.dumps([row, *snapshots])
    assert "[REDACTED]" in row["extraction_error"]["message"]
    workflow.assert_not_called()


def test_previous_response_and_usage_do_not_leak_into_failed_call(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    model = SyntheticModel(fail_before_content=True)
    model.last_extraction_content = "old response"
    model.usage_history.append({"total_tokens": 999})
    row = run_case(probe, model=model)
    assert row["usage"] == [] and row["raw_response_content"] is None
    metrics = probe.aggregate([row])
    assert metrics["tokens"] == {} and metrics["usage_unavailable_cases"] == 1
    assert metrics["raw_topics_exact_accuracy_including_errors"] == 0


def test_guard_and_workflow_errors_are_separate_no_retries(probe, monkeypatch):
    monkeypatch.setattr(probe, "validate_case_patch", Mock(side_effect=ValueError("Guard failed")))
    model = SyntheticModel()
    row = run_case(probe, model=model)
    assert "guard_error" in row and "workflow_error" not in row
    assert len(model.calls) == 1
    monkeypatch.setattr(probe, "validate_case_patch", lambda event, output: output)
    monkeypatch.setattr(probe, "exercise_workflow", Mock(side_effect=ValueError("Workflow failed")))
    row = run_case(probe)
    assert "workflow_error" in row and row["guarded_available"]


def test_low_confidence_next_step_is_not_repaired_to_expected_topic(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    body = "What should I prepare next for my UK visa?"
    output = case_patch(customer_questions=[question("next_step", body, confidence=0.1)])
    row = run_case(probe, item(body=body, expected_topics=["next_step"]), SyntheticModel(output))
    assert row["raw_checks"]["topics_exact"] and not row["guarded_checks"]["topics_exact"]
    assert row["raw_patch"]["customer_questions"] and not row["guarded_patch"]["customer_questions"]
    assert not row["passed"]


@pytest.mark.parametrize("paused", [False, True])
def test_real_workflow_next_step_is_informational_not_resume_or_consent(probe, paused):
    body = "What should I prepare next for my UK visa?"
    fixture = item(body=body, expected_topics=["next_step"], initially_paused=paused)
    output = case_patch(customer_questions=[question("next_step", body)])
    row = run_case(probe, fixture, SyntheticModel(output))
    assert row["passed"], row
    workflow = row["workflow"]
    assert workflow["control_before"] == workflow["control_after"]
    assert workflow["next_step_advice"]["kind"] == ("paused" if paused else "document")
    assert workflow["checks"]["no_profile_or_final_confirmation"]
    assert workflow["captured_extraction_calls"] == 1 and workflow["external_sends"] == 0
    assert workflow["manual_reply_review_still_required"] and not workflow["naturalness_scored"]


@pytest.mark.parametrize("paused", [False, True])
def test_real_workflow_faq_and_step_are_both_present(probe, paused):
    first = "Where is the official UK visitor visa application form?"
    second = "What should I prepare next for my UK visa?"
    fixture = item(body=first + " " + second, expected_topics=["application", "next_step"], initially_paused=paused)
    output = case_patch(customer_questions=[question("application", first), question("next_step", second)])
    row = run_case(probe, fixture, SyntheticModel(output))
    assert row["passed"], row
    reply = row["workflow"]["outbox"][0]["body"]
    assert "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" in reply
    assert row["workflow"]["next_step_advice"]["message"] in reply
    assert row["workflow"]["expected_reviewed_faq_answers"]


def test_next_step_probe_does_not_invent_advice_for_plain_faq(probe):
    body = "Where is the official UK visitor visa application form?"
    row = run_case(probe, item(body=body, expected_topics=["application"]),
                   SyntheticModel(case_patch(customer_questions=[question("application", body)])))
    assert row["passed"], row
    assert row["workflow"]["next_step_advice"] is None


def test_oracle_rejects_advice_object_without_delivered_concrete_step(probe):
    fake = SimpleNamespace(
        next_step_advice=SimpleNamespace(kind="document", message="Next step", question_field=None, requirement_id="passport"),
        preparation_paused=False, status=CaseStatus.DRAFT,
        requirements=[SimpleNamespace(id="passport", applicable=True, satisfied=False)],
    )
    checks = probe.next_step_checks(fake, item(expected_topics=["next_step"]), "Some unrelated reply")
    assert not checks["next_step_advice_message_delivered_verbatim"]
    assert not checks["next_step_advice_not_empty_label"]
    assert not checks["next_step_document_has_collection_action"]
    assert not checks["next_step_passport_named_explicitly"]


def original_report(probe, fixture, row):
    corpus = json.dumps([fixture]).encode()
    sources = {"synthetic-source.py": "a" * 64}
    report = {
        "probe_type": "next_step", "split": "development", "holdout_authorized": False,
        "completed": True, "source_unchanged_during_run": True, "corpus_unchanged_during_run": True,
        "no_provider_retries": True, "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
        "source_sha256": sources,
        "source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        "operation": "extract_case_patch", "calls_per_case": 1, "model": "offline-fictional-model",
        "expected_case_count": 1, "completed_case_count": 1, "model_calls": 1,
        "new_provider_result": True, "evaluation_mode": "provider_run", "results": [row],
    }
    return report, corpus


def load_original(probe, report, corpus, fixture):
    return probe.load_replay(json.dumps(report).encode(), corpus_bytes=corpus, items=[fixture],
                             policy_version=probe.load_policy(probe.POLICY).version)


def test_saved_development_replay_preserves_patch_provenance_and_zero_new_calls(probe):
    fixture = item()
    row = run_case(probe, fixture)
    report, corpus = original_report(probe, fixture, row)
    assert load_original(probe, report, corpus, fixture) == report
    replay = probe.replay_case(fixture, row, policy_version=probe.load_policy(probe.POLICY).version)
    assert replay["passed"] and replay["model_calls"] == 0 and replay["usage"] == []
    assert replay["raw_patch"] == row["raw_patch"]
    assert replay["historical_usage"] == row["usage"]
    assert replay["original_result"] == row
    metrics = probe.aggregate([replay], replay=True)
    assert metrics["new_provider_calls"] == 0 and metrics["not_new_model_accuracy"]
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


@pytest.mark.parametrize(("field", "value"), [
    ("split", "holdout"), ("holdout_authorized", True), ("completed", False),
    ("source_unchanged_during_run", False), ("corpus_sha256", "0" * 64),
    ("source_bundle_sha256", "1" * 64), ("new_provider_result", False),
    ("calls_per_case", 2), ("model_calls", 2), ("probe_type", "preparation"),
    ("evaluation_mode", None), ("new_provider_result", None),
])
def test_saved_replay_rejects_invalid_scope_or_provenance(probe, field, value):
    fixture = item()
    report, corpus = original_report(probe, fixture, run_case(probe, fixture))
    report[field] = value
    with pytest.raises(ValueError):
        load_original(probe, report, corpus, fixture)


@pytest.mark.parametrize(("field", "value"), [
    ("body", "Changed body"), ("expected_topics", ["next_step"]),
    ("expected_preparation_action", "resume"), ("expected_profile_updates", {"estimated_trip_cost_gbp": 2100}),
])
def test_saved_replay_rejects_changed_customer_body_or_evaluator_labels(probe, field, value):
    fixture = item()
    report, corpus = original_report(probe, fixture, run_case(probe, fixture))
    report["results"][0][field] = value
    with pytest.raises(ValueError):
        load_original(probe, report, corpus, fixture)


def test_failed_original_extraction_remains_unavailable_in_replay(probe, monkeypatch):
    fixture = item()
    row = run_case(probe, fixture, SyntheticModel(ValueError("Unparseable synthetic output")))
    report, corpus = original_report(probe, fixture, row)
    assert load_original(probe, report, corpus, fixture) == report
    monkeypatch.setattr(probe, "exercise_workflow", Mock(side_effect=AssertionError("No invented patch")))
    replay = probe.replay_case(fixture, row, policy_version=probe.load_policy(probe.POLICY).version)
    assert not replay["passed"] and replay["model_calls"] == 0
    assert "raw_patch" not in replay and replay["extraction_error"] == row["extraction_error"]


def test_metrics_sum_observed_token_keys_only_and_disclaim_naturalness(probe, monkeypatch):
    monkeypatch.setattr(probe, "exercise_workflow", stub_workflow)
    row = run_case(probe)
    metrics = probe.aggregate([row])
    assert metrics["tokens"] == {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26}
    assert "prompt_tokens" not in metrics["tokens"]
    assert not metrics["naturalness_scored"] and metrics["manual_reply_review_still_required"]


def test_retained_report_cannot_be_replaced_even_if_path_appears_after_cli_check(probe, tmp_path):
    path = tmp_path / "retained.json"
    probe.write_report(path, {"retained": True}, create=True)
    with pytest.raises(FileExistsError):
        probe.write_report(path, {"retained": False}, create=True)
    assert json.loads(path.read_text()) == {"retained": True}


@pytest.mark.parametrize("paused", [False, True])
def test_next_step_with_history_still_requires_human_review(probe, paused):
    history = "I had a UK visitor visa application refused."
    next_step = "What should I prepare next for my UK visa?"
    output = case_patch(
        updates=[{"field": "has_serious_history", "value": True, "source_excerpt": history, "confidence": 1}],
        requires_human_review=True, customer_questions=[question("next_step", next_step)],
    )
    fixture = item(body=history + " " + next_step, initially_paused=paused,
                   expected_topics=["next_step"], expected_profile_updates={"has_serious_history": True},
                   expected_human_review=True)
    row = run_case(probe, fixture, SyntheticModel(output))
    assert row["passed"], row
    assert row["workflow"]["next_step_advice"]["kind"] == "review"
    assert row["workflow"]["checks"]["next_step_human_review_not_bypassed"]
    assert row["workflow"]["control_before"] == row["workflow"]["control_after"]


@pytest.mark.parametrize("mutation", ["event", "profile_seed", "raw_patch"])
def test_replay_rejects_changed_input_seed_or_schema_normalization(probe, mutation):
    fixture = item()
    report, corpus = original_report(probe, fixture, run_case(probe, fixture))
    row = report["results"][0]
    if mutation == "event":
        row["input_event"]["body"] = "Altered customer message"
    elif mutation == "profile_seed":
        row["workflow"]["profile_before"]["estimated_trip_cost_gbp"] = 2500
    else:
        row["raw_patch"].pop("preparation_intent")
    with pytest.raises(ValueError):
        load_original(probe, report, corpus, fixture)


def test_cli_single_provider_run_is_one_call_with_raw_capture_and_redacted_checkpoints(
    probe, monkeypatch, tmp_path,
):
    fixture = item()
    corpus = tmp_path / "synthetic-corpus.json"
    output = tmp_path / "new-result.json"
    corpus.write_text(json.dumps([fixture]), encoding="utf-8")
    model = SyntheticModel()
    model.model = probe.MODEL
    factory = Mock(return_value=model)
    monkeypatch.setattr(probe, "read_secret", Mock(return_value="fictional-only-secret"))
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", factory)
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic.py": "a" * 64})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--output", str(output)])
    probe.main()
    report = json.loads(output.read_text())
    assert report["completed"] and report["all_passed"] and report["model_calls"] == 1
    assert report["new_provider_result"] and report["source_unchanged_during_run"]
    assert len(model.calls) == 1 and report["results"][0]["raw_response_content"]
    assert report["metrics"]["tokens"]["total_tokens"] == 26
    assert "fictional-only-secret" not in output.read_text()
    factory.assert_called_once_with(probe.MODEL, api_key="fictional-only-secret", capture_raw_responses=True)


def test_cli_saved_development_replay_never_loads_credentials_or_provider(probe, monkeypatch, tmp_path):
    fixture = item()
    original, corpus_bytes = original_report(probe, fixture, run_case(probe, fixture))
    corpus = tmp_path / "synthetic-corpus.json"
    source = tmp_path / "original-provider-result.json"
    output = tmp_path / "new-replay-result.json"
    corpus.write_bytes(corpus_bytes)
    source.write_text(json.dumps(original), encoding="utf-8")
    source_before = source.read_bytes()
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic.py": "b" * 64})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--output", str(output),
                                     "--replay-from", str(source)])
    probe.main()
    report = json.loads(output.read_text())
    assert report["completed"] and report["all_passed"]
    assert report["model_calls"] == 0 and report["new_provider_calls"] == 0
    assert not report["new_provider_result"] and report["not_new_model_accuracy"]
    assert report["original_report_sha256"] == hashlib.sha256(source_before).hexdigest()
    assert report["original_report_unchanged_during_replay"] and source.read_bytes() == source_before
    assert report["historical_provider"]["observed_usage_metrics"]["tokens"]["total_tokens"] == 26
    assert report["metrics"]["tokens"] == {}
    probe.read_secret.assert_not_called()
    probe.DeepSeekStructuredLLM.assert_not_called()


def test_cli_refuses_enabled_provider_retries_before_any_extraction(probe, monkeypatch, tmp_path):
    corpus = tmp_path / "synthetic-corpus.json"
    output = tmp_path / "startup-failure.json"
    corpus.write_text(json.dumps([item()]), encoding="utf-8")
    model = SyntheticModel()
    model.client.max_retries = 1
    monkeypatch.setattr(probe, "read_secret", Mock(return_value="fictional-only-secret"))
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", Mock(return_value=model))
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic.py": "b" * 64})
    monkeypatch.setattr(sys, "argv", ["probe", "--corpus", str(corpus), "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2 and model.calls == []
    report = json.loads(output.read_text())
    assert not report["completed"] and report["model_calls"] == 0
    assert "retries must be disabled" in report["startup_error"]["message"]


@pytest.mark.parametrize(("language", "checklist", "step"), [
    ("en", "Please send the full list of documents for my UK visitor visa.",
     "What should I prepare next for my UK visa?"),
    ("zh", "请发我英国访客签证需要的材料清单。", "我现在该先准备哪一份材料？"),
])
def test_case_aware_checklist_and_next_step_do_not_require_static_faq_answer(
    probe, language, checklist, step,
):
    fixture = item(language=language, body=checklist + " " + step,
                   expected_topics=["document_checklist", "next_step"])
    output = case_patch(customer_questions=[question("document_checklist", checklist), question("next_step", step)])
    row = run_case(probe, fixture, SyntheticModel(output))
    assert row["passed"], row
    workflow = row["workflow"]
    assert workflow["expected_reviewed_faq_answers"] == []
    expected = workflow["expected_case_aware_checklist_items"]
    assert len(expected) == 5
    reply = workflow["outbox"][0]["body"]
    assert all(entry["label"] in reply for entry in expected)
    assert workflow["checks"]["checklist_request_has_case_aware_items"]
    assert workflow["checks"]["all_requested_checklist_items_delivered_verbatim"]
    assert workflow["next_step_advice"]["message"] in reply


def test_checklist_does_not_substitute_for_independent_static_faq(probe):
    checklist = "Please send the full list of documents for my UK visitor visa."
    faq = "Where is the official UK visitor visa application form?"
    step = "What should I prepare next for my UK visa?"
    fixture = item(body=" ".join([checklist, faq, step]),
                   expected_topics=["document_checklist", "application", "next_step"])
    output = case_patch(customer_questions=[question(topic, excerpt) for topic, excerpt in (
        ("document_checklist", checklist), ("application", faq), ("next_step", step),
    )])
    row = run_case(probe, fixture, SyntheticModel(output))
    assert row["passed"], row
    workflow = row["workflow"]
    assert workflow["expected_reviewed_faq_answers"] and workflow["expected_case_aware_checklist_items"]
    assert workflow["checks"]["independent_faq_answers_retained"]
    assert workflow["checks"]["all_requested_checklist_items_delivered_verbatim"]


def checklist_case(probe):
    case = probe.seed_case(item(), probe.load_policy(probe.POLICY).version)
    case.requirements = [Requirement(
        id=key, title=title, blocker=blocker, applicable=applicable, satisfied=satisfied,
        rule_version=case.policy_version, source_urls=[],
    ) for key, title, blocker, applicable, satisfied in (
        ("passport", "Valid passport", True, True, False),
        ("funding_evidence", "Funding records", True, True, False),
        ("optional", "Optional item", False, True, False),
        ("not_applicable", "Inapplicable item", True, False, False),
        ("already_satisfied", "Already checked", True, True, True),
    )]
    return case


def test_checklist_oracle_requires_each_applicable_blocker_not_only_one_label(probe):
    case = checklist_case(probe)
    fixture = item(expected_topics=["document_checklist"])
    expected = probe.checklist_items(case, fixture)
    assert [entry["requirement_id"] for entry in expected] == ["passport", "funding_evidence"]
    complete = "\n".join(entry["label"] for entry in expected)
    assert all(probe.checklist_checks(case, fixture, complete).values())
    for omitted in expected:
        incomplete = complete.replace(omitted["label"], "")
        checks = probe.checklist_checks(case, fixture, incomplete)
        assert checks["checklist_request_has_case_aware_items"]
        assert not checks["all_requested_checklist_items_delivered_verbatim"]
    assert not probe.checklist_checks(case, fixture, "Please prepare your documents")[
        "all_requested_checklist_items_delivered_verbatim"
    ]


def test_checklist_oracle_cannot_pass_vacuously_without_case_requirements(probe):
    case = probe.seed_case(item(), probe.load_policy(probe.POLICY).version)
    checks = probe.checklist_checks(case, item(expected_topics=["document_checklist"]), "No documents yet")
    assert checks == {"checklist_request_has_case_aware_items": False,
                      "all_requested_checklist_items_delivered_verbatim": False}
    assert probe.checklist_checks(case, item(expected_topics=[]), "No checklist requested") == {}


def test_aggregate_preserves_independent_raw_guarded_workflow_counts_and_input(probe):
    rows = [
        {"raw_passed": True, "guarded_passed": False, "workflow_passed": False, "passed": False,
         "raw_checks": {"topics_exact": True, "preparation_action_exact": True},
         "guarded_checks": {"topics_exact": False, "preparation_action_exact": True}},
        {"raw_passed": False, "guarded_passed": True, "workflow_passed": True, "passed": True,
         "raw_checks": {"topics_exact": True, "preparation_action_exact": False},
         "guarded_checks": {"topics_exact": True, "preparation_action_exact": True}},
        {"raw_passed": True, "guarded_passed": True, "workflow_passed": False, "passed": False,
         "raw_checks": {"topics_exact": True, "preparation_action_exact": True},
         "guarded_checks": {"topics_exact": True, "preparation_action_exact": True}},
        {"raw_passed": True, "guarded_passed": False, "workflow_passed": False, "passed": False,
         "raw_checks": {"topics_exact": True, "preparation_action_exact": True},
         "guarded_checks": {"topics_exact": False, "preparation_action_exact": True}},
    ]
    original = deepcopy(rows)
    metrics = probe.aggregate(rows)
    assert metrics["raw_passed_cases"] == 3
    assert metrics["guarded_passed_cases"] == 2
    assert metrics["workflow_passed_cases"] == 1
    assert metrics["passed_cases"] == 1
    assert rows == original
