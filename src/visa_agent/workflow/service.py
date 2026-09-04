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

from visa_agent.documents.natural import DocumentReader, read_fixture_pdf
from visa_agent.documents.processor import sha256_file
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
from visa_agent.domain.rules import (
    advance_stage,
    evaluate_gate,
    required_profile_facts,
    run_consistency_checks,
)
from visa_agent.llm.guarded import ensure_guarded
from visa_agent.llm.ports import LLMClient
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, preparation_guidance
from visa_agent.workflow.conversation import (
    clear_natural_confirmation,
    confirmation_has_caveat,
    customer_requests_next_step,
    latest_reply_text,
    next_fact_questions,
    summary_fingerprint,
    update_deferred_questions,
    waiting_acknowledgement,
)
from visa_agent.workflow.customer_questions import grounded_customer_answers

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
        re.sub(r"\s+", " ", line).strip().casefold() for line in body.splitlines() if line.strip()
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
        document_reader: DocumentReader | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.llm = ensure_guarded(llm)
        self.today_provider = today_provider
        self.document_reader = document_reader or read_fixture_pdf

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
                    held_event=event if reason_code != "THREAD_SENDER_MISMATCH" else None,
                )
                return case, False, plan

        prior_outbox = [row for row in self.store.list_outbox() if row["case_id"] == case.id]
        sent_events = {row["event_id"] for row in prior_outbox if row["status"] == "SENT"}
        # Existing deployments have the last question set but no event ledger yet.
        # Associate it only with the matching, actually sent last reply, not arbitrary history.
        if not case.question_event_ids and case.outbound_message_ids:
            last_reply = next((row for row in prior_outbox
                               if stable_id("message", f"{row['event_id']}:{row['message_type']}")
                               == case.outbound_message_ids[-1] and row["status"] == "SENT"), None)
            if last_reply:
                case.question_event_ids = {field: [last_reply["event_id"]] for field in case.last_requested_fields}
        previously_asked = [field for field, event_ids in case.question_event_ids.items()
                            if any(event_id in sent_events for event_id in event_ids)]
        prior_pending = [field for field in previously_asked
                         if field not in case.deferred_fields and hasattr(case.profile, field)
                         and (getattr(case.profile, field) is None
                              or (field == "route_confirmed_standard_visitor" and not getattr(case.profile, field)))]
        case.question_plan = None
        case.pending_question_fields = []

        # Quoted messages are history, never a new instruction or fresh consent.
        # Recover a previously missed deferral from the saved latest customer turn.
        # This supports existing cases after a parser fix without replaying an event or email.
        update_deferred_questions(case, case.latest_customer_message)
        customer_event = event.model_copy(
            update={
                "body": latest_reply_text(event.body),
                "requested_fields": [field for field in (case.last_requested_fields or prior_pending)
                                     if field not in case.deferred_fields],
                "known_profile": case.profile.model_dump(mode="json"),
            }
        )
        case.latest_customer_message = customer_event.body
        case.latest_document_names = [Path(path).name for path in event.attachment_paths]
        if re.search(r"[\u4e00-\u9fff]", customer_event.body):
            case.customer_language = "zh"
        elif len(re.findall(r"[A-Za-z]+", customer_event.body)) > 4:
            case.customer_language = "en"
        prior_profile = summary_fingerprint(case, include_documents=False)
        prior_confirmation = case.confirmation_fingerprint
        prior_kind = case.confirmation_kind
        request_delivered = case.primary_channel != "gmail" or any(
            row["event_id"] == case.confirmation_request_event_id and row["status"] == "SENT"
            for row in self.store.list_outbox()
            if row["case_id"] == case.id
        )
        patch = self.llm.extract_case_patch(customer_event)
        was_paused = case.preparation_paused
        case.latest_preparation_action = None
        if patch.preparation_intent is not None:
            paused = patch.preparation_intent.action == "pause"
            if paused != case.preparation_paused:
                case.preparation_paused = paused
                case.preparation_control_epoch += 1
                case.preparation_control_event_id = event.id
                case.latest_preparation_action = patch.preparation_intent.action
                case.profile_confirmed = False
                case.final_summary_confirmed = False
                case.confirmation_fingerprint = None
                case.confirmation_kind = None
                case.confirmation_request_event_id = None
        # A restart of preparation is not consent to a previously sent summary.
        may_confirm = not was_paused and not case.preparation_paused
        case.proactive_guidance_offered = False
        case.customer_question_topics = [item.topic for item in patch.customer_questions]
        case.customer_question_exclusions = [item.source_excerpt for item in patch.customer_questions
                                             if item.topic in {"off_topic", "unsupported"}]
        case.customer_answers = grounded_customer_answers(
            customer_event.body, case.customer_language, self.today_provider(),
            sent_application_guidance=case.guidance_events.get("application_overview_v1") in sent_events,
            semantic_questions=patch.customer_questions,
            include_unsupported=not patch.requires_human_review,
        )
        if patch.requires_human_review:
            case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
            advance_stage(case, WorkflowStage.HUMAN_REVIEW_REQUIRED)
            case.human_review_reason = (
                "; ".join(patch.ambiguities) or "Bounded extractor requested human review."
            )
        case.latest_changes = {
            update.field: str(update.value)
            for update in patch.updates
            if customer_event.known_profile.get(update.field) is not None
            and customer_event.known_profile.get(update.field) != update.value
        }
        case.latest_received_facts = {
            update.field: str(update.value) for update in patch.updates
            if customer_event.known_profile.get(update.field) is None
        }
        # Preserve the existing deterministic date fallback even when the model finds
        # an unrelated question but omits the customer's separate date uncertainty.
        update_deferred_questions(case, customer_event.body)
        if (set(case.customer_question_topics) == {"off_topic"}
                and not patch.updates and not patch.question_deferrals and not event.attachment_paths
                and patch.preparation_intent is None and not case.preparation_paused
                and not case.latest_deferred_fields
                and not has_explicit_confirmation_line(
                    customer_event.body, PROFILE_CONFIRMATION_LINES | FINAL_CONFIRMATION_LINES,
                )
                and not clear_natural_confirmation(customer_event.body)
                and not customer_requests_next_step(customer_event.body)
                and case.status == CaseStatus.DRAFT and not case.open_blockers()
                and not self.store.has_unreviewed_held_updates(case.id)):
            # A scope-only exchange neither requests a new summary nor invalidates an
            # unchanged summary already sent to the customer. It has no gate authority.
            case.question_plan = []
            case.pending_question_fields = prior_pending
            case.last_requested_fields = []
            case.latest_deferred_fields = []
            case.last_inbound_received_at = event.received_at
            case.updated_at = datetime.now(UTC)
            message = self.llm.render_message(case, "blocked")
            message_id = stable_id("message", f"{event.id}:blocked")
            if message_id not in case.outbound_message_ids:
                case.outbound_message_ids.append(message_id)
            self.store.commit_event(case, event, "blocked", message)
            return case, False, "blocked"
        self._apply_patch(case, customer_event, patch.model_dump()["updates"])
        update_deferred_questions(case, customer_event.body)
        # Model intent may pause an unanswered question, never mutate a fact or release gate.
        for deferral in patch.question_deferrals:
            if getattr(case.profile, deferral.field) is None:
                if deferral.field not in case.deferred_fields:
                    case.deferred_fields.append(deferral.field)
                if deferral.field not in case.latest_deferred_fields:
                    case.latest_deferred_fields.append(deferral.field)
        self._ingest_attachments(case, event)
        profile_changed = prior_profile != summary_fingerprint(case, include_documents=False)
        if profile_changed:
            case.profile_confirmed = False
        case.final_summary_confirmed = False
        natural_confirmation = clear_natural_confirmation(customer_event.body)
        unchanged_summary = prior_confirmation == summary_fingerprint(
            case, include_documents=prior_kind == "final"
        )
        contextual_confirmation = bool(
            prior_confirmation
            and unchanged_summary
            and request_delivered
            and may_confirm
            and case.status != CaseStatus.HUMAN_REVIEW_REQUIRED
        )
        if (
            (
                has_explicit_confirmation_line(customer_event.body, PROFILE_CONFIRMATION_LINES)
                and not confirmation_has_caveat(customer_event.body)
            )
            or (contextual_confirmation and prior_kind == "profile" and natural_confirmation)
        ) and may_confirm and case.status != CaseStatus.HUMAN_REVIEW_REQUIRED:
            case.profile_confirmed = True
            for evidence in case.evidence:
                if evidence.source_document_id is None:
                    evidence.confirmed = True
        if (
            contextual_confirmation
            and prior_kind == "final"
            and (
                natural_confirmation
                or (
                    has_explicit_confirmation_line(customer_event.body, FINAL_CONFIRMATION_LINES)
                    and not confirmation_has_caveat(customer_event.body)
                )
            )
        ):
            case.final_summary_confirmed = True

        run_consistency_checks(case)
        case.last_inbound_received_at = event.received_at
        case.updated_at = datetime.now(UTC)
        gate = evaluate_gate(case, self.policy, self.today_provider())
        if self.store.has_unreviewed_held_updates(case.id, completing_event_id=event.id):
            gate.allowed = False
            gate.checks["all_held_updates_reviewed"] = False
            gate.reasons.append("Retained applicant updates still require review before finalization")
            case.customer_answers.append(
                "你之前补充的信息还有部分待复核，暂时不能定稿；已经收到的内容不用重新发送。"
                if case.customer_language == "zh" else
                "Some of your retained updates still need review before we can finalise the pack; you do not need to resend them."
            )
        failed_checks = {key for key, passed in gate.checks.items() if not passed}
        if case.status != CaseStatus.HUMAN_REVIEW_REQUIRED:
            if gate.allowed:
                advance_stage(case, WorkflowStage.READY_FOR_HUMAN_REVIEW)
            elif case.open_blockers():
                case.stage = WorkflowStage.DOCUMENT_REVIEW
            elif failed_checks == {"applicant_explicitly_confirmed_final_summary"}:
                case.stage = WorkflowStage.FINAL_CONFIRMATION
            elif not gate.checks["required_profile_facts_complete"]:
                case.stage = WorkflowStage.INTAKE
            elif not case.profile_confirmed:
                case.stage = WorkflowStage.PROFILE_CONFIRMATION
            else:
                case.stage = WorkflowStage.COLLECTING_DOCUMENTS
        plan = "ready" if gate.allowed else "blocked"
        if failed_checks == {"applicant_explicitly_confirmed_final_summary"}:
            plan = "awaiting_confirmation"
        elif (
            case.status != CaseStatus.HUMAN_REVIEW_REQUIRED
            and not case.preparation_paused
            and gate.checks["required_profile_facts_complete"]
            and gate.checks["route_in_scope"]
            and not case.open_blockers()
            and not case.profile_confirmed
        ):
            plan = "awaiting_profile_confirmation"
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        if plan in {"awaiting_confirmation", "awaiting_profile_confirmation"}:
            case.confirmation_kind = "final" if plan == "awaiting_confirmation" else "profile"
            case.confirmation_fingerprint = summary_fingerprint(
                case, include_documents=case.confirmation_kind == "final"
            )
            case.confirmation_request_event_id = event.id
        if plan == "blocked" and not case.preparation_paused and not waiting_acknowledgement(case):
            sent_topics = {topic for topic, source_event in case.guidance_events.items()
                           if source_event in sent_events}
            guidance = preparation_guidance(case, self.today_provider(), sent_topics)
            case.proactive_guidance_offered = bool(guidance)
            for topic, answer in guidance:
                case.customer_answers.append(answer)
                case.guidance_events[topic] = event.id
            if any(APPLICATION_URL in answer for answer in case.customer_answers):
                case.guidance_events["application_overview_v1"] = event.id
        if plan == "blocked":
            candidates = next_fact_questions(case)
            case.pending_question_fields = [field for field in prior_pending
                if field in required_profile_facts(case) and field not in case.deferred_fields and (
                    getattr(case.profile, field) is None
                    or (field == "route_confirmed_standard_visitor" and not getattr(case.profile, field)))]
            answered_fields = set(case.latest_received_facts) | set(case.latest_changes)
            if case.preparation_paused or waiting_acknowledgement(case):
                # This renderer emits only a receipt. Never record unseen candidate questions
                # against that receipt's SENT event, even when an older draft was never sent.
                case.question_plan = []
            elif ("off_topic" in case.customer_question_topics and not answered_fields
                    and not case.latest_document_names and not case.open_blockers()
                    and not customer_requests_next_step(customer_event.body)):
                # A scope reply is not permission to start or restart intake questions.
                case.question_plan = []
            elif case.pending_question_fields and (
                customer_requests_next_step(customer_event.body) or case.latest_preparation_action == "resume"
            ):
                case.question_plan = candidates[:1]
            elif case.pending_question_fields and not answered_fields.intersection(prior_pending):
                # Address a correction/question/later-reply instead of repeating the unanswered form.
                case.question_plan = []
            else:
                case.question_plan = candidates
            case.last_requested_fields = next_fact_questions(case)
            for field in case.last_requested_fields:
                delivered_ids = [value for value in case.question_event_ids.get(field, []) if value in sent_events]
                case.question_event_ids[field] = list(dict.fromkeys(delivered_ids[-1:] + [event.id]))
        else:
            case.last_requested_fields = []
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
                if old.source_document_id is None:
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
                    provenance_state=(
                        ProvenanceState.DEMO_SYNTHETIC
                        if event.channel.endswith("fixture")
                        else ProvenanceState.EXTRACTED_UNVERIFIED
                    ),
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
                inspection = self.document_reader(path)
                kind, language, page_count, facts = (
                    inspection.kind,
                    inspection.language,
                    inspection.page_count,
                    inspection.facts,
                )
            except (OSError, ValueError, PdfReadError):
                self._record_unreadable_document(case, event, path, document_id, digest)
                existing_hashes.add(digest)
                continue
            supersedes_document_id: str | None = None
            if kind == "conference_invitation" and not inspection.requires_review:
                for old in case.documents:
                    if old.kind == kind and old.status == DocumentStatus.ACCEPTED_FOR_REVIEW:
                        supersedes_document_id = old.id
                        old.status = DocumentStatus.SUPERSEDED
                        for old_evidence in case.evidence:
                            if old_evidence.source_document_id == old.id:
                                old_evidence.superseded = True
            translation_for_document_id: str | None = None
            translation_target_document: Document | None = None
            if kind == "certified_translation" and not inspection.requires_review:
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
            if kind == "unknown" or inspection.requires_review:
                document.status = DocumentStatus.HUMAN_REVIEW_REQUIRED
                case.issues.append(
                    Issue(
                        id=stable_id("issue", f"unclassified:{document_id}"),
                        code=f"UNCLASSIFIED_DOCUMENT_{document_id}",
                        title="Document needs manual classification",
                        detail=f"The content of {path.name} requires review before it can satisfy a requirement. {inspection.review_reason or 'A human must identify and verify this document.'}",
                        severity=IssueSeverity.BLOCKER,
                        related_document_ids=[document_id],
                    )
                )
            if translation_target_document is not None:
                translation_target_document.status = DocumentStatus.ACCEPTED_FOR_REVIEW
            existing_hashes.add(digest)
            for key, (value, page, excerpt) in facts.items():
                for prior_evidence in case.active_evidence(key):
                    if (
                        document.supersedes_document_id is not None
                        and prior_evidence.source_document_id == document.supersedes_document_id
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
                        extraction_method=inspection.method,
                        model_version=inspection.model_version,
                        confidence=inspection.confidence,
                        provenance_state=(
                            ProvenanceState.DEMO_SYNTHETIC
                            if inspection.method == "deterministic_pdf_fixture_extractor"
                            else ProvenanceState.EXTRACTED_UNVERIFIED
                        ),
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
