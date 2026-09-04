"""Case-aware advice through the real service, persistence and outbox, without API/mail."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import (
    Case,
    CaseProfile,
    CaseStatus,
    Document,
    DocumentStatus,
    InboundEvent,
    Issue,
    IssueSeverity,
)
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate, PreparationIntent
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 4)
STEP = "What should I prepare next for my UK visitor application?"
BOOKING = "Do I need to buy flights before applying?"
APPLICATION = "Where is the official visitor application page?"


class CapturedModel:
    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch
        self.calls = 0
        self.render_calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.calls += 1
        return self.patch.model_copy(deep=True)

    def render_message(self, case: Case, plan: str) -> str:
        self.render_calls += 1
        raise AssertionError("Reviewed next-step and FAQ replies must not need a draft model")


def _seed(*, paused: bool = False) -> Case:
    return Case(
        id="synthetic-step-case", external_thread_id="synthetic-step-thread",
        primary_channel="gmail", applicant_contact="fictional@example.test", policy_version=POLICY.version,
        profile=CaseProfile(full_name="Sample Applicant", date_of_birth=date(1998, 5, 12),
            nationality_country="China", application_country="Hong Kong", visit_purpose="tourism",
            occupation_status="student", funding_source="self", uk_accommodation="London",
            estimated_trip_cost_gbp=1500, current_address="Fictional Hong Kong campus address",
            has_serious_history=False, route_confirmed_standard_visitor=True),
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
        preparation_paused=paused, preparation_control_epoch=2 if paused else 0,
    )


def _patch(*questions: tuple[str, str], **kwargs: object) -> CasePatch:
    return CasePatch.model_validate({"updates": [], "ambiguities": [], "customer_questions": [
        {"topic": topic, "source_excerpt": excerpt, "confidence": .99} for topic, excerpt in questions
    ], **kwargs})


def _turn(path: Path, case: Case, body: str, patch: CasePatch, number: int = 1) -> tuple[Case, str, str]:
    # Reopen on each turn; an old in-memory object is not the conversation memory.
    store = SQLiteStore(path)
    if store.get_case(case.id) is None:
        store.save_case(case)
    model = CapturedModel(patch)
    service = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
    event = InboundEvent(id=f"step-{number}", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="UK visitor application", body=body, channel="gmail",
        received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=number))
    updated, duplicate, plan = service.process(event)
    assert not duplicate and model.calls == 1
    row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
    saved = store.get_case(case.id)
    assert saved is not None and saved.model_dump() == updated.model_dump()
    count = len(store.list_outbox())
    assert service.process(event)[1]
    assert model.calls == 1 and len(store.list_outbox()) == count
    assert not saved.final_summary_confirmed and saved.delivery_path is None and plan != "ready"
    store.close()
    return saved, row["payload"], plan


def test_mixed_faq_and_next_document_are_both_delivered_without_reasking_unknown_dates(tmp_path: Path) -> None:
    case, reply, plan = _turn(tmp_path / "case.db", _seed(), BOOKING + " " + STEP,
        _patch(("booking", BOOKING), ("next_step", STEP)))
    assert case.next_step_advice and case.next_step_advice.requirement_id == "passport"
    assert "do not need to buy flights" in reply and "passport" in reply and "PDF" in reply
    assert "gov.uk" in reply and case.next_step_advice.message in reply
    assert case.question_plan == [] and case.last_requested_fields == []
    assert case.preparation_control_epoch == 0 and not case.profile_confirmed and plan == "blocked"
    assert "What dates" not in reply and "We'll also need these documents" not in reply


def test_current_fact_correction_is_applied_before_choosing_one_missing_question(tmp_path: Path) -> None:
    initial = _seed()
    initial.profile.full_name = None
    initial.profile.date_of_birth = None
    statement = "My date of birth is 1997.7.1."
    case, reply, _ = _turn(tmp_path / "case.db", initial, statement + " " + STEP,
        _patch(("next_step", STEP), updates=[FactUpdate(field="date_of_birth", value="1997-07-01",
            source_excerpt=statement, confidence=.99)]))
    assert case.profile.date_of_birth == date(1997, 7, 1)
    assert case.next_step_advice and case.next_step_advice.question_field == "full_name"
    assert case.question_plan == case.last_requested_fields == ["full_name"]
    assert case.question_event_ids["full_name"] == ["step-1"]
    assert "date of birth?" not in reply and reply.count("?") == 1


@pytest.mark.parametrize("missing", [False, True])
def test_paused_information_request_keeps_faq_and_never_resumes_intake(tmp_path: Path, missing: bool) -> None:
    initial = _seed(paused=True)
    if missing:
        initial.profile.full_name = None
    question = "If I continue later, what should I prepare next for my UK visitor application?"
    case, reply, _ = _turn(tmp_path / "case.db", initial, question + " " + APPLICATION,
        _patch(("next_step", question), ("application", APPLICATION)))
    assert case.next_step_advice and case.next_step_advice.kind == "paused"
    assert case.preparation_paused and case.preparation_control_epoch == 2
    assert case.question_plan == [] and case.last_requested_fields == []
    assert case.confirmation_kind is None and not case.profile_confirmed
    assert "later" in reply and "gov.uk/standard-visitor/apply-standard-visitor-visa" in reply
    assert "?" not in reply


def test_explicit_resume_and_advice_do_not_consume_a_prior_confirmation(tmp_path: Path) -> None:
    initial = _seed(paused=True)
    initial.confirmation_kind = "final"
    initial.confirmation_fingerprint = "old-summary-fingerprint"
    initial.confirmation_request_event_id = "before-pause"
    resume = "Please resume the visa preparation now."
    case, reply, _ = _turn(tmp_path / "case.db", initial, resume + " " + BOOKING + " " + STEP,
        _patch(("booking", BOOKING), ("next_step", STEP), preparation_intent=PreparationIntent(
            action="resume", source_excerpt=resume, confidence=.99)))
    assert not case.preparation_paused and case.preparation_control_epoch == 3
    assert case.latest_preparation_action == "resume" and not case.profile_confirmed
    assert case.confirmation_kind is None and case.next_step_advice
    assert "do not need to buy flights" in reply and "passport" in reply


def test_later_faq_only_turn_clears_persisted_advice(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    case, _, _ = _turn(path, _seed(), STEP, _patch(("next_step", STEP)))
    old_advice = case.next_step_advice.message
    # The fallback renderer is intentionally used by a FAQ-only delegate as well.
    store = SQLiteStore(path)
    model = CapturedModel(_patch(("booking", BOOKING)))
    model.render_message = deterministic_fallback_message
    service = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
    event = InboundEvent(id="step-2", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="UK visitor application", body=BOOKING,
        received_at=datetime(2026, 9, 4, 12, 2, tzinfo=UTC))
    case, _, _ = service.process(event)
    reply = next(row["payload"] for row in store.list_outbox() if row["event_id"] == event.id)
    assert case.next_step_advice is None and old_advice not in reply
    assert "do not need to buy flights" in reply
    store.close()


@pytest.mark.parametrize("body", [
    'My friend wrote "What should I prepare next for my UK visitor application?". ' + BOOKING,
    "Do not answer what I should prepare next for my UK visitor application. " + BOOKING,
])
def test_quoted_or_declined_step_proposal_does_not_suppress_independent_faq(tmp_path: Path, body: str) -> None:
    excerpt = STEP if '"' in body else "what I should prepare next for my UK visitor application"
    patch = _patch(("next_step", excerpt), ("booking", BOOKING))
    store = SQLiteStore(tmp_path / "case.db")
    initial = _seed()
    store.save_case(initial)
    model = CapturedModel(patch)
    model.render_message = deterministic_fallback_message
    case, _, _ = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY).process(InboundEvent(
        id="negative", external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="UK visitor", body=body, received_at=datetime(2026, 9, 4, 12, tzinfo=UTC)))
    assert case.next_step_advice is None and case.customer_question_topics == ["booking"]
    assert "do not need to buy flights" in store.list_outbox()[0]["payload"]
    store.close()


@pytest.mark.parametrize("status", [DocumentStatus.RECEIVED, DocumentStatus.PROCESSING])
def test_received_document_is_not_requested_again(tmp_path: Path, status: DocumentStatus) -> None:
    initial = _seed()
    initial.documents = [Document(id="already-received", filename="fictional-passport.pdf", kind="passport",
        sha256="a" * 64, mime_type="application/pdf", status=status, source_event_id="earlier",
        path="/synthetic-not-read/fictional-passport.pdf")]
    case, reply, _ = _turn(tmp_path / "case.db", initial, STEP, _patch(("next_step", STEP)))
    assert case.next_step_advice and case.next_step_advice.kind == "waiting"
    assert "already been received" in reply and "not send another copy" in reply
    assert "Scan the passport" not in reply and case.last_requested_fields == []


def test_open_issue_details_are_not_hidden_by_the_next_step_intro(tmp_path: Path) -> None:
    initial = _seed()
    initial.issues = [Issue(id="fixture-issue", code="MANUAL_FIXTURE_REVIEW", title="Unclear source",
        detail="Please clarify the origin of the fictional letter.", severity=IssueSeverity.BLOCKER)]
    case, reply, _ = _turn(tmp_path / "case.db", initial, STEP, _patch(("next_step", STEP)))
    assert case.next_step_advice and case.next_step_advice.kind == "review"
    assert "Please clarify the origin of the fictional letter." in reply
    assert case.question_plan == [] and "Scan the passport" not in reply


def test_same_turn_history_needs_review_without_dropping_the_faq(tmp_path: Path) -> None:
    statement = "I had a visa refusal in 2022."
    case, reply, _ = _turn(tmp_path / "case.db", _seed(), statement + " " + APPLICATION + " " + STEP,
        _patch(("application", APPLICATION), ("next_step", STEP), updates=[FactUpdate(
            field="has_serious_history", value=True, source_excerpt=statement, confidence=.99)]))
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED and case.next_step_advice.kind == "review"
    assert "human adviser" in reply and "gov.uk/standard-visitor/apply-standard-visitor-visa" in reply
    assert "Scan the passport" not in reply and case.last_requested_fields == []


def test_current_profile_summary_is_the_next_step_without_also_requesting_a_pdf(tmp_path: Path) -> None:
    initial = _seed()
    initial.profile.planned_arrival_date = date(2026, 11, 1)
    initial.profile.planned_departure_date = date(2026, 11, 8)
    initial.deferred_fields = []
    case, reply, plan = _turn(tmp_path / "case.db", initial, STEP, _patch(("next_step", STEP)))
    assert plan == "awaiting_profile_confirmation" and case.confirmation_kind == "profile"
    assert case.next_step_advice and case.next_step_advice.kind == "waiting"
    assert "summary below" in reply and "1998-05-12" in reply
    assert "Scan the passport" not in reply and not case.profile_confirmed


def test_reviewed_reply_bypass_keeps_all_faq_answers_alongside_next_step(tmp_path: Path) -> None:
    case, reply, _ = _turn(tmp_path / "case.db", _seed(), APPLICATION + " " + BOOKING + " " + STEP,
        _patch(("application", APPLICATION), ("booking", BOOKING), ("next_step", STEP)))
    assert len(case.customer_answers) == 3
    assert all(answer in reply for answer in case.customer_answers)
    assert case.question_plan == []


@pytest.mark.parametrize("paused", [False, True])
def test_single_item_wording_does_not_expand_into_a_whole_checklist(tmp_path: Path, paused: bool) -> None:
    initial = _seed(paused=paused)
    initial.customer_language = "zh"
    question = "也请告诉我下一份材料轮到准备什么，以及我该怎样把那一份准备好。"
    case, reply, _ = _turn(tmp_path / "case.db", initial, question, _patch(("next_step", question)))
    assert case.next_step_advice and case.next_step_advice.requirement_id == "passport"
    assert "在读证明" not in reply and "银行流水" not in reply and "材料清单" not in reply
    assert case.last_requested_fields == []


def test_explicit_list_and_step_both_survive_semantic_scope_filter(tmp_path: Path) -> None:
    initial = _seed()
    initial.customer_language = "zh"
    checklist = "请发我这次申请需要的材料清单。"
    step = "另外，我先准备清单里的哪一项？"
    case, reply, _ = _turn(tmp_path / "case.db", initial, checklist + step,
        _patch(("document_checklist", checklist), ("next_step", step)))
    assert case.next_step_advice and "PDF" in reply
    assert "在读证明" in reply and "银行流水" in reply
    assert case.last_requested_fields == []


def test_combined_budget_and_birthday_correction_does_not_trigger_a_brochure(tmp_path: Path) -> None:
    initial = _seed()
    body = "My birthday is 14 February 1999. My budget is GBP 1750. Nothing else has changed."
    store = SQLiteStore(tmp_path / "case.db")
    store.save_case(initial)
    patch = _patch(updates=[
        FactUpdate(field="date_of_birth", value="1999-02-14", source_excerpt="My birthday is 14 February 1999", confidence=.99),
        FactUpdate(field="estimated_trip_cost_gbp", value=1750, source_excerpt="My budget is GBP 1750", confidence=.99),
    ])
    model = CapturedModel(patch)
    model.render_message = deterministic_fallback_message
    case, _, _ = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY).process(InboundEvent(
        id="correction-only", external_thread_id=initial.external_thread_id, sender=initial.applicant_contact,
        subject="Correction", body=body, received_at=datetime(2026, 9, 4, 12, tzinfo=UTC)))
    reply = store.list_outbox()[0]["payload"]
    assert "14 February 1999" in reply and "£1,750" in reply
    assert "GOV.UK" not in reply and "Trip Cost Gbp" not in reply and case.next_step_advice is None
    assert "dates" not in reply and "final check" not in reply
    assert not case.proactive_guidance_offered and not case.final_summary_confirmed
    store.close()


@pytest.mark.parametrize("paused", [False, True])
def test_actual_automatic_sender_preserves_reviewed_step_and_faq_once(tmp_path: Path, paused: bool) -> None:
    class CaptureAdapter(GmailAdapter):
        def __init__(self):
            self.calls = []

        def send_reply(self, **kwargs):
            self.calls.append(kwargs)
            return {"id": "fictional-provider-accepted"}

    path = tmp_path / "case.db"
    case, body, _ = _turn(path, _seed(paused=paused), STEP + " " + BOOKING,
        _patch(("next_step", STEP), ("booking", BOOKING)))
    store = SQLiteStore(path)
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, case.applicant_contact),
        channel="gmail", allowed_message_types=("blocked",))
    results = dispatcher.dispatch_due(datetime.now(UTC))
    assert len(results) == 1 and results[0].status == "SENT"
    assert len(adapter.calls) == 1 and adapter.calls[0]["body"] == body
    assert case.next_step_advice.message in adapter.calls[0]["body"]
    assert store.list_outbox()[0]["payload"] == body
    assert dispatcher.dispatch_due(datetime.now(UTC)) == [] and len(adapter.calls) == 1
    store.close()


@pytest.mark.parametrize("identifier", ["ns_holdout_04", "ns_holdout_07"])
def test_exposed_first_holdout_failures_are_local_regressions_not_a_new_score(tmp_path: Path, identifier: str) -> None:
    """The first report remains failed; captured patches here make no provider calls."""
    report = json.loads(Path("eval_output/next_step_holdout_2026-09-04.json").read_text())
    row = next(row for row in report["results"] if row["id"] == identifier)
    assert not row["passed"]  # Never relabel or replace the original measurement.
    initial = _seed(paused=row["initially_paused"])
    initial.customer_language = row["language"]
    store = SQLiteStore(tmp_path / "case.db")
    store.save_case(initial)
    model = CapturedModel(CasePatch.model_validate(row["raw_patch"]))
    model.render_message = deterministic_fallback_message
    service = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
    event = InboundEvent(id="exposed-local", external_thread_id=initial.external_thread_id,
        sender=initial.applicant_contact, subject="UK visitor preparation", body=row["body"], channel="gmail",
        received_at=datetime(2026, 9, 4, 12, tzinfo=UTC))
    case, _, plan = service.process(event)
    reply = store.list_outbox()[0]["payload"]
    assert sorted(case.customer_question_topics) == sorted(row["expected_topics"])
    assert not case.final_summary_confirmed and not case.profile_confirmed and plan != "ready"
    assert case.delivery_path is None and len(store.list_outbox()) == 1
    if identifier == "ns_holdout_04":
        assert not case.preparation_paused and case.preparation_control_epoch == initial.preparation_control_epoch + 1
        assert case.next_step_advice and case.next_step_advice.requirement_id == "passport"
        assert "do not need to buy flights" in reply and "Scan the passport" in reply
    else:
        assert case.next_step_advice is None and case.profile.model_dump() == initial.profile.model_dump()
        assert case.last_requested_fields == [] and "护照资料页" not in reply
    assert model.calls == 1 and service.process(event)[1]
    assert model.calls == 1 and len(store.list_outbox()) == 1
    store.close()
