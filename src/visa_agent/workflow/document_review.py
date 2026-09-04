"""Audited local recovery of unreadable/unknown PDFs, not document approval.

Callers hold the Gmail state lock. Source files and applicant facts are never edited.
An ordinary replacement must already have passed the configured reader; an explicit
retry invokes that same reader and keeps its original customer-event provenance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from visa_agent.documents.processor import sha256_file
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    Document,
    DocumentStatus,
    InboundEvent,
    WorkflowStage,
)
from visa_agent.domain.rules import resolve_issue, run_consistency_checks
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.service import WorkflowService


def _source_unchanged(document: Document) -> None:
    try:
        same = sha256_file(Path(document.path)) == document.sha256
    except OSError as error:
        raise ValueError("Retained document source is unavailable; recover the original bytes first") from error
    if not same:
        raise ValueError("Retained document source integrity check failed")


def _recoverable(case: Case, document: Document) -> bool:
    # A recognized but rejected identity document, specimen, missing identity fact or
    # low-confidence known-kind classification is NOT this technical recovery case.
    return document.kind == "unknown" and document.status in {
        DocumentStatus.NEEDS_REPLACEMENT, DocumentStatus.HUMAN_REVIEW_REQUIRED,
    } and any(issue.code in {
        f"UNREADABLE_DOCUMENT_{document.id}", f"UNCLASSIFIED_DOCUMENT_{document.id}",
    } for issue in case.open_blockers())


def _retry_lineage(workflow: WorkflowService, case: Case, selected: Document) -> list[Document]:
    """Resolve only explicit, same-source retry links created by a committed audit."""
    documents = {doc.id: doc for doc in case.documents}
    root = selected
    visited: set[str] = set()
    while root.retry_of_document_id is not None:
        if root.id in visited or root.retry_of_document_id not in documents:
            raise ValueError("Invalid document retry lineage; separate review required")
        visited.add(root.id)
        root = documents[root.retry_of_document_id]
    lineage = [root]
    cursor = 0
    while cursor < len(lineage):
        parent = lineage[cursor]
        for child in case.documents:
            if child.retry_of_document_id != parent.id:
                continue
            if child in lineage or len(lineage) >= len(case.documents):
                raise ValueError("Invalid document retry lineage; separate review required")
            if child.sha256 != root.sha256 or child.source_event_id != root.source_event_id:
                raise ValueError("Document retry source does not match its retained original")
            # The typed link alone is not permission: a committed document-retry
            # audit must have created this immutable source association.
            authorized = False
            for row in workflow.store.connection.execute(
                "SELECT before_json, after_json FROM review_actions WHERE case_id=? AND action_kind='document_retry'",
                (case.id,),
            ):
                before = Case.model_validate_json(row["before_json"])
                after = Case.model_validate_json(row["after_json"])
                recorded = next((doc for doc in after.documents if doc.id == child.id), None)
                if recorded is not None and not any(doc.id == child.id for doc in before.documents):
                    authorized = all(getattr(recorded, field) == getattr(child, field) for field in (
                        "id", "retry_of_document_id", "sha256", "source_event_id", "path", "kind",
                    ))
                    if authorized:
                        break
            if not authorized:
                raise ValueError("Document retry lineage lacks its committed review authorization")
            lineage.append(child)
        cursor += 1
    if any(not _recoverable(case, doc) for doc in lineage):
        raise ValueError("This retry lineage contains a non-technical review result; separate review required")
    return lineage


def recover_document(workflow: WorkflowService, *, case_id: str, document_id: str,
                     expected_fingerprint: str, actor: str, reason: str,
                     replacement_document_id: str | None = None) -> str:
    """Recover one specified failed document under a local operator's asserted identity.

