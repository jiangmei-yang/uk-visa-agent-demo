"""Probe contracts use synthetic local fixtures, never a real corpus or report."""

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[2] / "scripts/adviser_intent_probe.py"
    spec = importlib.util.spec_from_file_location("synthetic_adviser_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_item():
    return {
        "id": "synthetic-case", "body": "Thanks.", "language": "en",
        "expected_topics": [], "rationale": "Synthetic runner-contract fixture.",
    }


def synthetic_report(probe, item, corpus_hash):
    return {
        "split": "development", "completed": True, "model": probe.MODEL,
        "new_provider_result": True, "corpus_sha256": corpus_hash,
        "results": [{
            "id": item["id"], "body": item["body"], "language": item["language"],
            "expected_topics": item["expected_topics"],
            "raw_patch": {"updates": [], "ambiguities": []},
            "usage": [{"operation": "extract_case_patch"}],
            "profile_before": probe.seed_case(item, "synthetic-policy").profile.model_dump(mode="json"),
        }],
    }


def block_provider(monkeypatch, probe):
    key = Mock(side_effect=AssertionError("A synthetic contract test must not read a key"))
    model = Mock(side_effect=AssertionError("A synthetic contract test must not create a provider"))
    monkeypatch.setattr(probe, "read_secret", key)
    monkeypatch.setattr(probe, "DeepSeekStructuredLLM", model)
    return key, model


@pytest.mark.parametrize(("flags", "existing_output", "error"), [
    (["--split", "holdout"], False, "requires --allow-holdout"),
    (["--split", "development", "--allow-holdout"], False, "only meaningful"),
    (["--split", "holdout", "--allow-holdout", "--replay-report", "unused.json"],
     False, "Holdout cannot use --replay-report"),
    (["--split", "development"], True, "retained results cannot be overwritten"),
])
def test_invalid_cli_fails_before_corpus_key_or_provider(
    probe, tmp_path, monkeypatch, capsys, flags, existing_output, error,
):
    output = tmp_path / "report.json"
    if existing_output:
        output.write_text("Retained synthetic history", encoding="utf-8")
    key, model = block_provider(monkeypatch, probe)
    read_corpus = Mock(side_effect=AssertionError("Invalid arguments must not open a corpus"))
    monkeypatch.setattr(Path, "read_bytes", read_corpus)
    monkeypatch.setattr(sys, "argv", ["probe", *flags, "--output", str(output),
                                      "--corpus", str(tmp_path / "unread.json")])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert error in capsys.readouterr().err
    read_corpus.assert_not_called()
    key.assert_not_called()
    model.assert_not_called()
    if existing_output:
        assert output.read_text(encoding="utf-8") == "Retained synthetic history"
    else:
        assert not output.exists()


def test_report_create_refuses_overwrite_even_after_path_check(probe, tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("retained", encoding="utf-8")
    with pytest.raises(FileExistsError):
        probe.write_report(path, {"replacement": True}, create=True)
    assert path.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize("explicit_corpus", [True, False])
def test_selected_corpus_is_identified_hashed_and_replayed_without_a_provider(
    probe, tmp_path, monkeypatch, explicit_corpus,
):
    item = synthetic_item()
    corpus = tmp_path / "synthetic-corpus.json"
    corpus.write_text(json.dumps([item, {"holdout": True}]), encoding="utf-8")
    corpus_hash = hashlib.sha256(corpus.read_bytes()).hexdigest()
    replay = tmp_path / "synthetic-original.json"
    replay.write_text(json.dumps(synthetic_report(probe, item, corpus_hash)), encoding="utf-8")
    output = tmp_path / "synthetic-replay.json"
    key, model = block_provider(monkeypatch, probe)
    monkeypatch.setattr(probe, "CORPUS", tmp_path / "not-selected.json" if explicit_corpus else corpus)
    monkeypatch.setattr(probe, "source_fingerprints", lambda: {"synthetic-source": "unchanged"})
    monkeypatch.setattr(probe, "exercise_workflow", lambda *args, **kwargs: {
        "checks": {"synthetic_workflow_contract": True},
    })
    args = ["probe", "--split", "development", "--output", str(output),
            "--replay-report", str(replay)]
    if explicit_corpus:
        args.extend(["--corpus", str(corpus)])
    monkeypatch.setattr(sys, "argv", args)
    probe.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["corpus_id"] == str(corpus.resolve())
    assert result["corpus_sha256"] == corpus_hash
    assert result["original_corpus_sha256"] == corpus_hash
    assert result["expected_case_count"] == 1
    assert result["completed"] and result["all_passed"]
    assert result["new_provider_result"] is False
    assert result["model_calls"] == 0
    assert result["results"][0]["usage"] == []
    assert result["results"][0]["expected_profile_updates"] == {}
    assert result["results"][0]["original_usage"] == [{"operation": "extract_case_patch"}]
    key.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize(("change", "error"), [
    ("hash", "corpus hash mismatch"),
    ("model", "model or frozen corpus hash mismatch"),
    ("split", "completed development report"),
    ("incomplete", "completed development report"),
    ("replay_chain", "original real extraction"),
    ("not_provider_result", "original real extraction"),
    ("missing_case", "case count"),
    ("duplicate_case", "case count or ID uniqueness"),
    ("different_id", "IDs do not exactly match"),
    ("different_body", "body, language or expected topics mismatch"),
    ("different_language", "body, language or expected topics mismatch"),
    ("different_topics", "body, language or expected topics mismatch"),
    ("no_usage", "no real extraction usage evidence"),
])
def test_replay_requires_original_exact_selected_synthetic_cases(probe, tmp_path, change, error):
    item = synthetic_item()
    report = synthetic_report(probe, item, "synthetic-corpus-hash")
    row = report["results"][0]
    if change == "hash":
        report["corpus_sha256"] = "a-different-synthetic-corpus"
    elif change == "model":
        report["model"] = "a-different-model"
    elif change == "split":
        report["split"] = "holdout"
    elif change == "incomplete":
        report["completed"] = False
    elif change == "replay_chain":
        report["real_extraction_reused_from"] = "another-synthetic-replay.json"
    elif change == "not_provider_result":
        report["new_provider_result"] = False
    elif change == "missing_case":
        report["results"] = []
    elif change == "duplicate_case":
        report["results"].append(dict(row))
    elif change == "different_id":
        row["id"] = "other-synthetic-case"
    elif change == "different_body":
        row["body"] = "Different synthetic text."
    elif change == "different_language":
        row["language"] = "zh"
    elif change == "different_topics":
        row["expected_topics"] = ["off_topic"]
    elif change == "no_usage":
        row["usage"] = []
    path = tmp_path / "synthetic-original.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        probe.load_replay(path, [item], "synthetic-corpus-hash")


def test_replay_corpus_mismatch_rejected_before_key_provider_or_output(probe, tmp_path, monkeypatch):
    item = synthetic_item()
    corpus = tmp_path / "synthetic-corpus.json"
    corpus.write_text(json.dumps([item]), encoding="utf-8")
    replay = tmp_path / "synthetic-original.json"
    replay.write_text(json.dumps(synthetic_report(probe, item, "wrong-hash")), encoding="utf-8")
    output = tmp_path / "must-not-exist.json"
    key, model = block_provider(monkeypatch, probe)
    monkeypatch.setattr(sys, "argv", ["probe", "--split", "development", "--corpus", str(corpus),
                                      "--replay-report", str(replay), "--output", str(output)])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert not output.exists()
    key.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("original_expectations", [None, {}, {"estimated_trip_cost_gbp": 2100}])
def test_replay_requires_exact_independent_profile_expectations(probe, tmp_path, original_expectations):
    item = {**synthetic_item(), "expected_profile_updates": {"estimated_trip_cost_gbp": 2750}}
    report = synthetic_report(probe, item, "synthetic-hash")
    if original_expectations is not None:
        report["results"][0]["expected_profile_updates"] = original_expectations
    path = tmp_path / "synthetic-original.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Replay expected profile updates mismatch"):
        probe.load_replay(path, [item], "synthetic-hash")


def test_replay_accepts_matching_independent_profile_expectations(probe, tmp_path):
    expectations = {"date_of_birth": "1997-06-14", "estimated_trip_cost_gbp": 2750}
    item = {**synthetic_item(), "expected_profile_updates": expectations}
    report = synthetic_report(probe, item, "synthetic-hash")
    report["results"][0]["expected_profile_updates"] = expectations
    path = tmp_path / "synthetic-original.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    rows, metadata = probe.load_replay(path, [item], "synthetic-hash")
    assert rows[item["id"]]["expected_profile_updates"] == expectations
    assert metadata["original_corpus_sha256"] == "synthetic-hash"


@pytest.mark.parametrize(("field", "value", "body"), [
    ("date_of_birth", "1997-06-14", "My date of birth is 1997-06-14."),
    ("estimated_trip_cost_gbp", 2750, "My trip budget is 2750 GBP."),
])
@pytest.mark.parametrize("extraction_omits_update", [True, False])
def test_workflow_profile_expectations_do_not_trust_proposal_self_consistency(
    probe, field, value, body, extraction_omits_update,
):
    item = {**synthetic_item(), "body": body, "expected_profile_updates": {field: value}}
    initial = probe.seed_case(item, probe.load_policy(probe.POLICY).version)
    assert initial.profile.model_dump(mode="json")[field] != value
    event = probe.InboundEvent(
        id="synthetic-profile-event", channel="gmail",
        external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="Synthetic profile contract", body=body,
        known_profile=initial.profile.model_dump(mode="json"),
        received_at=datetime.now(UTC), rfc_message_id="<synthetic-profile@example.test>",
    )
    proposed = probe.CasePatch.model_validate({
        "updates": [] if extraction_omits_update else [{
            "field": field, "value": value, "source_excerpt": body, "confidence": 0.99,
        }],
        "ambiguities": [],
    })
    validated = probe.validate_case_patch(event, proposed)
    result = probe.exercise_workflow(item, initial, event, proposed, validated)
    # This old check passes even when extraction omitted the necessary correction.
    assert result["checks"]["profile_matches_only_grounded_updates"]
    assert result["checks"][f"expected_profile_update:{field}"] is not extraction_omits_update
    if extraction_omits_update:
        assert not all(result["checks"].values())
        assert result["profile_after"][field] != value
    else:
        assert result["profile_after"][field] == value


@pytest.mark.parametrize("explicit_empty_expectations", [True, False])
def test_old_corpus_without_profile_expectations_keeps_original_workflow_checks(
    probe, explicit_empty_expectations,
):
    item = synthetic_item()
    if explicit_empty_expectations:
        item["expected_profile_updates"] = {}
    initial = probe.seed_case(item, probe.load_policy(probe.POLICY).version)
    event = probe.InboundEvent(
        id="synthetic-no-update-event", channel="gmail",
        external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="Synthetic profile contract", body=item["body"], received_at=datetime.now(UTC),
        rfc_message_id="<synthetic-no-update@example.test>",
    )
    proposed = probe.CasePatch(updates=[], ambiguities=[])
    result = probe.exercise_workflow(item, initial, event, proposed, proposed)
    assert result["checks"]["profile_matches_only_grounded_updates"]
    assert not any(name.startswith("expected_profile_update:") for name in result["checks"])


def scope_case(probe, language="en"):
    case = probe.seed_case({**synthetic_item(), "language": language}, "synthetic-policy")
    case.question_plan = []
    case.customer_question_topics = ["off_topic"]
    return case


SCOPE_REPLY = "That question is outside UK visa preparation, so I cannot answer it here."


@pytest.mark.parametrize(("language", "body"), [
    ("en", SCOPE_REPLY),
    ("zh", "这个问题不属于英国签证准备，我无法在这里给出可靠答案。"),
])
def test_brief_off_topic_boundary_passes_without_development_only_checks(probe, language, body):
    checks = probe.content_checks(["off_topic"], scope_case(probe, language), body, language,
                                  unchanged_profile=True, development_checks=False)
    assert checks and all(checks.values())


@pytest.mark.parametrize(("body", "failed_check"), [
    ("Sorry.", "off_topic_scope_boundary"),
    (SCOPE_REPLY + "\n\nhttps://www.gov.uk/standard-visitor", "only_off_topic_no_official_visa_url"),
    (SCOPE_REPLY + "\n\nA human adviser will review it.", "only_off_topic_no_manual_legal_review"),
    (SCOPE_REPLY + "\n\nThe fee is £135.", "only_off_topic_not_answered_with_fee_or_timing"),
    (SCOPE_REPLY + "\n\n" + "Unrequested information. " * 60, "only_off_topic_reply_brief"),
    (SCOPE_REPLY + "\n\nWe'll also need these documents: passport.",
     "off_topic_no_fact_change_no_default_document_requests"),
])
def test_no_url_alone_cannot_make_an_off_topic_reply_pass(probe, body, failed_check):
    checks = probe.content_checks(["off_topic"], scope_case(probe), body, "en", unchanged_profile=True)
    assert checks[failed_check] is False
    assert not all(checks.values())


def test_off_topic_no_change_rejects_new_intake_questions_even_if_not_rendered(probe):
    case = scope_case(probe)
    case.profile.date_of_birth = None
    case.question_plan = ["date_of_birth"]
    checks = probe.content_checks(["off_topic"], case, SCOPE_REPLY, "en", unchanged_profile=True)
    assert not checks["off_topic_no_fact_change_no_new_intake_questions"]


def test_off_topic_no_change_rejects_rendered_intake_questions_even_if_not_planned(probe):
    body = SCOPE_REPLY + "\n\n" + probe.QUESTION_TEXT_EN["date_of_birth"]
    checks = probe.content_checks(["off_topic"], scope_case(probe), body, "en", unchanged_profile=True)
    assert not checks["off_topic_no_fact_change_no_new_intake_questions"]


def test_mixed_supported_clause_needs_answer_content_and_may_include_its_official_source(probe):
    case = scope_case(probe)
    expected = ["off_topic", "fees"]
    missing_answer = probe.content_checks(expected, case, SCOPE_REPLY, "en", unchanged_profile=True)
    assert not missing_answer["fees_official_source"]
    assert not missing_answer["fee_limited_to_six_month_route"]
    body = ("The 6-month Standard Visitor fee is £135.\nGOV.UK: "
            + probe.APPLICATION_URL + "\n\n" + SCOPE_REPLY)
    checks = probe.content_checks(expected, case, body, "en", unchanged_profile=True)
    assert all(checks.values())
    assert "only_off_topic_no_official_visa_url" not in checks


def test_mixed_fact_updates_are_not_blanket_suppressed_by_scope_checks(probe):
    case = scope_case(probe)
    case.profile.date_of_birth = None
    case.question_plan = ["date_of_birth"]
    body = "Your corrected travel budget has been recorded.\n\n" + SCOPE_REPLY
    checks = probe.content_checks(["off_topic"], case, body, "en", unchanged_profile=False)
    assert all(checks.values())
    assert "off_topic_no_fact_change_no_new_intake_questions" not in checks
    assert "off_topic_no_fact_change_no_default_document_requests" not in checks


def bank_reply(probe, language="en"):
    text = (
        "银行流水没有统一规定的月份数，重点是资金来源。"
        if language == "zh" else
        "The guidance does not set one fixed number of months; explain where the funds come from."
    )
    return text + "\nGOV.UK: " + probe.DOCUMENTS_URL


@pytest.mark.parametrize(("language", "extra_timing"), [
    ("en", "You can apply up to 3 months before travel."),
    ("en", "A decision usually takes up to 3 weeks."),
    ("zh", "最早可在出发前 3 个月申请。"),
    ("zh", "通常在 3 周内收到决定。"),
])
def test_bank_only_reply_rejects_extra_visa_timing_even_outside_development_checks(
    probe, language, extra_timing,
):
    body = bank_reply(probe, language) + "\n\n" + extra_timing
    checks = probe.content_checks(["bank_period"], scope_case(probe, language), body, language,
                                  development_checks=False)
    assert checks["bank_period_not_invented_fixed_rule"]
    assert not checks["bank_period_no_unrequested_visa_timing"]


def test_explicit_mixed_timing_request_is_not_suppressed_by_bank_only_check(probe):
    checks = probe.content_checks(["bank_period", "timing"], scope_case(probe),
                                  bank_reply(probe) + " A decision takes 3 weeks.", "en")
    assert "bank_period_no_unrequested_visa_timing" not in checks
    assert not checks["timing_window_and_decision_distinct"]


@pytest.mark.parametrize(("language", "incoming", "practical_answer"), [
    ("en", "How many months of statements? Where can I obtain them if I get no paper copies?",
     "Download statements from your bank portal or ask your bank for copies."),
    ("en", "How far back should bank statements go? Can I download them online?",
     "You can request statements from your bank."),
    ("zh", "银行流水要多久？网银能下载吗？", "可以从网银下载流水，或者向银行索取。"),
    ("zh", "银行流水没有纸质版，在哪里开具？", "可以联系银行申请流水。"),
])
def test_bank_acquisition_subquestion_needs_practical_answer_not_period_only(
    probe, language, incoming, practical_answer,
):
    baseline = bank_reply(probe, language)
    missing = probe.content_checks(["bank_period"], scope_case(probe, language), baseline, language,
                                   incoming_body=incoming)
    assert missing["bank_period_not_invented_fixed_rule"]
    assert not missing["bank_acquisition_practical_next_step"]
    complete = probe.content_checks(["bank_period"], scope_case(probe, language),
                                    baseline + "\n\n" + practical_answer, language,
                                    incoming_body=incoming)
    assert complete["bank_acquisition_practical_next_step"]
    assert complete["bank_acquisition_no_acceptance_guarantee"]
    assert all(complete.values())


@pytest.mark.parametrize(("language", "guarantee", "safe"), [
    ("en", "Downloaded copies will be accepted.", False),
    ("en", "Downloading copies guarantees acceptance.", False),
    ("en", "Downloading copies does not guarantee acceptance.", True),
    ("en", "This is a collection step, not a guarantee that any downloaded file will be accepted.", True),
    ("en", "There is no guarantee that a complete statement exported from the bank portal this morning will be accepted.", True),
    ("en", "Obtaining a copy does not mean that every statement exported from the bank portal will be accepted.", True),
    ("en", "I cannot guarantee that a document downloaded from your bank's mobile application will be accepted.", True),
    ("en", "Files are not automatically accepted.", True),
    ("en", "No paper copies are needed and downloaded copies will be accepted.", False),
    ("en", "The portal is not ready but the documents will be accepted.", False),
    ("en", "There is no guarantee of portal access; the copies will be accepted.", False),
    ("en", "I cannot guarantee that the bank will reply, but your files will be accepted.", False),
    ("en", "I cannot guarantee that the bank will reply and these files will be accepted.", False),
    ("en", "I cannot guarantee that the bank will reply and I guarantee acceptance.", False),
    ("en", "There is no guarantee that downloads will work. Copies will always be accepted.", False),
    ("en", "I cannot check these files. However downloaded statements are guaranteed to be accepted.", False),
    ("zh", "下载的流水一定会被接受。", False),
    ("zh", "银行流水下载后不能保证被接受。", True),
    ("zh", "不要提交截图，但是下载的流水一定会被接受。", False),
    ("zh", "无法确认下载是否成功，不过文件一定会被接受。", False),
])
def test_bank_acquisition_answer_must_not_promise_acceptance(probe, language, guarantee, safe):
    practical = "可以向银行索取流水。" if language == "zh" else "Ask your bank for statements."
    checks = probe.content_checks(["bank_period"], scope_case(probe, language),
                                  bank_reply(probe, language) + "\n\n" + practical + guarantee,
                                  language, incoming_body="Where can I download bank statements?")
    assert checks["bank_acquisition_practical_next_step"]
    assert checks["bank_acquisition_no_acceptance_guarantee"] is safe


@pytest.mark.parametrize("incoming", [
    'How many months of bank statements? I saved this quote: "Where can I download copies?"',
    "How many months of bank statements? I'm not asking where to get online copies.",
    "银行流水要覆盖多久？不用解释怎么下载。",
    "银行流水要覆盖多久？旧话是『网银能下载吗？』。",
])
def test_quoted_or_declined_bank_acquisition_is_not_a_new_subquestion(probe, incoming):
    checks = probe.content_checks(["bank_period"], scope_case(probe), bank_reply(probe), "en",
                                  incoming_body=incoming)
    assert "bank_acquisition_practical_next_step" not in checks


@pytest.mark.parametrize("incoming", [
    "Let's carry on with the application preparation. I'll send travel dates later.",
    "Please continue preparing my visa documents.",
    "I am ready to resume the visa preparation.",
    "我们继续准备签证材料，日期之后再补。",
    "请开始整理申请材料。",
])
def test_explicit_preparation_request_allows_reviewed_guidance_without_new_facts(probe, incoming):
    case = scope_case(probe)
    case.customer_question_topics = []
    case.guidance_events = {"application_overview_v1": "synthetic-event"}
    body = "Start by choosing Apply now.\nGOV.UK: " + probe.APPLICATION_URL
    checks = probe.content_checks([], case, body, "en", incoming_body=incoming,
                                  unchanged_profile=True, development_checks=True)
    assert "no_question_no_fact_change_no_proactive_intake_guidance" not in checks
    assert "no_question_no_fact_change_no_default_document_requests" not in checks
    assert checks["non_question_not_given_fee_or_timing"]


@pytest.mark.parametrize("incoming", [
    "Don't continue with application preparation yet.",
    "Please do not continue preparing the visa documents.",
    "I am not ready to resume visa preparation.",
    "If I later ask, please continue with application preparation.",
    'The old note said "Let\'s carry on with application preparation". No action now.',
    "The old note said ‘Please continue with visa preparation’.",
    "请不要继续准备签证材料。",
    "先不开始整理申请材料。",
    "旧邮件是『我们继续准备签证材料』，这次没有新问题。",
    "收到，谢谢。",
])
def test_declined_quoted_or_hypothetical_preparation_does_not_exempt_quiet_turn(probe, incoming):
    case = scope_case(probe)
    case.customer_question_topics = []
    case.guidance_events = {"application_overview_v1": "synthetic-event"}
    body = ("Start by choosing Apply now.\nGOV.UK: " + probe.APPLICATION_URL
            + "\n\nWe'll also need these documents: passport.")
    checks = probe.content_checks([], case, body, "en", incoming_body=incoming,
                                  unchanged_profile=True, development_checks=True)
    assert not checks["no_question_no_fact_change_no_proactive_intake_guidance"]
    assert not checks["no_question_no_fact_change_no_default_document_requests"]
