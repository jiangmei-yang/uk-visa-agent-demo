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
from visa_agent.domain.application_records import RECORD_KINDS
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    InboundEvent,
    Issue,
    IssueSeverity,
    NextStepAdvice,
    ProvenanceState,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import (
    advance_stage,
    evaluate_gate,
    profile_fact_complete,
    required_profile_facts,
    run_consistency_checks,
)
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.domain.sponsor_location_review import sponsor_location_binding
from visa_agent.llm.guarded import ensure_guarded, validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate, LLMClient
from visa_agent.privacy.consent import ConsentLedger, ProcessingConsentRequired
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.advice_continuation import (
    continue_advice,
    has_advice_continuation_request,
    is_advice_continuation,
    reconcile_answered_advice,
    remember_advice_plan,
)
from visa_agent.workflow.advice_preferences import (
    remember_reply_style,
    reply_style_only,
    wants_one_action,
)
from visa_agent.workflow.advice_queue import merge_unsent_advice, queue_advice
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, preparation_guidance
from visa_agent.workflow.consultant_overview import (
    comprehensive_case_overview,
    comprehensive_overview_requested,
)
from visa_agent.workflow.conversation import (
    QUESTION_TEXT_EN,
    QUESTION_TEXT_ZH,
    clear_natural_confirmation,
    confirmation_has_caveat,
    consultation_only_requested,
    customer_requests_next_step,
    document_list_requested,
    explicitly_requested_profile_question,
    latest_reply_text,
    next_fact_questions,
    quiet_preparation_resume,
    summary_fingerprint,
    update_deferred_questions,
    waiting_acknowledgement,
)
from visa_agent.workflow.customer_questions import (
    _active_clauses,
    _next_step_targets_current_case,
    capped_answer_plan,
    grounded_customer_answer_plan,
)
from visa_agent.workflow.document_preparation import (
    SCHOOL_RECORD_TOPIC,
    school_record_followup,
    school_record_guidance,
    school_record_resolved,
    school_record_unavailable,
    sent_school_record_context,
)
from visa_agent.workflow.explicit_answer_scope import explicit_personal_sponsor_replacement
from visa_agent.workflow.next_step import select_next_step
from visa_agent.workflow.pending_step_value import (
    pending_question_reminder,
    pending_question_support_action,
)
from visa_agent.workflow.record_collection_plan import (
    collection_question_text,
    contextual_collection_declaration,
    contextual_record_field_deferral,
    plan_collection_follow_up,
)
from visa_agent.workflow.record_intake import plan_record_intake, record_intake_receipt
from visa_agent.workflow.record_source_audit import audit_application_record_sources
from visa_agent.workflow.sponsor_location import pending_location_dimension, record_sponsor_location

PROFILE_CONFIRMATION_LINES = {
    "profile confirmed",
    "i confirm the profile summary",
    "我确认上述个人资料",
    "我确认个人资料摘要",
}


def _without_repeated_source_lines(answer: str, existing: list[str]) -> str:
    """Avoid repeating a source already present in this same customer reply."""
    seen = {
        url.rstrip(".,;:，。；：").split("#", 1)[0]
        for url in re.findall(r"https?://\S+", "\n".join(existing))
    }
    return "\n".join(
        line for line in answer.splitlines()
        if not (
            (match := re.fullmatch(r"\s*GOV\.UK:\s*(https?://\S+)\s*", line))
            and match[1].rstrip(".,;:，。；：").split("#", 1)[0] in seen
        )
    ).strip()


def _source_repeat_requested(body: str) -> bool:
    """Keep links when the customer explicitly asks us to send them again."""
    current = latest_reply_text(body)
    return bool(re.search(
        r"(?:链接|网址|官网|网页).{0,12}(?:再发|重发|再给|再来一次)|"
        r"(?:再发|重发|再给).{0,12}(?:链接|网址|官网|网页)|"
        r"\b(?:send|share|give).{0,24}(?:link|website|page).{0,12}\bagain\b|"
        r"\b(?:send|share|give).{0,12}\bagain\b.{0,24}(?:link|website|page)",
        current,
        re.I,
    ))


def _without_previously_sent_source_lines(
    answer: str,
    prior_outbox: list[dict[str, Any]],
    current_message: str,
) -> str:
    """Do not paste the same long source URL into every proactive email.

    The reviewed guidance remains source-bound in code and its topic ledger. This
    only removes a standalone display line after that exact source reached a SENT
    reply. Direct requests to resend a source always win.
    """
    if _source_repeat_requested(current_message):
        return answer
    seen = {
        match.rstrip(".,;:，。；：").split("#", 1)[0]
        for row in prior_outbox
        if row.get("status") == "SENT"
        for match in re.findall(r"https?://\S+", str(row.get("payload", "")))
    }
    return "\n".join(
        line for line in answer.splitlines()
        if not (
            (match := re.fullmatch(r"\s*GOV\.UK:\s*(https?://\S+)\s*", line))
            and match[1].rstrip(".,;:，。；：").split("#", 1)[0] in seen
        )
    ).strip()


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


def _sent_before_inbound(sent_at: Any, received_at: datetime) -> bool:
    """Missing/ambiguous transport times cannot authorize a contextual answer."""
    if not isinstance(sent_at, str):
        return False
    try:
        sent = datetime.fromisoformat(sent_at)
    except ValueError:
        return False
    if sent.utcoffset() is None or received_at.utcoffset() is None:
        return False
    return sent < received_at