No applicant event is forged, no held event is acknowledged, no reply is queued, and
no pack is produced. A failed normal reread is saved as a new attempt with its own
blocker; the original failure stays open. Unexpected transaction errors roll back.
"""
    if not 2 <= len(actor.strip()) <= 120 or not 12 <= len(reason.strip()) <= 2000:
        raise ValueError("Provide an operator name and a substantive document-review reason")
    store = workflow.store
    with store.atomic_write():
        case = store.get_case(case_id)
        if case is None or review_fingerprint(case) != expected_fingerprint:
            raise ValueError("Case changed or missing; inspect again before document review")
        if case.primary_channel != "gmail" or case.status != CaseStatus.DRAFT or case.delivery_path:
            raise ValueError("Only non-finalized draft Gmail cases support document recovery")
        if store.connection.execute(
            "SELECT 1 FROM outbox WHERE case_id=? AND status IN ('SENDING','AMBIGUOUS')", (case_id,),
        ).fetchone():
            raise ValueError("Reconcile uncertain sends before document review")
        old = next((doc for doc in case.documents if doc.id == document_id), None)
        if old is None or not _recoverable(case, old):
            raise ValueError("Select an unreadable or unknown document with its open technical blocker")
        lineage = _retry_lineage(workflow, case, old)
        replacement = None
        if replacement_document_id is not None:
            replacement = next((doc for doc in case.documents if doc.id == replacement_document_id), None)
            if (replacement is None or replacement.id == old.id or replacement.kind == "unknown"
                    or replacement.status not in {DocumentStatus.ACCEPTED_FOR_REVIEW,
                                                 DocumentStatus.NEEDS_CERTIFIED_TRANSLATION}
                    or replacement.supersedes_document_id is not None
                    or any(replacement.id in issue.related_document_ids for issue in case.open_blockers()
                           if issue.code != "MISSING_CERTIFIED_TRANSLATION")):
                raise ValueError("Select a separately received, classified replacement without unresolved review blockers")
        for member in lineage:
            _source_unchanged(member)
        if replacement is not None:
            _source_unchanged(replacement)
        before = case.model_dump_json()
        kind = "document_replacement" if replacement is not None else "document_retry"
        action_id = "document-review-" + uuid5(NAMESPACE_URL,
            f"{case.id}:{expected_fingerprint}:{old.id}:{replacement_document_id}:{kind}").hex
        if replacement is None:
            # Keep the true original source event. The audit action ID differentiates
            # extraction/document/evidence IDs; it is not a fictitious customer email.
            source = InboundEvent(id=old.source_event_id, channel=case.primary_channel,
                external_thread_id=case.external_thread_id, sender=case.applicant_contact,
                subject="", body="", attachment_paths=[old.path], received_at=old.received_at)
            workflow._ingest_attachments(case, source, reread_attempt_id=action_id)
            replacement = case.documents[-1]
            replacement.retry_of_document_id = old.id
            _source_unchanged(old)
        successful = replacement.kind != "unknown" and replacement.status in {
            DocumentStatus.ACCEPTED_FOR_REVIEW, DocumentStatus.NEEDS_CERTIFIED_TRANSLATION,
        }
        if successful:
            replacement.supersedes_document_id = lineage[0].id
            superseded_ids = {member.id for member in lineage}
            for member in lineage:
                member.status = DocumentStatus.SUPERSEDED
            for evidence in case.evidence:
                if evidence.source_document_id in superseded_ids:
                    evidence.superseded = True
            for member in lineage:
                for prefix in ("UNREADABLE_DOCUMENT_", "UNCLASSIFIED_DOCUMENT_"):
                    resolve_issue(case, prefix + member.id,
                        f"{action_id}: normally classified document {replacement.id} replaces this failed read; source retained.")
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        case.stage = WorkflowStage.DOCUMENT_REVIEW
        case.updated_at = datetime.now(UTC)
        run_consistency_checks(case)
        after = case.model_dump_json()
        store.connection.execute("UPDATE cases SET snapshot_json=?, updated_at=? WHERE id=?",
                                 (after, case.updated_at.isoformat(), case_id))
        # Independent audit key, not a held-inbound ID. Existing held-update checks
        # cannot confuse this operation with acknowledging an applicant update.
        store.connection.execute("""INSERT INTO review_actions
            (id, case_id, held_event_id, actor, reason, before_json, after_json, retry_event_id, action_kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_id, case_id, action_id, actor.strip(), reason.strip(), before, after, action_id, kind))
        store.connection.execute(
            "UPDATE outbox SET status='FAILED', next_attempt_at=NULL, last_error=? "
            "WHERE case_id=? AND status IN ('PENDING','RETRY')",
            ("Superseded by audited document review; fresh applicant confirmations are required", case_id))
    return action_id
