"""Controlled fictional-corpus comparison of three question-extraction approaches.

Each case gets original combined, neutral-wrapper combined and focused extraction,
once each without retries. Focused replaces only the ORIGINAL combined proposal's
customer_questions; neutral combined uses its own facts and questions. This tests
the simpler wrapper correction separately from a two-call focused architecture.
Workflow/Gmail simulation is network-disabled.
This is an explicit evaluation entry point, never a production workflow toggle.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch
from visa_agent.llm.question_understanding import with_customer_questions
from visa_agent.secrets import read_secret

# Works both as ``python scripts/question_understanding_probe.py`` and via a
# test's importlib loader, without depending on scripts being a Python package.
_helper_spec = importlib.util.spec_from_file_location(
    "_question_probe_adviser_helpers", Path(__file__).with_name("adviser_intent_probe.py"),
)
assert _helper_spec is not None and _helper_spec.loader is not None
adviser = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(adviser)

REPOSITORY = adviser.REPOSITORY
MODEL = adviser.MODEL
POLICY = adviser.POLICY
ARMS = ("baseline", "neutral_combined", "focused")
seed_case = adviser.seed_case
exercise_workflow = adviser.exercise_workflow
topic_metrics = adviser.topic_metrics
write_report = adviser.write_report


def source_fingerprints() -> dict[str, str]:
    fingerprints = adviser.source_fingerprints()
    for name in (
        "scripts/question_understanding_probe.py",
        "src/visa_agent/llm/question_understanding.py",
        str(POLICY.relative_to(REPOSITORY)),
    ):
        fingerprints[name] = hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest()
    return fingerprints


def error_record(error: Exception, key: str | None) -> dict[str, str]:
    message = str(error)
    return {"type": type(error).__name__,
            "message": message.replace(key, "[REDACTED]") if key else message}


def preserved_patch_fields(proposed: CasePatch) -> dict[str, Any]:
    return proposed.model_dump(mode="json", exclude={"customer_questions"})


def _extract(
    model: Any, arm: dict[str, Any], event: InboundEvent, *, name: str,
    key: str | None, checkpoint: Callable[[], None],
) -> Any | None:
    operation = {
        "baseline": "extract_case_patch_legacy_input",
        "neutral_combined": "extract_case_patch_neutral_input",
        "focused": "extract_customer_questions",
    }[name]
    content_field = "last_question_content" if name == "focused" else "last_extraction_content"
    usage_start = len(model.usage_history)
    arm.update({"operation": operation, "attempted": True, "model_calls": 1,
                "started_at": datetime.now(UTC).isoformat(), "completed": False,
                "extraction_available": False, "workflow_available": False,
                "checks": {}, "usage": []})
    checkpoint()
    started = time.perf_counter()
    output = None
    try:
        output = getattr(model, operation)(event)
        arm["raw_output"] = output.model_dump(mode="json")
        arm["raw_customer_questions"] = [
            question.model_dump(mode="json") for question in output.customer_questions
        ]
        arm["raw_topics"] = sorted({question.topic for question in output.customer_questions})
        arm["extraction_available"] = True
    except Exception as error:
        arm["error"] = error_record(error, key)
        arm["checks"]["extraction_available"] = False
        output = None
    finally:
        arm["extraction_latency_seconds"] = round(time.perf_counter() - started, 6)
        arm["usage"] = deepcopy(model.usage_history[usage_start:])
        raw_content = getattr(model, content_field, None)
        arm["raw_response_content"] = (
            raw_content.replace(key, "[REDACTED]") if key and isinstance(raw_content, str)
            else raw_content
        )
        arm["completed"] = True
        arm["finished_at"] = datetime.now(UTC).isoformat()
        checkpoint()
    return output


def evaluate_arm(
    item: dict[str, Any], initial: Any, event: InboundEvent,
    proposed: CasePatch, arm: dict[str, Any], *, development_checks: bool,
    key: str | None,
) -> None:
    arm["raw_patch"] = proposed.model_dump(mode="json")
    arm["checks"]["extraction_available"] = True
    arm["checks"]["raw_topics_exact"] = arm["raw_topic_metrics"]["exact"]
    try:
        validated = validate_case_patch(event, proposed)
        arm["validated_patch"] = validated.model_dump(mode="json")
        arm["actual_topics"] = sorted({question.topic for question in validated.customer_questions})
        arm["validated_topic_metrics"] = topic_metrics(item["expected_topics"], arm["actual_topics"])
        arm["checks"]["validated_topics_exact"] = arm["validated_topic_metrics"]["exact"]
        arm["accepted_update_fields"] = sorted({update.field for update in validated.updates})
        arm["guard_rejected_questions"] = [question.model_dump(mode="json")
            for question in proposed.customer_questions if question not in validated.customer_questions]
        arm["guard_rejected_or_normalized_updates"] = [update.model_dump(mode="json")
            for update in proposed.updates if update not in validated.updates]
        result = exercise_workflow(
            item, initial.model_copy(deep=True), event.model_copy(deep=True),
            proposed, validated, development_checks=development_checks,
        )
        arm["checks"].update(result.pop("checks"))
        arm.update(result)
        arm["workflow_available"] = True
    except Exception as error:
        arm["workflow_error"] = error_record(error, key)
        arm["checks"]["workflow_completed_without_error"] = False


def paired_case(
    item: dict[str, Any], *, index: int, policy_version: str, model: Any,
    development_checks: bool, key: str | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Exactly three attempted API calls with identical fictional event data."""
    started = time.perf_counter()
    row: dict[str, Any] = {
        "id": item["id"], "language": item["language"], "body": item["body"],
        "expected_topics": item["expected_topics"], "rationale": item.get("rationale", ""),
        "expected_profile_updates": item.get("expected_profile_updates", {}),
        **{name: {} for name in ARMS}, "completed": False,
        "call_order": list(ARMS[index % len(ARMS):] + ARMS[:index % len(ARMS)]),
    }

    def save() -> None:
        row["model_calls"] = sum(row[name].get("model_calls", 0) for name in ARMS)
        row["checkpoint_at"] = datetime.now(UTC).isoformat()
        if checkpoint is not None:
            checkpoint(row)

    initial = seed_case(item, policy_version)
    event = InboundEvent(
        id=f"inbound-{item['id']}", channel="gmail",
        external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="英国旅行材料咨询" if item["language"] == "zh" else "UK visit preparation",
        body=adviser.latest_reply_text(item["body"]),
        known_profile=initial.profile.model_dump(mode="json"), received_at=datetime.now(UTC),
        rfc_message_id=f"<inbound-{item['id']}@example.test>",
    )
    row["profile_before"] = initial.profile.model_dump(mode="json")
    row["shared_inbound_event_sha256"] = hashlib.sha256(
        event.model_dump_json().encode("utf-8")
    ).hexdigest()
    outputs: dict[str, Any] = {}
    for name in row["call_order"]:
        outputs[name] = _extract(
            model, row[name], event.model_copy(deep=True), name=name,
            key=key, checkpoint=save,
        )
        if outputs[name] is not None:
            row[name]["raw_topic_metrics"] = topic_metrics(item["expected_topics"], row[name]["raw_topics"])
        save()
    baseline, focused = outputs["baseline"], outputs["focused"]
    if outputs["neutral_combined"] is not None:
        evaluate_arm(item, initial, event, outputs["neutral_combined"], row["neutral_combined"],
                     development_checks=development_checks, key=key)
        save()
    if baseline is not None:
        evaluate_arm(item, initial, event, baseline, row["baseline"],
                     development_checks=development_checks, key=key)
        save()
        if focused is not None:
            merged = with_customer_questions(baseline, focused)
            row["focused"]["checks"]["shared_baseline_fields_preserved"] = (
                preserved_patch_fields(merged) == preserved_patch_fields(baseline)
            )
            evaluate_arm(item, initial, event, merged, row["focused"],
                         development_checks=development_checks, key=key)
            if "validated_patch" in row["baseline"] and "validated_patch" in row["focused"]:
                row["focused"]["checks"]["validated_shared_baseline_fields_preserved"] = (
                    {k: v for k, v in row["baseline"]["validated_patch"].items() if k != "customer_questions"}
                    == {k: v for k, v in row["focused"]["validated_patch"].items() if k != "customer_questions"}
                )
    else:
        row["focused"]["workflow_unavailable_reason"] = "combined_baseline_extraction_failed"
        row["focused"]["validated_classification_unavailable_reason"] = "No shared baseline patch; no patch was invented"
    for name in ARMS:
        arm = row[name]
        arm["checks"]["workflow_available"] = arm["workflow_available"]
        # Independent required fact corrections remain visible in both full
        # check sets, but are excluded from the question-scoped outcome.
        arm["question_checks"] = {k: v for k, v in arm["checks"].items()
                                  if not k.startswith("expected_profile_update:")}
        arm["passed"] = bool(arm["checks"]) and all(arm["checks"].values())
        arm["question_passed"] = (all(arm["question_checks"].values())
                                  if arm["workflow_available"] else None)
    fact_checks = {k: v for k, v in row["baseline"]["checks"].items()
                   if k.startswith("expected_profile_update:")}
    row["shared_baseline_fact_evaluation"] = {
        "available": row["baseline"]["workflow_available"],
        "expected_profile_updates": item.get("expected_profile_updates", {}),
        "checks": fact_checks,
        "missed_fields": [name.split(":", 1)[1] for name, ok in fact_checks.items() if not ok],
        "not_attributable_to_focused_question_pass": True,
    }
    neutral_fact_checks = {k: v for k, v in row["neutral_combined"]["checks"].items()
                           if k.startswith("expected_profile_update:")}
    row["neutral_combined_fact_evaluation"] = {
        "available": row["neutral_combined"]["workflow_available"],
        "expected_profile_updates": item.get("expected_profile_updates", {}),
        "checks": neutral_fact_checks,
        "missed_fields": [name.split(":", 1)[1] for name, ok in neutral_fact_checks.items() if not ok],
        "uses_own_fact_extraction": True,
    }
    row["paired_workflow_available"] = all(row[name]["workflow_available"] for name in ("baseline", "focused"))
    row["comparison_workflows_available"] = {
        f"baseline_to_{candidate}": all(row[name]["workflow_available"] for name in ("baseline", candidate))
        for candidate in ("neutral_combined", "focused")
    }
    row["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    row["completed"] = True
    save()
    return row


def _paired_outcomes(
    results: list[dict[str, Any]], key: str, candidate: str = "focused",
) -> dict[str, Any]:
    def outcome(arm: dict[str, Any]) -> bool | None:
        if key == "question_passed":
            return arm.get(key)
        return arm.get(key, {}).get("exact")

    counts = {"both_pass": 0, "baseline_only_pass": 0, "candidate_only_pass": 0, "both_fail": 0,
              "unavailable_pairs": 0}
    for row in results:
        baseline, focused = (outcome(row[name]) for name in ("baseline", candidate))
        if baseline is None or focused is None:
            counts["unavailable_pairs"] += 1
        elif baseline and focused:
            counts["both_pass"] += 1
        elif baseline:
            counts["baseline_only_pass"] += 1
        elif focused:
            counts["candidate_only_pass"] += 1
        else:
            counts["both_fail"] += 1
    counts["evaluated_pairs"] = len(results) - counts["unavailable_pairs"]
    denominator = counts["evaluated_pairs"]
    return {**counts, "candidate_minus_baseline_pass_rate":
            (counts["candidate_only_pass"] - counts["baseline_only_pass"]) / denominator
            if denominator else None}


def aggregate_pairs(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for name in ARMS:
        arms = [row[name] for row in results]
        latencies = [arm["extraction_latency_seconds"] for arm in arms if arm.get("completed")]
        usage_totals: dict[str, int] = {}
        for arm in arms:
            for usage in arm.get("usage", []):
                for field, value in usage.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage_totals[field] = usage_totals.get(field, 0) + value
        metrics[name] = {
            "attempted_calls": sum(arm.get("model_calls", 0) for arm in arms),
            "extraction_errors": sum(not arm.get("extraction_available", False) for arm in arms),
            "workflow_available": sum(arm.get("workflow_available", False) for arm in arms),
            "all_checks_passed": sum(arm.get("passed", False) for arm in arms),
            "question_scoped_passes": sum(arm.get("question_passed") is True for arm in arms),
            "raw_classification": adviser.aggregate(arms, "raw_topic_metrics"),
            "validated_classification": adviser.aggregate(arms, "validated_topic_metrics"),
            "latency_seconds": {"count": len(latencies),
                                "mean": statistics.mean(latencies) if latencies else None,
                                "median": statistics.median(latencies) if latencies else None},
            "usage_totals": usage_totals,
        }
    metrics["paired"] = {}
    for candidate in ("neutral_combined", "focused"):
        paired_latencies = [row[candidate]["extraction_latency_seconds"] - row["baseline"]["extraction_latency_seconds"]
                            for row in results if all(row[name].get("completed") for name in ("baseline", candidate))]
        metrics["paired"][f"baseline_to_{candidate}"] = {
            "raw_classification": _paired_outcomes(results, "raw_topic_metrics", candidate),
            "validated_classification": _paired_outcomes(results, "validated_topic_metrics", candidate),
            "question_scoped_workflow": _paired_outcomes(results, "question_passed", candidate),
            "candidate_minus_baseline_single_call_latency_seconds_mean": statistics.mean(paired_latencies) if paired_latencies else None,
        }
    metrics["fact_evaluation"] = {
        "shared_baseline_fact_miss_cases": sum(bool(row.get("shared_baseline_fact_evaluation", {}).get("missed_fields")) for row in results),
        "shared_baseline_fact_missed_fields": sum(len(row.get("shared_baseline_fact_evaluation", {}).get("missed_fields", [])) for row in results),
        "fact_misses_not_attributable_to_focused_question_pass": True,
        "neutral_combined_fact_miss_cases": sum(bool(row.get("neutral_combined_fact_evaluation", {}).get("missed_fields")) for row in results),
        "neutral_combined_fact_missed_fields": sum(len(row.get("neutral_combined_fact_evaluation", {}).get("missed_fields", [])) for row in results),
    }
    focused_pipeline_latency = [sum(row[name]["extraction_latency_seconds"] for name in ("baseline", "focused"))
                                for row in results if all(row[name].get("completed") for name in ("baseline", "focused"))]
    combined_usage = {field: metrics["baseline"]["usage_totals"].get(field, 0) + metrics["focused"]["usage_totals"].get(field, 0)
                      for field in metrics["baseline"]["usage_totals"].keys() | metrics["focused"]["usage_totals"].keys()}
    metrics["architecture_cost"] = {
        "focused_pipeline": {"calls_per_case": 2, "operations": ["baseline", "focused"],
                             "sequential_extraction_latency_seconds_mean": statistics.mean(focused_pipeline_latency) if focused_pipeline_latency else None,
                             "usage_totals": combined_usage},
        "neutral_combined_pipeline": {"calls_per_case": 1,
                                      "extraction_latency_seconds_mean": metrics["neutral_combined"]["latency_seconds"]["mean"],
                                      "usage_totals": metrics["neutral_combined"]["usage_totals"]},
        "latencies_include_failed_attempts": True,
        "focused_cost_includes_required_shared_baseline_call": True,
    }
    return metrics


def load_items(corpus_bytes: bytes, split: str) -> list[dict[str, Any]]:
    corpus = json.loads(corpus_bytes)
    if not isinstance(corpus, list) or not all(isinstance(item, dict) for item in corpus):
        raise ValueError("Corpus must be a JSON list of fictional case objects")
    items = [item for item in corpus if bool(item.get("holdout", False)) == (split == "holdout")]
    if not items:
        raise ValueError("Selected split is empty")
    for item in items:
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("Every selected case must have a nonempty string ID")
        if not isinstance(item.get("body"), str) or item.get("language") not in {"en", "zh"}:
            raise ValueError("Every selected case needs a string body and en/zh language")
        topics = item.get("expected_topics")
        if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
            raise ValueError("Every selected case needs an expected_topics list")
        # Exercise schema/context checks now, before credentials or provider use.
        seed_case(item, "preflight-fictional-policy")
        if not isinstance(item.get("expected_profile_updates", {}), dict):
            raise ValueError("expected_profile_updates must be an object")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Selected case IDs must be unique")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path, help="Explicit user-chosen frozen fictional JSON corpus")
    parser.add_argument("--split", required=True, choices=("development", "holdout"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-holdout", action="store_true", help="Explicit final evaluation only; never tune using it")
    args = parser.parse_args()
    if args.split == "holdout" and not args.allow_holdout:
        parser.error("The frozen holdout requires --allow-holdout; do not use it for development")
    if args.split == "development" and args.allow_holdout:
        parser.error("--allow-holdout is only meaningful with --split holdout")
    if args.output.exists():
        parser.error("Choose a NEW output path; retained results cannot be overwritten")
    try:
        corpus_bytes = args.corpus.read_bytes()
        items = load_items(corpus_bytes, args.split)
        policy = adviser.load_policy(POLICY)
        fingerprints = source_fingerprints()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    report: dict[str, Any] = {
        "scope": "Original combined vs neutral-wrapper combined vs focused question extraction on fictional messages; focused reuses original baseline facts; reviewed replies; captured Gmail only",
        "not_proven": ["answer quality in general", "naturalness", "universal legal accuracy", "real Gmail delivery", "final pack delivery"],
        "classification_is_not_answer_quality": True,
        "production_workflow_changed": False, "no_provider_retries": True,
        "focused_changes_only_customer_questions": True,
        "neutral_combined_uses_own_facts": True,
        "experimental_differences": {
            "baseline": "Explicit legacy user wrapper retained independently of the production default",
            "neutral_combined": "Same combined system/schema; user wrapper explicitly requests facts, date intent and questions",
            "focused": "Independent question-only prompt, user wrapper and schema; composed with original baseline facts",
        },
        "production_default_request_matches": "neutral_combined",
        "call_order_policy": "Rotate baseline, neutral_combined, focused by zero-based case index modulo three",
        "split": args.split, "holdout_authorized": args.allow_holdout, "model": MODEL,
        "model_calls": 0, "new_provider_result": True,
        "development_content_checks": args.split == "development",
        "reviewed_guidance_as_of": adviser.REVIEWED_AS_OF.isoformat(),
        "started_at": datetime.now(UTC).isoformat(), "corpus_id": str(args.corpus.resolve()),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "source_sha256": fingerprints, "expected_case_count": len(items),
        "completed_case_count": 0, "completed": False, "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report, create=True)
    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=REPOSITORY / ".secrets/deepseek_api_key.txt")
    if not key:
        report["startup_error"] = "Missing DeepSeek key; no API request made"
        write_report(args.output, report)
        parser.error(report["startup_error"])
    try:
        model = DeepSeekStructuredLLM(MODEL, api_key=key, capture_raw_responses=True)
    except Exception as error:
        report["startup_error"] = error_record(error, key)
        write_report(args.output, report)
        parser.error(str(report["startup_error"]))

    def checkpoint(row: dict[str, Any]) -> None:
        if report["results"] and report["results"][-1]["id"] == row["id"]:
            report["results"][-1] = row
        else:
            report["results"].append(row)
        report["model_calls"] = sum(result["model_calls"] for result in report["results"])
        report["completed_case_count"] = sum(result["completed"] for result in report["results"])
        report["checkpoint_at"] = datetime.now(UTC).isoformat()
        write_report(args.output, report)

    for index, item in enumerate(items):
        row = paired_case(item, index=index, policy_version=policy.version, model=model,
                          development_checks=args.split == "development", key=key, checkpoint=checkpoint)
        print(item["id"], *(name + "=" + ("PASS" if row[name]["passed"] else "FAIL") for name in ARMS), flush=True)
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged_during_run"] = fingerprints == source_fingerprints()
    report["metrics"] = aggregate_pairs(report["results"])
    report["all_passed"] = report["source_unchanged_during_run"] and all(
        row[name]["passed"] for row in report["results"] for name in ARMS
    )
    write_report(args.output, report)
    print("Report:", args.output, flush=True)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
