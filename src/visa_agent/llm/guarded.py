from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.llm.ports import CasePatch, FactUpdate, LLMClient

MIN_ACCEPTED_CONFIDENCE = 0.8
MAX_MODEL_ATTEMPTS = 2
MAX_REPLY_CHARACTERS = 4_000
FORBIDDEN_REPLY_CLAIMS = (
    "your visa is approved",
    "your application is approved",
    "you are eligible",
    "guaranteed success",
    "guarantee approval",
    "documents are sufficient for approval",
    "application has been submitted",
)


class UnsafeModelOutput(ValueError):
    pass


def _normalise_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def validate_case_patch(event: InboundEvent, proposed: CasePatch) -> CasePatch:
    """Return only grounded, type-valid, non-conflicting candidate facts."""

    body = _normalise_evidence(event.body)
    accepted: dict[str, FactUpdate] = {}
    rejected_fields: set[str] = set()
    ambiguities = list(proposed.ambiguities)
    requires_review = proposed.requires_human_review

    for update in proposed.updates:
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

    return CasePatch(
        updates=list(accepted.values()),
        ambiguities=list(dict.fromkeys(ambiguities)),
        requires_human_review=requires_review,
    )


def deterministic_fallback_message(case: Case, plan: str) -> str:
    if plan == "blocked":
        issues = "; ".join(item.title for item in case.open_blockers())
        detail = f" Please resolve: {issues}." if issues else ""
        return (
            "Thank you — your message is recorded, but the review pack cannot be prepared yet."
            f"{detail} A human adviser will review the case. This service does not decide "
            "eligibility or submit an application."
        )
    if plan == "ready":
        return (
            "Your review pack is ready for human review. This is not an approval prediction or a "
            "submitted visa application."
        )
    return (
        "Thank you — your message is recorded. A human adviser will review the current summary "
        "before any pack is released."
    )


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

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                return validate_case_patch(event, self.delegate.extract_case_patch(event))
            except Exception as error:  # Provider/SDK failures must not mutate or lose the case.
                last_error = error
        assert last_error is not None
        self._report("extract_case_patch", last_error)
        return CasePatch(
            updates=[],
            ambiguities=["Automated extraction was unavailable; manual review is required."],
            requires_human_review=True,
        )

    def render_message(self, case: Case, plan: str) -> str:
        if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
            return deterministic_fallback_message(case, plan)
        try:
            message = self.delegate.render_message(case, plan).strip()
            if not message:
                raise ValueError("Model returned an empty message")
            if len(message) > MAX_REPLY_CHARACTERS:
                raise UnsafeModelOutput("Model message exceeded the configured length limit")
            normalised = message.casefold()
            if any(claim in normalised for claim in FORBIDDEN_REPLY_CLAIMS):
                raise UnsafeModelOutput("Model message contained a prohibited outcome claim")
            return message
        except Exception as error:
            self._report("render_message", error)
            return deterministic_fallback_message(case, plan)

    def _report(self, operation: str, error: Exception) -> None:
        if self.on_failure is not None:
            self.on_failure(operation, error)


def ensure_guarded(llm: LLMClient) -> GuardedLLM:
    if isinstance(llm, GuardedLLM):
        return llm
    return GuardedLLM(llm)
