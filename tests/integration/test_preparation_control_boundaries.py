"""Synthetic local persistence/release boundaries; no model, provider, or live data."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from visa_agent import web
from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.config import Settings
from visa_agent.delivery import pack
from visa_agent.demo import DEMO_EVALUATION_DATE, run_demo
from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import (
    queue_finalized_revision,
    queue_review_retry,
    review_fingerprint,
)

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
MESSAGE_TYPES = (
    "blocked", "awaiting_profile_confirmation", "awaiting_confirmation", "ready",
    "held_update_received",
)


class CaptureSender:
    def __init__(self, found: str | None = None) -> None:
        self.requests: list[ReplyRequest] = []
        self.lookups: list[str] = []
        self.found = found

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return "synthetic-provider-accepted"

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        self.lookups.append(rfc_message_id)
        return self.found


@pytest.fixture
def store(tmp_path: Path):
    value = SQLiteStore(tmp_path / "synthetic-boundaries.db")
    try:
        yield value
    finally:
        value.close()


def _case(*, epoch: int = 0, paused: bool = False) -> Case:
    return Case(
        id="synthetic-preparation-case", external_thread_id="synthetic-preparation-thread",
        applicant_contact="applicant@example.test", primary_channel="gmail",
        policy_version="synthetic-boundary-only", preparation_paused=paused,
        preparation_control_epoch=epoch,
    )


def _event(case: Case, *, identifier: str = "synthetic-event") -> InboundEvent:
    return InboundEvent(
        id=identifier, external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, channel="gmail", received_at=NOW,
        subject="Preparation update", body="Synthetic local control fixture.",
    )


def _queue(store: SQLiteStore, case: Case, kind: str, *, status: str = "PENDING") -> str:
    store.commit_event(case, _event(case), kind, "Preparation is paused. Your update is retained.")
    row = store.list_outbox()[0]
    with store.connection:
        store.connection.execute("UPDATE outbox SET status=? WHERE id=?", (status, row["id"]))
    return str(row["id"])


@pytest.fixture
def complete_case(tmp_path: Path):
    settings = Settings(
        database_path=tmp_path / "synthetic-demo.db", output_dir=tmp_path / "synthetic-output",
        policy_path=POLICY_PATH,
    )
    result = run_demo(settings, reset=True)
    value = SQLiteStore(settings.database_path)
    try:
        yield value, value.get_case(result.case.id), settings
    finally:
        value.close()


def test_legacy_case_and_outbox_migrate_to_active_epoch_zero(tmp_path: Path) -> None:
    path = tmp_path / "legacy-synthetic.db"
    original = _case().model_dump(mode="json")
    for field in ("preparation_paused", "preparation_control_epoch", "latest_preparation_action"):
        original.pop(field, None)
    with sqlite3.connect(path) as legacy:
        legacy.executescript(
            "CREATE TABLE cases (id TEXT PRIMARY KEY, thread_id TEXT UNIQUE NOT NULL, "
            "snapshot_json TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "CREATE TABLE outbox (id TEXT PRIMARY KEY, case_id TEXT, event_id TEXT, "
            "message_type TEXT, payload TEXT, created_at TEXT);"
        )
        legacy.execute("INSERT INTO cases VALUES (?,?,?,?)", (
            original["id"], original["external_thread_id"], json.dumps(original), NOW.isoformat(),
        ))
        legacy.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?)", (
            "legacy-out", original["id"], "legacy-event", "blocked", "Synthetic prior reply", NOW.isoformat(),
        ))
    for _ in range(2):
        reopened = SQLiteStore(path)
        try:
            current = reopened.get_case(str(original["id"]))
            assert current is not None and current.preparation_paused is False
            assert current.preparation_control_epoch == 0
            assert reopened.list_outbox()[0]["preparation_control_epoch"] == 0
        finally:
            reopened.close()


def test_commit_export_claim_and_reconciliation_rows_keep_epoch(store: SQLiteStore) -> None:
    case = _case(epoch=7, paused=True)
    _queue(store, case, "blocked")
    assert store.get_case(case.id) == case
    assert store.list_outbox()[0]["preparation_control_epoch"] == 7
    assert store.export_case_data(case.id)["outbound_messages"][0]["preparation_control_epoch"] == 7
    assert store.claim_pending_outbox(NOW)[0]["preparation_control_epoch"] == 7
    assert store.list_sending_outbox()[0]["preparation_control_epoch"] == 7


@pytest.mark.parametrize("kind", MESSAGE_TYPES)
@pytest.mark.parametrize("status", ["PENDING", "RETRY"])
@pytest.mark.parametrize("paused,epoch", [(True, 1), (False, 2)])
def test_old_epoch_messages_never_send_even_after_resume(
    store: SQLiteStore, kind: str, status: str, paused: bool, epoch: int,
) -> None:
    case = _case()
    _queue(store, case, kind, status=status)
    case.preparation_paused = paused
    case.preparation_control_epoch = epoch
    store.save_case(case)
    sender = CaptureSender()
    assert OutboxDispatcher(store, sender).dispatch_due(NOW)[0].status == "FAILED"
    assert sender.requests == []
    row = store.list_outbox()[0]
    assert "preparation control epoch" in row["last_error"]
    assert row["preparation_control_epoch"] == 0
    assert store.get_case(case.id) == case


@pytest.mark.parametrize("kind", [*MESSAGE_TYPES, "unknown_future_instruction"])
def test_current_paused_epoch_allows_only_safe_receipt_types(
    store: SQLiteStore, kind: str,
) -> None:
    case = _case(epoch=3, paused=True)
    _queue(store, case, kind)
    sender = CaptureSender()
    outcome = OutboxDispatcher(store, sender).dispatch_due(NOW)[0]
    allowed = kind in {"blocked", "held_update_received"}
    assert outcome.status == ("SENT" if allowed else "FAILED")
    assert len(sender.requests) == int(allowed)
    if allowed:
        assert sender.requests[0].attachment is None
        assert sender.requests[0].body == "Preparation is paused. Your update is retained."
    else:
        assert "Preparation is paused" in store.list_outbox()[0]["last_error"]


@pytest.mark.parametrize("found", ["provider-accepted-before-pause", None])
def test_obsolete_uncertain_send_is_reconciled_without_resending_or_erasing_evidence(
    store: SQLiteStore, found: str | None,
) -> None:
    case = _case()
    identifier = _queue(store, case, "ready", status="SENDING")
    case.preparation_paused = True
    case.preparation_control_epoch = 1
    store.save_case(case)
    sender = CaptureSender(found)
    dispatcher = OutboxDispatcher(store, sender)
    outcome = dispatcher.reconcile_sending(sender, NOW)[0]
    assert outcome.status == ("SENT" if found else "AMBIGUOUS")
    assert sender.lookups == [f"<{identifier}@visa-agent.local>"]
    assert sender.requests == [] and dispatcher.dispatch_due(NOW) == []
    row = store.list_outbox()[0]
    assert row["provider_message_id"] == found
    assert row["preparation_control_epoch"] == 0
    if not found:
        with pytest.raises(ValueError, match="paused"):
            store.retry_ambiguous_outbox(identifier, NOW)
        case.preparation_paused = False
        case.preparation_control_epoch = 2
        store.save_case(case)
        with pytest.raises(ValueError, match="epoch"):
            store.retry_ambiguous_outbox(identifier, NOW)
        assert store.list_outbox()[0]["status"] == "AMBIGUOUS"


@pytest.mark.parametrize("paused", [False, True])
def test_manual_retry_requires_current_active_epoch(store: SQLiteStore, paused: bool) -> None:
    case = _case(epoch=4, paused=paused)
    identifier = _queue(store, case, "blocked", status="AMBIGUOUS")
    if paused:
        with pytest.raises(ValueError, match="paused"):
            store.retry_ambiguous_outbox(identifier, NOW)
        assert store.list_outbox()[0]["status"] == "AMBIGUOUS"
    else:
        store.retry_ambiguous_outbox(identifier, NOW)
        sender = CaptureSender()
        assert OutboxDispatcher(store, sender).dispatch_due(NOW)[0].status == "SENT"
        assert len(sender.requests) == 1


def test_paused_gate_blocks_cached_pack_and_web_download_without_deleting_it(
    complete_case, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, case, settings = complete_case
    policy = load_policy(POLICY_PATH)
    assert evaluate_gate(case, policy, DEMO_EVALUATION_DATE).allowed
    archive = Path(case.delivery_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    case.preparation_paused = True
    case.preparation_control_epoch = 1
    store.save_case(case)
    gate = evaluate_gate(case, policy, DEMO_EVALUATION_DATE)
    assert not gate.allowed and gate.checks["preparation_active"] is False
    assert [key for key, passed in gate.checks.items() if not passed] == ["preparation_active"]
    result, reasons = pack.generate_pack(case, policy, store, settings.output_dir, DEMO_EVALUATION_DATE)
    assert result is None and any("paused" in reason for reason in reasons)
    monkeypatch.setattr(web, "settings", settings)
    with pytest.raises(HTTPException) as error:
        web.get_pack(case.id)
    assert error.value.status_code == 409
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    assert store.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1


@pytest.mark.parametrize("paused,epoch", [(True, 1), (False, 2)])
@pytest.mark.parametrize("recover_cached", [False, True])
def test_stale_case_cannot_generate_or_recover_pack_after_control_transition(
    complete_case, tmp_path: Path, paused: bool, epoch: int, recover_cached: bool,
) -> None:
    store, stale, _ = complete_case
    current = stale.model_copy(deep=True)
    current.preparation_paused = paused
    current.preparation_control_epoch = epoch
    current.profile_confirmed = False
    current.final_summary_confirmed = False
    store.save_case(current)
    if not recover_cached:
        stale.delivery_path = None
    output = tmp_path / "must-not-create"
    result, reasons = pack.generate_pack(stale, load_policy(POLICY_PATH), store, output, DEMO_EVALUATION_DATE)
    assert result is None and reasons
    assert not output.exists()
    assert store.get_case(stale.id) == current


def test_paused_case_cannot_register_a_new_delivery(store: SQLiteStore) -> None:
    case = _case(epoch=1, paused=True)
    store.save_case(case)
    with pytest.raises(ValueError, match="paused"):
        store.save_delivery(case.id, "synthetic-not-created.zip", "a" * 64)
    assert store.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
    assert store.get_case(case.id) == case


def test_nested_delivery_and_case_writes_roll_back_as_one_unit(store: SQLiteStore) -> None:
    case = _case()
    store.save_case(case)
    changed = case.model_copy(update={"delivery_path": "synthetic-not-created.zip"})
    with pytest.raises(RuntimeError, match="synthetic failure"), store.atomic_write():
        store.save_delivery(case.id, changed.delivery_path, "a" * 64)
        store.save_case(changed)
        raise RuntimeError("synthetic failure after both writes")
    assert store.get_case(case.id) == case
    assert store.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
    assert not store.connection.in_transaction


def test_pack_serializes_pause_against_materialization_and_both_persistence_writes(
    complete_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, case, _ = complete_case
    case.delivery_path = None
    case.status = CaseStatus.DRAFT
    case.stage = WorkflowStage.FINAL_CONFIRMATION
    store.save_case(case)
    locked_at: list[str] = []
    original_zip = pack._write_zip
    original_save = store.save_case
    contender = sqlite3.connect(store.path, timeout=0)

    def assert_pause_write_waits(point: str) -> None:
        paused = case.model_copy(update={"preparation_paused": True, "preparation_control_epoch": 1})
        with pytest.raises(sqlite3.OperationalError, match="locked"), contender:
            contender.execute("UPDATE cases SET snapshot_json=? WHERE id=?", (paused.model_dump_json(), case.id))
        locked_at.append(point)

    def checked_zip(source: Path, destination: Path) -> None:
        assert_pause_write_waits("materialization")
        original_zip(source, destination)

    def checked_save(prepared: Case) -> None:
        # This runs after save_delivery: its nested context must not release the lock.
        assert_pause_write_waits("after_registration_before_snapshot")
        original_save(prepared)

    monkeypatch.setattr(pack, "_write_zip", checked_zip)
    monkeypatch.setattr(store, "save_case", checked_save)
    try:
        generated, reasons = pack.generate_pack(
            case, load_policy(POLICY_PATH), store, tmp_path / "regenerated", DEMO_EVALUATION_DATE,
        )
        assert generated is not None and reasons == []
        assert locked_at == ["materialization", "after_registration_before_snapshot"]
        persisted = store.get_case(case.id)
        assert persisted.delivery_path == str(generated)
        assert store.connection.execute("SELECT path FROM deliveries WHERE case_id=?", (case.id,)).fetchone()[0] == str(generated)
        # A pause accepted after the generation transaction still disables access.
        persisted.preparation_paused = True
        persisted.preparation_control_epoch = 1
        original_save(persisted)
        assert not evaluate_gate(persisted, load_policy(POLICY_PATH), DEMO_EVALUATION_DATE).allowed
    finally:
        contender.close()


@pytest.mark.parametrize("write_event", [False, True])
@pytest.mark.parametrize("stale_epoch", [0, 1])
def test_stale_or_same_epoch_snapshot_cannot_unpause_persistent_case(
    store: SQLiteStore, write_event: bool, stale_epoch: int,
) -> None:
    current = _case(epoch=1, paused=True)
    store.save_case(current)
    stale = current.model_copy(update={
        "preparation_paused": False, "preparation_control_epoch": stale_epoch,
    })
    with pytest.raises(ValueError, match="preparation control epoch"):
        if write_event:
            store.commit_event(stale, _event(stale), "awaiting_confirmation", "Never send stale consent")
        else:
            store.save_case(stale)
    assert store.get_case(current.id) == current
    assert store.list_outbox() == []
    assert not store.event_processed("synthetic-event")


@pytest.mark.parametrize("finalized", [False, True])
def test_operator_retry_or_revision_is_not_customer_resume(
    store: SQLiteStore, tmp_path: Path, finalized: bool,
) -> None:
    case = _case()
    case.status = CaseStatus.READY_FOR_HUMAN_REVIEW if finalized else CaseStatus.HUMAN_REVIEW_REQUIRED
    case.stage = WorkflowStage(case.status.value)
    case.profile_confirmed = case.final_summary_confirmed = True
    case.confirmation_kind = "final"
    case.confirmation_fingerprint = "synthetic-old-consent"
    case.confirmation_request_event_id = "synthetic-old-request"
    if finalized:
        archive = tmp_path / "synthetic-existing-archive.zip"
        archive.write_bytes(b"Synthetic authorization placeholder, not application evidence.")
        case.delivery_path = str(archive)
        store.save_case(case)
        store.save_delivery(case.id, str(archive), hashlib.sha256(archive.read_bytes()).hexdigest())
    case.preparation_paused = True
    case.preparation_control_epoch = 9
    store.save_case(case)
    event = _event(case)
    reason_code = "FINALIZED_CASE_NEW_EVENT" if finalized else "HUMAN_REVIEW_CASE_NEW_EVENT"
    store.record_rejected_event(
        event_id=event.id, case_id=case.id, thread_id=case.external_thread_id,
        reason_code=reason_code, detail="Synthetic retained correction", held_event=event,
    )
    action = queue_finalized_revision if finalized else queue_review_retry
    identifier = action(
        store, case_id=case.id, held_event_id=event.id,
        expected_fingerprint=review_fingerprint(case), actor="Synthetic reviewer",
        reason="Review this retained correction through the normal validation path.",
    )
    current = store.get_case(case.id)
    assert current.preparation_paused and current.preparation_control_epoch == 9
    assert current.status == CaseStatus.DRAFT and current.stage == WorkflowStage.INTAKE
    assert not current.profile_confirmed and not current.final_summary_confirmed
    assert current.confirmation_request_event_id is None and current.confirmation_fingerprint is None
    assert current.profile == case.profile and current.documents == case.documents
    assert len(store.list_inbound_queue()) == 1 and not store.event_processed(identifier)
    assert store.list_outbox() == []
