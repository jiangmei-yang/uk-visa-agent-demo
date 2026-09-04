"""Fictional cold-start Gmail-path probe; real extraction, zero Gmail network calls.

Every turn reconstructs the runtime around one isolated persistent SQLite database.
Rendering is deliberately deterministic: this is not full production model cost.
Development replay consumes saved attempts only; it never loads credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cold_start_checks import check_turn, load_journeys

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm import guarded as guarded_module
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"
MODEL = "deepseek-v4-flash"
AS_OF = date(2026, 9, 4)
MAX_PROVIDER_ATTEMPTS = 24
SENDER = "applicant@example.test"
MAILBOX = "visa-agent@example.test"
MESSAGE_TYPES = ("blocked", "awaiting_profile_confirmation", "awaiting_confirmation", "held_update_received")


def digest(value: Any) -> str:
    content = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(content).hexdigest()


def redact(value: Any, key: str | None) -> Any:
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, dict):
        return {name: redact(item, key) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value


def error_record(error: Exception, key: str | None = None) -> dict[str, str]:
    return {"type": type(error).__name__, "message": redact(str(error), key)}


class EvidenceWriteError(BaseException):
    """Cannot be swallowed by the production guard's provider Exception retry loop."""


class EvidenceFile:
    def __init__(self, path: Path, report: dict[str, Any]):
        self.path = path
        self.expected_digest: str | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            content = self._bytes(report)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self.expected_digest = digest(content)

    @staticmethod
    def _bytes(report):
        return (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()

    def checkpoint(self, report):
        temporary = None
        try:
            if self.path.is_symlink() or digest(self.path.read_bytes()) != self.expected_digest:
                raise ValueError("Evidence file changed outside this run")
            content = self._bytes(report)
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent,
                                             prefix=".cold-start-evidence-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.expected_digest = digest(content)
        except Exception:
            raise EvidenceWriteError("Evidence checkpoint failed; no further provider requests allowed") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def source_fingerprints():
    paths = [*sorted((ROOT / "src/visa_agent").rglob("*.py")), POLICY,
             Path(__file__).resolve(), Path(__file__).with_name("cold_start_checks.py").resolve()]
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}


def schema_fingerprints():
    return {model.__name__: digest(model.model_json_schema()) for model in (Case, CasePatch, InboundEvent)}


def input_description(journeys):
    return [{"id": journey["id"], "language": journey["language"], "subject": journey["subject"],
             "turns": [{"id": turn["id"], "body": turn["body"]} for turn in journey["turns"]]}
            for journey in journeys]


def usage_metrics(rows, *, historical=False):
    field = "historical_attempts" if historical else "attempts"
    attempts = [attempt for row in rows for attempt in row.get(field, [])
                if historical or attempt.get("new_provider_attempt")]
    records = [entry for attempt in attempts for entry in attempt.get("usage", [])]
    keys = sorted({key for record in records for key, value in record.items()
                   if key.endswith("_tokens") and type(value) is int and value >= 0})
    return {"attempts": len(attempts), "usage_records": len(records),
            "tokens": {key: sum(record[key] for record in records
                                if type(record.get(key)) is int and record[key] >= 0) for key in keys},
            "token_key_record_counts": {key: sum(type(record.get(key)) is int and record[key] >= 0
                                                   for record in records) for key in keys},
            "usage_unavailable_attempts": sum(not attempt.get("usage") for attempt in attempts),
            "missing_usage_is_zero": False}


class ReplayFailure(RuntimeError):
    pass


class ReplayModel:
    version = MODEL
    model = MODEL

    def __init__(self, original):
        self.original = original
        self.index = 0
        self.usage_history = []
        self.last_extraction_content = None
        self.integrity_errors = []

    def extract_case_patch(self, event):
        if self.index >= len(self.original["attempts"]):
            self.integrity_errors.append("Saved attempts exhausted; no replacement attempt allowed")
            raise ReplayFailure(self.integrity_errors[-1])
        attempt = self.original["attempts"][self.index]
        self.index += 1
        if attempt["prepared_event"] != event.model_dump(mode="json"):
            self.integrity_errors.append("Prepared input differs from the original provider attempt")
            raise ReplayFailure(self.integrity_errors[-1])
        self.last_extraction_content = attempt["raw_response_content"]
        if not attempt["extraction_available"]:
            raise ReplayFailure("Recorded extraction failure: " + attempt["error"]["type"])
        return CasePatch.model_validate(deepcopy(attempt["raw_patch"]))


