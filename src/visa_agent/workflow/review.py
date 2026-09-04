"""Audited local retry/revision authorization; never document approval or automatic delivery."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.privacy.consent import ConsentLedger
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
        ConsentLedger(store).require(case)
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


def queue_finalized_revision(store: SQLiteStore, *, case_id: str, held_event_id: str,
                             expected_fingerprint: str, actor: str, reason: str,
                             include_held_updates: bool = False) -> str:
    """Preserve the old pack and queue explicitly reviewed corrections in chronological order.

    The caller owns the Gmail state lock. This local operator identity is asserted,
    not authenticated. The action does not approve facts, produce a pack or send mail.
    """
    if not 2 <= len(actor.strip()) <= 120 or not 12 <= len(reason.strip()) <= 2000:
        raise ValueError("Provide an operator name and a substantive revision reason")
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        case = store.get_case(case_id)
        if case is None or review_fingerprint(case) != expected_fingerprint:
            raise ValueError("Case changed or missing; inspect again before revision")
        ConsentLedger(store).require(case)
        if case.status not in {CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION} or not case.delivery_path:
            raise ValueError("Only a finalized case with a registered pack can start a revision")
        if case.primary_channel != "gmail":
            raise ValueError("Reviewed revisions currently support registered Gmail intake only")
        row = store.connection.execute(
            "SELECT * FROM held_inbound_events WHERE id=? AND case_id=?", (held_event_id, case_id),
        ).fetchone()
        if row is None or row["reason_code"] != "FINALIZED_CASE_NEW_EVENT":
            raise ValueError("Select a held applicant update for this finalized case")
        if store.connection.execute("SELECT 1 FROM review_actions WHERE held_event_id=?", (held_event_id,)).fetchone():
            raise ValueError("This held update already has a reviewed retry")
        pending = store.connection.execute(
            "SELECT * FROM held_inbound_events h WHERE case_id=? AND NOT EXISTS "
            "(SELECT 1 FROM review_actions a WHERE a.held_event_id=h.id)", (case_id,),
        ).fetchall()
        batch = sorted(((item, InboundEvent.model_validate_json(item["payload_json"])) for item in pending),
                       key=lambda item: (item[1].received_at, item[1].id))
        if len(batch) > 1 and not include_held_updates:
            raise ValueError("Multiple held updates require explicit include_held_updates batch review")
        if not batch or batch[0][1].id != held_event_id:
            raise ValueError("Select the earliest held update when reviewing a revision batch")
        owner_addresses = [address.casefold() for _, address in getaddresses([case.applicant_contact])]
        for item, event in batch:
            sender_addresses = [address.casefold() for _, address in getaddresses([event.sender])]
            if (item["reason_code"] != "FINALIZED_CASE_NEW_EVENT" or event.channel != "gmail"
                    or event.external_thread_id != case.external_thread_id
                    or len(owner_addresses) != 1 or not owner_addresses[0] or sender_addresses != owner_addresses):
                raise ValueError("Every held update must belong to this applicant and finalized Gmail thread")
            if case.last_inbound_received_at and event.received_at < case.last_inbound_received_at:
                raise ValueError("An older update requires separate chronology review")
        if store.connection.execute(
            "SELECT 1 FROM outbox WHERE case_id=? AND status IN ('SENDING','AMBIGUOUS')", (case_id,),
        ).fetchone():
            raise ValueError("Reconcile uncertain sends before starting a revision")
        registered = store.connection.execute("SELECT * FROM deliveries WHERE case_id=?", (case_id,)).fetchone()
        if (registered is None or registered["path"] != case.delivery_path
                or registered["case_revision"] != case.delivery_revision):
            raise ValueError("Original pack does not match its registered revision")
        try:
            digest = hashlib.sha256(Path(case.delivery_path).read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError("Original pack is missing or unreadable; recover it before revision") from error
        if digest != registered["sha256"]:
            raise ValueError("Original pack integrity check failed; recover it before revision")
        before = case.model_dump_json()
        store.connection.execute(
            "INSERT OR IGNORE INTO delivery_versions(case_id, path, sha256, case_revision) VALUES (?, ?, ?, ?)",
            (case_id, registered["path"], digest, case.delivery_revision),
        )
        store.connection.execute(
            "UPDATE outbox SET status='FAILED', next_attempt_at=NULL, last_error=? "
            "WHERE case_id=? AND case_revision=? AND status IN ('PENDING','RETRY')",
            ("Superseded by operator-authorized revision; no resend of an old reply", case_id, case.delivery_revision),
        )
        case.delivery_revision += 1
        case.delivery_path = None
        case.status = CaseStatus.DRAFT
        case.stage = WorkflowStage.INTAKE
        case.human_review_reason = None
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        case.last_requested_fields = []
        case.pending_question_fields = []
        case.question_plan = None
        case.updated_at = datetime.now(UTC)
        after = case.model_dump_json()
        store.connection.execute("UPDATE cases SET snapshot_json=?, updated_at=? WHERE id=?",
                                 (after, case.updated_at.isoformat(), case_id))
        retry_ids = []
        for index, (_, event) in enumerate(batch):
            action_id = "revision-" + uuid5(NAMESPACE_URL, case_id + ":" + event.id).hex
            retry_id = action_id + "-retry"
            retry_ids.append(retry_id)
            retry = event.model_copy(update={"id": retry_id, "requested_fields": [], "known_profile": {}})
            store.connection.execute("""INSERT INTO review_actions
                (id, case_id, held_event_id, actor, reason, before_json, after_json, retry_event_id, action_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (action_id, case_id, event.id, actor.strip(), reason.strip(), before, after, retry_id,
                 "revision" if index == 0 else "revision_update"))
            store.connection.execute("INSERT INTO inbound_queue(id, channel, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (retry_id, "gmail_review", retry.model_dump_json(),
                 (case.updated_at + timedelta(microseconds=index)).isoformat()))
    return retry_ids[0]
