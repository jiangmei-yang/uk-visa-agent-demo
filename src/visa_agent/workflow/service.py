from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pypdf.errors import PdfReadError

from visa_agent.documents.processor import inspect_pdf, sha256_file
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    InboundEvent,
    Issue,
    IssueSeverity,
    ProvenanceState,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import advance_stage, evaluate_gate, run_consistency_checks
from visa_agent.llm.guarded import ensure_guarded
from visa_agent.llm.ports import LLMClient
from visa_agent.storage.sqlite import SQLiteStore

PROFILE_CONFIRMATION_LINES = {
    "profile confirmed",
    "i confirm the profile summary",
    "我确认上述个人资料",
    "我确认个人资料摘要",
}
FINAL_CONFIRMATION_LINES = {
    "i confirm the final summary",
    "final summary confirmed",
    "我确认最终资料摘要",
    "我确认最终材料清单和资料摘要",
}


def has_explicit_confirmation_line(body: str, accepted_lines: set[str]) -> bool:
    """Accept only a standalone confirmation line, not a quoted/injected substring."""

    lines = {
        re.sub(r"\s+", " ", line).strip().casefold()
        for line in body.splitlines()
        if line.strip()
    }
    return bool(lines & accepted_lines)


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, value).hex[:12]}"


class WorkflowService:
    def __init__(
        self,
        store: SQLiteStore,
        policy: Policy,
        llm: LLMClient,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self.store = store
        self.policy = policy
        self.llm = ensure_guarded(llm)
        self.today_provider = today_provider

    def process(self, event: InboundEvent) -> tuple[Case, bool, str]:
        if self.store.event_processed(event.id):
            existing = self.store.get_case_by_thread(event.external_thread_id)
            if existing is None:
                raise RuntimeError("Processed event has no case")
            return existing, True, "duplicate_ignored"

        case = self.store.get_case_by_thread(event.external_thread_id)
        if case is None:
            case = Case(
                id=stable_id("case", event.external_thread_id),
                external_thread_id=event.external_thread_id,
                applicant_contact=event.sender,
                primary_channel=event.channel,
                policy_version=self.policy.version,
            )
        else:
            rejection = self._inbound_rejection(case, event)
            if rejection is not None:
                reason_code, plan, detail = rejection
                self.store.record_rejected_event(
                    event_id=event.id,
                    case_id=case.id,
                    thread_id=event.external_thread_id,
                    reason_code=reason_code,
                    detail=detail,
                )
                return case, False, plan

        patch = self.llm.extract_case_patch(event)
        if patch.requires_human_review:
            case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
            advance_stage(case, WorkflowStage.HUMAN_REVIEW_REQUIRED)
            case.human_review_reason = "Bounded extractor requested human review."
        self._apply_patch(case, event, patch.model_dump()["updates"])
        self._ingest_attachments(case, event)

        if has_explicit_confirmation_line(event.body, PROFILE_CONFIRMATION_LINES):
            case.profile_confirmed = True
            advance_stage(case, WorkflowStage.COLLECTING_DOCUMENTS)
            for evidence in case.evidence:
                if evidence.source_document_id is None:
                    evidence.confirmed = True
        if has_explicit_confirmation_line(event.body, FINAL_CONFIRMATION_LINES):
            case.final_summary_confirmed = True
            advance_stage(case, WorkflowStage.FINAL_CONFIRMATION)

        run_consistency_checks(case)
        case.last_inbound_received_at = event.received_at
        case.updated_at = datetime.now(UTC)
        gate = evaluate_gate(case, self.policy, self.today_provider())
        if case.status != CaseStatus.HUMAN_REVIEW_REQUIRED:
            if gate.allowed:
                advance_stage(case, WorkflowStage.READY_FOR_HUMAN_REVIEW)
            elif case.open_blockers():
                advance_stage(case, WorkflowStage.DOCUMENT_REVIEW)
            elif not case.final_summary_confirmed:
                advance_stage(case, WorkflowStage.FINAL_CONFIRMATION)
        failed_checks = {key for key, passed in gate.checks.items() if not passed}
        plan = "ready" if gate.allowed else "blocked"
        if failed_checks == {"applicant_explicitly_confirmed_final_summary"}:
            plan = "awaiting_confirmation"
        message = self.llm.render_message(case, plan)
        message_id = stable_id("message", f"{event.id}:{plan}")
        if message_id not in case.outbound_message_ids:
            case.outbound_message_ids.append(message_id)
        self.store.commit_event(case, event, plan, message)
        return case, False, plan

    def _inbound_rejection(
        self,
        case: Case,
        event: InboundEvent,
    ) -> tuple[str, str, str] | None:
        if case.primary_channel.startswith("whatsapp"):
            applicant_address = case.applicant_contact.casefold()
            sender_address = event.sender.casefold()
        else:
            applicant_address = parseaddr(case.applicant_contact)[1].casefold()
            sender_address = parseaddr(event.sender)[1].casefold()
        if not applicant_address or sender_address != applicant_address:
            return (
                "THREAD_SENDER_MISMATCH",
                "sender_mismatch_rejected",
                "The sender did not match the applicant address recorded for this thread.",
            )
        if case.last_inbound_received_at and event.received_at < case.last_inbound_received_at:
            return (
                "OUT_OF_ORDER_EVENT",
                "out_of_order_held",
                "The message was older than the latest processed event and was held for review.",
            )
        if case.status in {
            CaseStatus.READY_FOR_HUMAN_REVIEW,
            CaseStatus.DELIVERED_AFTER_CONFIRMATION,
        }:
            return (
                "FINALIZED_CASE_NEW_EVENT",
                "finalized_case_held",
                "New information for a finalized case was held for a human-controlled revision.",
            )
        if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
            return (
                "HUMAN_REVIEW_CASE_NEW_EVENT",
                "human_review_case_held",
                "Automatic processing remained paused because the case requires human review.",
            )
        return None

    def _apply_patch(self, case: Case, event: InboundEvent, updates: list[dict[str, Any]]) -> None:
        allowed = set(type(case.profile).model_fields)
        for update in updates:
            field = str(update["field"])
            if field not in allowed:
                continue
            value: Any = update["value"]
            if field.endswith("_date") or field == "date_of_birth":
                value = date.fromisoformat(str(value))
            setattr(case.profile, field, value)
            for old in case.active_evidence(field):
                old.superseded = True
            case.evidence.append(
                Evidence(
                    id=stable_id("ev", f"{event.id}:{field}:{value}"),
                    fact_key=field,
                    value=value.isoformat() if isinstance(value, date) else value,
                    source_event_id=event.id,
                    source_excerpt=str(update["source_excerpt"]),
                    extraction_method="bounded_structured_extraction",
                    model_version=getattr(self.llm, "version", "unknown"),
                    confidence=float(update["confidence"]),
                    provenance_state=ProvenanceState.DEMO_SYNTHETIC,
                )
            )

    def _ingest_attachments(self, case: Case, event: InboundEvent) -> None:
        existing_hashes = {document.sha256 for document in case.documents}
        for raw_path in event.attachment_paths:
            path = Path(raw_path)
            try:
                digest = sha256_file(path)
            except OSError:
                digest = hashlib.sha256(f"{event.id}:{path.name}:unavailable".encode()).hexdigest()
            if digest in existing_hashes:
                continue
            document_id = stable_id("doc", digest)
            try:
                kind, language, page_count, facts = inspect_pdf(path)
            except (OSError, ValueError, PdfReadError):
                self._record_unreadable_document(case, event, path, document_id, digest)
                existing_hashes.add(digest)
                continue
            supersedes_document_id: str | None = None
            if kind == "conference_invitation":
                for old in case.documents:
                    if old.kind == kind and old.status == DocumentStatus.ACCEPTED_FOR_REVIEW:
                        supersedes_document_id = old.id
                        old.status = DocumentStatus.SUPERSEDED
                        for old_evidence in case.evidence:
                            if old_evidence.source_document_id == old.id:
                                old_evidence.superseded = True
            translation_for_document_id: str | None = None
            translation_target_document: Document | None = None
            if kind == "certified_translation":
                translation_target = facts.pop("translation_for_filename", None)
                if translation_target is not None:
                    target_filename = str(translation_target[0])
                    target = next(
                        (
                            item
                            for item in case.documents
                            if item.filename == target_filename
                            and item.status != DocumentStatus.SUPERSEDED
                        ),
                        None,
                    )
                    if target is not None:
                        translation_for_document_id = target.id
                        translation_target_document = target
            document = Document(
                id=document_id,
                filename=path.name,
                kind=kind,
                sha256=digest,
                mime_type="application/pdf",
                status=(
                    DocumentStatus.NEEDS_CERTIFIED_TRANSLATION
                    if language not in {"en", "cy"}
                    else DocumentStatus.ACCEPTED_FOR_REVIEW
                ),
                source_event_id=event.id,
                path=str(path),
                language=language,
                page_count=page_count,
                supersedes_document_id=supersedes_document_id,
                translation_for_document_id=translation_for_document_id,
            )
            case.documents.append(document)
            if translation_target_document is not None:
                translation_target_document.status = DocumentStatus.ACCEPTED_FOR_REVIEW
            existing_hashes.add(digest)
            for key, (value, page, excerpt) in facts.items():
                for prior_evidence in case.active_evidence(key):
                    if prior_evidence.source_document_id == document.supersedes_document_id:
                        prior_evidence.superseded = True
                case.evidence.append(
                    Evidence(
                        id=stable_id("ev", f"{document_id}:{key}:{value}"),
                        fact_key=key,
                        value=value,
                        source_event_id=event.id,
                        source_document_id=document.id,
                        source_excerpt=excerpt,
                        page=page,
                        extraction_method="deterministic_pdf_fixture_extractor",
                        model_version="none",
                        confidence=1.0,
                        provenance_state=ProvenanceState.DEMO_SYNTHETIC,
                    )
                )

    def _record_unreadable_document(
        self,
        case: Case,
        event: InboundEvent,
        path: Path,
        document_id: str,
        digest: str,
    ) -> None:
        case.documents.append(
            Document(
                id=document_id,
                filename=path.name or "unnamed.pdf",
                kind="unknown",
                sha256=digest,
                mime_type="application/pdf",
                status=DocumentStatus.NEEDS_REPLACEMENT,
                source_event_id=event.id,
                path=str(path),
            )
        )
        issue_code = f"UNREADABLE_DOCUMENT_{document_id}"
        if not any(issue.code == issue_code for issue in case.issues):
            case.issues.append(
                Issue(
                    id=stable_id("issue", issue_code),
                    code=issue_code,
                    title="Document could not be read",
                    detail=f"Please replace {path.name or 'the unnamed PDF'} with a readable PDF.",
                    severity=IssueSeverity.BLOCKER,
                    related_document_ids=[document_id],
                )
            )
