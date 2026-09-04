"""Replay saved neutral development patches through current guarded reply rendering.

This is exposed deterministic replay, not a fresh classifier evaluation. No key
is read, no model is called, and Gmail delivery is captured with network disabled.
Only the supplied completed three-arm development report's neutral raw patches
may be replayed. Original reports and the supplied corpus are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch

_helper_spec = importlib.util.spec_from_file_location(
    "_adviser_reply_replay_helpers", Path(__file__).with_name("adviser_intent_probe.py"),
)
assert _helper_spec is not None and _helper_spec.loader is not None
adviser = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(adviser)

REPOSITORY = adviser.REPOSITORY
POLICY = adviser.POLICY
seed_case = adviser.seed_case
exercise_workflow = adviser.exercise_workflow
write_report = adviser.write_report


def source_fingerprints() -> dict[str, str]:
    fingerprints = adviser.source_fingerprints()
    for name in (
        "scripts/adviser_reply_replay.py",
        "src/visa_agent/llm/question_understanding.py",
        str(POLICY.relative_to(REPOSITORY)),
    ):
        fingerprints[name] = hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest()
    return fingerprints


def load_source_report(path: Path) -> tuple[dict[str, Any], str]:
    """Reject holdout/incomplete/replayed sources before even opening the corpus."""
    source_bytes = path.read_bytes()
    source = json.loads(source_bytes)
    if not isinstance(source, dict):
        raise ValueError("Source report must be an object")
    if source.get("split") != "development":
        raise ValueError("Only development reports may be replayed; holdout is forbidden")
    if source.get("completed") is not True:
        raise ValueError("Source report must be completed")
    if source.get("new_provider_result") is not True:
        raise ValueError("Source must be the original provider report, not another replay")
    fingerprints = source.get("source_sha256")
    if not isinstance(fingerprints, dict) or not fingerprints or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in fingerprints.items()
    ):
        raise ValueError("Source report must retain its original source fingerprints")
    return source, hashlib.sha256(source_bytes).hexdigest()


def verify_source_cases(
    source: dict[str, Any], corpus_bytes: bytes, policy_version: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match the exact development cases; never substitute another arm's patch."""
    corpus_hash = hashlib.sha256(corpus_bytes).hexdigest()
    if source.get("corpus_sha256") != corpus_hash:
        raise ValueError("Source report corpus hash mismatch")
    corpus = json.loads(corpus_bytes)
    if not isinstance(corpus, list) or not all(isinstance(item, dict) for item in corpus):
        raise ValueError("Corpus must be a JSON list of fictional case objects")
    items = [item for item in corpus if not bool(item.get("holdout", False))]
    if not items:
        raise ValueError("Selected development split is empty")
    if not all(isinstance(item.get("id"), str) and item["id"].strip() for item in items):
        raise ValueError("Development cases must have nonempty string IDs")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Development case IDs must be unique")
    rows = source.get("results")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Source results must be case objects")
    if (len(rows) != len(items) or source.get("expected_case_count") != len(items)
            or source.get("completed_case_count") != len(items)):
        raise ValueError("Source completed case count does not match the development split")
    if not all(isinstance(row.get("id"), str) for row in rows):
        raise ValueError("Source rows must have string IDs")
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != {item["id"] for item in items}:
        raise ValueError("Source IDs must uniquely and exactly match the development split")
    selected = []
    for item in items:
        row = by_id[item["id"]]
        if row.get("completed") is not True:
            raise ValueError(f"Source case is incomplete: {item['id']}")
        if (row.get("body") != item.get("body") or row.get("language") != item.get("language")
                or row.get("expected_topics") != item.get("expected_topics")):
            raise ValueError(f"Source body, language or expected topics mismatch: {item['id']}")
        if row.get("expected_profile_updates", {}) != item.get("expected_profile_updates", {}):
            raise ValueError(f"Source expected profile updates mismatch: {item['id']}")
        if not isinstance(item.get("body"), str) or item.get("language") not in {"en", "zh"}:
            raise ValueError(f"Invalid fictional message or language: {item['id']}")
        if not isinstance(item.get("expected_topics"), list):
            raise ValueError(f"Missing expected topics: {item['id']}")
        initial = seed_case(item, policy_version)
        if row.get("profile_before") != initial.profile.model_dump(mode="json"):
            raise ValueError(f"Source seed profile mismatch: {item['id']}")
        if not all(isinstance(row.get(arm), dict) for arm in ("baseline", "neutral_combined", "focused")):
            raise ValueError(f"Source must be a three-arm report with neutral extraction: {item['id']}")
        neutral = row["neutral_combined"]
        if (neutral.get("operation") != "extract_case_patch_neutral_input"
                or neutral.get("attempted") is not True or neutral.get("completed") is not True):
            raise ValueError(f"Missing completed neutral extraction attempt: {item['id']}")
        if neutral.get("extraction_available") is False and isinstance(neutral.get("error"), dict):
            if neutral.get("raw_patch") is not None:
                raise ValueError(f"Failed neutral extraction has a contradictory raw patch: {item['id']}")
            # A recorded provider/parse failure is retained as unavailable, not
            # repaired with baseline/focused output or a fabricated empty patch.
        elif neutral.get("extraction_available") is True and "raw_patch" in neutral:
            CasePatch.model_validate(neutral["raw_patch"])
        else:
            raise ValueError(f"Missing neutral raw patch or recorded extraction error: {item['id']}")
        selected.append((item, row))
    return selected


