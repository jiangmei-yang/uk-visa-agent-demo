"""Local evidence-query recovery: real Gmail/outbox boundaries, no OAuth or mail."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.outbound import (
    OutboxDispatcher,
    PermanentChannelError,
    ReconciliationAccessError,
)
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
PRIVATE_DETAIL = "synthetic-private-provider-detail"


def _http_error(status: int, reason: str | None = None) -> Exception:
    error = RuntimeError(PRIVATE_DETAIL)
    error.resp = SimpleNamespace(status=status)  # type: ignore[attr-defined]
    error.content = json.dumps({"error": {"errors": [{"reason": reason}]}})  # type: ignore[attr-defined]
    return error


def _refresh_error(monkeypatch: pytest.MonkeyPatch) -> Exception:
    # Exercise typed SDK recognition without requiring the optional live dependencies.
    class RefreshError(Exception):
        pass

    module = ModuleType("google.auth.exceptions")
    module.RefreshError = RefreshError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", module)
    return RefreshError(PRIVATE_DETAIL)


class FakeGmailService:
    def __init__(self) -> None:
        self.failure: Exception | None = None
        self.failure_on = "list"
        self.list_results: list[dict[str, Any]] = []
        self.get_results: list[dict[str, Any]] = []
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.send_calls = 0

    def users(self) -> FakeGmailService:
        return self

    def messages(self) -> FakeGmailService:
        return self

    def list(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.reads.append(("list", kwargs))
            if self.failure is not None and (
                self.failure_on == "list"
                or (self.failure_on == "fallback_list" and kwargs["q"] == "in:sent")
            ):
                raise self.failure
            return self.list_results.pop(0)

        return SimpleNamespace(execute=execute)

    def get(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.reads.append(("get", kwargs))
            if self.failure is not None and self.failure_on == "get" and not self.get_results:
                raise self.failure
            return self.get_results.pop(0)

        return SimpleNamespace(execute=execute)

    def send(self, **kwargs: Any) -> SimpleNamespace:
        self.send_calls += 1

        def execute() -> dict[str, Any]:
            if self.failure is not None:
                raise self.failure
            pytest.fail("Evidence-query recovery must never send an email")

        return SimpleNamespace(execute=execute)


def _seed(store: SQLiteStore, *, uncertain: bool = True) -> tuple[Case, str]:
    case = Case(id="reconcile-case", external_thread_id="gmail-thread",
                applicant_contact="applicant@example.test", primary_channel="gmail",
                policy_version="synthetic-policy")
    event = InboundEvent(id="prior-email", external_thread_id=case.external_thread_id,
                         sender=case.applicant_contact, subject="UK visit", body="A synthetic enquiry",
                         channel="gmail", received_at=NOW)
    store.commit_event(case, event, "blocked", "Previously prepared synthetic reply")
    identifier = str(store.list_outbox()[0]["id"])
    if uncertain:
        assert store.claim_pending_outbox(NOW, channel="gmail")[0]["id"] == identifier
        store.mark_outbox_uncertain(identifier, "Previous send response was lost")
    return case, identifier


@pytest.mark.parametrize("failure_kind", [
    "401", "insufficientPermissions", "authError", "domainPolicy", "forbidden", "sdk_refresh",
])
@pytest.mark.parametrize("failure_on", ["list", "fallback_list"])
def test_access_failure_keeps_sending_and_reopens_for_query_only_recovery(
    tmp_path, monkeypatch, failure_kind, failure_on,
):
    service = FakeGmailService()
    service.failure_on = failure_on
    service.failure = (_refresh_error(monkeypatch) if failure_kind == "sdk_refresh" else
                       _http_error(401 if failure_kind == "401" else 403, failure_kind))
    sender = GmailReplySender(GmailAdapter(service))
    path = tmp_path / "recovery.db"
    store = SQLiteStore(path)
    try:
        case, identifier = _seed(store)
        original = store.list_outbox()[0]
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        for days in (0, 1):
            service.list_results = [{"messages": []}]
            outcome = dispatcher.reconcile_sending(sender, NOW + timedelta(days=days))
            assert [item.status for item in outcome] == ["SENDING"]
            assert outcome[0].reason_code == "ACCESS_REQUIRED"
            assert store.list_outbox()[0] == original
            assert dispatcher.dispatch_due(NOW + timedelta(days=days)) == []
        assert store.get_case(case.id) == case
        assert service.send_calls == 0
    finally:
        store.close()

    service.failure = None
    service.list_results = [{"messages": [{"id": "already-accepted-provider-id"}]}]
    reopened = SQLiteStore(path)
    try:
        dispatcher = OutboxDispatcher(reopened, sender, channel="gmail")
        assert dispatcher.dispatch_due(NOW + timedelta(days=2)) == []
        outcome = dispatcher.reconcile_sending(sender, NOW + timedelta(days=2))
        assert [(item.outbox_id, item.status) for item in outcome] == [(identifier, "SENT")]
        assert outcome[0].reason_code is None
        row = reopened.list_outbox()[0]
        assert row["provider_message_id"] == "already-accepted-provider-id"
        assert row["attempt_count"] == original["attempt_count"] == 1
        assert row["payload"] == original["payload"]
        assert row["sent_at"] is not None and row["last_error"] is None
        assert PRIVATE_DETAIL not in json.dumps(row)
        assert dispatcher.reconcile_sending(sender, NOW + timedelta(days=3)) == []
        assert dispatcher.dispatch_due(NOW + timedelta(days=3)) == []
        assert service.send_calls == 0
    finally:
        reopened.close()


def test_access_failure_during_marker_scan_does_not_accept_partial_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "marker.db")
    service = FakeGmailService()
    service.failure = _http_error(403, "insufficientPermissions")
    service.failure_on = "get"
    sender = GmailReplySender(GmailAdapter(service))
    try:
        _, identifier = _seed(store)
        marker = f"<{identifier}@visa-agent.local>"
        match = {"labelIds": ["SENT"], "payload": {"headers": [
            {"name": "X-Visa-Agent-Message-ID", "value": marker},
        ]}}
        pages = [{"messages": []}, {"messages": [{"id": "matching"}, {"id": "unchecked"}]}]
        service.list_results = list(pages)
        service.get_results = [match]
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        assert dispatcher.reconcile_sending(sender, NOW)[0].status == "SENDING"
        assert store.list_outbox()[0]["provider_message_id"] is None
        assert dispatcher.dispatch_due(NOW) == []

        service.failure = None
        service.list_results = list(pages)
        service.get_results = [match, {"labelIds": ["SENT"], "payload": {"headers": []}}]
        assert dispatcher.reconcile_sending(sender, NOW)[0].status == "SENT"
        assert store.list_outbox()[0]["provider_message_id"] == "matching"
        assert service.send_calls == 0
    finally:
        store.close()


@pytest.mark.parametrize("result_kind", [
    "no_match", "multiple_exact", "multiple_markers", "bad_request", "not_found",
    "daily_limit", "unknown_forbidden", "malformed_forbidden", "invalid_json",
    "mixed_reasons", "unclassified",
])
def test_true_ambiguity_and_other_permanent_errors_still_stop_reconciliation(tmp_path, result_kind):
    store = SQLiteStore(tmp_path / "finite.db")
    service = FakeGmailService()
    sender = GmailReplySender(GmailAdapter(service))
    try:
        _, identifier = _seed(store)
        if result_kind == "no_match":
            service.list_results = [{"messages": []}, {"messages": []}]
        elif result_kind == "multiple_exact":
            service.list_results = [{"messages": [{"id": "one"}, {"id": "two"}]}]
        elif result_kind == "multiple_markers":
            service.list_results = [{"messages": []}, {"messages": [{"id": "one"}, {"id": "two"}]}]
            service.get_results = [{"labelIds": ["SENT"], "payload": {"headers": [
                {"name": "X-Visa-Agent-Message-ID", "value": f"<{identifier}@visa-agent.local>"},
            ]}}] * 2
        else:
            service.failure = {
                "bad_request": _http_error(400), "not_found": _http_error(404),
                "daily_limit": _http_error(403, "dailyLimitExceeded"),
                "unknown_forbidden": _http_error(403, "unclassifiedProviderReason"),
                "malformed_forbidden": _http_error(403),
                "invalid_json": _http_error(403),
                "mixed_reasons": _http_error(403),
                "unclassified": PermanentChannelError("Ambiguous provider evidence"),
            }[result_kind]
            if result_kind == "invalid_json":
                service.failure.content = PRIVATE_DETAIL  # type: ignore[attr-defined]
            elif result_kind == "mixed_reasons":
                service.failure.content = json.dumps({"error": {"errors": [  # type: ignore[attr-defined]
                    {"reason": "insufficientPermissions"}, {"reason": "dailyLimitExceeded"},
                ]}})
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        outcome = dispatcher.reconcile_sending(sender, NOW)[0]
        assert outcome.status == "AMBIGUOUS" and outcome.reason_code is None
        row = store.list_outbox()[0]
        assert row["attempt_count"] == 1 and row["provider_message_id"] is None
        assert PRIVATE_DETAIL not in str(row["last_error"])
        reads = len(service.reads)
        assert dispatcher.reconcile_sending(sender, NOW + timedelta(days=1)) == []
        assert dispatcher.dispatch_due(NOW + timedelta(days=1)) == []
        assert len(service.reads) == reads and service.send_calls == 0
    finally:
        store.close()


@pytest.mark.parametrize("state", ["paused", "resumed_new_epoch"])
def test_restored_evidence_can_be_recorded_for_a_superseded_control_epoch(tmp_path, state):
    path = tmp_path / "superseded.db"
    store = SQLiteStore(path)
    service = FakeGmailService()
    service.failure = _http_error(403, "insufficientPermissions")
    sender = GmailReplySender(GmailAdapter(service))
    try:
        case, _ = _seed(store)
        case.preparation_paused = state == "paused"
        case.preparation_control_epoch = 1 if case.preparation_paused else 2
        store.save_case(case)
        original_case = case.model_dump_json()
        original_row = store.list_outbox()[0]
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        assert dispatcher.reconcile_sending(sender, NOW)[0].status == "SENDING"
        assert store.list_outbox()[0] == original_row
        assert dispatcher.dispatch_due(NOW + timedelta(days=1)) == []
    finally:
        store.close()

    service.failure = None
    service.list_results = [{"messages": [{"id": "prior-accepted-send"}]}]
    reopened = SQLiteStore(path)
    try:
        dispatcher = OutboxDispatcher(reopened, sender, channel="gmail")
        assert dispatcher.reconcile_sending(sender, NOW)[0].status == "SENT"
        assert reopened.get_case(case.id).model_dump_json() == original_case
        row = reopened.list_outbox()[0]
        assert row["attempt_count"] == 1 and row["preparation_control_epoch"] == 0
        assert row["payload"] == original_row["payload"]
        assert row["provider_message_id"] == "prior-accepted-send"
        assert dispatcher.dispatch_due(NOW + timedelta(days=1)) == []
        assert service.send_calls == 0
    finally:
        reopened.close()


def test_query_access_error_is_typed_and_provider_detail_is_not_the_public_message():
    service = FakeGmailService()
    service.failure = _http_error(403, "insufficientPermissions")
    sender = GmailReplySender(GmailAdapter(service))
    with pytest.raises(ReconciliationAccessError, match="restore access") as raised:
        sender.find_sent_message("<synthetic@visa-agent.local>")
    assert PRIVATE_DETAIL not in str(raised.value)
    assert service.send_calls == 0


@pytest.mark.parametrize("status,reason", [
    (429, None), (503, None), (403, "userRateLimitExceeded"),
])
def test_transient_evidence_failure_does_not_request_authorization_repair(tmp_path, status, reason):
    service = FakeGmailService()
    service.failure = _http_error(status, reason)
    sender = GmailReplySender(GmailAdapter(service))
    store = SQLiteStore(tmp_path / "transient.db")
    try:
        _seed(store)
        original = store.list_outbox()[0]
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        outcome = dispatcher.reconcile_sending(sender, NOW)[0]
        assert outcome.status == "SENDING" and outcome.reason_code is None
        assert store.list_outbox()[0] == original
        assert dispatcher.dispatch_due(NOW + timedelta(days=1)) == []
        assert service.send_calls == 0
    finally:
        store.close()


def test_optional_sdk_absence_does_not_classify_exception_text_as_access_failure(monkeypatch):
    def unavailable(name):
        assert name == "google.auth.exceptions"
        raise ModuleNotFoundError("The optional Google SDK is not installed")

    monkeypatch.setattr("visa_agent.channels.gmail.import_module", unavailable)
    service = FakeGmailService()
    service.failure = RuntimeError("RefreshError invalid_grant insufficientPermissions " + PRIVATE_DETAIL)
    sender = GmailReplySender(GmailAdapter(service))
    with pytest.raises(PermanentChannelError) as raised:
        sender.find_sent_message("<synthetic@visa-agent.local>")
    assert not isinstance(raised.value, ReconciliationAccessError)
    assert PRIVATE_DETAIL not in str(raised.value)


@pytest.mark.parametrize("status,reason", [(401, None), (403, "insufficientPermissions")])
def test_send_rejection_does_not_inherit_query_only_recovery(tmp_path, status, reason):
    store = SQLiteStore(tmp_path / "rejected.db")
    service = FakeGmailService()
    service.failure = _http_error(status, reason)
    sender = GmailReplySender(GmailAdapter(service))
    try:
        _seed(store, uncertain=False)
        dispatcher = OutboxDispatcher(store, sender, channel="gmail")
        assert dispatcher.dispatch_due(NOW)[0].status == "FAILED"
        row = store.list_outbox()[0]
        assert row["attempt_count"] == 1 and row["next_attempt_at"] is None
        assert PRIVATE_DETAIL not in str(row["last_error"])
        assert dispatcher.dispatch_due(NOW + timedelta(days=1)) == []
        assert dispatcher.reconcile_sending(sender, NOW + timedelta(days=1)) == []
        assert service.send_calls == 1
    finally:
        store.close()


class RunnerGmailService(FakeGmailService):
    def __init__(self) -> None:
        super().__init__()
        self.profile_calls = 0
        self.intake_calls = 0

    def getProfile(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.profile_calls += 1
            return {"emailAddress": "service@example.test", "historyId": "100"}

        return SimpleNamespace(execute=execute)

    def list(self, **kwargs: Any) -> SimpleNamespace:
        if kwargs["q"].startswith("in:sent"):
            return super().list(**kwargs)

        def execute() -> dict[str, Any]:
            self.intake_calls += 1
            assert self.failure is None, "No intake before evidence-query access is restored"
            return {"messages": []}

        return SimpleNamespace(execute=execute)

    def history(self) -> SimpleNamespace:
        return SimpleNamespace(list=lambda **kwargs: SimpleNamespace(
            execute=lambda: {"history": [], "historyId": "101"},
        ))


def _runner_setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "gmail_query_recovery_runner", Path("scripts/gmail_sandbox.py"),
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    service = RunnerGmailService()
    model_calls = []

    def read_synthetic_secret(*args, **kwargs):
        assert service.failure is None, "No model setup before evidence-query access is restored"
        return "unused-offline-synthetic-key"

    def build_offline_model(*args, **kwargs):
        model_calls.append(True)
        return OfflineFixtureLLM()

    monkeypatch.setattr(runner, "build_gmail_service", lambda *args, **kwargs: service)
    monkeypatch.setattr(runner, "read_secret", read_synthetic_secret)
    monkeypatch.setattr(runner, "DeepSeekStructuredLLM", build_offline_model)
    args = argparse.Namespace(action="serve", sender="applicant@example.test",
        mailbox="service@example.test", subject=None, after=1, state_dir=tmp_path,
        model="offline", watch=True)
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        case, _ = _seed(store)
        original_case = case.model_dump_json()
        original_row = store.list_outbox()[0]
    finally:
        store.close()
    return runner, service, args, model_calls, original_case, original_row


@pytest.mark.parametrize("failure_kind", ["http_permission", "sdk_refresh"])
def test_actual_runner_stops_before_intake_and_recovers_sent_evidence_without_resend(
    tmp_path, monkeypatch, capsys, failure_kind,
):
    runner, service, args, model_calls, original_case, original_row = _runner_setup(tmp_path, monkeypatch)
    service.failure = (_refresh_error(monkeypatch) if failure_kind == "sdk_refresh"
                       else _http_error(403, "insufficientPermissions"))
    with pytest.raises(ReconciliationAccessError, match="restore") as raised:
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert PRIVATE_DETAIL not in "".join(traceback.format_exception(raised.value))
    captured = capsys.readouterr()
    assert PRIVATE_DETAIL not in captured.out + captured.err
    assert "Automatic dispatch" not in captured.out
    assert service.profile_calls == 1
    assert model_calls == [] and service.intake_calls == 0 and service.send_calls == 0
    assert not (tmp_path / "sync.db").exists()
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.list_outbox() == [original_row]
        assert store.get_case(original_row["case_id"]).model_dump_json() == original_case
    finally:
        store.close()

    service.failure = None
    service.list_results = [{"messages": [{"id": "accepted-before-auth-failure"}]}]
    runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        row = store.list_outbox()[0]
        assert row["status"] == "SENT" and row["provider_message_id"] == "accepted-before-auth-failure"
        assert row["attempt_count"] == original_row["attempt_count"] == 1
        assert row["payload"] == original_row["payload"]
        assert store.get_case(original_row["case_id"]).model_dump_json() == original_case
        # Reconciliation/idle cycles need no extraction client or model key.
        assert model_calls == [] and service.intake_calls == 1 and service.send_calls == 0
    finally:
        store.close()
    captured = capsys.readouterr()
    assert PRIVATE_DETAIL not in captured.out + captured.err


def test_actual_watch_heartbeat_reports_access_error_and_retries_at_original_interval(
    tmp_path, monkeypatch, capsys,
):
    runner, service, _, model_calls, _, original_row = _runner_setup(tmp_path, monkeypatch)
    service.failure = _refresh_error(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "gmail_sandbox.py", "serve", "--sender", "applicant@example.test",
        "--mailbox", "service@example.test", "--after", "1", "--state-dir", str(tmp_path),
        "--watch", "--interval", "37",
    ])
    heartbeat_snapshots = []

    class EndWatch(BaseException):
        pass

    def next_cycle(interval):
        assert interval == 37
        heartbeat = json.loads((tmp_path / "worker_status.json").read_text())
        heartbeat_snapshots.append(heartbeat)
        if len(heartbeat_snapshots) == 1:
            assert heartbeat["phase"] == "error"
            assert heartbeat["error_type"] == "ReconciliationAccessError"
            assert model_calls == [] and service.intake_calls == 0 and service.send_calls == 0
            store = SQLiteStore(tmp_path / "sandbox.db")
            try:
                assert store.list_outbox() == [original_row]
            finally:
                store.close()
            service.failure = None
            service.list_results = [{"messages": [{"id": "accepted-before-worker-recovery"}]}]
            return
        raise EndWatch

    monkeypatch.setattr(runner.time, "sleep", next_cycle)
    with pytest.raises(EndWatch):
        runner.main()
    assert [item["phase"] for item in heartbeat_snapshots] == ["error", "idle"]
    assert PRIVATE_DETAIL not in json.dumps(heartbeat_snapshots)
    captured = capsys.readouterr()
    assert "Worker iteration failed: ReconciliationAccessError" in captured.out
    assert PRIVATE_DETAIL not in captured.out + captured.err
    assert model_calls == [] and service.send_calls == 0
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        row = store.list_outbox()[0]
        assert row["status"] == "SENT" and row["provider_message_id"] == "accepted-before-worker-recovery"
        assert row["attempt_count"] == 1
    finally:
        store.close()
