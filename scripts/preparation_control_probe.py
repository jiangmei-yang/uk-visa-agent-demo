"""One-call preparation-control evaluation on an explicitly selected fictional corpus.

The default production DeepSeek extractor is called once per case, without retries.
The exact resulting patch is guarded and replayed through WorkflowService using a
captured local model. No Gmail adapter, dispatcher, mailbox or document is opened.
Holdout execution requires explicit opt-in; reports cannot replace earlier runs.
``--replay-from`` re-evaluates saved successful development patches locally, with
zero provider calls and no credential loading. This is not a new model evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import tempfile
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import deterministic_fallback_message, validate_case_patch
from visa_agent.llm.ports import CasePatch
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import latest_reply_text
from visa_agent.workflow.service import WorkflowService

REPOSITORY = Path(__file__).resolve().parents[1]
POLICY = REPOSITORY / "knowledge/uk_standard_visitor_2026-02-25.yaml"
MODEL = "deepseek-v4-flash"
REVIEWED_AS_OF = date(2026, 9, 4)
CONTROL_FIELDS = (
    "preparation_paused", "preparation_control_epoch", "latest_preparation_action",
    "preparation_control_event_id",
)


def assert_schema_ready() -> None:
    missing = [name for name in CONTROL_FIELDS if name not in Case.model_fields]
    if "preparation_intent" not in CasePatch.model_fields:
        missing.append("CasePatch.preparation_intent")
    if missing:
        raise ValueError("Preparation-control runtime schema is not ready: " + ", ".join(missing))


def source_fingerprints() -> dict[str, str]:
    paths = [*sorted((REPOSITORY / "src/visa_agent").rglob("*.py")), POLICY, Path(__file__)]
    return {str(path.relative_to(REPOSITORY)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def redact(value: Any, key: str | None) -> Any:
    """Redact provider credentials even if an exception or diagnostic echoes them."""
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, dict):
        return {name: redact(item, key) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value


def error_record(error: Exception, key: str | None) -> dict[str, str]:
    return {"type": type(error).__name__, "message": redact(str(error), key)}


def load_items(corpus_bytes: bytes, split: str) -> list[dict[str, Any]]:
    """Inspect split metadata globally, but validate labels only for the selected split."""
    if split not in {"development", "holdout"}:
        raise ValueError("Unknown corpus split")
    corpus = json.loads(corpus_bytes)
    if not isinstance(corpus, list):
        raise ValueError("Corpus must be an array of case objects")
    if any(not isinstance(item, dict) or type(item.get("holdout")) is not bool
           for item in corpus):
        raise ValueError("Every case needs an explicit boolean holdout marker")
    items = [item for item in corpus if item["holdout"] == (split == "holdout")]
    if not items:
        raise ValueError("Selected split is empty")
    for item in items:
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("Every selected case needs a nonempty id")
        if not isinstance(item.get("body"), str) or not item["body"].strip():
            raise ValueError("Every selected case needs a nonempty body")
        if item.get("language") not in {"en", "zh"}:
            raise ValueError("Every selected case needs en/zh language")
        if type(item.get("initially_paused")) is not bool:
            raise ValueError("initially_paused must be a boolean")
        if "expected_action" not in item or item["expected_action"] not in (None, "pause", "resume"):
            raise ValueError("expected_action must be pause, resume, or null")
        updates = item.get("expected_profile_updates", {})
        if not isinstance(updates, dict):
            raise ValueError("expected_profile_updates must be an object")
        for field, value in updates.items():
            if field == "date_of_birth":
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ValueError("Expected birth date must be a canonical ISO date")
            elif field == "estimated_trip_cost_gbp":
                if type(value) is not int or value <= 0:
                    raise ValueError("Expected trip budget must be a positive GBP integer")
            elif field == "has_serious_history":
                if type(value) is not bool:
                    raise ValueError("Expected history flag must be boolean")
            else:
                raise ValueError(f"Unsupported evaluator profile field: {field}")
        if "expected_human_review" in item and type(item["expected_human_review"]) is not bool:
            raise ValueError("expected_human_review must be a boolean")
        if not isinstance(item.get("rationale", ""), str):
            raise ValueError("rationale must be a string")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Selected case IDs must be unique")
    return items


def seed_case(item: dict[str, Any], policy_version: str) -> Case:
    """A fixed fictional adult applicant; no documents, appointments or consent."""
    return Case(
        id=f"preparation-probe-{item['id']}",
        external_thread_id=f"preparation-probe-thread-{item['id']}",
        applicant_contact="fictional-preparation@example.test",
        primary_channel="gmail", customer_language=item["language"],
        policy_version=policy_version,
        profile=CaseProfile(
            full_name="Example Applicant", date_of_birth=date(1998, 5, 12),
            nationality="Chinese", nationality_country="China", application_country="Hong Kong",
            visit_purpose="tourism", uk_accommodation="Planned stay in London; no booking made",
            # New synthetic seed has residential detail; historical reports retain
            # their original incomplete seed and are not retroactively replayed.
            estimated_trip_cost_gbp=1500, current_address="Room 4, Example Hall, 88 Synthetic Road, Hong Kong",
            occupation_status="student", annual_income_gbp=0, funding_source="self",
            has_serious_history=False, route_confirmed_standard_visitor=True,
        ),
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
        preparation_paused=item["initially_paused"],
        preparation_control_epoch=int(item["initially_paused"]),
        latest_preparation_action=None,
        preparation_control_event_id=f"seed-pause-{item['id']}" if item["initially_paused"] else None,
    )


def make_event(item: dict[str, Any], initial: Case, received_at: datetime) -> InboundEvent:
    return InboundEvent(
        id=f"inbound-{item['id']}", channel="gmail",
        external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="英国签证材料准备" if item["language"] == "zh" else "UK visa preparation",
        body=latest_reply_text(item["body"]), known_profile=initial.profile.model_dump(mode="json"),
        received_at=received_at, rfc_message_id=f"<preparation-{item['id']}@example.test>",
    )


def load_replay(
    report_bytes: bytes, *, corpus_bytes: bytes, items: list[dict[str, Any]], policy_version: str,
) -> dict[str, Any]:
    """Accept only an immutable, complete, same-corpus development provider run.

    Failed original extractions remain failures. Never parse raw response text to
    recover a patch, and never interpret a prior local replay as provider output.
    """
    report = json.loads(report_bytes)
    if not isinstance(report, dict) or report.get("split") != "development":
        raise ValueError("Replay requires a development report; holdout replay is forbidden")
    if report.get("holdout_authorized") is not False or any(item["holdout"] for item in items):
        raise ValueError("Replay must contain development cases only")
    if report.get("evaluation_mode") == "saved_patch_replay" or report.get("new_provider_result") is False:
        raise ValueError("Replay source must be the original provider report, not another replay")
    if any(report.get(name) is not True for name in (
        "completed", "source_unchanged_during_run", "corpus_unchanged_during_run", "no_provider_retries",
    )):
        raise ValueError("Replay source must be completed with unchanged source and corpus")
    if report.get("corpus_sha256") != hashlib.sha256(corpus_bytes).hexdigest():
        raise ValueError("Replay corpus hash does not match the frozen report")
    sources = report.get("source_sha256")
    if not isinstance(sources, dict) or not sources or any(
        not isinstance(name, str) or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None for name, digest in sources.items()
    ):
        raise ValueError("Replay source fingerprints are missing or invalid")
    bundle = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    if report.get("source_bundle_sha256") != bundle:
        raise ValueError("Replay source fingerprint bundle does not match its contents")
    if report.get("operation") != "extract_case_patch" or report.get("calls_per_case") != 1:
        raise ValueError("Replay source must use one production-default extraction per case")
    if not isinstance(report.get("model"), str) or not report["model"]:
        raise ValueError("Replay source model identity is missing")
    if any(report.get(name) != len(items) for name in (
        "expected_case_count", "completed_case_count", "model_calls",
    )):
        raise ValueError("Replay case and historical call counts do not match the development split")
    results = report.get("results")
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise ValueError("Replay source results must be case objects")
    if [row.get("id") for row in results] != [item["id"] for item in items]:
        raise ValueError("Replay development IDs and order must match exactly")
    label_fields = {
        "id", "language", "body", "initially_paused", "expected_action", "expected_profile_updates",
        "rationale", "holdout", "expected_human_review",
    }
    for item, row in zip(items, results, strict=True):
        if {name: row[name] for name in label_fields if name in row} != {
            name: item[name] for name in label_fields if name in item
        }:
            raise ValueError(f"Replay body or evaluator labels changed for {item['id']}")
        if (row.get("completed") is not True or row.get("model_calls") != 1
                or row.get("operation") != "extract_case_patch" or row.get("model") != report["model"]):
            raise ValueError(f"Replay original extraction provenance is invalid for {item['id']}")
        if type(row.get("extraction_available")) is not bool:
            raise ValueError(f"Replay extraction status missing for {item['id']}")
        if not isinstance(row.get("usage"), list) or any(not isinstance(use, dict) for use in row["usage"]):
            raise ValueError(f"Replay original usage is invalid for {item['id']}")
        initial = seed_case(item, policy_version)
        event = InboundEvent.model_validate(row.get("input_event"))
        if event.model_dump(mode="json") != make_event(item, initial, event.received_at).model_dump(mode="json"):
            raise ValueError(f"Replay input event or fictional seed changed for {item['id']}")
        if row.get("workflow_available"):
            saved_workflow = row.get("workflow", {})
            if saved_workflow.get("profile_before") != initial.profile.model_dump(mode="json") or (
                saved_workflow.get("control_before") != {
                    name: getattr(initial, name) for name in CONTROL_FIELDS
                }
            ):
                raise ValueError(f"Replay workflow seed changed for {item['id']}")
        if row["extraction_available"]:
            if "extraction_error" in row:
                raise ValueError(f"Replay successful extraction also claims an error for {item['id']}")
            saved_patch = CasePatch.model_validate(row.get("raw_patch"))
            if saved_patch.model_dump(mode="json") != row["raw_patch"]:
                raise ValueError(f"Replay raw patch would require repair or normalization for {item['id']}")
        elif "extraction_error" not in row or "raw_patch" in row:
            raise ValueError(f"Replay failed extraction must retain its error without a patch for {item['id']}")
    return report


class CapturedLLM:
    """Reuse the paid extraction exactly; rendering never calls a provider."""

    version = "captured-preparation-patch-no-extra-provider-call"

    def __init__(self, proposed: CasePatch) -> None:
        self.proposed = proposed.model_copy(deep=True)
        self.extract_calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.extract_calls += 1
        if self.extract_calls != 1:
            raise AssertionError("Workflow attempted a second captured extraction")
        return self.proposed.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


def action_of(proposed: CasePatch) -> str | None:
    intent = proposed.preparation_intent
    return intent.action if intent is not None else None


def patch_checks(item: dict[str, Any], proposed: CasePatch) -> dict[str, bool]:
    values = {update.field: update.value for update in proposed.updates}
    checks = {"action_exact": action_of(proposed) == item["expected_action"]}
    checks.update({f"expected_profile_update:{field}": field in values and values[field] == value
                   for field, value in item.get("expected_profile_updates", {}).items()})
    if "expected_human_review" in item:
        checks["expected_human_review"] = proposed.requires_human_review == item["expected_human_review"]
    return checks


def exercise_workflow(
    item: dict[str, Any], initial: Case, event: InboundEvent,
    proposed: CasePatch, guarded: CasePatch,
) -> dict[str, Any]:
    """Persist and replay the production workflow with all socket connections blocked."""
    captured = CapturedLLM(proposed)
    with (
        patch("socket.socket.connect", side_effect=AssertionError("Probe workflow network disabled")),
        patch("socket.create_connection", side_effect=AssertionError("Probe workflow network disabled")),
        tempfile.TemporaryDirectory(prefix="visa-preparation-probe-") as directory,
    ):
        store = SQLiteStore(Path(directory) / "case.db")
        try:
            store.save_case(initial)
            workflow = WorkflowService(store, load_policy(POLICY), captured,
                                       today_provider=lambda: REVIEWED_AS_OF)
            case, duplicate, plan = workflow.process(event)
            before = initial.profile.model_dump(mode="json")
            after = case.profile.model_dump(mode="json")
            expected_profile = {**before, **item.get("expected_profile_updates", {})}
            expected_paused = (item["expected_action"] == "pause"
                               if item["expected_action"] is not None else item["initially_paused"])
            changed = expected_paused != item["initially_paused"]
            expected_transition = item["expected_action"] if changed else None
            expected_epoch = initial.preparation_control_epoch + int(changed)
            checks = {
                "workflow_first_processing_not_duplicate": not duplicate,
                "workflow_no_extraction_fallback": not workflow.llm.last_extraction_fallback,
                "paused_state_exact": case.preparation_paused == expected_paused,
                "control_epoch_exact": case.preparation_control_epoch == expected_epoch,
                "transition_action_exact": case.latest_preparation_action == expected_transition,
                "transition_event_exact": case.preparation_control_event_id == (
                    event.id if changed else initial.preparation_control_event_id),
                "profile_matches_expected": after == expected_profile,
                "workflow_preserves_guarded_question_topics": case.customer_question_topics == [
                    question.topic for question in guarded.customer_questions],
                "no_profile_or_final_confirmation": not case.profile_confirmed and not case.final_summary_confirmed,
                "no_pack_or_release": case.delivery_path is None and plan != "ready",
                "single_isolated_case": len(store.list_cases()) == 1 and case.id == initial.id,
                "no_provider_delivery": all(row["status"] != "SENT" for row in store.list_outbox()),
            }
            for field, value in item.get("expected_profile_updates", {}).items():
                checks[f"expected_profile_update:{field}"] = after.get(field) == value
            if "expected_human_review" in item:
                checks["expected_human_review"] = (
                    case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
                ) == item["expected_human_review"]
            if expected_paused:
                checks.update({
                    "paused_no_question_plan": case.question_plan == [],
                    "paused_no_requested_fields": case.last_requested_fields == [],
                    "paused_no_confirmation_request": plan not in {
                        "awaiting_profile_confirmation", "awaiting_confirmation"},
                    "paused_no_confirmation_metadata": all(value is None for value in (
                        case.confirmation_kind, case.confirmation_fingerprint,
                        case.confirmation_request_event_id)),
                })
            persisted = store.get_case_by_thread(initial.external_thread_id)
            checks["control_state_persisted"] = persisted is not None and all(
                getattr(persisted, name) == getattr(case, name) for name in CONTROL_FIELDS)
            rows = store.list_outbox()
            _, replayed, replay_plan = workflow.process(event)
            checks["duplicate_no_new_outbox_or_extraction"] = (
                replayed and replay_plan == "duplicate_ignored" and captured.extract_calls == 1
                and len(store.list_outbox()) == len(rows))
            return {
                "checks": checks, "plan": plan, "case_status": case.status.value,
                "profile_before": before, "profile_after": after,
                "control_before": {name: getattr(initial, name) for name in CONTROL_FIELDS},
                "control_after": {name: getattr(case, name) for name in CONTROL_FIELDS},
                "expected_paused": expected_paused, "expected_transition": expected_transition,
                "expected_epoch": expected_epoch, "question_plan": case.question_plan,
                "workflow_topics": case.customer_question_topics,
                "customer_answers": case.customer_answers,
                "human_review_reason": case.human_review_reason,
                "outbox": [{"message_type": row["message_type"], "status": row["status"],
                            "body": row["payload"]} for row in rows],
                "captured_extraction_calls": captured.extract_calls,
                "simulation_network_disabled": True,
                "external_sends": 0,
            }
        finally:
            store.close()


def single_case(
    item: dict[str, Any], *, policy_version: str, model: Any, key: str | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """One production-default provider attempt; retain failures without a replacement."""
    started = time.perf_counter()
    initial = seed_case(item, policy_version)
    event = make_event(item, initial, datetime.now(UTC))
    row: dict[str, Any] = {
        **deepcopy(item), "input_event": event.model_dump(mode="json"),
        "operation": "extract_case_patch", "model": getattr(model, "model", MODEL),
        "model_calls": 1, "completed": False, "extraction_available": False,
        "guarded_available": False, "workflow_available": False,
        "started_at": datetime.now(UTC).isoformat(), "usage": [],
    }

    def save() -> None:
        if checkpoint is not None:
            checkpoint(redact(deepcopy(row), key))

    save()
    usage_start = len(model.usage_history)
    model.last_extraction_content = None
    extraction_started = time.perf_counter()
    proposed = None
    try:
        proposed = model.extract_case_patch(event.model_copy(deep=True))
        row["raw_patch"] = proposed.model_dump(mode="json")
        row["raw_checks"] = patch_checks(item, proposed)
        row["extraction_available"] = True
    except Exception as error:
        row["extraction_error"] = error_record(error, key)
        proposed = None
    finally:
        row["extraction_latency_seconds"] = round(time.perf_counter() - extraction_started, 6)
        row["usage"] = deepcopy(model.usage_history[usage_start:])
        row["raw_response_content"] = getattr(model, "last_extraction_content", None)
        save()
    if proposed is not None:
        try:
            guarded = validate_case_patch(event, proposed.model_copy(deep=True))
            row["guarded_patch"] = guarded.model_dump(mode="json")
            row["guarded_checks"] = patch_checks(item, guarded)
            row["guarded_available"] = True
            save()
            row["workflow"] = exercise_workflow(
                item, initial.model_copy(deep=True), event.model_copy(deep=True), proposed, guarded,
            )
            row["workflow_available"] = True
        except Exception as error:
            row["workflow_error" if row["guarded_available"] else "guard_error"] = error_record(error, key)
    row["raw_passed"] = row["extraction_available"] and all(row["raw_checks"].values())
    row["guarded_passed"] = row["guarded_available"] and all(row["guarded_checks"].values())
    row["workflow_passed"] = row["workflow_available"] and all(row["workflow"]["checks"].values())
    row["passed"] = row["guarded_passed"] and row["workflow_passed"]
    row["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    row["finished_at"] = datetime.now(UTC).isoformat()
    row["completed"] = True
    save()
    return redact(row, key)


def replay_case(
    item: dict[str, Any], original: dict[str, Any], *, policy_version: str,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Re-guard only an originally successful schema patch; perform zero API calls."""
    started = time.perf_counter()
    row: dict[str, Any] = {
        **deepcopy(item), "evaluation_mode": "saved_patch_replay", "new_provider_result": False,
        "original_result": deepcopy(original), "input_event": deepcopy(original["input_event"]),
        "model": original["model"], "operation": "saved_patch_guard_and_workflow",
        "model_calls": 0, "usage": [], "completed": False,
        "historical_model_calls": original["model_calls"], "historical_usage": deepcopy(original["usage"]),
        "historical_extraction_latency_seconds": original.get("extraction_latency_seconds"),
        "extraction_available": original["extraction_available"], "raw_extraction_is_historical_only": True,
        "raw_response_content": original.get("raw_response_content"),
        "guarded_available": False, "workflow_available": False,
        "started_at": datetime.now(UTC).isoformat(),
    }

    def save() -> None:
        if checkpoint is not None:
            checkpoint(deepcopy(row))

    save()
    if original["extraction_available"]:
        row["raw_patch"] = deepcopy(original["raw_patch"])
        row["historical_raw_checks"] = deepcopy(original.get("raw_checks", {}))
        try:
            proposed = CasePatch.model_validate(original["raw_patch"])
            event = InboundEvent.model_validate(original["input_event"])
            guarded = validate_case_patch(event, proposed.model_copy(deep=True))
            row["guarded_patch"] = guarded.model_dump(mode="json")
            row["guarded_checks"] = patch_checks(item, guarded)
            row["guarded_available"] = True
            save()
            row["workflow"] = exercise_workflow(
                item, seed_case(item, policy_version), event.model_copy(deep=True), proposed, guarded,
            )
            row["workflow_available"] = True
        except Exception as error:
            row["workflow_error" if row["guarded_available"] else "guard_error"] = error_record(error, None)
    else:
        row["extraction_error"] = deepcopy(original["extraction_error"])
        row["replay_unavailable_reason"] = "Original extraction failed; no reparse, invented patch, or provider retry"
    row["guarded_passed"] = row["guarded_available"] and all(row["guarded_checks"].values())
    row["workflow_passed"] = row["workflow_available"] and all(row["workflow"]["checks"].values())
    row["passed"] = row["guarded_passed"] and row["workflow_passed"]
    row["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    row["finished_at"] = datetime.now(UTC).isoformat()
    row["completed"] = True
    save()
    return row


def usage_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum only token keys actually recorded by the adapter; missing is not zero."""
    usage = [use for row in results for use in row.get("usage", [])]
    names = sorted({name for use in usage for name, value in use.items()
                    if name.endswith("_tokens") and type(value) is int and value >= 0})
    return {
        "usage_records": len(usage),
        "tokens": {name: sum(use[name] for use in usage if name in use and type(use[name]) is int
                             and use[name] >= 0) for name in names},
        "token_key_record_counts": {name: sum(name in use and type(use[name]) is int and use[name] >= 0
                                              for use in usage) for name in names},
        "usage_unavailable_cases": sum(not row.get("usage") for row in results),
        "usage_metric_method": "observed_numeric_token_keys_only_v2",
    }


def aggregate(results: list[dict[str, Any]], *, replay: bool = False) -> dict[str, Any]:
    count = len(results)
    latencies = [row["extraction_latency_seconds"] for row in results if "extraction_latency_seconds" in row]
    metrics: dict[str, Any] = {
        "cases": count, "model_calls": sum(row.get("model_calls", 0) for row in results),
        "completed_cases": sum(row.get("completed", False) for row in results),
        "original_extraction_errors_retained" if replay else "extraction_errors": sum(
            not row.get("extraction_available", False) for row in results),
        **usage_metrics(results),
        "mean_extraction_latency_seconds": statistics.mean(latencies) if latencies else None,
    }
    if replay:
        metrics.update({"not_new_model_accuracy": True, "new_provider_calls": 0,
                        "usage_not_applicable_cases": count, "usage_unavailable_cases": 0})
    for name in (("guarded", "workflow") if replay else ("raw", "guarded", "workflow")):
        passed = sum(row.get(f"{name}_passed", False) for row in results)
        metrics[f"{name}_passed_cases"] = passed
        metrics[f"{name}_accuracy_including_errors"] = passed / count if count else 0.0
    for name in (("guarded",) if replay else ("raw", "guarded")):
        exact = sum(row.get(f"{name}_checks", {}).get("action_exact", False) for row in results)
        metrics[f"{name}_action_accuracy_including_errors"] = exact / count if count else 0.0
    metrics["passed_cases"] = sum(row.get("passed", False) for row in results)
    return metrics


def write_report(path: Path, report: dict[str, Any], *, create: bool = False) -> None:
    with path.open("x" if create else "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path, help="Frozen fictional JSON corpus")
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--replay-from", type=Path,
                        help="Zero-API local re-guard of an immutable original development report")
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
        original = (load_replay(original_bytes, corpus_bytes=corpus_bytes, items=items,
                                policy_version=policy.version) if original_bytes is not None else None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    report: dict[str, Any] = {
        "scope": "Single production-default extraction and guarded preparation-control workflow on fictional mail",
        "evaluation_mode": "provider_run", "new_provider_result": True,
        "not_proven": ["real email delivery", "general legal accuracy", "all natural-language control requests"],
        "split": args.split, "holdout_authorized": args.allow_holdout, "model": MODEL,
        "operation": "extract_case_patch", "no_provider_retries": True,
        "calls_per_case": 1, "workflow_uses_captured_patch": True, "external_sends": 0,
        "reviewed_as_of": REVIEWED_AS_OF.isoformat(),
        "corpus_id": str(args.corpus.resolve()), "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "source_sha256": fingerprints,
        "source_bundle_sha256": hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest(),
        "started_at": datetime.now(UTC).isoformat(), "expected_case_count": len(items),
        "completed_case_count": 0, "model_calls": 0, "completed": False, "results": [],
        "corrected_usage_metrics": True,
        "usage_metric_correction": "Sum only observed numeric token keys, including input_tokens/output_tokens; never invent absent prompt/completion zeros",
    }
    if original is not None:
        report.update({
            "scope": "Zero-API development-only replay of frozen provider patches through the current guard and local workflow",
            "evaluation_mode": "saved_patch_replay", "new_provider_result": False,
            "model": original["model"],
            "not_new_model_accuracy": True, "calls_per_case": 0,
            "operation": "saved_patch_guard_and_workflow", "new_provider_calls": 0,
            "original_report_path": str(args.replay_from.resolve()),
            "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "original_source_sha256": deepcopy(original["source_sha256"]),
            "original_source_bundle_sha256": original["source_bundle_sha256"],
            "original_report_metadata": {name: deepcopy(value) for name, value in original.items()
                                         if name != "results"},
            "historical_provider": {
                "model": original["model"], "model_calls": original["model_calls"],
                "metrics_as_recorded": deepcopy(original.get("metrics", {})),
                "corrected_usage_metrics": usage_metrics(original["results"]),
                "not_new_replay_cost": True,
            },
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report, create=True)
    key = None
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
        report["completed_case_count"] = sum(result["completed"] for result in report["results"])
        report["model_calls"] = sum(result["model_calls"] for result in report["results"])
        report["checkpoint_at"] = datetime.now(UTC).isoformat()
        write_report(args.output, redact(report, key))

    for index, item in enumerate(items):
        if original is None:
            row = single_case(item, policy_version=policy.version, model=model, key=key, checkpoint=checkpoint)
        else:
            row = replay_case(item, original["results"][index], policy_version=policy.version, checkpoint=checkpoint)
        print(item["id"], "PASS" if row["passed"] else "FAIL", flush=True)
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged_during_run"] = fingerprints == source_fingerprints()
    report["corpus_unchanged_during_run"] = hashlib.sha256(args.corpus.read_bytes()).hexdigest() == report["corpus_sha256"]
    report["metrics"] = aggregate(report["results"], replay=original is not None)
    report["all_passed"] = (report["source_unchanged_during_run"] and report["corpus_unchanged_during_run"]
                            and all(row["passed"] for row in report["results"]))
    if original is not None:
        report["original_report_unchanged_during_replay"] = (
            hashlib.sha256(args.replay_from.read_bytes()).hexdigest() == report["original_report_sha256"])
        report["all_passed"] = report["all_passed"] and report["original_report_unchanged_during_replay"]
    write_report(args.output, redact(report, key))
    print("Report:", args.output, flush=True)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
