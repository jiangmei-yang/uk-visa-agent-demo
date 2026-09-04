"""Local synthetic revision tests; no real Gmail, identity validation, or model calls.

The authorization tests use a labelled placeholder archive, not applicant evidence.
The end-to-end test uses the existing explicitly synthetic fixture reader/documents.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.config import Settings
from visa_agent.delivery.pack import generate_pack
from visa_agent.demo import run_demo
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    InboundEvent,
    WorkflowStage,
)
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import queue_finalized_revision, review_fingerprint
from visa_agent.workflow.service import WorkflowService

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
POLICY = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


class CaptureSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return f"local-capture-{len(self.requests)}"


class CaptureAdapter(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"local-summary-capture-{len(self.calls)}"}


def placeholder_archive(path: Path) -> str:
    with ZipFile(path, "w") as archive:
        archive.writestr("SYNTHETIC.txt", "Synthetic authorization fixture. Not a visa application.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_finalized(tmp_path: Path) -> tuple[SQLiteStore, Case, InboundEvent]:
    store = SQLiteStore(tmp_path / "db")
    old_path = tmp_path / "synthetic-old.zip"
    digest = placeholder_archive(old_path)
    case = Case(
        id="synthetic-revision", external_thread_id="synthetic-thread",
        applicant_contact="Example Applicant <applicant@example.test>", primary_channel="gmail",
        policy_version="synthetic-control-only", status=CaseStatus.READY_FOR_HUMAN_REVIEW,
        stage=WorkflowStage.READY_FOR_HUMAN_REVIEW, delivery_path=str(old_path),
        profile_confirmed=True, final_summary_confirmed=True,
        confirmation_kind="final", confirmation_fingerprint="prior-summary",
        confirmation_request_event_id="prior-confirmation", last_inbound_received_at=NOW,
        last_requested_fields=["date_of_birth"],
    )
    case.profile.full_name = "Example Applicant"
    case.documents.append(Document(
        id="placeholder-source", filename="synthetic-old.zip", kind="other_supporting_document",
        sha256=digest, mime_type="application/zip", status=DocumentStatus.RECEIVED,
        source_event_id="original", path=str(old_path),
    ))
    case.evidence.append(Evidence(
        id="placeholder-fact", fact_key="full_name", value="Example Applicant",
        source_event_id="original", source_excerpt="Example Applicant",
        extraction_method="synthetic-test", model_version="synthetic-test", confidence=1,
        confirmed=True,
    ))
    store.save_case(case)
    store.save_delivery(case.id, str(old_path), digest)
    held = InboundEvent(
        id="held-correction", external_thread_id=case.external_thread_id,
        sender="applicant@example.test", channel="gmail", received_at=NOW + timedelta(minutes=1),
        subject="My travel details changed", body="Please change the budget to GBP 2,600.",
        rfc_message_id="<held-correction@example.test>",
    )
    store.record_rejected_event(
        event_id=held.id, case_id=case.id, thread_id=case.external_thread_id,
        reason_code="FINALIZED_CASE_NEW_EVENT", detail="Finalized update held", held_event=held,
    )
    return store, case, held


def authorize(store: SQLiteStore, case: Case, held: InboundEvent, **overrides: object) -> str:
    values = dict(
        case_id=case.id, held_event_id=held.id, expected_fingerprint=review_fingerprint(case),
        actor="Local reviewer", reason="Applicant requested a budget correction; run all checks again.",
    )
    values.update(overrides)
    return queue_finalized_revision(store, **values)


def add_held(store: SQLiteStore, case: Case, held: InboundEvent, *, identifier: str,
             received_at: datetime) -> InboundEvent:
    event = held.model_copy(update={"id": identifier, "received_at": received_at,
        "body": f"A separately retained synthetic correction: {identifier}."})
    store.record_rejected_event(event_id=event.id, case_id=case.id, thread_id=case.external_thread_id,
        reason_code="FINALIZED_CASE_NEW_EVENT", detail="Synthetic batch update held", held_event=event)
    return event


def add_outbox(store: SQLiteStore, case: Case, *, status: str, identifier: str = "old-ready") -> str:
    event = InboundEvent(
        id=identifier, external_thread_id=case.external_thread_id, sender=case.applicant_contact,
        channel="gmail", received_at=NOW, subject="Synthetic original delivery", body="Original",
    )
    store.commit_event(case, event, "ready", "Original synthetic pack")
    with store.connection:
        store.connection.execute(
            "UPDATE outbox SET status=?, provider_message_id=? WHERE event_id=?",
            (status, "original-accepted" if status == "SENT" else None, identifier),
        )
    return f"out-{identifier}-ready"


@pytest.mark.parametrize("status", [CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION])
def test_authorized_revision_is_durable_and_does_not_edit_applicant_evidence(tmp_path, status):
    store, case, held = setup_finalized(tmp_path)
    case.status = status
    store.save_case(case)
    pending_id = add_outbox(store, case, status="PENDING")
    retry_id = add_outbox(store, case, status="RETRY", identifier="old-retry")
    sent_id = add_outbox(store, case, status="SENT", identifier="old-sent")
    old_bytes = Path(case.delivery_path).read_bytes()
    try:
        queued_id = authorize(store, case, held)
        resumed = store.get_case(case.id)
        assert resumed.delivery_revision == 2 and resumed.delivery_path is None
        assert resumed.status == CaseStatus.DRAFT and resumed.stage == WorkflowStage.INTAKE
        assert resumed.profile == case.profile and resumed.documents == case.documents
        assert resumed.evidence == case.evidence
        assert not resumed.profile_confirmed and not resumed.final_summary_confirmed
        assert resumed.confirmation_kind is None and resumed.confirmation_fingerprint is None
        assert resumed.confirmation_request_event_id is None
        assert resumed.last_requested_fields == []
        assert store.event_processed(held.id) and not store.event_processed(queued_id)
        queued = store.list_inbound_queue()
        assert len(queued) == 1 and queued[0]["channel"] == "gmail_review"
        retry = InboundEvent.model_validate_json(queued[0]["payload_json"])
        assert retry.body == held.body and retry.attachment_paths == held.attachment_paths
        assert retry.sender == held.sender and retry.external_thread_id == held.external_thread_id
        assert retry.rfc_message_id == held.rfc_message_id and retry.channel == "gmail"
        assert retry.requested_fields == [] and retry.known_profile == {}
        rows = {row["id"]: row for row in store.list_outbox()}
        assert rows[pending_id]["status"] == rows[retry_id]["status"] == "FAILED"
        assert rows[sent_id]["status"] == "SENT"
        assert rows[sent_id]["provider_message_id"] == "original-accepted"
        assert all(row["case_revision"] == 1 for row in rows.values())
        exported = store.export_case_data(case.id)
        audit = exported["review_actions"]
        assert len(audit) == 1 and audit[0]["action_kind"] == "revision"
        assert audit[0]["actor"] == "Local reviewer" and "budget correction" in audit[0]["reason"]
        assert audit[0]["held_event_id"] == held.id and audit[0]["retry_event_id"] == queued_id
        assert Case.model_validate_json(audit[0]["before_json"]) == case
        assert Case.model_validate_json(audit[0]["after_json"]) == resumed
        versions = exported["delivery_versions"]
        assert len(versions) == 1 and versions[0]["path"] == case.delivery_path
        assert versions[0]["case_revision"] == 1
        assert versions[0]["sha256"] == hashlib.sha256(old_bytes).hexdigest()
        assert Path(case.delivery_path).read_bytes() == old_bytes
        store.close()
        store = SQLiteStore(tmp_path / "db")
        assert store.get_case(case.id) == resumed
        assert len(store.list_inbound_queue()) == 1
        # Both replaying the original authorization and re-inspecting the new draft
        # must not allocate another revision or another retry event.
        for inspected in (case, resumed):
            with pytest.raises(ValueError):
                authorize(store, inspected, held)
        assert store.get_case(case.id).delivery_revision == 2
        assert len(store.export_case_data(case.id)["review_actions"]) == 1
        assert len(store.list_inbound_queue()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("overrides", [
    {"actor": ""}, {"reason": "ok"}, {"expected_fingerprint": "stale"},
    {"case_id": "another-case"}, {"held_event_id": "missing-update"},
])
def test_invalid_revision_authorization_changes_nothing(tmp_path, overrides):
    store, case, held = setup_finalized(tmp_path)
    before = store.export_case_data(case.id)
    try:
        with pytest.raises(ValueError):
            authorize(store, case, held, **overrides)
        assert store.export_case_data(case.id) == before
        assert not store.list_inbound_queue()
    finally:
        store.close()


@pytest.mark.parametrize("change", [
    "draft", "human_review", "whatsapp_case", "wrong_sender", "wrong_thread", "wrong_channel",
    "wrong_hold_reason", "older_update", "missing_registry", "different_registry_path", "different_registry_revision",
    "wrong_hash", "missing_archive", "no_delivery_path",
])
def test_revision_scope_and_archive_guards_reject_without_mutation(tmp_path, change):
    store, case, held = setup_finalized(tmp_path)
    if change == "draft":
        case.status = CaseStatus.DRAFT
    elif change == "human_review":
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    elif change == "whatsapp_case":
        case.primary_channel = "whatsapp_twilio"
    elif change == "no_delivery_path":
        case.delivery_path = None
    elif change == "wrong_sender":
        held.sender = "outsider@example.test"
    elif change == "wrong_thread":
        held.external_thread_id = "different-thread"
    elif change == "wrong_channel":
        held.channel = "whatsapp_twilio"
    elif change == "older_update":
        held.received_at = NOW - timedelta(minutes=1)
    elif change == "missing_archive":
        Path(case.delivery_path).unlink()
    with store.connection:
        if change == "wrong_hold_reason":
            store.connection.execute("UPDATE held_inbound_events SET reason_code='HUMAN_REVIEW_CASE_NEW_EVENT'")
        elif change == "missing_registry":
            store.connection.execute("DELETE FROM deliveries")
        elif change == "different_registry_path":
            store.connection.execute("UPDATE deliveries SET path='different.zip'")
        elif change == "different_registry_revision":
            store.connection.execute("UPDATE deliveries SET case_revision=2")
        elif change == "wrong_hash":
            store.connection.execute("UPDATE deliveries SET sha256='not-the-archive-hash'")
        store.connection.execute(
            "UPDATE held_inbound_events SET payload_json=? WHERE id=?", (held.model_dump_json(), held.id),
        )
    store.save_case(case)
    before = store.export_case_data(case.id)
    try:
        with pytest.raises(ValueError):
            authorize(store, case, held)
        assert store.export_case_data(case.id) == before
        assert not store.list_inbound_queue()
    finally:
        store.close()


@pytest.mark.parametrize("status", ["SENDING", "AMBIGUOUS"])
def test_uncertain_original_send_prevents_revision(tmp_path, status):
    store, case, held = setup_finalized(tmp_path)
    add_outbox(store, case, status=status)
    before = store.export_case_data(case.id)
    try:
        with pytest.raises(ValueError):
            authorize(store, case, held)
        assert store.export_case_data(case.id) == before
        assert store.list_outbox()[0]["status"] == status
        assert not store.list_inbound_queue()
    finally:
        store.close()


def test_revision_queue_failure_rolls_back_archive_audit_case_and_outbox(tmp_path):
    store, case, held = setup_finalized(tmp_path)
    add_outbox(store, case, status="PENDING")
    before = store.export_case_data(case.id)
    old_bytes = Path(case.delivery_path).read_bytes()
    store.connection.execute("""CREATE TRIGGER fail_revision_queue BEFORE INSERT ON inbound_queue
        BEGIN SELECT RAISE(ABORT, 'injected revision queue failure'); END""")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            authorize(store, case, held)
        assert store.export_case_data(case.id) == before
        assert not store.list_inbound_queue()
        assert Path(case.delivery_path).read_bytes() == old_bytes
    finally:
        store.close()


def test_old_ready_outbox_cannot_attach_new_revision_even_if_requeued(tmp_path):
    store, case, held = setup_finalized(tmp_path)
    old_id = add_outbox(store, case, status="PENDING")
    retry_id = authorize(store, case, held)
    revised = store.get_case(case.id)
    new_path = tmp_path / "synthetic-revised.zip"
    digest = placeholder_archive(new_path)
    revised.delivery_path = str(new_path)
    revised.status = CaseStatus.READY_FOR_HUMAN_REVIEW
    store.save_case(revised)
    # This transport-only test seeds an already-processed authorized retry. The
    # separate synthetic workflow test below exercises the actual worker/gates.
    with store.connection:
        store.connection.execute(
            "INSERT INTO processed_events(event_id, case_id) VALUES (?, ?)", (retry_id, case.id),
        )
    store.save_delivery(case.id, str(new_path), digest, case_revision=revised.delivery_revision)
    # Simulate a stale queue restoration after an authorized revision. The dispatcher
    # must bind a ready message to its original revision, not merely the case ID.
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='PENDING' WHERE id=?", (old_id,))
    sender = CaptureSender()
    try:
        assert not store.has_unreviewed_held_updates(case.id)
        result = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(NOW)
        assert result[0].status == "FAILED" and sender.requests == []
        assert store.list_outbox()[0]["case_revision"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("choose_later", [False, True])
def test_multiple_held_updates_require_explicit_whole_batch_authorization(tmp_path, choose_later):
    store, case, held = setup_finalized(tmp_path)
    later = add_held(store, case, held, identifier="later-update", received_at=NOW + timedelta(minutes=2))
    before = store.export_case_data(case.id)
    try:
        with pytest.raises(ValueError):
            authorize(store, case, later if choose_later else held, include_held_updates=choose_later)
        assert store.export_case_data(case.id) == before
        assert store.list_inbound_queue() == []
    finally:
        store.close()


def test_authorized_batch_keeps_one_revision_and_processes_held_updates_chronologically(tmp_path):
    store, case, held = setup_finalized(tmp_path)
    # Insert the tied later events backwards. Processing must use (received_at, id),
    # not insertion order or the hashes in their generated retry identifiers.
    later_z = add_held(store, case, held, identifier="z-later", received_at=NOW + timedelta(minutes=2))
    later_a = add_held(store, case, held, identifier="a-later", received_at=NOW + timedelta(minutes=2))
    try:
        selected_id = authorize(store, case, held, include_held_updates=True)
        revised = store.get_case(case.id)
        assert revised.delivery_revision == 2 and revised.delivery_path is None
        assert revised.profile == case.profile and revised.documents == case.documents
        exported = store.export_case_data(case.id)
        assert len(exported["delivery_versions"]) == 1
        audits = {row["held_event_id"]: row for row in exported["review_actions"]}
        assert set(audits) == {held.id, later_a.id, later_z.id}
        assert audits[held.id]["action_kind"] == "revision"
        assert audits[later_a.id]["action_kind"] == audits[later_z.id]["action_kind"] == "revision_update"
        assert audits[held.id]["retry_event_id"] == selected_id
        assert {row["before_json"] for row in audits.values()} == {case.model_dump_json()}
        assert {row["after_json"] for row in audits.values()} == {revised.model_dump_json()}
        assert {row["actor"] for row in audits.values()} == {"Local reviewer"}
        assert len({row["reason"] for row in audits.values()}) == 1
        queued = store.list_inbound_queue()
        assert len(queued) == 3 and {row["channel"] for row in queued} == {"gmail_review"}
        assert store.has_unreviewed_held_updates(case.id)
        workflow = WorkflowService(store, load_policy(POLICY), OfflineFixtureLLM(),
            today_provider=lambda: date(2026, 9, 4))
        worker = InboundEventWorker(store, workflow, channel="gmail_review")
        expected = [audits[event.id]["retry_event_id"] for event in (held, later_a, later_z)]
        first = worker.process_due(NOW + timedelta(minutes=3), limit=1)
        assert [(item.event_id, item.status) for item in first] == [(expected[0], "PROCESSED")]
        assert store.has_unreviewed_held_updates(case.id)
        rest = worker.process_due(NOW + timedelta(minutes=3), limit=2)
        assert [(item.event_id, item.status) for item in rest] == [(expected[1], "PROCESSED")]
        assert store.has_unreviewed_held_updates(case.id)
        last = worker.process_due(NOW + timedelta(minutes=3), limit=2)
        assert [(item.event_id, item.status) for item in last] == [(expected[2], "PROCESSED")]
        assert not store.has_unreviewed_held_updates(case.id)
        assert store.get_case(case.id).delivery_revision == 2
        assert not store.get_case(case.id).profile_confirmed
        assert not store.get_case(case.id).final_summary_confirmed
        assert worker.process_due(NOW + timedelta(minutes=3)) == []
        assert len(store.list_held_inbound(case.id)) == 3
        assert len(store.export_case_data(case.id)["review_actions"]) == 3
    finally:
        store.close()


@pytest.mark.parametrize("invalid_member", ["sender", "thread", "channel", "reason", "chronology"])
def test_bad_batch_member_rejects_the_entire_revision_without_mutation(tmp_path, invalid_member):
    store, case, held = setup_finalized(tmp_path)
    later = add_held(store, case, held, identifier="invalid-member", received_at=NOW + timedelta(minutes=2))
    if invalid_member == "sender":
        later.sender = "outsider@example.test"
    elif invalid_member == "thread":
        later.external_thread_id = "another-thread"
    elif invalid_member == "channel":
        later.channel = "whatsapp_twilio"
    elif invalid_member == "chronology":
        later.received_at = NOW - timedelta(minutes=1)
    with store.connection:
        store.connection.execute("UPDATE held_inbound_events SET payload_json=? WHERE id=?",
            (later.model_dump_json(), later.id))
        if invalid_member == "reason":
            store.connection.execute("UPDATE held_inbound_events SET reason_code='OUT_OF_ORDER_EVENT' WHERE id=?",
                (later.id,))
    before = store.export_case_data(case.id)
    try:
        with pytest.raises(ValueError):
            authorize(store, case, later if invalid_member == "chronology" else held, include_held_updates=True)
        assert store.export_case_data(case.id) == before
        assert store.list_inbound_queue() == []
    finally:
        store.close()


def test_second_batch_queue_insert_failure_rolls_back_first_insert_and_all_audits(tmp_path):
    store, case, held = setup_finalized(tmp_path)
    add_held(store, case, held, identifier="second-update", received_at=NOW + timedelta(minutes=2))
    add_outbox(store, case, status="PENDING")
    before = store.export_case_data(case.id)
    store.connection.execute("""CREATE TRIGGER fail_second_revision_queue BEFORE INSERT ON inbound_queue
        WHEN (SELECT COUNT(*) FROM inbound_queue) > 0
        BEGIN SELECT RAISE(ABORT, 'injected second batch queue failure'); END""")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            authorize(store, case, held, include_held_updates=True)
        assert store.export_case_data(case.id) == before
        assert store.list_inbound_queue() == []
    finally:
        store.close()


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_batch_worker_does_not_skip_failed_or_not_yet_due_first_update(tmp_path, max_attempts):
    store, case, held = setup_finalized(tmp_path)
    later = add_held(store, case, held, identifier="second-update", received_at=NOW + timedelta(minutes=2))
    first_id = authorize(store, case, held, include_held_updates=True)
    second_id = next(row["retry_event_id"] for row in store.export_case_data(case.id)["review_actions"]
        if row["held_event_id"] == later.id)
    workflow = WorkflowService(store, load_policy(POLICY), OfflineFixtureLLM(),
        today_provider=lambda: date(2026, 9, 4))

    class FailOnce:
        def __init__(self):
            self.calls = []

        def process(self, event):
            self.calls.append(event.id)
            if len(self.calls) == 1:
                raise RuntimeError("Synthetic interruption before first correction processing")
            return workflow.process(event)

    failing = FailOnce()
    worker = InboundEventWorker(store, failing, channel="gmail_review", max_attempts=max_attempts,
        base_backoff_seconds=30)
    attempt_at = NOW + timedelta(minutes=3)
    try:
        first = worker.process_due(attempt_at)
        expected_status = "FAILED" if max_attempts == 1 else "RETRY"
        assert [(item.event_id, item.status) for item in first] == [(first_id, expected_status)]
        assert worker.process_due(attempt_at + timedelta(seconds=10)) == []
        assert not store.event_processed(first_id) and not store.event_processed(second_id)
        assert store.has_unreviewed_held_updates(case.id)
        if max_attempts == 1:
            assert worker.process_due(attempt_at + timedelta(hours=1)) == []
            assert failing.calls == [first_id]
        else:
            retried = worker.process_due(attempt_at + timedelta(seconds=31))
            assert [(item.event_id, item.status) for item in retried] == [(first_id, "PROCESSED")]
            assert store.has_unreviewed_held_updates(case.id)
            last = worker.process_due(attempt_at + timedelta(seconds=31))
            assert [(item.event_id, item.status) for item in last] == [(second_id, "PROCESSED")]
            assert failing.calls == [first_id, first_id, second_id]
            assert not store.has_unreviewed_held_updates(case.id)
    finally:
        store.close()


def test_synthetic_revision_reprocesses_correction_and_requires_new_delivered_confirmations(tmp_path):
    settings = Settings(database_path=tmp_path / "db", output_dir=tmp_path / "output", policy_path=POLICY)
    original = run_demo(settings, evaluation_date=date(2026, 9, 4))
    store = SQLiteStore(settings.database_path)
    case = store.get_case(original.case.id)
    # Only the transport is modelled as Gmail. All evidence retains the synthetic
    # fixture provenance; this does not claim ordinary passport/PDF acceptance.
    case.primary_channel = "gmail"
    store.save_case(case)
    with store.connection:
        store.connection.execute("UPDATE outbox SET channel='gmail'")
    sender = CaptureSender()
    policy = load_policy(POLICY)
    workflow = WorkflowService(store, policy, OfflineFixtureLLM(), today_provider=lambda: date(2026, 9, 4))
    adapter = CaptureAdapter()
    automatic = AutomaticGmailReplySender(adapter, store, parseaddr(case.applicant_contact)[1])
    old_path = Path(case.delivery_path)
    old_bytes = old_path.read_bytes()
    try:
        first = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(NOW)
        assert first[0].status == "SENT" and sender.requests[0].attachment[1] == old_bytes
        held = InboundEvent(
            id="synthetic-budget-correction", external_thread_id=case.external_thread_id,
            sender=case.applicant_contact, channel="gmail", received_at=NOW + timedelta(minutes=1),
            subject="Correcting the trip budget", body="The budget is now GBP 2,600.\n"
            "<!-- DEMO_FACTS\nestimated_trip_cost_gbp=2600\n-->",
        )
        assert workflow.process(held)[2] == "finalized_case_held"
        later = held.model_copy(update={"id": "synthetic-budget-followup",
            "received_at": NOW + timedelta(minutes=1, seconds=30),
            "body": "Please use GBP 2,700 after including the extra travel costs.\n"
            "<!-- DEMO_FACTS\nestimated_trip_cost_gbp=2700\n-->"})
        assert workflow.process(later)[2] == "finalized_case_held"
        assert store.get_case(case.id).profile.estimated_trip_cost_gbp == 2200
        # The original case gate was satisfied. Held updates must override that
        # result before even reusing its existing archive, not just at send time.
        withheld_path, withheld_reasons = generate_pack(case, policy, store, settings.output_dir, date(2026, 9, 4))
        assert withheld_path is None and withheld_reasons
        queued_id = authorize(store, case, held, include_held_updates=True)
        worker = InboundEventWorker(store, workflow, channel="gmail_review")
        assert worker.process_due(NOW + timedelta(minutes=2), limit=1)[0].status == "PROCESSED"
        revised = store.get_case(case.id)
        assert revised.delivery_revision == 2 and revised.profile.estimated_trip_cost_gbp == 2600
        assert revised.documents == case.documents
        assert not revised.profile_confirmed and not revised.final_summary_confirmed
        assert revised.delivery_path is None
        assert generate_pack(revised, policy, store, settings.output_dir, date(2026, 9, 4))[0] is None
        assert old_path.read_bytes() == old_bytes
        assert store.event_processed(queued_id) and store.has_unreviewed_held_updates(case.id)
        assert worker.process_due(NOW + timedelta(minutes=2), limit=1)[0].status == "PROCESSED"
        revised = store.get_case(case.id)
        assert revised.profile.estimated_trip_cost_gbp == 2700
        assert not store.has_unreviewed_held_updates(case.id)
        assert not revised.profile_confirmed and not revised.final_summary_confirmed
        assert worker.process_due(NOW + timedelta(minutes=2)) == []
        confirm = held.model_copy(update={
            "id": "synthetic-unsent-confirmation", "body": "Everything is correct, please proceed.",
            "received_at": NOW + timedelta(minutes=3),
        })
        revised, _, plan = workflow.process(confirm)
        assert plan == "awaiting_profile_confirmation" and not revised.profile_confirmed
        # Only local captures can mark these two replacement summaries delivered.
        assert automatic.withhold_obsolete_unsent() == 2
        profile_dispatch = OutboxDispatcher(store, automatic, allowed_message_types=("awaiting_profile_confirmation",))
        assert [result.status for result in profile_dispatch.dispatch_due(NOW + timedelta(minutes=4))] == ["SENT"]
        confirm = confirm.model_copy(update={"id": "synthetic-profile-confirmed", "received_at": NOW + timedelta(minutes=5)})
        revised, _, plan = workflow.process(confirm)
        assert plan == "awaiting_confirmation" and revised.profile_confirmed
        assert not revised.final_summary_confirmed
        assert generate_pack(revised, policy, store, settings.output_dir, date(2026, 9, 4))[0] is None
        assert OutboxDispatcher(store, automatic, allowed_message_types=("awaiting_confirmation",)).dispatch_due(
            NOW + timedelta(minutes=6),
        )[0].status == "SENT"
        confirm = confirm.model_copy(update={"id": "synthetic-final-confirmed", "received_at": NOW + timedelta(minutes=7)})
        revised, _, plan = workflow.process(confirm)
        assert plan == "ready" and revised.final_summary_confirmed
        revised_path, reasons = generate_pack(revised, policy, store, settings.output_dir, date(2026, 9, 4))
        assert reasons == [] and revised_path is not None and revised_path != old_path
        assert old_path.read_bytes() == old_bytes
        with ZipFile(revised_path) as archive:
            answers = json.loads(archive.read("05_application_answers.json"))
        assert answers["profile"]["estimated_trip_cost_gbp"] == 2700
        assert answers["case_id"] == case.id
        assert answers["delivery_revision"] == 2
        ready = [row for row in store.list_outbox() if row["message_type"] == "ready"]
        assert len(ready) == 2 and {row["case_revision"] for row in ready} == {1, 2}
        final = OutboxDispatcher(store, sender, allowed_message_types=("ready",))
        outcomes = final.dispatch_due(NOW + timedelta(minutes=8))
        assert len(outcomes) == 1 and outcomes[0].status == "SENT"
        assert sender.requests[-1].attachment[1] == revised_path.read_bytes()
        assert final.dispatch_due(NOW + timedelta(minutes=9)) == []
        assert len([request for request in sender.requests if request.attachment]) == 2
        assert len(adapter.calls) == 2
        assert workflow.process(confirm)[1] and old_path.read_bytes() == old_bytes
        exported = store.export_case_data(case.id)
        assert len(exported["review_actions"]) == 2
        assert len(exported["delivery_versions"]) == 1
        assert exported["delivery_versions"][0]["case_revision"] == 1
        assert exported["delivery_versions"][0]["sha256"] == hashlib.sha256(old_bytes).hexdigest()
    finally:
        store.close()