class ObservedModel:
    """Record every delegate call before and after the real production guard sees it."""

    def __init__(self, delegate, row, save, budget, *, replay=False):
        self.delegate, self.row, self.save, self.budget = delegate, row, save, budget
        self.replay = replay
        self.version = getattr(delegate, "version", MODEL)

    def extract_case_patch(self, event):
        prepared = event.model_dump(mode="json")
        if self.row["attempts"]:
            self.row["attempts"][-1]["guard_retry_followed"] = True
        attempt = {"index": len(self.row["attempts"]) + 1, "prepared_event": prepared,
                   "prepared_input_sha256": digest(prepared), "completed": False,
                   "extraction_available": False, "usage": [], "raw_response_content": None,
                   "new_provider_attempt": not self.replay, "guard_retry_followed": False}
        self.row["attempts"].append(attempt)
        if not self.replay:
            if self.budget["calls"] >= MAX_PROVIDER_ATTEMPTS:
                attempt.update(new_provider_attempt=False, completed=True,
                               error={"type": "BudgetError", "message": "Provider attempt cap reached"})
                self.save()
                raise RuntimeError("Provider attempt cap reached")
            self.budget["calls"] += 1
        self.save()
        self.delegate.last_extraction_content = None
        start = len(self.delegate.usage_history)
        started = time.perf_counter()
        try:
            proposed = self.delegate.extract_case_patch(event.model_copy(deep=True))
            attempt["raw_patch"] = proposed.model_dump(mode="json")
            attempt["extraction_available"] = True
            return proposed
        except Exception as error:
            attempt["error"] = error_record(error)
            raise
        finally:
            attempt.update(usage=deepcopy(self.delegate.usage_history[start:]),
                           raw_response_content=self.delegate.last_extraction_content,
                           elapsed_seconds=round(time.perf_counter() - started, 6), completed=True)
            self.save()

    render_message = staticmethod(deterministic_fallback_message)


class ObservedGuard(GuardedLLM):
    def __init__(self, delegate, row, save):
        super().__init__(delegate, max_attempts=2)
        self.row, self.save = row, save

    def extract_case_patch(self, event):
        validator = guarded_module.validate_case_patch

        def observe_validation(prepared, proposed):
            attempt = self.row["attempts"][-1]
            try:
                result = validator(prepared, proposed)
                attempt["guard_validation"] = {"patch": result.model_dump(mode="json")}
                return result
            except Exception as error:
                attempt["guard_validation"] = {"error": error_record(error)}
                raise
            finally:
                self.save()

        # Transparent observation of the actual guard call; no alternate validator.
        with patch.object(guarded_module, "validate_case_patch", observe_validation):
            result = super().extract_case_patch(event)
        for index, attempt in enumerate(self.row["attempts"]):
            attempt["selected_by_guard"] = not self.last_extraction_fallback and index == len(self.row["attempts"]) - 1
        self.row["guard"] = {"patch": result.model_dump(mode="json"),
                             "fallback": self.last_extraction_fallback,
                             "error": self.last_extraction_error}
        self.save()
        return result


class NoProvider:
    version = "duplicate-must-not-call-provider"

    def __init__(self):
        self.calls = 0

    def extract_case_patch(self, event):
        self.calls += 1
        raise AssertionError("Duplicate replay must not invoke extraction")

    def render_message(self, case, plan):
        self.calls += 1
        raise AssertionError("Duplicate replay must not invoke rendering")


class CaptureGmail(GmailAdapter):
    def __init__(self, requests, save, *, forbidden=False):
        self.requests, self.save, self.forbidden = requests, save, forbidden

    def send_reply(self, **request):
        if self.forbidden:
            raise AssertionError("Duplicate replay must not send")
        if parseaddr(request["recipient"])[1] != SENDER or request.get("attachment") is not None:
            raise AssertionError("Only fictional recipient text captures are allowed")
        self.requests.append(deepcopy(request))
        self.save()
        return {"id": "capture-" + digest(request["message_id"])[:24]}

    def find_sent_message(self, rfc_message_id):
        raise AssertionError("This extraction probe does not reconcile or contact Gmail")


