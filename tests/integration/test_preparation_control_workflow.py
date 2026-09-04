"""Persisted customer pacing, independently specified synthetic multi-turn contracts."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate, PreparationIntent
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import confirmation_message, summary_fingerprint
from visa_agent.workflow.service import WorkflowService

POLICY = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 4)


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
        return deterministic_fallback_message(case, plan)


def action_patch(action: str, excerpt: str, **kwargs: object) -> CasePatch:
    return CasePatch.model_validate({
        "updates": [], "ambiguities": [],
        "preparation_intent": {"action": action, "source_excerpt": excerpt, "confidence": 0.99},
        **kwargs,
    })


def seed(path: Path, *, complete: bool = False) -> Case:
    case = Case(id="pace-case", external_thread_id="pace-thread",
                applicant_contact="fictional@example.test", policy_version=load_policy(POLICY).version)
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    if complete:
        case.profile.full_name = "Fictional Applicant"
        case.profile.date_of_birth = date(1998, 5, 12)
        case.profile.planned_arrival_date = date(2026, 11, 1)
        case.profile.planned_departure_date = date(2026, 11, 8)
        case.profile.uk_accommodation = "London"
        case.profile.estimated_trip_cost_gbp = 1500
        case.profile.current_address = "Room 4, Example Hall, 88 Synthetic Road, Hong Kong"
        case.profile.has_serious_history = False
        case.profile.route_confirmed_standard_visitor = True
    store = SQLiteStore(path)
    store.save_case(case)
    store.close()
    return case


def turn(path: Path, number: int, body: str, patch: CasePatch) -> tuple[Case, str, str]:
    # Reopen storage and construct a new service each turn, as after a worker restart.
    store = SQLiteStore(path)
    model = CapturedModel(patch)
    service = WorkflowService(store, load_policy(POLICY), model, today_provider=lambda: TODAY)
    event = InboundEvent(id=f"pace-{number}", external_thread_id="pace-thread",
                         sender="fictional@example.test", subject="Visitor preparation", body=body,
                         received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=number))
    case, duplicate, plan = service.process(event)
    assert not duplicate and model.calls == 1
    row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
    saved = store.get_case(case.id)
    assert saved is not None and saved.model_dump() == case.model_dump()
    before = saved.model_dump_json()
    assert service.process(event)[1]
    assert model.calls == 1 and store.get_case(case.id).model_dump_json() == before
    if case.preparation_paused and case.status != CaseStatus.HUMAN_REVIEW_REQUIRED:
        assert model.render_calls == 0  # Wording generation has no authority to restart intake.
        assert case.question_plan == [] and not case.last_requested_fields
        assert case.confirmation_fingerprint is None and not case.final_summary_confirmed
        assert not evaluate_gate(case, load_policy(POLICY), TODAY).allowed
    store.close()
    return saved, row["payload"], plan


def test_pause_survives_restart_faq_and_fact_corrections_then_resume(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path)
    pause = "Please pause the visa preparation for now."
    case, reply, plan = turn(path, 1, pause, action_patch("pause", pause))
    assert case.preparation_paused and case.preparation_control_epoch == 1 and plan == "blocked"
    assert "on hold" in reply and "?" not in reply
    body = "My date of birth is 1998-05-21. Where is the official visitor application page?"
    patch = CasePatch(updates=[FactUpdate(field="date_of_birth", value="1998-05-21",
                                        source_excerpt="1998-05-21", confidence=0.99)], ambiguities=[],
                      customer_questions=[CustomerQuestion(topic="application",
                          source_excerpt="Where is the official visitor application page?", confidence=0.99)])
    case, reply, _ = turn(path, 2, body, patch)
    assert case.profile.date_of_birth == date(1998, 5, 21)
    assert case.preparation_paused and case.preparation_control_epoch == 1
    assert "gov.uk/standard-visitor/apply-standard-visitor-visa" in reply
    assert case.active_evidence("date_of_birth")[0].source_event_id == "pace-2"
    resume = "Let's resume the visa preparation now."
    case, reply, _ = turn(path, 3, resume, action_patch("resume", resume))
    assert not case.preparation_paused and case.preparation_control_epoch == 2
    assert case.latest_preparation_action == "resume" and case.preparation_control_event_id == "pace-3"
    assert "pick this up again" in reply
    assert not case.profile_confirmed and not case.final_summary_confirmed


@pytest.mark.parametrize("confirmation", ["profile confirmed", "Everything is correct, please proceed."])
def test_resume_cannot_consume_pre_pause_profile_summary(tmp_path: Path, confirmation: str) -> None:
    path = tmp_path / "case.db"
    initial = seed(path, complete=True)
    initial.preparation_paused = True
    initial.preparation_control_epoch = 1
    # Deliberately retain legacy/stale summary context to test both consent branches.
    initial.confirmation_kind = "profile"
    initial.confirmation_fingerprint = summary_fingerprint(initial, include_documents=False)
    initial.confirmation_request_event_id = "old-request"
    store = SQLiteStore(path)
    store.save_case(initial)
    store.close()
    resume = "Let's resume the visa preparation now."
    case, reply, plan = turn(path, 1, resume + "\n" + confirmation, action_patch("resume", resume))
    assert not case.preparation_paused and not case.profile_confirmed and not case.final_summary_confirmed
    assert plan == "awaiting_profile_confirmation"
    assert case.confirmation_request_event_id == "pace-1" and "fresh summary" in reply
    case, _, _ = turn(path, 2, "Everything is correct, please proceed.", CasePatch(updates=[], ambiguities=[]))
    assert case.profile_confirmed and not case.final_summary_confirmed


def test_paused_complete_profile_never_requests_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path, complete=True)
    pause = "请先暂停签证材料准备。"
    case, reply, plan = turn(path, 1, pause, action_patch("pause", pause))
    assert plan == "blocked" and case.preparation_paused
    assert "暂停" in reply and "麻烦核对" not in reply and "资料摘要" not in reply
    assert confirmation_message(case, profile_only=True) == reply
    case, reply, _ = turn(path, 2, "我确认上述个人资料", CasePatch(updates=[], ambiguities=[]))
    assert case.preparation_paused and not case.profile_confirmed


def test_same_pause_is_idempotent_and_dates_unknown_are_not_whole_pause(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path)
    case, _, _ = turn(path, 1, "旅行日期还没确定。", CasePatch(updates=[], ambiguities=[]))
    assert not case.preparation_paused and case.preparation_control_epoch == 0
    pause = "请先暂停签证材料准备。"
    case, _, _ = turn(path, 2, pause, action_patch("pause", pause))
    case, _, _ = turn(path, 3, pause, action_patch("pause", pause))
    assert case.preparation_control_epoch == 1 and case.preparation_control_event_id == "pace-2"


def test_pause_does_not_discard_refusal_history_or_bypass_review(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path)
    pause = "Please pause the visa preparation for now."
    body = pause + " I had a visa refusal in 2022."
    case, reply, _ = turn(path, 1, body, action_patch("pause", pause, updates=[{
        "field": "has_serious_history", "value": True,
        "source_excerpt": "I had a visa refusal in 2022.", "confidence": 0.99,
    }]))
    assert case.preparation_paused and case.profile.has_serious_history is True
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert "human adviser" in reply and "on hold" in reply
    # A later request goes to retained human review, not an automatic unlock.
    store = SQLiteStore(path)
    model = CapturedModel(action_patch("resume", "Resume the visa preparation now."))
    service = WorkflowService(store, load_policy(POLICY), model, today_provider=lambda: TODAY)
    event = InboundEvent(id="held-resume", external_thread_id="pace-thread", sender="fictional@example.test",
                         subject="Continue", body="Resume the visa preparation now.",
                         received_at=datetime(2026, 9, 4, 13, tzinfo=UTC))
    case, _, plan = service.process(event)
    assert plan == "human_review_case_held" and case.preparation_paused and model.calls == 0
    assert store.has_unreviewed_held_updates(case.id)
    store.close()


def test_requested_material_information_remains_available_while_paused(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path, complete=True)
    pause = "Please pause the visa preparation for now."
    turn(path, 1, pause, action_patch("pause", pause))
    question = "What documents will I need for my visitor application?"
    case, reply, _ = turn(path, 2, question, CasePatch(updates=[], ambiguities=[], customer_questions=[
        CustomerQuestion(topic="document_checklist", source_excerpt=question, confidence=0.99),
    ]))
    assert case.preparation_paused and case.question_plan == []
    assert "preparation list" in reply and "no need to send these now" in reply
    assert "GOV.UK:" in reply
    assert "We'll also need these documents" not in reply


def test_rejected_control_proposal_keeps_independent_facts(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path)
    body = "Do not pause the visa preparation. My budget is 2000 GBP."
    case, _, _ = turn(path, 1, body, CasePatch(updates=[FactUpdate(
        field="estimated_trip_cost_gbp", value=2000, source_excerpt="My budget is 2000 GBP.", confidence=0.99,
    )], ambiguities=[], preparation_intent=PreparationIntent(action="pause",
        source_excerpt="pause the visa preparation", confidence=0.99)))
    assert not case.preparation_paused and case.profile.estimated_trip_cost_gbp == 2000


def test_pause_keeps_attachment_without_treating_it_as_verified(tmp_path: Path) -> None:
    path = tmp_path / "case.db"
    seed(path)
    attachment = tmp_path / "fictional-note.pdf"
    attachment.write_bytes(b"Synthetic reader fixture; not a real identity document.")
    pause = "Please pause the visa preparation for now."
    model = CapturedModel(action_patch("pause", pause))
    store = SQLiteStore(path)
    service = WorkflowService(
        store, load_policy(POLICY), model, today_provider=lambda: TODAY,
        document_reader=lambda _: DocumentReadResult(kind="unknown", language="en", page_count=1,
            facts={}, requires_review=True, review_reason="Synthetic unclassified fixture"),
    )
    event = InboundEvent(id="attachment-pause", external_thread_id="pace-thread",
        sender="fictional@example.test", subject="Hold preparation", body=pause,
        attachment_paths=[str(attachment)], received_at=datetime(2026, 9, 4, 14, tzinfo=UTC))
    case, _, plan = service.process(event)
    assert case.preparation_paused and plan == "blocked" and case.question_plan == []
    assert len(case.documents) == 1 and case.documents[0].source_event_id == event.id
    assert case.documents[0].status == "HUMAN_REVIEW_REQUIRED" and case.open_blockers()
    reply = store.list_outbox()[0]["payload"]
    assert attachment.name in reply and "on hold" in reply and "?" not in reply
    store.close()
