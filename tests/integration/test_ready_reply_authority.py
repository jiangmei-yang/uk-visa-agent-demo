"""Synthetic workflow and captured transports; no real identity approval or network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.delivery.pack import generate_pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.llm.ports import CasePatch, CustomerQuestion
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import queue_finalized_revision, review_fingerprint
from visa_agent.workflow.service import WorkflowService

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
POLICY = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
OFF_TOPIC = "Can you explain this geometry exercise?"


class Capture:
    def __init__(self):
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return "captured-" + request.outbox_id


class FixtureModel(OfflineFixtureLLM):
    def extract_case_patch(self, event):
        if event.body == OFF_TOPIC:
            return CasePatch(updates=[], ambiguities=[], customer_questions=[
                CustomerQuestion(topic="off_topic", source_excerpt=OFF_TOPIC, confidence=1)])
        return super().extract_case_patch(event)


def seed(directory):
    documents = directory / "documents"
    generate_sample_documents(documents)
    store = SQLiteStore(directory / "case.db")
    workflow = WorkflowService(store, load_policy(POLICY), FixtureModel(),
                               today_provider=lambda: DEMO_EVALUATION_DATE)
    for index, filename in enumerate(sorted(Path("samples/emails").glob("*.eml"))):
        event = parse_eml(filename, documents)
        event.channel = "gmail"
        case, _, plan = workflow.process(event)
        if index < 2:
            sent = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,)).dispatch_due(NOW)
            assert len(sent) == 1 and sent[0].status == "SENT"
        if index == 1:
            assert plan == "awaiting_profile_confirmation" and not case.profile_confirmed
            case, _, plan = workflow.process(event.model_copy(update={
                "id": "seed-profile-confirmation", "body": "I confirm the profile summary",
                "attachment_paths": [], "received_at": event.received_at + timedelta(minutes=1),
            }))
            assert plan == "awaiting_confirmation" and case.profile_confirmed
            sent = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,)).dispatch_due(NOW)
            assert len(sent) == 1 and sent[0].status == "SENT"
    assert plan == "ready" and case.final_summary_confirmed and case.delivery_path is None
    return store, workflow, case, event


def turn(workflow, original, identifier, body, minute):
    event = original.model_copy(update={"id": identifier, "body": body, "attachment_paths": [],
        "received_at": NOW + timedelta(minutes=minute), "rfc_message_id": f"<{identifier}@example.test>"})
    return workflow.process(event)


def confirm_correction(store, workflow, original, *, prefix="updated", start=1):
    case, _, plan = turn(workflow, original, prefix + "-correction",
                        "My budget changed.\n<!-- DEMO_FACTS\nestimated_trip_cost_gbp=2600\n-->", start)
    assert plan == "awaiting_profile_confirmation" and not case.final_summary_confirmed
    assert not case.profile_confirmed
    dispatcher = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,))
    assert dispatcher.dispatch_due(NOW + timedelta(minutes=start + 1))[0].status == "SENT"
    case, _, plan = turn(workflow, original, prefix + "-profile-confirmed",
                        "Everything is correct, please proceed.", start + 2)
    assert plan == "awaiting_confirmation" and case.profile_confirmed
    assert not case.final_summary_confirmed
    dispatcher = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,))
    assert dispatcher.dispatch_due(NOW + timedelta(minutes=start + 3))[0].status == "SENT"
    case, _, plan = turn(workflow, original, prefix + "-final-confirmed",
                        "Everything is correct, please proceed.", start + 4)
    assert plan == "ready" and case.final_summary_confirmed
    return case


def ready_rows(store):
    return [row for row in store.list_outbox() if row["message_type"] == "ready"]


@pytest.mark.parametrize("timestamps", ["same_second", "clock_skew"])
def test_same_revision_correction_new_confirmation_sends_only_current_final_reply(tmp_path, timestamps):
    store, workflow, case, original = seed(tmp_path)
    try:
        old = ready_rows(store)[0]
        # Failure is before materialization; there is no registered historical pack.
        case = confirm_correction(store, workflow, original)
        assert case.delivery_revision == 1
        archive, reasons = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive and not reasons
        with ZipFile(archive) as zipped:
            assert json.loads(zipped.read("05_application_answers.json"))["profile"]["estimated_trip_cost_gbp"] == 2600
        current = ready_rows(store)[-1]
        assert current["event_id"] == "updated-final-confirmed" and current["id"] != old["id"]
        with store.connection:
            store.connection.execute("UPDATE outbox SET created_at='2026-09-02 12:00:00' WHERE message_type='ready'")
            if timestamps == "clock_skew":
                store.connection.execute("UPDATE outbox SET created_at='2026-09-03 12:00:00' WHERE id=?", (old["id"],))
        capture = Capture()
        dispatcher = OutboxDispatcher(store, capture, allowed_message_types=("ready",))
        dispatcher.dispatch_due(NOW + timedelta(minutes=10))
        dispatcher.dispatch_due(NOW + timedelta(minutes=11))
        assert [request.outbox_id for request in capture.requests] == [current["id"]]
        assert capture.requests[0].in_reply_to == "<updated-final-confirmed@example.test>"
        assert capture.requests[0].attachment[1] == archive.read_bytes()
        old_after = next(row for row in ready_rows(store) if row["id"] == old["id"])
        assert old_after["status"] == "FAILED" and old_after["attempt_count"] == 0
    finally:
        store.close()


def test_superseded_retry_is_withheld_without_erasing_prior_attempt_or_provider_evidence(tmp_path):
    store, workflow, case, original = seed(tmp_path)
    try:
        case = confirm_correction(store, workflow, original)
        archive, _ = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive
        old = ready_rows(store)[0]
        with store.connection:
            store.connection.execute(
                "UPDATE outbox SET status='RETRY',attempt_count=2,provider_message_id='retained-provider-evidence' WHERE id=?",
                (old["id"],),
            )
        capture = Capture()
        OutboxDispatcher(store, capture, allowed_message_types=("ready",)).dispatch_due(NOW + timedelta(minutes=10))
        assert capture.requests == []
        after = next(row for row in ready_rows(store) if row["id"] == old["id"])
        assert after["status"] == "FAILED" and after["attempt_count"] == 2
        assert after["provider_message_id"] == "retained-provider-evidence"
        assert "superseded final confirmation" in after["last_error"]
    finally:
        store.close()


def test_ready_preflight_before_materialization_does_not_fabricate_provider_attempt(tmp_path):
    store, workflow, case, original = seed(tmp_path)
    try:
        capture = Capture()
        result = OutboxDispatcher(store, capture, allowed_message_types=("ready",)).dispatch_due(NOW)
        assert len(result) == 1 and result[0].status == "FAILED" and not capture.requests
        assert ready_rows(store)[0]["attempt_count"] == 0
        archive, reasons = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive and not reasons
        # A local preflight rejection is not automatic retry or permission to send.
        assert ready_rows(store)[0]["status"] == "FAILED"
        assert OutboxDispatcher(store, capture, allowed_message_types=("ready",)).dispatch_due(NOW) == []
    finally:
        store.close()


@pytest.mark.parametrize("status", ["SENDING", "AMBIGUOUS", "SENT"])
def test_other_ready_same_revision_with_send_evidence_blocks_new_dispatch_without_mutation(tmp_path, status):
    store, workflow, case, original = seed(tmp_path)
    try:
        case = confirm_correction(store, workflow, original)
        archive, _ = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive
        old = ready_rows(store)[0]
        with store.connection:
            store.connection.execute("UPDATE outbox SET status=?, attempt_count=1, provider_message_id=? WHERE id=?",
                                     (status, "retained-provider-evidence", old["id"]))
        evidence = next(row for row in ready_rows(store) if row["id"] == old["id"])
        capture = Capture()
        dispatcher = OutboxDispatcher(store, capture, allowed_message_types=("ready",))
        dispatcher.dispatch_due(NOW + timedelta(minutes=10))
        assert capture.requests == []
        assert next(row for row in ready_rows(store) if row["id"] == old["id"]) == evidence
    finally:
        store.close()


def test_unrelated_consultation_that_preserves_final_consent_does_not_supersede_ready(tmp_path):
    store, workflow, case, original = seed(tmp_path)
    try:
        original_ready = ready_rows(store)[0]
        case, _, plan = turn(workflow, original, "unrelated-consultation", OFF_TOPIC, 1)
        assert plan == "blocked" and case.final_summary_confirmed
        assert len(ready_rows(store)) == 1
        archive, _ = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive
        capture = Capture()
        OutboxDispatcher(store, capture, allowed_message_types=("ready",)).dispatch_due(NOW + timedelta(minutes=2))
        assert [request.outbox_id for request in capture.requests] == [original_ready["id"]]
    finally:
        store.close()


def test_operator_authorized_next_revision_can_send_once_after_prior_revision_sent(tmp_path):
    from visa_agent.channels.inbound_worker import InboundEventWorker

    store, workflow, case, original = seed(tmp_path)
    try:
        first_archive, _ = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        capture = Capture()
        dispatcher = OutboxDispatcher(store, capture, allowed_message_types=("ready",))
        assert dispatcher.dispatch_due(NOW)[0].status == "SENT"
        held = InboundEvent(id="held-after-delivery", channel="gmail", sender=case.applicant_contact,
            external_thread_id=case.external_thread_id, received_at=NOW + timedelta(minutes=1),
            subject="Correction after the original delivery",
            body="My budget is now GBP 2,600.\n<!-- DEMO_FACTS\nestimated_trip_cost_gbp=2600\n-->")
        assert workflow.process(held)[2] == "finalized_case_held"
        queue_finalized_revision(store, case_id=case.id, held_event_id=held.id,
            expected_fingerprint=review_fingerprint(store.get_case(case.id)), actor="Synthetic reviewer",
            reason="Review applicant correction and require new sent confirmations.")
        worker = InboundEventWorker(store, workflow, channel="gmail_review")
        assert worker.process_due(NOW + timedelta(minutes=2))[0].status == "PROCESSED"
        # Complete the real re-confirmation flow; no direct edits to confirmation flags.
        case = confirm_correction(store, workflow, original, prefix="revision-two", start=3)
        assert case.delivery_revision == 2
        second_archive, _ = generate_pack(case, workflow.policy, store, tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert second_archive and second_archive != first_archive
        outcomes = dispatcher.dispatch_due(NOW + timedelta(minutes=10))
        assert len(outcomes) == 1 and outcomes[0].status == "SENT"
        assert dispatcher.dispatch_due(NOW + timedelta(minutes=11)) == []
        assert len(capture.requests) == 2
        assert capture.requests[1].attachment[1] == second_archive.read_bytes()
    finally:
        store.close()