def database_snapshot(store):
    return {"cases": [case.model_dump(mode="json") for case in store.list_cases()],
            "outbox": store.list_outbox(), "counts": store.counts(),
            "processed_events": [dict(row) for row in store.connection.execute(
                "SELECT * FROM processed_events ORDER BY event_id")],
            "held_events": [dict(row) for row in store.connection.execute(
                "SELECT * FROM held_inbound_events ORDER BY id")]}


def make_mime(journey, turn, index, store):
    provider_id = "event-" + digest([journey["id"], turn["id"]])[:24]
    thread_id = "thread-" + digest(journey["id"])[:24]
    message = EmailMessage()
    message["From"], message["To"] = SENDER, MAILBOX
    message["Subject"] = journey["subject"]
    message["Date"] = format_datetime(datetime(AS_OF.year, AS_OF.month, AS_OF.day, 12, tzinfo=UTC)
                                       + timedelta(minutes=index))
    message["Message-ID"] = f"<{provider_id}@example.test>"
    previous = store.connection.execute(
        "SELECT id FROM outbox WHERE status='SENT' ORDER BY rowid DESC LIMIT 1",
    ).fetchone()
    if previous:
        message["In-Reply-To"] = f"<{previous['id']}@visa-agent.local>"
        message["References"] = message["In-Reply-To"]
    message.set_content(turn["body"])
    return provider_id, thread_id, message.as_bytes()


def duplicate_check(database, event):
    store = SQLiteStore(database)
    try:
        before = database_snapshot(store)
        no_provider = NoProvider()
        workflow = WorkflowService(store, load_policy(POLICY), no_provider, today_provider=lambda: AS_OF)
        _, duplicate, plan = workflow.process(event.model_copy(deep=True))
        requests = []
        sender = AutomaticGmailReplySender(CaptureGmail(requests, lambda: None, forbidden=True), store, SENDER)
        outcomes = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=MESSAGE_TYPES).dispatch_due(
            event.received_at, limit=1)
        after = database_snapshot(store)
        return {"duplicate": duplicate, "plan": plan, "case_outbox_processed_unchanged": before == after,
                "zero_send": not requests and not outcomes, "zero_provider_calls": no_provider.calls == 0,
                "passed": duplicate and before == after and not requests and not outcomes and no_provider.calls == 0}
    finally:
        store.close()


