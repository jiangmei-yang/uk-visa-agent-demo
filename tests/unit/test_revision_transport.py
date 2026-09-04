from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.delivery import pack
from visa_agent.domain.models import Case, CaseProfile, CaseStatus, GateResult, WorkflowStage
from visa_agent.domain.policy import load_policy
from visa_agent.storage.sqlite import SQLiteStore


class CaptureSender:
    def __init__(self, found: str | None = None) -> None:
        self.requests: list[ReplyRequest] = []
        self.lookups: list[str] = []
        self.found = found

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return "current-revision-accepted"

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        self.lookups.append(rfc_message_id)
        return self.found


def revision_store(tmp_path: Path, *, registry_revision: int = 2) -> tuple[SQLiteStore, Case]:
    """Seed transport state only; these bytes are not a generated/validated visa pack."""
    store = SQLiteStore(tmp_path / "transport.db")
    pack_path = tmp_path / "transport-only_revision-2.zip"
    pack_path.write_bytes(b"transport fixture bytes for current revision")
    case = Case(
        id="case-revision-transport",
        external_thread_id="thread-revision-transport",
        applicant_contact="applicant@example.test",
        primary_channel="gmail",
        policy_version="fixture",
        delivery_revision=2,
        delivery_path=str(pack_path),
        status=CaseStatus.READY_FOR_HUMAN_REVIEW,
        stage=WorkflowStage.READY_FOR_HUMAN_REVIEW,
        # Simulated transport precondition, not real applicant consent or identity evidence.
        final_summary_confirmed=True,
    )
    store.save_case(case)
    with store.connection:
        store.connection.execute(
            "INSERT INTO deliveries(id, case_id, path, sha256, case_revision) VALUES (?, ?, ?, ?, ?)",
            ("delivery-transport", case.id, str(pack_path),
             hashlib.sha256(pack_path.read_bytes()).hexdigest(), registry_revision),
        )
    return store, case


def queue_reply(
    store: SQLiteStore,
    case: Case,
    *,
    revision: int,
    status: str = "PENDING",
    message_type: str = "ready",
    channel: str = "gmail",
) -> None:
    with store.connection:
        store.connection.execute(
            "INSERT INTO outbox(id, case_id, event_id, message_type, payload, channel, "
            "status, case_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("out-transport", case.id, "event-transport", message_type,
             "A reply tied to its original case revision.", channel, status, revision),
        )


@pytest.mark.parametrize("status", ["PENDING", "RETRY"])
@pytest.mark.parametrize("message_type", ["ready", "blocked", "final_confirmation"])
def test_superseded_reply_cannot_be_sent_with_current_pack(
    tmp_path: Path, status: str, message_type: str,
) -> None:
    store, case = revision_store(tmp_path)
    sender = CaptureSender()
    try:
        queue_reply(store, case, revision=1, status=status, message_type=message_type)
        dispatcher = OutboxDispatcher(store, sender)
        result = dispatcher.dispatch_due(datetime(2026, 9, 4, 10, tzinfo=UTC))
        assert [item.status for item in result] == ["FAILED"]
        assert sender.requests == []
        row = store.list_outbox()[0]
        assert row["case_revision"] == 1
        assert "superseded delivery revision" in row["last_error"]
        assert dispatcher.dispatch_due(datetime(2026, 9, 4, 11, tzinfo=UTC)) == []
    finally:
        store.close()


@pytest.mark.parametrize("channel", ["gmail", "whatsapp_twilio"])
def test_registered_archive_must_match_current_revision(tmp_path: Path, channel: str) -> None:
    store, case = revision_store(tmp_path, registry_revision=1)
    sender = CaptureSender()
    try:
        queue_reply(store, case, revision=2, channel=channel)
        result = OutboxDispatcher(store, sender).dispatch_due(datetime(2026, 9, 4, 10, tzinfo=UTC))
        assert [item.status for item in result] == ["FAILED"]
        assert sender.requests == []
        assert "registered delivery" in store.list_outbox()[0]["last_error"]
    finally:
        store.close()


def test_matching_revision_sends_exact_registered_bytes_once(tmp_path: Path) -> None:
    store, case = revision_store(tmp_path)
    sender = CaptureSender()
    try:
        queue_reply(store, case, revision=2)
        dispatcher = OutboxDispatcher(store, sender)
        now = datetime(2026, 9, 4, 10, tzinfo=UTC)
        assert [item.status for item in dispatcher.dispatch_due(now)] == ["SENT"]
        assert sender.requests[0].attachment == (
            "transport-only_revision-2.zip", b"transport fixture bytes for current revision",
        )
        assert dispatcher.dispatch_due(now) == []
        assert len(sender.requests) == 1
    finally:
        store.close()


@pytest.mark.parametrize("found", [None, "old-revision-accepted-before-interruption"])
def test_old_sending_revision_is_reconciled_without_resend_or_current_case_transition(
    tmp_path: Path, found: str | None,
) -> None:
    store, case = revision_store(tmp_path)
    sender = CaptureSender(found)
    try:
        queue_reply(store, case, revision=1, status="SENDING")
        dispatcher = OutboxDispatcher(store, sender)
        now = datetime(2026, 9, 4, 10, tzinfo=UTC)
        result = dispatcher.reconcile_sending(sender, now)
        expected_status = "SENT" if found else "AMBIGUOUS"
        assert [item.status for item in result] == [expected_status]
        assert sender.lookups == ["<out-transport@visa-agent.local>"]
        assert sender.requests == []
        row = store.list_outbox()[0]
        assert row["case_revision"] == 1
        assert row["provider_message_id"] == found
        assert dispatcher.dispatch_due(now) == []
        assert dispatcher.reconcile_sending(sender, now) == []
        assert store.get_case(case.id) == case
    finally:
        store.close()


def test_registration_refusal_does_not_mutate_case_or_persist_ready_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate persistence ordering; no PDF is authored and no real gate is claimed."""
    store = SQLiteStore(tmp_path / "registration.db")
    policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    case = Case(
        id="case-registration-order",
        external_thread_id="thread-registration-order",
        applicant_contact="applicant@example.test",
        policy_version=policy.version,
        delivery_revision=2,
        stage=WorkflowStage.INTAKE,
        profile=CaseProfile(estimated_trip_cost_gbp=2000),
    )
    store.save_case(case)
    before = case.model_copy(deep=True)
    # The workflow integration tests exercise real gates. These stubs exercise only
    # failure ordering after rendering has succeeded, without manufacturing valid PDFs.
    monkeypatch.setattr(pack, "evaluate_gate", lambda *_: GateResult(allowed=True, checks={}, reasons=[]))
    monkeypatch.setattr(pack, "_pdf", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pack, "_document_index_pdf", lambda *_args, **_kwargs: None)

    def refuse_registration(case_id: str, path: str, digest: str, *, case_revision: int) -> None:
        assert case_id == case.id and case_revision == 2
        assert Path(path).name.endswith("_revision-2.zip") and len(digest) == 64
        assert case == before and store.get_case(case.id) == before
        raise ValueError("Revision registration refused")

    monkeypatch.setattr(store, "save_delivery", refuse_registration)
    try:
        with pytest.raises(ValueError, match="registration refused"):
            pack.generate_pack(case, policy, store, tmp_path / "output", date(2026, 9, 4))
        assert case == before and store.get_case(case.id) == before
        assert not store.connection.execute("SELECT 1 FROM deliveries").fetchone()
    finally:
        store.close()
