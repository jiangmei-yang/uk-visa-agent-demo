"""Captured Gmail receipts cannot bypass current held-state ownership."""

from datetime import UTC, datetime

import pytest
from test_automatic_gmail_reply import CaptureAdapter

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("changed", ["status", "owner", "thread", "held_missing", "pause_epoch"])
def test_pending_review_receipt_revalidates_before_send(tmp_path, changed):
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="fictional", external_thread_id="fictional-thread", applicant_contact="user@example.test",
                primary_channel="gmail", policy_version="synthetic", status=CaseStatus.HUMAN_REVIEW_REQUIRED)
    store.save_case(case)
    event = InboundEvent(id="followup", external_thread_id=case.external_thread_id, sender=case.applicant_contact,
        channel="gmail", body="Here is my clarification.", subject="Fictional", received_at=now)
    store.record_rejected_event(event_id=event.id, case_id=case.id, thread_id=case.external_thread_id,
        reason_code="HUMAN_REVIEW_CASE_NEW_EVENT", detail="Fictional hold", held_event=event)
    adapter = CaptureAdapter()
    sender = AutomaticGmailReplySender(adapter, store, case.applicant_contact)
    try:
        assert sender.queue_held_update_receipts() == 1
        assert sender.queue_held_update_receipts() == 0
        if changed == "status":
            case.status = CaseStatus.DRAFT
        elif changed == "owner":
            case.applicant_contact = "different@example.test"
        elif changed == "thread":
            case.external_thread_id = "different-thread"
        elif changed == "pause_epoch":
            case.preparation_control_epoch += 1
        else:
            # A missing source cannot authorize a previously queued receipt.
            store.connection.execute("DELETE FROM held_inbound_events WHERE id=?", (event.id,))
            store.connection.commit()
        store.save_case(case)
        dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("held_update_received",))
        assert dispatcher.dispatch_due(now)[0].status == "FAILED"
        assert adapter.calls == []
    finally:
        store.close()


def test_review_receipt_binds_actual_granted_processing_epoch(tmp_path):
    from test_processing_consent_boundaries import Journey

    journey = Journey(tmp_path)
    try:
        journey.grant()  # Synthetic applicant grant after a captured SENT notice.
        case = journey.case()
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
        journey.store.save_case(case)
        event = journey.event("Here is the clarification for the adviser.")
        assert journey.workflow.process(event)[2] == "human_review_case_held"
        adapter = CaptureAdapter()
        sender = AutomaticGmailReplySender(adapter, journey.store, case.applicant_contact)
        assert sender.queue_held_update_receipts() == 1
        row = next(row for row in journey.store.list_outbox() if row["message_type"] == "held_update_received")
        assert row["processing_consent_epoch"] == journey.ledger.epoch(case.id)
        dispatcher = OutboxDispatcher(journey.store, sender, channel="gmail", allowed_message_types=("held_update_received",))
        assert dispatcher.dispatch_due(journey.tick())[0].status == "SENT"
        assert len(adapter.calls) == 1
        assert "review is still open" in adapter.calls[0]["body"]
    finally:
        journey.store.close()