def execute_turn(journey, turn, index, database, row, factory, save, budget, original=None):
    store = SQLiteStore(database)
    delegate = None
    event = None
    try:
        row["stage"] = "starting_fresh_runtime"
        row["before"] = database_snapshot(store)
        provider_id, thread_id, raw = make_mime(journey, turn, index, store)
        row.update(provider_message_id=provider_id, provider_thread_id=thread_id, mime_sha256=digest(raw))
        result = EmailIngestionBoundary(store, database.parent / "attachments").ingest(
            raw, provider_message_id=provider_id, provider_thread_id=thread_id, channel="gmail")
        if result.event is None:
            raise ValueError("Fictional MIME ingestion rejected: " + str(result.failure_code))
        event = result.event
        row["input_event"] = event.model_dump(mode="json")
        row["input_event_sha256"] = digest(row["input_event"])
        if original is not None and row["input_event"] != original["input_event"]:
            raise ReplayFailure("MIME event input changed from original provider run")
        delegate = ReplayModel(original) if original is not None else factory()
        if original is None and getattr(getattr(delegate, "client", None), "max_retries", 0) != 0:
            raise ValueError("Probe requires SDK max_retries=0")
        observed = ObservedModel(delegate, row, save, budget, replay=original is not None)
        guard = ObservedGuard(observed, row, save)
        workflow = WorkflowService(store, load_policy(POLICY), guard, today_provider=lambda: AS_OF)
        save()
        case, duplicate, plan = workflow.process(event)
        row.update(stage="workflow_committed", plan=plan, first_processing_duplicate=duplicate,
                   workflow_outbox_before_send=deepcopy(store.list_outbox()),
                   render_fallback=guard.last_render_fallback, render_error=guard.last_render_error)
        save()
        sender = AutomaticGmailReplySender(CaptureGmail(row["captured_requests"], save), store, SENDER)
        row["held_receipts_queued"] = sender.queue_finalized_update_receipts()
        row["obsolete_unsent_withheld"] = sender.withhold_obsolete_unsent()
        outcomes = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=MESSAGE_TYPES).dispatch_due(
            event.received_at, limit=1)
        row["dispatch_outcomes"] = [vars(outcome) for outcome in outcomes]
        row["after"] = database_snapshot(store)
        stored = [item for item in store.list_outbox() if item["event_id"] == event.id]
        capture = row["captured_requests"]
        reply = capture[0]["body"] if len(capture) == 1 else ""
        row["body"] = reply
        row["flow_checks"] = {
            "fresh_event_not_duplicate": not duplicate,
            "one_expected_current_outbox": len(stored) == 1,
            "one_captured_send": len(capture) == len(outcomes) == 1 and outcomes[0].status == "SENT",
            "exact_persisted_body": len(stored) == len(capture) == 1 and stored[0]["payload"] == reply,
            "capture_matches_current_outbox": len(stored) == len(capture) == 1
                and capture[0]["message_id"] == f"<{stored[0]['id']}@visa-agent.local>",
            "no_pack_release": case.delivery_path is None and not any(item.get("attachment") for item in capture),
        }
        row["proxy_checks"] = check_turn(journey, turn, case, reply)
        save()
    except Exception as error:
        row["error"] = error_record(error)
    finally:
        row["after"] = database_snapshot(store)
        store.close()
        if original is not None:
            row["unused_historical_attempts"] = deepcopy(original["attempts"][getattr(delegate, "index", 0):])
            row["replay_integrity_errors"] = getattr(delegate, "integrity_errors", [])
        client = getattr(delegate, "client", None)
        if client is not None and hasattr(client, "close"):
            client.close()
        save()
    if event is not None and "error" not in row:
        try:
            row["duplicate_reopen"] = duplicate_check(database, event)
        except Exception as error:
            row["duplicate_error"] = error_record(error)
    row.update(stage="finished", completed=True)
    row["passed"] = bool("error" not in row and "duplicate_error" not in row
                         and row.get("flow_checks") and all(row["flow_checks"].values())
                         and row.get("proxy_checks") and all(row["proxy_checks"].values())
                         and row.get("duplicate_reopen", {}).get("passed")
                         and not row.get("replay_integrity_errors"))
    save()


