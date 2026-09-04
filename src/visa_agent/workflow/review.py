"""Local operator-authorized retry, never document approval or final-case reopening."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.storage.sqlite import SQLiteStore


def review_fingerprint(case: Case) -> str:
    return hashlib.sha256(case.model_dump_json().encode()).hexdigest()


def queue_review_retry(store: SQLiteStore, *, case_id: str, held_event_id: str,
                       expected_fingerprint: str, actor: str, reason: str) -> str:
    """Atomically invalidate consent and queue an original held update for normal validation.

    Caller owns the Gmail state lock. The local operator identity is asserted, not authenticated
    by this function. No edit to facts/documents is accepted, and no mail is sent here.
    """
    if not 2 <= len(actor.strip()) <= 120 or not 12 <= len(reason.strip()) <= 2000:
        raise ValueError("Provide an operator name and a substantive review/retry reason")
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        case = store.get_case(case_id)
        if case is None or review_fingerprint(case) != expected_fingerprint:
            raise ValueError("Case changed or missing; inspect again before retrying")
        if case.status != CaseStatus.HUMAN_REVIEW_REQUIRED or case.delivery_path:
            raise ValueError("Only non-finalized human-review cases can retry intake")
        if case.primary_channel != "gmail":
            raise ValueError("This reviewed retry currently supports registered Gmail intake only")
        row = store.connection.execute(
            "SELECT * FROM held_inbound_events WHERE id=? AND case_id=?", (held_event_id, case_id),
        ).fetchone()
        if row is None or row["reason_code"] != "HUMAN_REVIEW_CASE_NEW_EVENT":
            raise ValueError("Select a held applicant update for this paused case")
        if store.connection.execute("SELECT 1 FROM review_actions WHERE held_event_id=?",
                                    (held_event_id,)).fetchone():
            raise ValueError("This held update already has a reviewed retry")
        event = InboundEvent.model_validate_json(row["payload_json"])
        if case.last_inbound_received_at and event.received_at < case.last_inbound_received_at:
            raise ValueError("An older update requires controlled revision, not automatic retry")
        if store.connection.execute("SELECT 1 FROM outbox WHERE case_id=? AND status='SENDING'",
                                    (case_id,)).fetchone():
            raise ValueError("Reconcile uncertain sends before review retry")
        before = case.model_dump_json()
        action_id = "review-" + uuid5(NAMESPACE_URL, case_id + ":" + held_event_id).hex
        retry_id = action_id + "-retry"
        retry = event.model_copy(update={"id": retry_id, "requested_fields": [], "known_profile": {}})
        case.status = CaseStatus.DRAFT
        case.stage = WorkflowStage.INTAKE
        case.human_review_reason = None
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        case.last_requested_fields = []
        case.updated_at = datetime.now(UTC)
        after = case.model_dump_json()
        store.connection.execute("UPDATE cases SET snapshot_json=?, updated_at=? WHERE id=?",
                                  (after, case.updated_at.isoformat(), case_id))
        store.connection.execute("""INSERT INTO review_actions
            (id, case_id, held_event_id, actor, reason, before_json, after_json, retry_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_id, case_id, held_event_id, actor.strip(), reason.strip(), before, after, retry_id))
        store.connection.execute("INSERT INTO inbound_queue(id, channel, payload_json) VALUES (?, ?, ?)",
                                  (retry_id, "gmail_review", retry.model_dump_json()))
    return retry_id
