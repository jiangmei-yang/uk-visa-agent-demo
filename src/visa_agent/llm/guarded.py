from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.llm.ports import CasePatch, FactUpdate, LLMClient
from visa_agent.workflow.conversation import (
    blocked_customer_message,
    confirmation_message,
    reply_items,
)

MIN_ACCEPTED_CONFIDENCE = 0.8
MAX_MODEL_ATTEMPTS = 2
MAX_REPLY_CHARACTERS = 4_000
FORBIDDEN_REPLY_CLAIMS = (
    "保证获批", "保证通过", "一定获批", "签证已经批准", "已替你提交申请",
    "your visa is approved",
    "your application is approved",
    "you are eligible",
    "guaranteed success",
    "guarantee approval",
    "documents are sufficient for approval",
    "application has been submitted",
    "ready for approval",
    "sufficient for approval",
)
SPONSOR_RELATIONSHIPS = (
    "mother",
    "father",
    "sister",
    "brother",
    "spouse",
    "partner",
    "friend",
    "employer",
)


class UnsafeModelOutput(ValueError):
    pass


def _normalise_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalise_message_formatting(value: str) -> str:
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    return re.sub(r"`([^`]+)`", r"\1", value)


def _canonical_value(field: str, value: str | int | bool) -> str | int | bool:
    if field == "sponsor_relationship" and isinstance(value, str):
        normalised = _normalise_evidence(value)
        matches = [item for item in SPONSOR_RELATIONSHIPS if item in normalised]
        if len(matches) == 1:
            return matches[0]
    return value


def validate_case_patch(event: InboundEvent, proposed: CasePatch) -> CasePatch:
    """Return only grounded, type-valid, non-conflicting candidate facts."""

    body = _normalise_evidence(event.body)
    accepted: dict[str, FactUpdate] = {}
    rejected_fields: set[str] = set()
    ambiguities = list(proposed.ambiguities)
    # A model cannot acknowledge unresolved ambiguity while allowing automatic progression.
    requires_review = proposed.requires_human_review or bool(proposed.ambiguities)

    for update in proposed.updates:
        update = update.model_copy(update={"value": _canonical_value(update.field, update.value)})
        if update.field in {"planned_arrival_date", "planned_departure_date", "date_of_birth"} and not re.search(
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}[日号]|\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4}",
            update.source_excerpt,
        ):
            # "November" is useful conversational context, not a precise date and
            # not a reason to lock a routine enquiry into human review.
            continue
        # A literal quote is necessary but not sufficient: employment/passport facts
        # must not silently become residential/application-location facts.
        cues = {
            "application_country": r"appl(?:y|ying|ication)|申请|递交|提交",
            "current_address": r"address|residen|live|living|住址|居住|住在|家在|地址",
        }
        if update.field in cues and event.requested_fields != [update.field] and not re.search(
            cues[update.field], update.source_excerpt, re.I
        ):
            # Leave the ordinary question open, rather than escalating routine missing data.
            continue
        reason: str | None = None
        field_info = CaseProfile.model_fields.get(update.field)
        if field_info is None:
            reason = f"Unsupported field proposed: {update.field}."
        elif (
            not update.source_excerpt.strip()
            or _normalise_evidence(update.source_excerpt) not in body
        ):
            reason = f"Evidence excerpt for {update.field} was not found in the inbound message."
        elif update.confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = f"Low-confidence value proposed for {update.field}."
        else:
            try:
                TypeAdapter(field_info.annotation).validate_python(update.value)
            except ValidationError:
                reason = f"Invalid value proposed for {update.field}."

        if reason is not None:
            ambiguities.append(reason)
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue

        prior = accepted.get(update.field)
        if prior is not None and prior.value != update.value:
            ambiguities.append(f"Conflicting values proposed for {update.field}.")
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue
        if update.field not in rejected_fields:
            accepted[update.field] = update

    route_update = accepted.get("route_confirmed_standard_visitor")
    history_update = accepted.get("has_serious_history")
    if (route_update is not None and route_update.value is False) or (
        history_update is not None and history_update.value is True
    ):
        requires_review = True

    return CasePatch(
        updates=list(accepted.values()),
        ambiguities=list(dict.fromkeys(ambiguities)),
        requires_human_review=requires_review,
    )


def deterministic_fallback_message(case: Case, plan: str) -> str:
    if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
        return (
            "收到你的信息了。这部分需要顾问进一步核实，我暂时不能自动继续整理材料包。已收到的资料会保留，不需要重复提交。"
            if case.customer_language == "zh" else
            "Thank you for explaining. This needs a human adviser to check before we continue. Your information is retained; you don't need to resend it. I haven't prepared or submitted an application."
        )
    if plan == "blocked":
        return blocked_customer_message(case)
    if plan == "ready":
        if case.customer_language == "zh":
            return "材料包已整理好，供顾问复核。请先看包内的说明和资料摘要；如发现任何错误，请回复这封邮件。这不代表签证获批，也没有替你提交申请。"
        return (
            "Your review pack is ready for human review. This is not an approval prediction or a "
            "submitted visa application."
        )
    return confirmation_message(case)