def validate_replay(original, journeys, corpus_hash, schema_hashes):
    if (not isinstance(original, dict) or original.get("split") != "development"
            or original.get("mode") != "provider" or original.get("holdout_authorized") is not False
            or original.get("completed") is not True or original.get("source_unchanged") is not True
            or original.get("corpus_unchanged") is not True):
        raise ValueError("Replay requires a complete original development provider report")
    if (original.get("format_version") != 1 or original.get("guard_max_attempts") != 2
            or original.get("sdk_max_retries") != 0 or original.get("max_provider_attempts") != MAX_PROVIDER_ATTEMPTS
            or type(original.get("provider_attempts")) is not int
            or not 0 <= original["provider_attempts"] <= MAX_PROVIDER_ATTEMPTS):
        raise ValueError("Original probe or retry configuration mismatch")
    if (original.get("corpus_sha256") != corpus_hash
            or original.get("selected_input_sha256") != digest(input_description(journeys))
            or original.get("schema_sha256") != schema_hashes
            or original.get("model") != MODEL or original.get("as_of") != AS_OF.isoformat()):
        raise ValueError("Original corpus, inputs, schema, model or evaluation date mismatch")
    sources = original.get("source_sha256")
    if (not isinstance(sources, dict) or not sources
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value) for value in sources.values())
            or original.get("source_bundle_sha256") != digest(sources)):
        raise ValueError("Original source manifest integrity mismatch")
    expected = [(journey["id"], turn["id"]) for journey in journeys for turn in journey["turns"]]
    rows = original.get("turns")
    if not isinstance(rows, list) or [(row.get("journey_id"), row.get("turn_id")) for row in rows] != expected:
        raise ValueError("Original report omitted, duplicated or reordered turns")
    for row in rows:
        if (row.get("completed") is not True or "input_event" not in row
                or not isinstance(row.get("attempts"), list) or len(row["attempts"]) > 2):
            raise ValueError("Original report lacks a complete turn input or attempt ledger")
        if row.get("input_event_sha256") != digest(row["input_event"]):
            raise ValueError("Original event input integrity mismatch")
        for index, attempt in enumerate(row["attempts"], 1):
            if (attempt.get("index") != index or attempt.get("completed") is not True
                    or attempt.get("new_provider_attempt") is not True
                    or attempt.get("prepared_input_sha256") != digest(attempt.get("prepared_event"))
                    or "raw_response_content" not in attempt or not isinstance(attempt.get("usage"), list)):
                raise ValueError("Original attempt ledger is incomplete")
            if attempt.get("extraction_available") is True:
                parsed = CasePatch.model_validate_json(attempt["raw_response_content"])
                if parsed.model_dump(mode="json") != attempt.get("raw_patch"):
                    raise ValueError("Original raw response and parsed patch disagree")
            elif "error" not in attempt or "raw_patch" in attempt:
                raise ValueError("Original failed extraction may not be silently repaired")
    if sum(len(row["attempts"]) for row in rows) != original.get("provider_attempts"):
        raise ValueError("Original provider attempt accounting mismatch")
    return rows


