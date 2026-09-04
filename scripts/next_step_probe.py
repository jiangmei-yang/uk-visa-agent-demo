"""One-call next-step/FAQ evaluation with a network-disabled real local workflow.

Only fictional mail from the explicitly selected split is used. Holdout requires
--allow-holdout. Reports never overwrite an existing path. --replay-from accepts
only the original development provider report and makes zero new provider calls.
The single-call/checkpoint/usage/replay transport is reused from the preparation
probe through a private helper module; its files and reports are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args
from unittest.mock import patch

from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, CustomerQuestion
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import explained_document_label
from visa_agent.workflow.customer_questions import grounded_customer_answers
from visa_agent.workflow.service import WorkflowService

_helper_spec = importlib.util.spec_from_file_location(
    "_next_step_probe_transport", Path(__file__).with_name("preparation_control_probe.py"),
)
assert _helper_spec is not None and _helper_spec.loader is not None
transport = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(transport)
REPOSITORY = transport.REPOSITORY
POLICY = transport.POLICY
MODEL = transport.MODEL
REVIEWED_AS_OF = transport.REVIEWED_AS_OF
CONTROL_FIELDS = transport.CONTROL_FIELDS
LABEL_FIELDS = {
    "id", "language", "body", "initially_paused", "expected_topics", "expected_profile_updates",
    "expected_preparation_action", "rationale", "holdout", "expected_human_review",
}
ALLOWED_TOPICS = {
    "application", "timing", "translation", "booking", "fees", "bank_period",
    "document_checklist", "unsupported", "off_topic", "next_step",
}
write_report = transport.write_report
redact = transport.redact
error_record = transport.error_record


def assert_schema_ready() -> None:
    transport.assert_schema_ready()
    if "next_step_advice" not in Case.model_fields or "next_step" not in get_args(
        CustomerQuestion.model_fields["topic"].annotation,
    ):
        raise ValueError("Next-step runtime schema is not ready")


def source_fingerprints() -> dict[str, str]:
    sources = transport.source_fingerprints()
    sources[str(Path(__file__).relative_to(REPOSITORY))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return sources


def _control_item(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "expected_action": item.get("expected_preparation_action")}


def load_items(corpus_bytes: bytes, split: str) -> list[dict[str, Any]]:
    """Validate only selected labels; unselected cases remain unexpanded metadata."""
    corpus = json.loads(corpus_bytes)
    if not isinstance(corpus, list) or any(
        not isinstance(item, dict) or type(item.get("holdout")) is not bool for item in corpus
    ):
        raise ValueError("Corpus must be an array with explicit boolean holdout markers")
    if split not in {"development", "holdout"}:
        raise ValueError("Unknown split")
    selected = [item for item in corpus if item["holdout"] == (split == "holdout")]
    # Reuse canonical profile/control validation only on the selected split.
    transport.load_items(json.dumps([_control_item(item) for item in selected]).encode(), split)
    for item in selected:
        if "expected_preparation_action" not in item:
            raise ValueError("expected_preparation_action must be explicit")
        topics = item.get("expected_topics")
        if (not isinstance(topics, list) or any(not isinstance(topic, str) or topic not in ALLOWED_TOPICS
                                               for topic in topics)):
            raise ValueError("expected_topics must be an array of supported topics")
        if "rationale" not in item:
            raise ValueError("Rationale must be explicit")
        if set(item) - LABEL_FIELDS:
            raise ValueError("Unknown evaluator labels")
    return selected


def seed_case(item: dict[str, Any], policy_version: str) -> Case:
    """Same fixed fictional adult, unknown deferred dates, no documents or consent."""
    return transport.seed_case(item, policy_version)


def patch_checks(item: dict[str, Any], proposed: CasePatch) -> dict[str, bool]:
    values = {update.field: update.value for update in proposed.updates}
    action = proposed.preparation_intent.action if proposed.preparation_intent else None
    checks = {
        "topics_exact": Counter(question.topic for question in proposed.customer_questions)
        == Counter(item["expected_topics"]),
        "preparation_action_exact": action == item["expected_preparation_action"],
    }
    checks.update({f"expected_profile_update:{field}": values.get(field) == value and field in values
                   for field, value in item.get("expected_profile_updates", {}).items()})
    if "expected_human_review" in item:
        checks["expected_human_review"] = proposed.requires_human_review == item["expected_human_review"]
    return checks


def next_step_checks(case: Case, item: dict[str, Any], body: str) -> dict[str, bool]:
    """Check delivery and case relevance, not subjective naturalness or policy correctness."""
    expected = "next_step" in item["expected_topics"]
    advice = case.next_step_advice
    checks = {"next_step_advice_presence_exact": (advice is not None) == expected}
    if not expected or advice is None:
        return checks
    checks["next_step_advice_message_delivered_verbatim"] = bool(advice.message.strip()) and advice.message in body
    checks["next_step_advice_not_empty_label"] = len(advice.message.strip()) >= 12
    if case.preparation_paused:
        checks["next_step_paused_is_conditional_not_resume"] = (
            advice.kind in {"paused", "review"} and advice.question_field is None and case.question_plan == []
            and bool(re.search(r"恢复|继续准备时|之后|等你|如果|\b(?:when|if|later|after)\b", advice.message, re.I))
        )
    if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
        checks["next_step_human_review_not_bypassed"] = advice.kind == "review" and advice.question_field is None
    elif case.preparation_paused:
        checks["next_step_paused_preserves_information_only"] = advice.kind == "paused"
    elif advice.kind == "question":
        field = advice.question_field
        checks["next_step_question_is_single_current_missing_fact"] = (
            field is not None and field in type(case.profile).model_fields
            and getattr(case.profile, field) is None and field not in case.deferred_fields
            and case.question_plan == [field] and case.last_requested_fields == [field]
        )
    elif advice.kind == "document":
        checks["next_step_document_is_one_current_unsatisfied_requirement"] = (
            advice.question_field is None and advice.requirement_id is not None
            and any(requirement.id == advice.requirement_id and requirement.applicable and not requirement.satisfied
                    for requirement in case.requirements)
        )
        checks["next_step_document_has_collection_action"] = bool(re.search(
            r"发|上传|提供|准备|扫描|拍|整理|收集|索取|\b(?:send|upload|provide|prepare|scan|copy|collect|request|gather)\b",
            advice.message, re.I,
        ))
        # The fixed seed has no documents: a vague 'prepare materials' label must
        # not satisfy the expected first document's concrete identity.
        if advice.requirement_id == "passport":
            checks["next_step_passport_named_explicitly"] = bool(re.search(r"护照|旅行证件|\bpassport\b", advice.message, re.I))
    else:
        checks["next_step_waiting_only_when_no_actionable_gap"] = advice.kind == "waiting" and not any(
            requirement.applicable and not requirement.satisfied for requirement in case.requirements
        )
    return checks


def checklist_items(case: Case, item: dict[str, Any]) -> list[dict[str, str]]:
    """The full requested list comes from the current case, not static FAQ answers."""
    if "document_checklist" not in item["expected_topics"]:
        return []
    return [{"requirement_id": requirement.id, "label": explained_document_label(case, requirement)}
            for requirement in case.requirements
            if requirement.applicable and requirement.blocker and not requirement.satisfied]


def checklist_checks(case: Case, item: dict[str, Any], body: str) -> dict[str, bool]:
    if "document_checklist" not in item["expected_topics"]:
        return {}
    expected = checklist_items(case, item)
    return {
        # This evaluator's fictional seed has no supporting documents. An empty
        # requirements list cannot silently turn a checklist request into a pass.
        "checklist_request_has_case_aware_items": bool(expected),
        "all_requested_checklist_items_delivered_verbatim": bool(expected) and all(
            entry["label"] in body for entry in expected
        ),
    }


def exercise_workflow(
    item: dict[str, Any], initial: Case, event: InboundEvent, proposed: CasePatch, guarded: CasePatch,
) -> dict[str, Any]:
    captured = transport.CapturedLLM(proposed)
    with (
        patch("socket.socket.connect", side_effect=AssertionError("Probe workflow network disabled")),
        patch("socket.create_connection", side_effect=AssertionError("Probe workflow network disabled")),
        tempfile.TemporaryDirectory(prefix="visa-next-step-probe-") as directory,
    ):
        store = SQLiteStore(Path(directory) / "case.db")
        try:
            store.save_case(initial)
            workflow = WorkflowService(store, load_policy(POLICY), captured, today_provider=lambda: REVIEWED_AS_OF)
            case, duplicate, plan = workflow.process(event)
            rows = store.list_outbox()
            body = "\n\n".join(row["payload"] for row in rows)
            action = item["expected_preparation_action"]
            expected_paused = item["initially_paused"] if action is None else action == "pause"
            changed = expected_paused != item["initially_paused"]
            before = initial.profile.model_dump(mode="json")
            after = case.profile.model_dump(mode="json")
            has_static_faq = any(topic not in {"next_step", "document_checklist"}
                                 for topic in item["expected_topics"])
            expected_faq = grounded_customer_answers(
                event.body, item["language"], REVIEWED_AS_OF,
                semantic_questions=[question for question in guarded.customer_questions
                                    if question.topic not in {"next_step", "document_checklist"}],
            ) if has_static_faq else []
            checks = {
                "workflow_first_processing_not_duplicate": not duplicate,
                "workflow_no_extraction_fallback": not workflow.llm.last_extraction_fallback,
                "workflow_topics_exact": Counter(case.customer_question_topics) == Counter(item["expected_topics"]),
                "profile_matches_expected": after == {**before, **item.get("expected_profile_updates", {})},
                "paused_state_exact": case.preparation_paused == expected_paused,
                "control_epoch_exact": case.preparation_control_epoch == initial.preparation_control_epoch + int(changed),
                "transition_action_exact": case.latest_preparation_action == (action if changed else None),
                "transition_event_exact": case.preparation_control_event_id == (event.id if changed else initial.preparation_control_event_id),
                "no_profile_or_final_confirmation": not case.profile_confirmed and not case.final_summary_confirmed,
                "no_pack_or_release": case.delivery_path is None and plan != "ready",
                "single_isolated_case_and_outbox": len(store.list_cases()) == 1 and case.id == initial.id and len(rows) == 1,
                "no_provider_delivery": all(row["status"] != "SENT" for row in rows),
                "all_customer_answers_delivered_verbatim": all(answer in body for answer in case.customer_answers),
                "independent_faq_answers_retained": all(answer in body for answer in expected_faq),
                "faq_request_has_reviewed_answer": not has_static_faq or bool(expected_faq),
                **checklist_checks(case, item, body),
                **next_step_checks(case, item, body),
            }
            if expected_paused:
                checks["paused_no_questions_or_confirmation"] = (
                    case.question_plan == [] and case.last_requested_fields == []
                    and plan not in {"awaiting_confirmation", "awaiting_profile_confirmation"}
                    and all(value is None for value in (
                        case.confirmation_kind, case.confirmation_fingerprint, case.confirmation_request_event_id,
                    ))
                )
            if "expected_human_review" in item:
                checks["expected_human_review"] = (case.status == CaseStatus.HUMAN_REVIEW_REQUIRED) == item["expected_human_review"]
            persisted = store.get_case_by_thread(initial.external_thread_id)
            checks["control_and_advice_persisted"] = persisted is not None and all(
                getattr(persisted, field) == getattr(case, field) for field in (*CONTROL_FIELDS, "next_step_advice")
            )
            _, replayed, replay_plan = workflow.process(event)
            checks["duplicate_no_new_outbox_or_extraction"] = (
                replayed and replay_plan == "duplicate_ignored" and captured.extract_calls == 1
                and len(store.list_outbox()) == len(rows)
            )
            return {
                "checks": checks, "plan": plan, "case_status": case.status.value,
                "profile_before": before, "profile_after": after,
                "control_before": {name: getattr(initial, name) for name in CONTROL_FIELDS},
                "control_after": {name: getattr(case, name) for name in CONTROL_FIELDS},
                "workflow_topics": case.customer_question_topics, "question_plan": case.question_plan,
                "last_requested_fields": case.last_requested_fields,
                "next_step_advice": case.next_step_advice.model_dump(mode="json") if case.next_step_advice else None,
                "consent_before": {"profile_confirmed": initial.profile_confirmed,
                                   "final_summary_confirmed": initial.final_summary_confirmed},
                "consent_after": {"profile_confirmed": case.profile_confirmed,
                                  "final_summary_confirmed": case.final_summary_confirmed},
                "human_review_reason": case.human_review_reason,
                "customer_answers": case.customer_answers, "expected_reviewed_faq_answers": expected_faq,
                "expected_case_aware_checklist_items": checklist_items(case, item),
                "requirements": [requirement.model_dump(mode="json") for requirement in case.requirements],
                "outbox": [{"message_type": row["message_type"], "status": row["status"], "body": row["payload"]} for row in rows],
                "captured_extraction_calls": captured.extract_calls,
                "simulation_network_disabled": True, "external_sends": 0,
                "naturalness_scored": False, "manual_reply_review_still_required": True,
            }
        finally:
            store.close()


@contextmanager
def _transport_hooks() -> Iterator[None]:
    # The helper instance is private to this evaluator; patch only extension seams,
    # never the provider call or returned model patch. Restore hooks after each use.
    with (patch.object(transport, "patch_checks", patch_checks),
          patch.object(transport, "exercise_workflow", exercise_workflow),
          patch.object(transport, "validate_case_patch", validate_case_patch)):
        yield


def single_case(item: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    with _transport_hooks():
        return transport.single_case(item, **kwargs)


def replay_case(item: dict[str, Any], original: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    with _transport_hooks():
        return transport.replay_case(item, original, **kwargs)


def load_replay(report_bytes: bytes, *, corpus_bytes: bytes, items: list[dict[str, Any]], policy_version: str) -> dict[str, Any]:
    report = json.loads(report_bytes)
    if (not isinstance(report, dict) or report.get("probe_type") != "next_step"
            or report.get("evaluation_mode") != "provider_run" or report.get("new_provider_result") is not True):
        raise ValueError("Replay requires an original next-step report")
    rows = report.get("results")
    if not isinstance(rows, list) or len(rows) != len(items) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Replay rows must match selected development items")
    for row, item in zip(rows, items, strict=True):
        if {key: row[key] for key in LABEL_FIELDS if key in row} != item:
            raise ValueError(f"Replay body or next-step labels changed for {item['id']}")
    # The shared verifier checks all original provenance, seeds, schema identity,
    # errors and hashes. Translate the action label only in an in-memory copy.
    translated = {**report, "results": [_control_item(row) for row in rows]}
    transport.load_replay(json.dumps(translated).encode(), corpus_bytes=corpus_bytes,
                          items=[_control_item(item) for item in items], policy_version=policy_version)
    return report


def aggregate(rows: list[dict[str, Any]], *, replay: bool = False) -> dict[str, Any]:
    metrics = transport.aggregate(rows, replay=replay)
    count = len(rows)
    for stage in (("guarded",) if replay else ("raw", "guarded")):
        metrics.pop(f"{stage}_action_accuracy_including_errors", None)
        for check in ("topics_exact", "preparation_action_exact"):
            metrics[f"{stage}_{check}_accuracy_including_errors"] = sum(
                row.get(f"{stage}_checks", {}).get(check, False) for row in rows
            ) / count if count else 0.0
    metrics["naturalness_scored"] = False
    metrics["manual_reply_review_still_required"] = True
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--replay-from", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.replay_from and (args.split != "development" or args.allow_holdout):
        parser.error("--replay-from is development-only; holdout replay is forbidden")
    if args.split == "holdout" and not args.allow_holdout:
        parser.error("The frozen holdout requires --allow-holdout")
    if args.split != "holdout" and args.allow_holdout:
        parser.error("--allow-holdout is only meaningful with --split holdout")
    if args.output.exists():
        parser.error("Choose a NEW output path; retained results cannot be overwritten")
    try:
        corpus_bytes = args.corpus.read_bytes()
        items = load_items(corpus_bytes, args.split)
        assert_schema_ready()
        policy = load_policy(POLICY)
        fingerprints = source_fingerprints()
        original_bytes = args.replay_from.read_bytes() if args.replay_from else None
        original = load_replay(original_bytes, corpus_bytes=corpus_bytes, items=items, policy_version=policy.version) if original_bytes else None
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    report: dict[str, Any] = {
        "probe_type": "next_step", "scope": "Fictional next-step and independent FAQ/control understanding",
        "evaluation_mode": "saved_patch_replay" if original else "provider_run", "new_provider_result": original is None,
        "split": args.split, "holdout_authorized": args.allow_holdout,
        "model": original["model"] if original else MODEL,
        "operation": "saved_patch_guard_and_workflow" if original else "extract_case_patch",
        "calls_per_case": 0 if original else 1, "no_provider_retries": True,
        "workflow_uses_captured_patch": True, "external_sends": 0,
        "reviewed_as_of": REVIEWED_AS_OF.isoformat(), "corpus_id": str(args.corpus.resolve()),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(), "source_sha256": fingerprints,
        "source_bundle_sha256": hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest(),
        "started_at": datetime.now(UTC).isoformat(), "expected_case_count": len(items),
        "completed_case_count": 0, "model_calls": 0, "completed": False, "results": [],
        "not_proven": ["real email delivery", "general legal accuracy", "subjective naturalness", "all unstructured requests"],
        "manual_reply_review_still_required": True,
    }
    if original:
        report.update({
            "not_new_model_accuracy": True, "new_provider_calls": 0,
            "original_report_path": str(args.replay_from.resolve()),
            "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "original_source_sha256": deepcopy(original["source_sha256"]),
            "original_source_bundle_sha256": original["source_bundle_sha256"],
            "historical_provider": {"model": original["model"], "model_calls": original["model_calls"],
                                    "metrics_as_recorded": deepcopy(original.get("metrics", {})),
                                    "observed_usage_metrics": transport.usage_metrics(original["results"]),
                                    "not_new_replay_cost": True},
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report, create=True)
    key = None
    model = None
    if original is None:
        try:
            key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                              default_file=REPOSITORY / ".secrets/deepseek_api_key.txt")
            if not key:
                raise ValueError("Missing DeepSeek key; no API request made")
            model = DeepSeekStructuredLLM(MODEL, api_key=key, capture_raw_responses=True)
            if model.client.max_retries != 0:
                raise ValueError("Provider retries must be disabled before evaluation")
        except Exception as error:
            report["startup_error"] = error_record(error, key)
            write_report(args.output, redact(report, key))
            parser.error(str(report["startup_error"]))

    def checkpoint(row: dict[str, Any]) -> None:
        if report["results"] and report["results"][-1]["id"] == row["id"]:
            report["results"][-1] = row
        else:
            report["results"].append(row)
        report["completed_case_count"] = sum(row["completed"] for row in report["results"])
        report["model_calls"] = sum(row["model_calls"] for row in report["results"])
        report["checkpoint_at"] = datetime.now(UTC).isoformat()
        write_report(args.output, redact(report, key))

    for index, item in enumerate(items):
        row = (replay_case(item, original["results"][index], policy_version=policy.version, checkpoint=checkpoint)
               if original else single_case(item, policy_version=policy.version, model=model, key=key, checkpoint=checkpoint))
        print(item["id"], "PASS" if row["passed"] else "FAIL", flush=True)
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged_during_run"] = fingerprints == source_fingerprints()
    report["corpus_unchanged_during_run"] = hashlib.sha256(args.corpus.read_bytes()).hexdigest() == report["corpus_sha256"]
    report["metrics"] = aggregate(report["results"], replay=original is not None)
    report["all_passed"] = (report["source_unchanged_during_run"] and report["corpus_unchanged_during_run"]
                            and all(row["passed"] for row in report["results"]))
    if original:
        report["original_report_unchanged_during_replay"] = hashlib.sha256(args.replay_from.read_bytes()).hexdigest() == report["original_report_sha256"]
        report["all_passed"] = report["all_passed"] and report["original_report_unchanged_during_replay"]
    write_report(args.output, redact(report, key))
    print("Report:", args.output, flush=True)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