class GuardedLLM:
    """Mandatory safety boundary around an interchangeable model adapter."""

    def __init__(
        self,
        delegate: LLMClient,
        *,
        max_attempts: int = MAX_MODEL_ATTEMPTS,
        on_failure: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.delegate = delegate
        self.max_attempts = max_attempts
        self.on_failure = on_failure
        self.version = f"guarded:{getattr(delegate, 'version', 'unknown')}"
        self.last_extraction_fallback = False
        self.last_render_fallback = False
        self.last_render_error: str | None = None

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                patch = validate_case_patch(event, self.delegate.extract_case_patch(event))
                self.last_extraction_fallback = False
                return patch
            except Exception as error:  # Provider/SDK failures must not mutate or lose the case.
                last_error = error
        assert last_error is not None
        self._report("extract_case_patch", last_error)
        self.last_extraction_fallback = True
        return CasePatch(
            updates=[],
            ambiguities=["Automated extraction was unavailable; manual review is required."],
            requires_human_review=True,
        )

    def render_message(self, case: Case, plan: str) -> str:
        if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
            self.last_render_fallback = True
            self.last_render_error = "case_requires_human_review"
            return deterministic_fallback_message(case, plan)
        if plan in {"awaiting_confirmation", "awaiting_profile_confirmation"}:
            self.last_render_fallback = False
            self.last_render_error = None
            return confirmation_message(case, profile_only=plan == "awaiting_profile_confirmation")
        try:
            message = _normalise_message_formatting(
                self.delegate.render_message(case, plan).strip()
            )
            if not message:
                raise ValueError("Model returned an empty message")
            if len(message) > MAX_REPLY_CHARACTERS:
                raise UnsafeModelOutput("Model message exceeded the configured length limit")
            normalised = message.casefold()
            if any(claim in normalised for claim in FORBIDDEN_REPLY_CLAIMS):
                raise UnsafeModelOutput("Model message contained a prohibited outcome claim")
            if any(
                placeholder in normalised
                for placeholder in ("[name]", "[applicant name]", "[your name]")
            ):
                raise UnsafeModelOutput("Model message contained an unresolved placeholder")
            if plan == "blocked":
                issues, questions, documents = reply_items(case)
                required_items = issues + questions + documents
                if any(item.casefold() not in normalised for item in required_items):
                    raise UnsafeModelOutput("Model message omitted or changed a grounded next action")
                length_budget = max(420 if case.customer_language == "zh" else 1100,
                                    len("\n".join(required_items)) + 180)
                if len(message) > length_budget:
                    raise UnsafeModelOutput("Model buried the next action in excessive prose")
                if any(phrase in normalised for phrase in ("没有现成的标准答案", "不能给你一个确切的步骤清单", "no standard answer")):
                    raise UnsafeModelOutput("Model denied a preparation step already supplied in its brief")
                if re.search(r"(?:时间|日期).{0,12}(?:没问题|没有问题|来得及)|\benough time\b|\bdates (?:are|look) (?:fine|acceptable)\b", normalised):
                    raise UnsafeModelOutput("Model added an unsupported timing assurance")
                if case.customer_language == "zh" and not re.search(r"[\u4e00-\u9fff]", message):
                    raise UnsafeModelOutput("Model ignored the customer's language")
            if plan == "awaiting_confirmation" and "i confirm the final summary" not in normalised:
                raise UnsafeModelOutput("Model message omitted the exact confirmation statement")
            if plan == "awaiting_confirmation" and any(
                claim in normalised
                for claim in ("pack is ready", "pack has been prepared", "pack is released")
            ):
                raise UnsafeModelOutput("Model message claimed release before confirmation")
            if plan == "ready" and not (
                ("human" in normalised and "review" in normalised)
                if case.customer_language != "zh" else "顾问复核" in message
            ):
                raise UnsafeModelOutput("Model message omitted the human-review boundary")
            self.last_render_fallback = False
            self.last_render_error = None
            return message
        except Exception as error:
            self._report("render_message", error)
            self.last_render_fallback = True
            self.last_render_error = f"{type(error).__name__}: {str(error)[:160]}"
            return deterministic_fallback_message(case, plan)

    def _report(self, operation: str, error: Exception) -> None:
        if self.on_failure is not None:
            self.on_failure(operation, error)


def ensure_guarded(llm: LLMClient) -> GuardedLLM:
    if isinstance(llm, GuardedLLM):
        return llm
    return GuardedLLM(llm)