def run_probe(*, corpus_path, split, output, allow_holdout=False, replay_from=None, model_factory=None):
    if split not in {"development", "holdout"} or (split == "holdout" and not allow_holdout):
        raise ValueError("Holdout requires explicit authorization")
    if replay_from is not None and split != "development":
        raise ValueError("Holdout replay is forbidden")
    forbidden = {corpus_path.resolve(), *(set() if replay_from is None else {replay_from.resolve()})}
    if output.resolve() in forbidden or ".secrets" in output.resolve().parts:
        raise ValueError("Evidence output must not replace inputs or use the secrets directory")
    report = {"format_version": 1, "mode": "replay" if replay_from else "provider", "split": split,
        "holdout_authorized": split == "holdout" and allow_holdout, "model": MODEL, "as_of": AS_OF.isoformat(),
        "transport": "injected_test_factory" if model_factory is not None else "real_deepseek",
        "started_at": datetime.now(UTC).isoformat(), "completed": False, "all_passed": False,
        "turns": [], "errors": [], "provider_attempts": 0, "max_provider_attempts": MAX_PROVIDER_ATTEMPTS,
        "guard_max_attempts": 2, "sdk_max_retries": 0, "gmail_network_calls": 0,
        "render_mode": "deterministic_extraction_only_not_full_production_cost", "automatic_mode": "reviewed",
        "acceptance_basis": "post_guard_proxy_checks_not_first_attempt_success_or_semantic_quality",
        "attempt_counter_boundary": "delegate_extraction_calls_SDK_retries_disabled_not_a_billing_invoice",
        "naturalness_scored": False, "manual_information_review_required": True,
        "cold_start_mode": "new_database_connections_and_runtime_instances_not_new_OS_processes"}
    writer = EvidenceFile(output, report)
    budget = {"calls": 0}
    key = None

    def save():
        report["provider_attempts"] = budget["calls"]
        report["checkpoint_at"] = datetime.now(UTC).isoformat()
        writer.checkpoint(redact(report, key))

    corpus_bytes = original_bytes = None
    sources = None
    try:
        sources = source_fingerprints()
        schemas = schema_fingerprints()
        corpus_bytes = corpus_path.read_bytes()
        journeys = load_journeys(corpus_bytes, split)
        if len(journeys) != 2 or any(len(journey["turns"]) != 6 for journey in journeys):
            raise ValueError("This bounded probe requires exactly two six-turn journeys per split")
        report.update(source_sha256=sources, source_bundle_sha256=digest(sources), schema_sha256=schemas,
                      corpus_sha256=digest(corpus_bytes), selected_input_sha256=digest(input_description(journeys)),
                      expected_turn_count=12)
        originals = None
        if replay_from is not None:
            original_bytes = replay_from.read_bytes()
            originals = validate_replay(json.loads(original_bytes), journeys, digest(corpus_bytes), schemas)
            report.update(original_report_sha256=digest(original_bytes),
                          original_source_sha256=json.loads(original_bytes)["source_sha256"],
                          transport="saved_provider_attempts_no_network")
        for journey in journeys:
            for turn in journey["turns"]:
                row = {"journey_id": journey["id"], "turn_id": turn["id"], "language": journey["language"],
                       "body_input": turn["body"], "attempts": [], "captured_requests": [],
                       "completed": False, "stage": "not_started", "passed": False}
                if originals is not None:
                    row["historical_attempts"] = deepcopy(originals[len(report["turns"])]["attempts"])
                report["turns"].append(row)
        save()
        if originals is None and model_factory is None:
            key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                              default_file=ROOT / ".secrets/deepseek_api_key.txt")
            if not key:
                raise ValueError("Missing configured DeepSeek credential")
            def model_factory():
                return DeepSeekStructuredLLM(MODEL, api_key=key, capture_raw_responses=True)
        offset = 0
        for journey in journeys:
            with tempfile.TemporaryDirectory(prefix="visa-cold-start-probe-") as directory:
                Path(directory).chmod(0o700)
                for index, turn in enumerate(journey["turns"]):
                    row = report["turns"][offset]
                    try:
                        execute_turn(journey, turn, index, Path(directory) / "case.db", row,
                                     model_factory, save, budget, originals[offset] if originals else None)
                    except Exception as error:
                        row.update(error=error_record(error), completed=True, stage="failed", passed=False)
                        save()
                    for field in ("error", "duplicate_error"):
                        if field in row:
                            report["errors"].append({"journey_id": journey["id"], "turn_id": turn["id"],
                                                     "stage": field, **row[field]})
                    offset += 1
        report["completed"] = all(row["completed"] for row in report["turns"])
    except Exception as error:
        report["errors"].append(error_record(error, key))
    finally:
        try:
            report["source_unchanged"] = sources is not None and sources == source_fingerprints()
            report["corpus_unchanged"] = corpus_bytes is not None and corpus_path.read_bytes() == corpus_bytes
            if replay_from is not None:
                report["original_report_unchanged"] = original_bytes is not None and replay_from.read_bytes() == original_bytes
        except Exception as error:
            report.update(source_unchanged=False, corpus_unchanged=False)
            report["errors"].append(error_record(error, key))
        report["metrics"] = usage_metrics(report["turns"])
        report["guard_summary"] = {
            "turns_with_extraction": sum(bool(row["attempts"]) for row in report["turns"]),
            "first_attempt_accepted_turns": sum(len(row["attempts"]) == 1
                and row["attempts"][0].get("selected_by_guard", False) for row in report["turns"]),
            "turns_with_guard_retry": sum(len(row["attempts"]) > 1 for row in report["turns"]),
            "fallback_turns": sum(row.get("guard", {}).get("fallback", False) for row in report["turns"]),
        }
        if replay_from is not None:
            report["historical_metrics"] = usage_metrics(report["turns"], historical=True)
        report["all_passed"] = bool(report["completed"] and not report["errors"] and report["source_unchanged"]
                                    and report["corpus_unchanged"] and all(row["passed"] for row in report["turns"])
                                    and report.get("original_report_unchanged", True))
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()
    return redact(report, key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-from", type=Path)
    args = parser.parse_args()
    try:
        report = run_probe(corpus_path=args.corpus, split=args.split, output=args.output,
                           allow_holdout=args.allow_holdout, replay_from=args.replay_from)
    except (ValueError, OSError, EvidenceWriteError) as error:
        parser.exit(2, f"Probe stopped ({type(error).__name__}); no report was silently overwritten.\n")
    print(json.dumps({"completed": report["completed"], "all_passed": report["all_passed"],
                      "provider_attempts": report["provider_attempts"], "metrics": report["metrics"]}, indent=2))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