def replay_case(item: dict[str, Any], original: dict[str, Any], policy_version: str) -> dict[str, Any]:
    started = time.perf_counter()
    neutral = original["neutral_combined"]
    row: dict[str, Any] = {
        "id": item["id"], "body": item["body"], "language": item["language"],
        "expected_topics": item["expected_topics"],
        "expected_profile_updates": item.get("expected_profile_updates", {}),
        "new_model_calls": 0, "new_provider_result": False, "usage": [],
        "reused_arm": "neutral_combined", "available": False, "completed": False,
        "original_neutral_usage": deepcopy(neutral.get("usage", [])),
        "original_neutral_checks": deepcopy(neutral.get("checks", {})),
        "original_provider_bound_body": neutral.get("provider_bound_body"),
        "checks": {},
    }
    if neutral.get("extraction_available") is not True:
        row["unavailable_reason"] = "original_neutral_extraction_failed"
        row["original_neutral_error"] = deepcopy(neutral["error"])
        row["checks"]["saved_neutral_extraction_available"] = False
    else:
        row["raw_patch"] = deepcopy(neutral["raw_patch"])
        try:
            initial = seed_case(item, policy_version)
            event = InboundEvent(
                id=f"inbound-{item['id']}", channel="gmail",
                external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
                subject="英国旅行材料咨询" if item["language"] == "zh" else "UK visit preparation",
                body=adviser.latest_reply_text(item["body"]),
                known_profile=initial.profile.model_dump(mode="json"), received_at=datetime.now(UTC),
                rfc_message_id=f"<inbound-{item['id']}@example.test>",
            )
            proposed = CasePatch.model_validate(row["raw_patch"])
            validated = validate_case_patch(event, proposed)
            row["validated_patch"] = validated.model_dump(mode="json")
            row["replayed_guarded_topics"] = sorted({question.topic for question in validated.customer_questions})
            result = exercise_workflow(item, initial, event, proposed, validated, development_checks=True)
            row["checks"].update(result.pop("checks"))
            row.update(result)
            row["available"] = True
        except Exception as error:
            row["error"] = {"type": type(error).__name__, "message": str(error)}
            row["checks"]["replay_completed_without_error"] = False
    row["passed"] = row["available"] and bool(row["checks"]) and all(row["checks"].values())
    row["completed"] = True
    row["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a NEW output path; retained results cannot be overwritten")
    try:
        source, source_report_hash = load_source_report(args.source_report)
        corpus_bytes = args.corpus.read_bytes()
        policy = adviser.load_policy(POLICY)
        selected = verify_source_cases(source, corpus_bytes, policy.version)
        fingerprints = source_fingerprints()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    report: dict[str, Any] = {
        "scope": "Exposed deterministic replay of saved neutral development patches through current guards and captured Gmail replies; not fresh classifier accuracy",
        "exposed_deterministic_replay": True, "split": "development",
        "new_provider_result": False, "new_classifier_result": False, "new_model_calls": 0,
        "not_proven": ["fresh classifier accuracy", "unseen holdout generalization",
                       "real customer acceptance", "real Gmail delivery", "universal legal accuracy"],
        "source_report": str(args.source_report.resolve()), "source_report_sha256": source_report_hash,
        "original_source_sha256": source["source_sha256"], "current_source_sha256": fingerprints,
        "original_model": source.get("model"), "original_started_at": source.get("started_at"),
        "original_source_unchanged_during_run": source.get("source_unchanged_during_run"),
        "corpus_id": str(args.corpus.resolve()), "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "reviewed_guidance_as_of": adviser.REVIEWED_AS_OF.isoformat(),
        "development_content_checks": True, "expected_case_count": len(selected),
        "completed_case_count": 0, "completed": False, "results": [],
        "started_at": datetime.now(UTC).isoformat(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report, create=True)
    for item, original in selected:
        row = replay_case(item, original, policy.version)
        report["results"].append(row)
        report["completed_case_count"] += 1
        report["checkpoint_at"] = datetime.now(UTC).isoformat()
        write_report(args.output, report)
        failed = [name for name, passed in row["checks"].items() if not passed]
        print(item["id"], "PASS" if row["passed"] else "FAIL", ", ".join(failed), flush=True)
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged_during_replay"] = fingerprints == source_fingerprints()
    report["available_replay_cases"] = sum(row["available"] for row in report["results"])
    report["unavailable_replay_cases"] = len(selected) - report["available_replay_cases"]
    report["deterministic_reply_checks_passed_cases"] = sum(row["passed"] for row in report["results"])
    report["all_passed"] = report["source_unchanged_during_replay"] and all(row["passed"] for row in report["results"])
    write_report(args.output, report)
    print("Deterministic replay report:", args.output, flush=True)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
