from __future__ import annotations

from datetime import UTC, datetime

from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.llm.guarded import GuardedLLM, validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


def _event(
    body: str = "My name is Ada Lovelace and I plan to arrive on 2026-10-04.",
) -> InboundEvent:
    return InboundEvent(
        id="event-agent-eval",
        external_thread_id="thread-agent-eval",
        sender="applicant@example.test",
        subject="Application details",
        body=body,
        received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
    )


class ScriptedLLM:
    version = "scripted-fault-injection"

    def __init__(self, extraction: list[CasePatch | Exception], message: str | Exception = "ok"):
        self.extraction = extraction
        self.message = message
        self.extraction_calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        del event
        self.extraction_calls += 1
        outcome = self.extraction.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def render_message(self, case: Case, plan: str) -> str:
        del case, plan
        if isinstance(self.message, Exception):
            raise self.message
        return self.message


def _update(
    field: str, value: str | int | bool, excerpt: str, confidence: float = 1.0
) -> FactUpdate:
    return FactUpdate(
        field=field,
        value=value,
        source_excerpt=excerpt,
        confidence=confidence,
    )


def test_guard_rejects_unknown_ungrounded_low_confidence_and_invalid_values() -> None:
    proposed = CasePatch(
        updates=[
            _update("workflow_state", "READY", "My name is Ada Lovelace"),
            _update("full_name", "Mallory", "This excerpt was invented"),
            _update("nationality", "British", "My name is Ada Lovelace", 0.2),
            _update("planned_arrival_date", "not-a-date", "arrive on 2026-10-04"),
        ],
        ambiguities=[],
    )

    guarded = validate_case_patch(_event(), proposed)

    assert guarded.updates == []
    assert guarded.requires_human_review is True
    assert len(guarded.ambiguities) == 4


def test_guard_rejects_both_conflicting_values_for_one_field() -> None:
    proposed = CasePatch(
        updates=[
            _update("full_name", "Ada Lovelace", "My name is Ada Lovelace"),
            _update("full_name", "Grace Hopper", "My name is Ada Lovelace"),
        ],
        ambiguities=[],
    )

    guarded = validate_case_patch(_event(), proposed)

    assert guarded.updates == []
    assert guarded.requires_human_review is True
    assert "Conflicting values proposed for full_name." in guarded.ambiguities


def test_any_model_reported_ambiguity_deterministically_requires_review() -> None:
    proposed = CasePatch(
        updates=[],
        ambiguities=["The applicant supplied two unresolved arrival dates."],
        requires_human_review=False,
    )

    guarded = validate_case_patch(_event(), proposed)

    assert guarded.ambiguities == proposed.ambiguities
    assert guarded.requires_human_review is True


def test_explicit_unsupported_route_deterministically_requires_review() -> None:
    body = "I have not chosen the Standard Visitor route."
    proposed = CasePatch(
        updates=[
            _update(
                "route_confirmed_standard_visitor",
                False,
                "I have not chosen the Standard Visitor route.",
            )
        ],
        ambiguities=[],
        requires_human_review=False,
    )

    guarded = validate_case_patch(_event(body), proposed)

    assert guarded.requires_human_review is True


def test_explicit_serious_history_deterministically_requires_review() -> None:
    body = "I have a criminal conviction."
    proposed = CasePatch(
        updates=[_update("has_serious_history", True, "I have a criminal conviction.")],
        ambiguities=[],
        requires_human_review=False,
    )

    guarded = validate_case_patch(_event(body), proposed)

    assert guarded.requires_human_review is True


def test_transient_extraction_retries_once_then_returns_grounded_patch() -> None:
    expected = CasePatch(
        updates=[_update("full_name", "Ada Lovelace", "My name is Ada Lovelace")],
        ambiguities=[],
    )
    delegate = ScriptedLLM([TimeoutError("timeout"), expected])

    result = GuardedLLM(delegate).extract_case_patch(_event())

    assert delegate.extraction_calls == 2
    assert result.updates == expected.updates
    assert result.requires_human_review is False


def test_extraction_exhaustion_abstains_and_requests_human_review() -> None:
    failures: list[tuple[str, str]] = []
    delegate = ScriptedLLM([TimeoutError("first"), TimeoutError("second")])
    guarded = GuardedLLM(
        delegate,
        on_failure=lambda operation, error: failures.append((operation, type(error).__name__)),
    )

    result = guarded.extract_case_patch(_event())

    assert result.updates == []
    assert result.requires_human_review is True
    assert failures == [("extract_case_patch", "TimeoutError")]


def test_message_failure_uses_bounded_non_advisory_fallback() -> None:
    delegate = ScriptedLLM([], message=RuntimeError("provider down"))
    guarded = GuardedLLM(delegate)
    case = Case(
        id="case-agent-eval",
        external_thread_id="thread-agent-eval",
        applicant_contact="applicant@example.test",
        policy_version="test-policy",
    )

    message = guarded.render_message(case, "ready")

    assert "human review" in message.lower()
    assert "not an approval prediction" in message.lower()


def test_unsafe_outcome_claim_uses_fallback_and_is_reported() -> None:
    failures: list[str] = []
    delegate = ScriptedLLM([], message="Congratulations, your visa is approved.")
    guarded = GuardedLLM(delegate, on_failure=lambda operation, error: failures.append(operation))
    case = Case(
        id="case-agent-eval",
        external_thread_id="thread-agent-eval",
        applicant_contact="applicant@example.test",
        policy_version="test-policy",
    )

    message = guarded.render_message(case, "ready")

    assert "your visa is approved" not in message.lower()
    assert failures == ["render_message"]


def test_human_review_case_never_delegates_customer_message() -> None:
    delegate = ScriptedLLM([], message="ordinary model reply")
    case = Case(
        id="case-agent-eval",
        external_thread_id="thread-agent-eval",
        applicant_contact="applicant@example.test",
        policy_version="test-policy",
        status=CaseStatus.HUMAN_REVIEW_REQUIRED,
    )

    message = GuardedLLM(delegate).render_message(case, "awaiting_confirmation")

    assert "human adviser" in message.lower()
    assert message != "ordinary model reply"
