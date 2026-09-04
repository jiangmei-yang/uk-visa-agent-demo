"""Offline two-pass proposals composed through the existing workflow guard.

Only customer_questions is replaced. Both extractors and Gmail delivery are
synthetic test doubles; SQLite, validation, workflow and outbox are real. This is
an experiment-composition contract, not a production feature switch or model eval.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import (
    CasePatch,
    CustomerQuestion,
    CustomerQuestionBatch,
    FactUpdate,
    QuestionDeferral,
)
from visa_agent.llm.question_understanding import with_customer_questions
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.customer_questions import SOURCE
from visa_agent.workflow.service import WorkflowService

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 4)


def question(topic: str, excerpt: str, confidence: float = 0.99) -> CustomerQuestion:
    return CustomerQuestion.model_validate({
        "topic": topic, "source_excerpt": excerpt, "confidence": confidence,
    })


def empty_patch() -> CasePatch:
    return CasePatch(updates=[], ambiguities=[])


class OfflineComposition:
    """Test-only two-call wiring; production extraction is not replaced."""

    version = "fictional-two-pass-composition"

    def __init__(self) -> None:
        self.patches: dict[str, CasePatch] = {}
        self.batches: dict[str, CustomerQuestionBatch] = {}
        self.baseline_events: list[InboundEvent] = []
        self.focused_events: list[InboundEvent] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.baseline_events.append(event)
        patch = self.patches[event.id]
        self.focused_events.append(event)
        batch = self.batches[event.id]
        return with_customer_questions(patch, batch)

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"fictional-two-pass-sent-{len(self.calls)}"}


class Conversation:
    def __init__(self, tmp_path: Path, *, complete_profile: bool = False) -> None:
        self.path = tmp_path / "question-composition.db"
        self.model = OfflineComposition()
        self.gmail = CaptureGmail()
        self.sequence = 0
        self.initial = Case(
            id="fictional-two-pass-case", external_thread_id="fictional-two-pass-thread",
            applicant_contact="fictional-two-pass@example.test", primary_channel="gmail",
            customer_language="en", policy_version=load_policy(POLICY_PATH).version,
        )
        profile = self.initial.profile
        profile.visit_purpose = "tourism"
        profile.nationality_country = "China"
        profile.application_country = "Hong Kong"
        profile.occupation_status = "student"
        profile.funding_source = "self"
        if complete_profile:
            profile.full_name = "Fictional Applicant"
            profile.date_of_birth = date(1992, 4, 16)
            profile.uk_accommodation = "Intended London stay; no booking made"
            profile.estimated_trip_cost_gbp = 2300
            profile.current_address = "Room 4, Example Hall, 88 Synthetic Road, Hong Kong"
            profile.has_serious_history = False
            profile.route_confirmed_standard_visitor = True
            profile.planned_arrival_date = date(2026, 11, 1)
            profile.planned_departure_date = date(2026, 11, 8)
        evaluate_gate(self.initial, load_policy(POLICY_PATH), TODAY)
        store = SQLiteStore(self.path)
        try:
            store.save_case(self.initial)
        finally:
            store.close()

    def turn(
        self, body: str, patch: CasePatch, *questions: CustomerQuestion,
        expected_plan: str = "blocked", send: bool = True,
    ) -> tuple[Case, str]:
        self.sequence += 1
        event = InboundEvent(
            id=f"fictional-two-pass-{self.sequence}", channel="gmail",
            external_thread_id=self.initial.external_thread_id,
            sender=self.initial.applicant_contact, subject="A question", body=body,
            received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=self.sequence),
            rfc_message_id=f"<fictional-two-pass-{self.sequence}@example.test>",
        )
        self.model.patches[event.id] = patch
        self.model.batches[event.id] = CustomerQuestionBatch(customer_questions=list(questions))
        original_patch = patch.model_dump_json()
        store = SQLiteStore(self.path)
        try:
            workflow = WorkflowService(
                store, load_policy(POLICY_PATH), self.model, today_provider=lambda: TODAY,
            )
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == expected_plan
            assert patch.model_dump_json() == original_patch
            assert self.model.baseline_events[-1] is self.model.focused_events[-1]
            sender = AutomaticGmailReplySender(self.gmail, store, event.sender)
            dispatcher = OutboxDispatcher(
                store, sender, channel="gmail", allowed_message_types=(expected_plan,),
            )
            if send:
                sender.withhold_obsolete_unsent()
                outcomes = dispatcher.dispatch_due(event.received_at)
                assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            if send:
                assert row["payload"] == self.gmail.calls[-1]["body"]
            else:
                assert row["status"] == "PENDING"
            persisted = store.get_case(case.id)
            assert persisted is not None
            assert persisted.delivery_revision == self.initial.delivery_revision
            before_replay = persisted.model_dump_json()
            call_counts = (len(self.model.baseline_events), len(self.model.focused_events))
            sent_count = len(self.gmail.calls)
        finally:
            store.close()

        # A restart/replay must not repeat either extraction or accepted delivery.
        store = SQLiteStore(self.path)
        try:
            workflow = WorkflowService(
                store, load_policy(POLICY_PATH), self.model, today_provider=lambda: TODAY,
            )
            assert workflow.process(event)[1]
            assert call_counts == (
                len(self.model.baseline_events), len(self.model.focused_events),
            )
            assert store.get_case(case.id).model_dump_json() == before_replay
            if send:
                dispatcher = OutboxDispatcher(
                    store, AutomaticGmailReplySender(self.gmail, store, event.sender),
                    channel="gmail", allowed_message_types=(expected_plan,),
                )
                assert dispatcher.dispatch_due(event.received_at) == []
                assert len(self.gmail.calls) == sent_count
            return persisted, row["payload"]
        finally:
            store.close()


def assert_unreleased(case: Case) -> None:
    assert not case.final_summary_confirmed
    assert case.delivery_path is None
    assert case.status not in {CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION}
    assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed


def test_composition_replaces_only_questions_and_does_not_alias_inputs() -> None:
    baseline = CasePatch(
        updates=[FactUpdate(field="full_name", value="Fictional Applicant",
                            source_excerpt="Fictional Applicant", confidence=0.99)],
        ambiguities=["An independent baseline ambiguity."], requires_human_review=True,
        question_deferrals=[QuestionDeferral(
            field="planned_arrival_date", source_excerpt="Dates are undecided", confidence=0.99,
        )],
        customer_questions=[question("fees", "An obsolete baseline question.")],
    )
    batch = CustomerQuestionBatch(customer_questions=[question("booking", "A focused question.")])
    before = baseline.model_dump()
    focused_before = batch.model_dump()
    composed = with_customer_questions(baseline, batch)
    assert composed.model_dump(exclude={"customer_questions"}) == baseline.model_dump(
        exclude={"customer_questions"},
    )
    assert composed.customer_questions == batch.customer_questions
    composed.updates[0].value = "Changed only in composed output"
    composed.ambiguities.append("Added only to composed output")
    composed.question_deferrals[0].source_excerpt = "Changed composed deferral"
    composed.customer_questions[0].source_excerpt = "Changed composed question"
    assert baseline.model_dump() == before and batch.model_dump() == focused_before


def test_empty_focused_batch_removes_baseline_questions_without_merging(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    text = "I will reply later."
    baseline = empty_patch()
    baseline.customer_questions = [question("fees", text)]
    case, body = conversation.turn(text, baseline)
    assert case.customer_question_topics == [] and case.customer_answers == []
    assert case.profile == conversation.initial.profile and case.evidence == []
    assert "£" not in body and "https://" not in body
    assert_unreleased(case)


def test_mixed_correction_and_deferral_survive_focused_question_replacement(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    correction = "I am employed now"
    deferral = "The trip dates are still undecided"
    request = "Do I need to book flights before a decision?"
    baseline = CasePatch(
        updates=[FactUpdate(field="occupation_status", value="employed",
                            source_excerpt=correction, confidence=0.99)],
        ambiguities=[], customer_questions=[question("off_topic", request)],
        question_deferrals=[QuestionDeferral(
            field=field, source_excerpt=deferral, confidence=0.99,
        ) for field in ("planned_arrival_date", "planned_departure_date")],
    )
    case, body = conversation.turn(
        f"{correction}; that is a correction. {deferral}. {request}",
        baseline, question("booking", request),
    )
    assert case.profile.occupation_status == "employed"
    assert case.latest_changes == {"occupation_status": "employed"}
    assert set(case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert case.customer_question_topics == ["booking"] and SOURCE in body
    evidence = case.active_evidence("occupation_status")
    assert len(evidence) == 1 and evidence[0].source_excerpt == correction
    assert evidence[0].source_event_id == "fictional-two-pass-1" and not evidence[0].confirmed
    assert not case.profile_confirmed
    assert_unreleased(case)


@pytest.mark.parametrize(("ambiguities", "requires_review"), [
    (["An independent unresolved ambiguity."], False), ([], True),
])
def test_focused_batch_cannot_clear_baseline_review_or_ambiguity(
    tmp_path: Path, ambiguities: list[str], requires_review: bool,
) -> None:
    conversation = Conversation(tmp_path)
    request = "Can you explain the timing?"
    baseline = CasePatch(updates=[], ambiguities=ambiguities, requires_human_review=requires_review)
    case, _ = conversation.turn(request, baseline, question("timing", request), send=False)
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert case.human_review_reason
    if ambiguities:
        assert ambiguities[0] in case.human_review_reason
    assert not case.profile_confirmed
    assert_unreleased(case)


@pytest.mark.parametrize(("text", "excerpt", "confidence"), [
    ("I will reply later.", "A fabricated excerpt.", 0.99),
    ("I will reply later.\n> Where do I begin?", "Where do I begin?", 0.99),
    ('I am quoting "Where do I begin?"', "Where do I begin?", 0.99),
    ("Do not explain where I begin.", "where I begin", 0.99),
    ("Where do I begin?", "Where do I begin?", 0.79),
])
def test_downstream_guard_rejects_ungrounded_quoted_refused_or_low_confidence_sources(
    tmp_path: Path, text: str, excerpt: str, confidence: float,
) -> None:
    conversation = Conversation(tmp_path)
    case, _ = conversation.turn(text, empty_patch(), question("application", excerpt, confidence))
    assert case.customer_question_topics == []
    assert case.profile == conversation.initial.profile and case.evidence == []
    if "\n>" in text:
        assert conversation.model.baseline_events[0].body == "I will reply later."
        assert conversation.model.focused_events[0].body == "I will reply later."
    assert not case.profile_confirmed
    assert_unreleased(case)


def test_focused_success_does_not_bypass_existing_fact_guard(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    request = "Can you explain this question?"
    baseline = CasePatch(updates=[FactUpdate(
        field="full_name", value="Invented Name", source_excerpt="Absent source", confidence=0.99,
    )], ambiguities=[])
    case, _ = conversation.turn(request, baseline, question("application", request), send=False)
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert case.profile.full_name is None and case.evidence == []
    assert not case.profile_confirmed
    assert_unreleased(case)


@pytest.mark.parametrize("topic", ["application", "fees", "document_checklist", "unsupported", "off_topic"])
def test_topical_proposal_has_no_fact_confirmation_or_pack_release_authority(
    tmp_path: Path, topic: str,
) -> None:
    conversation = Conversation(tmp_path)
    request = "Can you explain this question?"
    case, _ = conversation.turn(request, empty_patch(), question(topic, request))
    assert case.customer_question_topics == [topic]
    assert case.profile == conversation.initial.profile
    assert case.evidence == conversation.initial.evidence and case.documents == []
    assert not case.profile_confirmed
    assert case.confirmation_fingerprint is None and case.confirmation_kind is None
    assert_unreleased(case)


@pytest.mark.parametrize("context", ["sent", "unsent", "changed"])
def test_focused_pass_preserves_sent_summary_context_and_cannot_supply_consent(
    tmp_path: Path, context: str,
) -> None:
    conversation = Conversation(tmp_path, complete_profile=True)
    before, summary = conversation.turn(
        "Please continue checking my details.", empty_patch(),
        expected_plan="awaiting_profile_confirmation", send=context != "unsent",
    )
    off_topic = "Please explain this geometry exercise."
    paused, body = conversation.turn(off_topic, empty_patch(), question("off_topic", off_topic))
    assert before.confirmation_kind == "profile" and before.confirmation_fingerprint
    assert paused.confirmation_kind == before.confirmation_kind
    assert paused.confirmation_fingerprint == before.confirmation_fingerprint
    assert paused.confirmation_request_event_id == before.confirmation_request_event_id
    assert paused.profile == before.profile and paused.question_event_ids == before.question_event_ids
    assert body != summary and before.profile.full_name not in body
    assert not paused.profile_confirmed
    assert_unreleased(paused)

    confirmation = "Everything is correct, please proceed."
    baseline = empty_patch()
    if context == "changed":
        correction = "My trip budget is GBP 2700."
        confirmation += "\n" + correction
        baseline.updates = [FactUpdate(
            field="estimated_trip_cost_gbp", value=2700, source_excerpt=correction, confidence=0.99,
        )]
    final, _ = conversation.turn(
        confirmation, baseline,
        expected_plan="blocked" if context == "sent" else "awaiting_profile_confirmation",
    )
    assert final.profile_confirmed == (context == "sent")
    assert final.customer_question_topics == [] and final.customer_answers == []
    if context == "changed":
        assert final.profile.estimated_trip_cost_gbp == 2700
        assert final.confirmation_fingerprint != before.confirmation_fingerprint
    assert_unreleased(final)
