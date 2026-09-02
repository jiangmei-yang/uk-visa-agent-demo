from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from visa_agent.documents.processor import inspect_pdf, sha256_file
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    InboundEvent,
    ProvenanceState,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import advance_stage, evaluate_gate, run_consistency_checks
from visa_agent.llm.ports import LLMClient
from visa_agent.storage.sqlite import SQLiteStore


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, value).hex[:12]}"


class WorkflowService:
    def __init__(self, store: SQLiteStore, policy: Policy, llm: LLMClient) -> None:
        self.store = store
        self.policy = policy
        self.llm = llm

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
                email_thread_id=event.external_thread_id,
                applicant_email=event.sender,
                policy_version=self.policy.version,
            )

        patch = self.llm.extract_case_patch(event)
        if patch.requires_human_review:
            case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
            advance_stage(case, WorkflowStage.HUMAN_REVIEW_REQUIRED)
            case.human_review_reason = "Bounded extractor requested human review."
        self._apply_patch(case, event, patch.model_dump()["updates"])
        self._ingest_attachments(case, event)

        if "PROFILE CONFIRMED" in event.body.upper():
            case.profile_confirmed = True
            advance_stage(case, WorkflowStage.COLLECTING_DOCUMENTS)
            for evidence in case.evidence:
                if evidence.source_document_id is None:
                    evidence.confirmed = True
        if "I CONFIRM THE FINAL SUMMARY" in event.body.upper():
            case.final_summary_confirmed = True
            advance_stage(case, WorkflowStage.FINAL_CONFIRMATION)

        run_consistency_checks(case)
        case.updated_at = datetime.now(UTC)
        gate = evaluate_gate(case, self.policy, date.today())
        if case.status != CaseStatus.HUMAN_REVIEW_REQUIRED:
            if gate.allowed:
                advance_stage(case, WorkflowStage.READY_FOR_HUMAN_REVIEW)
            elif case.open_blockers():
                advance_stage(case, WorkflowStage.DOCUMENT_REVIEW)
            elif not case.final_summary_confirmed:
                advance_stage(case, WorkflowStage.FINAL_CONFIRMATION)
        plan = "ready" if gate.allowed else (
            "blocked" if case.open_blockers() else "awaiting_confirmation"
        )
        message = self.llm.render_message(case, plan)
        message_id = stable_id("message", f"{event.id}:{plan}")
        if message_id not in case.outbound_message_ids:
            case.outbound_message_ids.append(message_id)
        self.store.commit_event(case, event.id, plan, message)
        return case, False, plan

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
            digest = sha256_file(path)
            if digest in existing_hashes:
                continue
            kind, language, page_count, facts = inspect_pdf(path)
            document_id = stable_id("doc", digest)
            if kind == "conference_invitation":
                for old in case.documents:
                    if old.kind == kind and old.status == DocumentStatus.ACCEPTED_FOR_REVIEW:
                        old.status = DocumentStatus.SUPERSEDED
                        for old_evidence in case.evidence:
                            if old_evidence.source_document_id == old.id:
                                old_evidence.superseded = True
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
            )
            case.documents.append(document)
            existing_hashes.add(digest)
            for key, (value, page, excerpt) in facts.items():
                for prior_evidence in case.active_evidence(key):
                    if (
                        prior_evidence.source_document_id
                        and prior_evidence.source_document_id != document.id
                    ):
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