def _sponsor_identity(case: Case) -> str | None:
    profile = case.profile
    if profile.funding_source != "personal_sponsor" or not profile.sponsor_name or not profile.sponsor_relationship:
        return None
    return stable_id("sponsor", repr((profile.sponsor_name, profile.sponsor_relationship)))


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

        # Privacy controls precede ordinary finalized/review rejection and any
        # model, attachment reader or full-body held-event persistence.
        consent = ConsentLedger(self.store)
        decision = consent.handle(event, policy_version=self.policy.version)
        if decision.action != "allow":
            controlled_case = self.store.get_case(decision.case_id)
            if controlled_case is None:
                raise RuntimeError("Consent control has no case")
            return controlled_case, False, (
                "processing_notice" if decision.action == "defer" else "processing_receipt"
            )

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

        processing_epoch = self._require_processing(case)
        prior_outbox = [row for row in self.store.list_outbox() if row["case_id"] == case.id]
        reconcile_answered_advice(case, prior_outbox)
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
                         and not profile_fact_complete(case, field)]
        prior_last_requested = list(case.last_requested_fields)
        latest_sent_row = next((row for row in reversed(prior_outbox) if row["status"] == "SENT"), None)
        latest_sent_payload = latest_sent_row["payload"] if latest_sent_row is not None else ""
        pending_reminder_in_latest_reply = any(
            marker in latest_sent_payload
            for marker in (
                "上一封邮件里的那项信息仍待补",
                "item from my previous email is still open",
                "我先不重复提问",
                "I will not repeat the question here",
            )
        )
        case.question_plan = None
        case.pending_question_fields = []
        case.next_step_advice = None

        # Quoted messages are history, never a new instruction or fresh consent.
        # Recover a previously missed deferral from the saved latest customer turn.
        # This supports existing cases after a parser fix without replaying an event or email.
        update_deferred_questions(case, case.latest_customer_message)
        customer_event = event.model_copy(
            update={
                "body": (decision.business_body if decision.business_body is not None
                         else latest_reply_text(event.body)),
                "requested_fields": [field for field in prior_pending
                                     if field not in case.deferred_fields],
                "known_profile": {
                    **case.profile.model_dump(mode="json"),
                    # Conversation-control context only. It is not an applicant
                    # fact and cannot be written back into the CaseProfile.
                    "_preparation_paused": case.preparation_paused,
                },
            }
        )
        duration_question = None
        if (prior_last_requested == ["current_address_duration"]
                and case.profile.current_address
                and case.residence_duration_question_address == case.profile.current_address):
            matches = [row for row in prior_outbox if row["status"] == "SENT"
                       and row == latest_sent_row
                       and _sent_before_inbound(row.get("sent_at"), event.received_at)
                       and row["event_id"] in case.question_event_ids.get("current_address_duration", [])[-1:]
                       and row["recipient"] == case.applicant_contact
                       and row["external_thread_id"] == case.external_thread_id
                       and ("About how long have you lived at your current address?" in row["payload"]
                            or "你在现在的住址大概住了多久？" in row["payload"])]
            if len(matches) == 1 and matches[0]["payload"] == latest_sent_payload:
                duration_question = matches[0]
        customer_event.known_profile["_residence_duration_question_verified"] = duration_question is not None
        sponsor_question = None
        sponsor_identity = _sponsor_identity(case)
        if (prior_last_requested == ["sponsor_address"] and sponsor_identity
                and case.sponsor_address_question_identity == sponsor_identity):
            matches = [row for row in prior_outbox if row["status"] == "SENT"
                       and row == latest_sent_row
                       and _sent_before_inbound(row.get("sent_at"), event.received_at)
                       and row["event_id"] in case.question_event_ids.get("sponsor_address", [])[-1:]
                       and row["recipient"] == case.applicant_contact
                       and row["external_thread_id"] == case.external_thread_id
                       and any(text in row["payload"] for text in
                               (QUESTION_TEXT_EN["sponsor_address"], QUESTION_TEXT_ZH["sponsor_address"]))]
            if len(matches) == 1 and matches[0]["payload"] == latest_sent_payload:
                sponsor_question = matches[0]
        customer_event.known_profile["_sponsor_address_question_verified"] = sponsor_question is not None
        from visa_agent.workflow.conversation import _profile_question_text

        location_dimension = None
        if (prior_last_requested == ["sponsor_is_in_uk"] and latest_sent_row is not None
                and case.sponsor_location_question_binding == sponsor_location_binding(case)
                and latest_sent_row["event_id"] in case.question_event_ids.get("sponsor_is_in_uk", [])[-1:]
                and latest_sent_row["recipient"] == case.applicant_contact
                and latest_sent_row["external_thread_id"] == case.external_thread_id
                and _sent_before_inbound(latest_sent_row.get("sent_at"), event.received_at)
                and _profile_question_text(case, "sponsor_is_in_uk") in latest_sent_payload):
            location_dimension = pending_location_dimension(case)
        employer_question = None
        employer_field = prior_last_requested[0] if len(prior_last_requested) == 1 else ""
        employer_context = {"field": employer_field, "employer_name": case.profile.employer_name or ""}
        if (employer_field in {"employer_name", "employer_address", "employer_phone"}
                and case.profile.occupation_status == "employed"
                and case.employer_question_context == employer_context
                and (employer_field == "employer_name" or case.profile.employer_name)):
            matches = [row for row in prior_outbox if row == latest_sent_row
                       and row["status"] == "SENT" and _sent_before_inbound(row.get("sent_at"), event.received_at)
                       and row["event_id"] in case.question_event_ids.get(employer_field, [])[-1:]
                       and row["recipient"] == case.applicant_contact
                       and row["external_thread_id"] == case.external_thread_id
                       and any(text in row["payload"] for text in
                               (QUESTION_TEXT_EN[employer_field], QUESTION_TEXT_ZH[employer_field]))]
            if len(matches) == 1:
                employer_question = matches[0]
        customer_event.known_profile["_employer_question_verified"] = employer_field if employer_question else None
        if employer_question is not None:
            customer_event.requested_fields = [employer_field]
        elif sponsor_question is not None:
            # Only this question was in the last actual reply. Older unanswered
            # fields remain in case memory, but must not compete as this turn's
            # requested extraction context for a short answer.
            customer_event.requested_fields = ["sponsor_address"]
        elif duration_question is not None:
            customer_event.requested_fields = ["current_address_duration"]
        case.latest_customer_message = customer_event.body
        if case.application_records is not None:
            customer_event.known_profile["_application_record_context"] = [
                {"kind": record.kind, "fields": {name: fact.value for name, fact in record.fields.items()}}
                for record in case.application_records.current().values()
            ]
            customer_event.known_profile["_application_collection_states"] = {
                kind: case.application_records.collection_state(kind) for kind in RECORD_KINDS
            }
            current_records = case.application_records.current()
            customer_event.known_profile["_application_deferred_details"] = [
                {"kind": current_records[item.record_id].kind, "field": item.field, "state": "unknown",
                 "record": {name: fact.value for name, fact in current_records[item.record_id].fields.items()}}
                for item in case.application_records.active_field_deferrals()
            ]
        remember_reply_style(case, event.id)
        if school_record_resolved(customer_event.body) or school_record_unavailable(customer_event.body):
            # Retire only this discussion, not applicant facts, evidence or other
            # unanswered FAQs. Receipt of an actual file still uses normal checks.
            case.guidance_events.pop(SCHOOL_RECORD_TOPIC, None)
            for prior_advice in [*case.unsent_advice, *case.pending_advice]:
                if prior_advice.topic == SCHOOL_RECORD_TOPIC:
                    prior_advice.deferred_by_event_id = event.id
        case.latest_document_names = [Path(path).name for path in event.attachment_paths]
        if re.search(r"[\u4e00-\u9fff]", customer_event.body):
            case.customer_language = "zh"
        elif len(re.findall(r"[A-Za-z]+", customer_event.body)) > 4:
            case.customer_language = "en"
        if not event.attachment_paths and is_advice_continuation(customer_event.body):
            # A pure consultation continuation has no fact, preparation-resume,
            # summary-confirmation or delivery authority. It needs no model guess.
            self.llm.last_extraction_fallback = False
            self.llm.last_extraction_error = None
            case.latest_changes = {}
            case.latest_received_facts = {}
            case.latest_deferred_fields = []
            case.latest_preparation_action = None
            case.proactive_guidance_offered = False
            case.customer_question_topics = ["advice_continuation"]
            case.customer_question_exclusions = []
            case.customer_answers = continue_advice(case, event.id, prior_outbox, self.today_provider())
            case.question_plan = []
            case.pending_question_fields = prior_pending
            case.last_requested_fields = []
            case.last_inbound_received_at = event.received_at
            case.updated_at = datetime.now(UTC)
            self._render_and_commit(case, event, "blocked", processing_epoch)
            return case, False, "blocked"
        prior_profile = summary_fingerprint(case, include_documents=False)
        prior_confirmation = case.confirmation_fingerprint
        prior_kind = case.confirmation_kind
        request_delivered = case.primary_channel != "gmail" or any(
            row["event_id"] == case.confirmation_request_event_id and row["status"] == "SENT"
            for row in self.store.list_outbox()
            if row["case_id"] == case.id
        )
        patch = self.llm.extract_case_patch(customer_event)
        literal_employer_fields: set[str] = set()
        if (not patch.requires_human_review and not patch.ambiguities
                and not getattr(self.llm, "last_extraction_fallback", False)):
            from visa_agent.domain.employer_evidence import literal_employer_details

            proposed_fields = {item.field for item in patch.updates}
            literal_updates = [FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=1)
                for field, value, excerpt in literal_employer_details(
                    customer_event.body, employer_field if employer_question is not None else None)
                if field not in proposed_fields]
            # The same outer ownership/type guards still apply. A rejected model
            # extraction or ambiguity is never silently cleared by this path.
            grounded = validate_case_patch(customer_event, CasePatch(updates=literal_updates, ambiguities=[]))
            if not grounded.requires_human_review and not grounded.ambiguities:
                patch.updates.extend(grounded.updates)
                literal_employer_fields = {item.field for item in grounded.updates}
        self._require_processing(case, processing_epoch)
        record_plan = plan_record_intake(
            customer_event, case.application_records, case_id=case.id,
            records=patch.application_records, declarations=patch.collection_declarations,
            contextual_declaration=contextual_collection_declaration(case, customer_event.body, prior_outbox),
            contextual_field_deferral=contextual_record_field_deferral(case, customer_event.body, event.id, prior_outbox),
        )
        if record_plan.changed:
            case.application_records = record_plan.ledger
        if record_plan.requires_review:
            patch.requires_human_review = True
            patch.ambiguities.append(record_plan.reason or "Application record statement needs review")
        continuation_requested = has_advice_continuation_request(customer_event.body)
        current_questions = [item for item in patch.customer_questions if not (
            continuation_requested and is_advice_continuation(item.source_excerpt)
        )]
        if reply_style_only(customer_event.body):
            current_questions = []
        if comprehensive_overview_requested(case, customer_event.body):
            # The model may call an obvious in-thread request for the complete
            # personal preparation list "unsupported" or "next_step".  Rescue
            # only the matching, tightly bounded clause; other questions keep
            # their original safety classification.
            current_questions = [
                item.model_copy(update={"topic": "document_checklist"})
                if item.topic in {"unsupported", "next_step", "off_topic"}
                and comprehensive_overview_requested(case, item.source_excerpt)
                else item
                for item in current_questions
            ]
        school_followup = (sent_school_record_context(case, prior_outbox)
                           and school_record_followup(customer_event.body)
                           and not any(item.topic == "off_topic" for item in current_questions))
        if school_followup:
            # Deterministic, source-bound continuation of an actually sent
            # discussion. Preserve the raw model patch; normal facts/files still
            # pass through their validators below. Do not invent an LLM proposal.
            current_questions = [item for item in current_questions if not (
                item.topic in {"unsupported", "document_checklist", "next_step"}
                and school_record_followup(item.source_excerpt)
            )]
        was_paused = case.preparation_paused
        case.latest_preparation_action = None
        if patch.preparation_intent is not None and not (
            decision.grant_business and patch.preparation_intent.action == "resume"
        ):
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
        may_confirm = not was_paused and not case.preparation_paused and not decision.grant_business
        case.proactive_guidance_offered = False
        case.customer_question_topics = [item.topic for item in current_questions]
        explicit_next_step_wording = bool(re.search(
            r"下一步|接下来|还缺|还需要什么|先准备哪|"
            r"\bnext step\b|\bwhat(?:'s| is) next\b|"
            r"\b(?:what|which).{0,24}(?:prepare|work on|do).{0,18}(?:next|now|first)\b|"
            r"\bwhat else (?:can|should) I (?:do|prepare)\b",
            latest_reply_text(customer_event.body),
            re.I,
        ))
        explicit_pending_resume = bool(re.search(
            r"(?:我现在)?(?:可以|能)?继续(?:问|提问)|请继续问|"
            r"\b(?:I(?:'m| am) )?ready to continue\b|\bcontinue asking\b|\bask me (?:the )?next\b",
            latest_reply_text(customer_event.body),
            re.I,
        ))
        resume_only = case.latest_preparation_action == "resume" and not explicit_next_step_wording
        deterministic_next_step = any(
            customer_requests_next_step(clause)
            and _next_step_targets_current_case(customer_event.body, clause)
            for clause in _active_clauses(customer_event.body, split_commas=False)
        ) and not quiet_preparation_resume(case) and not resume_only
        if deterministic_next_step and "next_step" not in case.customer_question_topics:
            # This is a pacing/request signal only. It creates no fact, evidence,
            # consent or route decision, but prevents a missed/narrow model excerpt
            # from turning a natural "what can I do without that detail?" into an
            # empty acknowledgement.
            case.customer_question_topics.append("next_step")
        if school_followup and "next_step" not in case.customer_question_topics:
            case.customer_question_topics.append("next_step")
        case.customer_question_exclusions = [item.source_excerpt for item in current_questions
                                             if item.topic in {"off_topic", "unsupported", "next_step"}]
        answer_plan = grounded_customer_answer_plan(
            customer_event.body, case.customer_language, self.today_provider(),
            sent_application_guidance=case.guidance_events.get("application_overview_v1") in sent_events,
            semantic_questions=current_questions,
            include_unsupported=not patch.requires_human_review,
            case=case,
        )
        case.customer_answers = list(answer_plan.answers)
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
                and not parse_sponsor_location_statements(customer_event.body, source_event_id=event.id)
                and location_dimension is None
                and not patch.updates and not patch.question_deferrals and not event.attachment_paths
                and not record_plan.changed and not record_plan.requires_review
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
            self._render_and_commit(case, event, "blocked", processing_epoch)
            return case, False, "blocked"
        self._apply_patch(case, customer_event, patch.model_dump()["updates"])
        record_sponsor_location(case, customer_event, verified_dimension=location_dimension,
            verified_question_event_id=latest_sent_row["event_id"] if location_dimension and latest_sent_row else None)
        for literal_fact_key in literal_employer_fields:
            for evidence in case.active_evidence(literal_fact_key):
                if evidence.source_event_id == event.id:
                    evidence.extraction_method = "bounded_literal_employer_parser"
        update_deferred_questions(case, customer_event.body)
        # Model intent may pause an unanswered question, never mutate a fact or release gate.
        duration_answer = customer_event.body.strip().rstrip("。.!！").casefold()
        if (employer_question is not None and case.employer_question_context == employer_context
                and getattr(case.profile, employer_field) is None
                and duration_answer in {"不知道", "暂时不知道", "不清楚", "不确定", "需要问一下",
                                        "i don't know", "i need to check", "not sure", "i'm not sure"}):
            if employer_field not in case.deferred_fields:
                case.deferred_fields.append(employer_field)
            case.latest_deferred_fields.append(employer_field)
            case.employer_detail_deferrals.append({"source_event_id": event.id,
                "source_excerpt": customer_event.body.strip(), "question_event_id": employer_question["event_id"],
                **employer_context})
        if (sponsor_question is not None and _sponsor_identity(case) == sponsor_identity
                and case.profile.sponsor_address is None
                and duration_answer in {"不知道", "暂时不知道", "不清楚", "不确定", "需要问一下",
                                        "i don't know", "i need to check", "not sure", "i'm not sure"}):
            if "sponsor_address" not in case.deferred_fields:
                case.deferred_fields.append("sponsor_address")
            case.latest_deferred_fields.append("sponsor_address")
            case.sponsor_address_deferrals.append({"source_event_id": event.id,
                "source_excerpt": customer_event.body.strip(), "question_event_id": sponsor_question["event_id"],
                "sponsor_identity": sponsor_identity or ""})
        if (duration_question is not None and case.profile.current_address_duration is None
                and duration_answer in {"不记得", "记不清", "暂时记不清", "不确定", "需要核实",
                                        "i don't remember", "i need to check", "not sure", "i'm not sure"}):
            if "current_address_duration" not in case.deferred_fields:
                case.deferred_fields.append("current_address_duration")
            case.latest_deferred_fields.append("current_address_duration")
            case.residence_duration_deferrals.append({"source_event_id": event.id,
                "source_excerpt": customer_event.body.strip(), "question_event_id": duration_question["event_id"],
                "address": case.profile.current_address or ""})
        for deferral in patch.question_deferrals:
            if getattr(case.profile, deferral.field) is None:
                if deferral.field not in case.deferred_fields:
                    case.deferred_fields.append(deferral.field)
                if deferral.field not in case.latest_deferred_fields:
                    case.latest_deferred_fields.append(deferral.field)
        self._ingest_attachments(case, event)
        full_overview = (
            case.status != CaseStatus.HUMAN_REVIEW_REQUIRED
            and comprehensive_overview_requested(case, customer_event.body)
        )
        if full_overview:
            # The current email may have supplied the profile facts that make a
            # personal overview possible. Reclassify only the same bounded
            # request after those validated facts have been applied.
            current_questions = [
                item.model_copy(update={"topic": "document_checklist"})
                if item.topic in {"unsupported", "next_step", "off_topic"}
                and comprehensive_overview_requested(case, item.source_excerpt)
                else item
                for item in current_questions
            ]
            case.customer_question_topics = [item.topic for item in current_questions]
            case.customer_question_exclusions = [
                item.source_excerpt for item in current_questions
                if item.topic in {"off_topic", "unsupported", "next_step"}
            ]
        # Compile once more against the updated case. This is deterministic and
        # ensures current-turn sponsor/location facts shape the actual answer.
        answer_plan = grounded_customer_answer_plan(
            customer_event.body, case.customer_language, self.today_provider(),
            sent_application_guidance=case.guidance_events.get("application_overview_v1") in sent_events,
            semantic_questions=current_questions,
            include_unsupported=not patch.requires_human_review,
            case=case,
        )
        case.customer_answers = list(answer_plan.answers)
        if full_overview and "personal_overview" not in answer_plan.selected_topics:
            # Compose after applying this event's validated facts, so a complete
            # first enquiry can be useful without treating the model as the author.
            overview = comprehensive_case_overview(
                case,
                self.today_provider(),
                include_first_action=not (
                    any(item.topic == "next_step" for item in current_questions)
                    or bool(set(answer_plan.selected_topics).intersection({
                        "application", "application_link", "route_orientation", "sponsor_support",
                    }))
                ),
            )
            answer_plan = capped_answer_plan(
                [("personal_overview", overview), *(
                    item for item in answer_plan.reviewed_answers
                    if item[0] not in {"application", "application_link"}
                )],
                case.customer_language,
            )
            case.customer_answers = list(answer_plan.answers)
        remember_advice_plan(case, event.id, customer_event.body, current_questions,
                             answer_plan, prior_outbox, self.today_provider())
        queue_advice(case, event.id, customer_event.body, current_questions, answer_plan,
                     application_guidance_event_id=(case.guidance_events.get("application_overview_v1")
                         if case.guidance_events.get("application_overview_v1") in sent_events else None))
        record_receipt = record_intake_receipt(record_plan, event.id, case.customer_language)
        if record_receipt:
            case.customer_answers.append(record_receipt)
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
                # Gmail exact wording has no more authority than a natural reply:
                # it must refer to the current profile summary actually sent.
                and (case.primary_channel != "gmail"
                     or (contextual_confirmation and prior_kind == "profile"))
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
        if gate.allowed and not audit_application_record_sources(self.store, case).registered_sources_match:
            gate.allowed = False
            gate.checks["application_record_sources_registered"] = False
            gate.reasons.append("Application record source registration requires review before finalization")
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
            elif (not gate.checks["required_profile_facts_complete"]
                  or not gate.checks["application_collections_explicitly_declared"]
                  or not gate.checks["application_record_descriptive_fields_complete"]
                  or not gate.checks["application_record_details_not_deferred"]):
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
            and gate.checks["application_collections_explicitly_declared"]
            and gate.checks["application_record_descriptive_fields_complete"]
            and gate.checks["application_record_details_not_deferred"]
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
        overview_already_has_action = any(
            marker in answer
            for answer in case.customer_answers
            for marker in ("最先做的一步：", "Your first practical step:")
        )
        if "next_step" in case.customer_question_topics and not overview_already_has_action:
            # Advice sees this event's validated facts/documents and current gate.
            # Asking for a step is not resume, profile consent or final consent.
            case.next_step_advice = select_next_step(
                case, self.policy, gate, today=self.today_provider(),
                school_record_context=sent_school_record_context(case, prior_outbox),
            )
            if (case.status == CaseStatus.DRAFT and not case.preparation_paused
                    and all(profile_fact_complete(case, field) or field in case.deferred_fields
                            for field in required_profile_facts(case))):
                collection_plan = plan_collection_follow_up(case.application_records, case_id=case.id)
                asked = frozenset(key for key, ids in case.collection_question_event_ids.items()
                                  if any(source in sent_events for source in ids))
                collection_question = collection_plan.next_unasked(asked)
                if collection_question is not None:
                    case.next_step_advice = NextStepAdvice(kind="question", message=collection_question_text(
                        collection_question, case.application_records, case.customer_language,
                    ))
                    case.collection_question_event_ids[collection_question.key] = [event.id]
            case.next_step_advice = case.next_step_advice.model_copy(update={
                "message": _without_repeated_source_lines(
                    _without_previously_sent_source_lines(
                        case.next_step_advice.message,
                        prior_outbox,
                        customer_event.body,
                    ),
                    case.customer_answers,
                ),
            })
            case.customer_answers.append(case.next_step_advice.message)
        extras = [answer for answer in case.customer_answers if answer not in answer_plan.answers]
        case.customer_answers = merge_unsent_advice(
            case, event.id, customer_event.body, answer_plan, prior_outbox, self.today_provider(),
        ) + extras
        if any(school_record_guidance(case.customer_language) in answer for answer in case.customer_answers):
            case.guidance_events[SCHOOL_RECORD_TOPIC] = event.id
        if (continuation_requested and not case.customer_answers and not case.customer_question_topics
                and case.status == CaseStatus.DRAFT):
            # Mixed facts/files still pass normal validation above. A separate
            # continuation question is answered after that work, not turned into
            # the next missing personal field. A fresh independent FAQ has priority.
            case.customer_answers = continue_advice(case, event.id, prior_outbox, self.today_provider())
            case.customer_question_topics = ["advice_continuation"]
        # Separate answers to the customer's current question from proactive
        # guidance below. Supplying one fact alongside a FAQ does not ask us to
        # resume the rest of the intake form.
        has_information_answer = any(answer != record_receipt for answer in case.customer_answers) or document_list_requested(case)
        actionable_preparation_guidance = False
        current_preparation_guidance: list[str] = []
        if plan == "blocked" and not case.preparation_paused and not waiting_acknowledgement(case):
            sent_topics = {topic for topic, source_event in case.guidance_events.items()
                           if source_event in sent_events}
            guidance = ([] if "personal_overview" in answer_plan.selected_topics else
                        preparation_guidance(case, self.today_provider(), sent_topics))
            case.proactive_guidance_offered = bool(guidance)
            sponsor_identity_guidance = any(
                topic in {
                    "personal_sponsor_preparation_v1",
                    "family_personal_sponsor_preparation_v1",
                }
                for topic, _ in guidance
            )
            actionable_preparation_guidance = any(
                topic not in {"application_overview_v1", "route_orientation_v1"}
                for topic, _ in guidance
            )
            if guidance and case.next_step_advice is not None and case.next_step_advice.kind == "question":
                # The concrete preparation guidance supplies the useful answer;
                # do not precede it with "first supply another missing detail".
                case.customer_answers = [answer for answer in case.customer_answers
                                         if answer != case.next_step_advice.message]
                if (
                    case.next_step_advice.question_field in {"full_name", "date_of_birth"}
                    or (
                        case.next_step_advice.question_field == "current_address"
                        and not case.profile.current_address
                    )
                ) and not explicitly_requested_profile_question(
                    customer_event.body,
                    case.next_step_advice.question_field,
                ):
                    # Administrative form fields still remain required, but a
                    # customer asking what to prepare should first receive the
                    # case-specific evidence action we have just selected.
                    case.next_step_advice = None
            for topic, answer in guidance:
                answer = _without_previously_sent_source_lines(
                    answer,
                    prior_outbox,
                    customer_event.body,
                )
                case.customer_answers.append(answer)
                current_preparation_guidance.append(answer)
                case.guidance_events[topic] = event.id
            if any(APPLICATION_URL in answer for answer in case.customer_answers):
                case.guidance_events["application_overview_v1"] = event.id
        if plan == "blocked":
            candidates = next_fact_questions(case)
            case.pending_question_fields = [field for field in prior_pending
                if field in required_profile_facts(case) and field not in case.deferred_fields
                and not profile_fact_complete(case, field)]
            answered_fields = set(case.latest_received_facts) | set(case.latest_changes)
            if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
                # The handoff renderer asks no intake question. Do not record a
                # hidden question against the SENT review reply or leave it as an
                # active pending field. Historical actually-sent question events
                # remain auditable in question_event_ids.
                case.question_plan = []
                case.pending_question_fields = []
            elif case.preparation_paused or waiting_acknowledgement(case):
                # This renderer emits only a receipt. Never record unseen candidate questions
                # against that receipt's SENT event, even when an older draft was never sent.
                case.question_plan = []
            elif consultation_only_requested(customer_event.body) or reply_style_only(customer_event.body):
                # Asking to understand the process first is not starting an
                # identity questionnaire. Keep every required fact/gate intact.
                case.question_plan = []
                if case.next_step_advice is not None and case.next_step_advice.kind == "question":
                    case.customer_answers = [answer for answer in case.customer_answers
                                             if answer != case.next_step_advice.message]
                    case.next_step_advice = None
            elif overview_already_has_action or (actionable_preparation_guidance and wants_one_action(customer_event.body)):
                # A comprehensive personal overview already names a practical
                # first action. Do not append a second, unrelated intake step.
                case.question_plan = []
            elif case.next_step_advice is not None:
                field = case.next_step_advice.question_field
                if (
                    field in case.pending_question_fields
                    and (field in prior_last_requested or pending_reminder_in_latest_reply)
                    and not answered_fields.intersection(prior_pending)
                    and not explicitly_requested_profile_question(customer_event.body, field)
                    and not explicit_pending_resume
                ):
                    # Repeated "what next" messages do not create a fresh copy
                    # of the question that was actually asked in the immediately
                    # preceding reply or advance past it.  If an intervening FAQ
                    # was answered without repeating the intake question,
                    # last_requested_fields is empty and an explicit request to
                    # proceed may recover the pending, contextual clarification.
                    case.customer_answers = [answer for answer in case.customer_answers
                                             if answer != case.next_step_advice.message]
                    if current_preparation_guidance:
                        case.customer_answers.append(pending_question_reminder(case))
                    else:
                        support_topic, support_answer = pending_question_support_action(
                            case,
                            field,
                            self.policy,
                            self.today_provider(),
                            {topic for topic, source_event in case.guidance_events.items()
                             if source_event in sent_events},
                        )
                        support_answer = _without_previously_sent_source_lines(
                            support_answer,
                            prior_outbox,
                            customer_event.body,
                        )
                        case.customer_answers.append(support_answer)
                        case.guidance_events[support_topic] = event.id
                    case.next_step_advice = None
                    case.question_plan = []
                else:
                    # A travel-date range is one main question, with both asked
                    # fields retained in the SENT ledger. Do not split it into two
                    # emails merely because the next-step selector names arrival.
                    paired_question = candidates in (
                        ["planned_arrival_date", "planned_departure_date"],
                        ["sponsor_relationship", "sponsor_name"],
                    )
                    case.question_plan = (candidates if paired_question
                        else [field] if field in candidates else [])
            elif actionable_preparation_guidance and case.latest_preparation_action != "resume":
                # Keep early route-shaping intake moving while still giving
                # useful consultant advice. Administrative form fields wait
                # behind the case-specific material action. Sponsor identity is
                # part of that action rather than an unrelated form field: asking
                # it here gives a natural short follow-up a real SENT-question
                # context without inferring that a host or relative is a sponsor.
                sponsor_identity_followup = (
                    sponsor_identity_guidance
                    and case.profile.funding_source == "personal_sponsor"
                    and candidates == ["sponsor_relationship", "sponsor_name"]
                )
                case.question_plan = (
                    candidates
                    if candidates and (
                        bool(answered_fields.intersection(prior_pending))
                        or sponsor_identity_followup
                        or candidates[0] in {
                            "nationality_country", "application_country", "occupation_status", "funding_source",
                        }
                    )
                    else []
                )
            elif quiet_preparation_resume(case):
                # A resume with "that's all for now" is a receipt, even when
                # a newly enforced completeness check finds another missing fact.
                case.question_plan = []
            elif ("off_topic" in case.customer_question_topics and not answered_fields
                    and not case.latest_document_names and not case.open_blockers()
                    and not customer_requests_next_step(customer_event.body)):
                # A scope reply is not permission to start or restart intake questions.
                case.question_plan = []
            elif (has_information_answer and not customer_requests_next_step(customer_event.body)
                    and case.latest_preparation_action != "resume"):
                # Answer the actual FAQ/checklist without repeating unanswered
                # intake questions or silently adding new ones. Persisted pending
                # fields remain available for a later explicit next step.
                case.question_plan = []
            elif ("current_address" in case.pending_question_fields
                    and any(update.field == "current_address" for update in patch.updates)):
                # A same-valued city reply is still an answer to our SENT address
                # question, not silence. Explain the missing residential detail
                # instead of telling the customer to wait for new plans.
                case.question_plan = ["current_address"]
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
            if "sponsor_is_in_uk" in case.last_requested_fields and pending_location_dimension(case) is not None:
                case.sponsor_location_question_binding = sponsor_location_binding(case)
            if "current_address_duration" in case.last_requested_fields:
                case.residence_duration_question_address = case.profile.current_address
            if "sponsor_address" in case.last_requested_fields:
                case.sponsor_address_question_identity = _sponsor_identity(case)
            for field in case.last_requested_fields:
                if field in {"employer_name", "employer_address", "employer_phone"}:
                    case.employer_question_context = {"field": field, "employer_name": case.profile.employer_name or ""}
            for field in case.last_requested_fields:
                delivered_ids = [value for value in case.question_event_ids.get(field, []) if value in sent_events]
                case.question_event_ids[field] = list(dict.fromkeys(delivered_ids[-1:] + [event.id]))
        else:
            case.last_requested_fields = []
        if (
            plan == "blocked" and case.status == CaseStatus.DRAFT
            and not case.preparation_paused and not waiting_acknowledgement(case)
            and not quiet_preparation_resume(case)
            and not consultation_only_requested(customer_event.body)
            and not reply_style_only(customer_event.body)
            and not case.last_requested_fields and case.next_step_advice is None
            and not has_information_answer and not actionable_preparation_guidance
            and "off_topic" not in case.customer_question_topics
            and (record_plan.changed or continuation_requested
                 or bool(set(case.latest_received_facts).intersection(prior_pending)))
            and all(profile_fact_complete(case, field) or field in case.deferred_fields
                    for field in required_profile_facts(case))
        ):
            collection_plan = plan_collection_follow_up(case.application_records, case_id=case.id)
            asked = frozenset(key for key, ids in case.collection_question_event_ids.items()
                              if any(source in sent_events for source in ids))
            follow_up = collection_plan.next_unasked(asked)
            if follow_up is not None:
                case.customer_answers.append(collection_question_text(
                    follow_up, case.application_records, case.customer_language,
                ))
                case.collection_question_event_ids[follow_up.key] = [event.id]
        self._render_and_commit(case, event, plan, processing_epoch)
        return case, False, plan

    def _require_processing(self, case: Case, expected_epoch: int | None = None) -> int:
        consent = ConsentLedger(self.store)
        consent.require(case)
        epoch = consent.epoch(case.id)
        if expected_epoch is not None and epoch != expected_epoch:
            raise ProcessingConsentRequired("Processing consent changed during applicant processing")
        return epoch

    def _render_and_commit(self, case: Case, event: InboundEvent, plan: str, processing_epoch: int) -> None:
        self._require_processing(case, processing_epoch)
        message = self.llm.render_message(case, plan)
        message_id = stable_id("message", f"{event.id}:{plan}")
        with self.store.atomic_write():
            self._require_processing(case, processing_epoch)
            if message_id not in case.outbound_message_ids:
                case.outbound_message_ids.append(message_id)
            self.store.commit_event(case, event, plan, message)
            ConsentLedger(self.store).mark_completed(event.id)

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
        update_fields = {str(update["field"]) for update in updates}
        prior_sponsor_identity = (case.profile.sponsor_name, case.profile.sponsor_relationship)
        prior_funding_source = case.profile.funding_source
        prior_home_address = case.profile.current_address
        prior_employer_name = case.profile.employer_name
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
        if ("current_address" in update_fields and prior_home_address != case.profile.current_address
                and "current_address_duration" not in update_fields):
            # Time at the former home is not evidence for the new address.
            case.profile.current_address_duration = None
            case.residence_duration_question_address = None
            case.deferred_fields = [field for field in case.deferred_fields if field != "current_address_duration"]
            for old in case.active_evidence("current_address_duration"):
                old.superseded = True
        employer_changed = ("employer_name" in update_fields and prior_employer_name is not None
                            and prior_employer_name != case.profile.employer_name)
        left_employment = "occupation_status" in update_fields and case.profile.occupation_status != "employed"
        if employer_changed:
            for field in ("employer_address", "employer_phone"):
                if field not in update_fields:
                    setattr(case.profile, field, None)
                    for old in case.active_evidence(field):
                        old.superseded = True
        if left_employment:
            for field in ("employer_name", "employer_address", "employer_phone"):
                setattr(case.profile, field, None)
                for old in case.active_evidence(field):
                    old.superseded = True
        if employer_changed or left_employment:
            case.employer_question_context = {}
            case.deferred_fields = [field for field in case.deferred_fields
                                    if field not in {"employer_name", "employer_address", "employer_phone"}]
            for document in case.documents:
                if (document.kind == "employment_letter"
                        and document.status == DocumentStatus.ACCEPTED_FOR_REVIEW
                        and document.source_event_id != event.id):
                    # A changed employment context does not prove an old letter
                    # is false; it means its current applicability is unverified.
                    # Keep the file and evidence rather than deleting history.
                    document.status = DocumentStatus.NEEDS_CLARIFICATION
                    case.employment_document_reviews.append({
                        "document_id": document.id, "source_event_id": event.id,
                        "reason": "employer_changed" if employer_changed else "occupation_changed",
                    })
        sponsor_replaced_without_complete_identity = (
            prior_funding_source == "personal_sponsor"
            and case.profile.funding_source == "personal_sponsor"
            and explicit_personal_sponsor_replacement(event.body)
        )
        if sponsor_replaced_without_complete_identity:
            # "Someone else will sponsor me instead" proves that the old
            # person is no longer the payer, but it does not prove the new
            # person's name, relationship or location. Keep any newly grounded
            # sponsor fields from this turn and retire only stale inherited ones.
            for field in ("sponsor_name", "sponsor_address", "sponsor_relationship", "sponsor_is_in_uk"):
                if field in update_fields:
                    continue
                setattr(case.profile, field, None)
                for old in case.active_evidence(field):
                    old.superseded = True
        sponsor_identity_changed = prior_sponsor_identity != (
            case.profile.sponsor_name,
            case.profile.sponsor_relationship,
        )
        relationship_changed = prior_sponsor_identity[1] != case.profile.sponsor_relationship
        if relationship_changed and "sponsor_name" not in update_fields:
            # A mother→father (or equivalent) correction changes the entity.
            # Do not silently bind the previous person's name to the new role.
            case.profile.sponsor_name = None
            for old in case.active_evidence("sponsor_name"):
                old.superseded = True
        if sponsor_identity_changed and "sponsor_is_in_uk" not in update_fields:
            case.profile.sponsor_is_in_uk = None
            for old in case.active_evidence("sponsor_is_in_uk"):
                old.superseded = True
        if sponsor_identity_changed and "sponsor_address" not in update_fields:
            case.profile.sponsor_address = None
            for old in case.active_evidence("sponsor_address"):
                old.superseded = True
        if sponsor_identity_changed or sponsor_replaced_without_complete_identity:
            case.sponsor_location_epoch += 1
            case.sponsor_address_question_identity = None
            case.deferred_fields = [field for field in case.deferred_fields if field != "sponsor_address"]
            for item in [*case.unsent_advice, *case.pending_advice]:
                if item.topic == "sponsor_support" and item.source_event_id != event.id:
                    item.deferred_by_event_id = event.id
        if case.profile.funding_source != "personal_sponsor" and "funding_source" in update_fields:
            case.sponsor_location_epoch += 1
            case.sponsor_address_question_identity = None
            case.deferred_fields = [field for field in case.deferred_fields if field != "sponsor_address"]
            for field in ("sponsor_name", "sponsor_address", "sponsor_relationship", "sponsor_is_in_uk"):
                setattr(case.profile, field, None)
                for old in case.active_evidence(field):
                    old.superseded = True
            for item in [*case.unsent_advice, *case.pending_advice]:
                if item.topic == "sponsor_support" and item.source_event_id != event.id:
                    item.deferred_by_event_id = event.id

    def _ingest_attachments(
        self, case: Case, event: InboundEvent, *, reread_attempt_id: str | None = None,
    ) -> None:
        # Audited rereads call this entry point directly; operator approval is
        # not permission to send applicant data to a model.
        processing_epoch = self._require_processing(case)
        # Only the audited local document-review operation supplies an attempt ID.
        # Ordinary inbound delivery always retains hash deduplication. A reread keeps
        # the original customer source event, but records distinct extraction IDs.
        existing_hashes = {document.sha256 for document in case.documents}
        for raw_path in event.attachment_paths:
            self._require_processing(case, processing_epoch)
            path = Path(raw_path)
            try:
                digest = sha256_file(path)
            except OSError:
                digest = hashlib.sha256(f"{event.id}:{path.name}:unavailable".encode()).hexdigest()
            if digest in existing_hashes and reread_attempt_id is None:
                continue
            document_id = stable_id("doc", f"{digest}:{reread_attempt_id}" if reread_attempt_id else digest)
            try:
                inspection = self.document_reader(path)
                kind, language, page_count, facts = (
                    inspection.kind,
                    inspection.language,
                    inspection.page_count,
                    inspection.facts,
                )
            except (OSError, ValueError, PdfReadError):
                self._require_processing(case, processing_epoch)
                self._record_unreadable_document(case, event, path, document_id, digest)
                existing_hashes.add(digest)
                continue
            self._require_processing(case, processing_epoch)
            supersedes_document_id: str | None = None
            if (kind == "conference_invitation" and not inspection.requires_review
                    and reread_attempt_id is None):
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
            expected_financial = {
                "employment_letter": "salary",
                "bank_statement": "closing_balance",
                "sponsor_funds": "closing_balance",
            }.get(kind)
            missing_financial = bool(
                expected_financial
                and inspection.method != "deterministic_pdf_fixture_extractor"
                and not any(item.kind == expected_financial for item in inspection.financial_observations)
            )
            needs_review = inspection.requires_review or missing_financial
            if kind == "unknown" or needs_review:
                document.status = DocumentStatus.HUMAN_REVIEW_REQUIRED
                case.issues.append(
                    Issue(
                        id=stable_id("issue", f"unclassified:{document_id}"),
                        code=f"UNCLASSIFIED_DOCUMENT_{document_id}",
                        title="Document needs manual classification",
                        detail=(f"The content of {path.name} requires review before it can satisfy a requirement. "
                            + (inspection.review_reason or (
                                f"Missing grounded financial observation: {expected_financial}."
                                if missing_financial else "A human must identify and verify this document."
                            ))),
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
            for index, observation in enumerate(inspection.financial_observations):
                # This evidence is intentionally separate from CaseProfile. A
                # quoted balance or salary never becomes a customer-confirmed
                # income, trip cost, sponsor or funding decision automatically.
                observation_value = observation.model_dump(
                    mode="json", exclude={"amount_page", "amount_excerpt", "confidence"}
                )
                case.evidence.append(
                    Evidence(
                        id=stable_id("ev", f"{document_id}:financial:{index}:{observation_value}"),
                        fact_key="financial_observation",
                        value=observation_value,
                        source_event_id=event.id,
                        source_document_id=document.id,
                        source_excerpt=observation.amount_excerpt,
                        page=observation.amount_page,
                        extraction_method=inspection.method,
                        model_version=inspection.model_version,
                        confidence=observation.confidence,
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
