"""Offline date-obstacle advice through real workflow, reopen and captured SENT.

The model returns explicit synthetic proposals, not a provider response. These
unconfigured fixture databases establish no real applicant-processing permission.
"""

from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import reply_items
from visa_agent.workflow.preparation_obstacles import preparation_obstacle_kind
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 5)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
SENDER = "undecided-dates@example.test"
ZH = "旅行日期还没有确定。有什么能先做的？"
EN = "My travel dates are still undecided. Is there anything I can do in the meantime?"


def patch(*, facts=(), question=None, control=None):
    data = {"updates": [
        {"field": field, "value": value, "source_excerpt": excerpt, "confidence": 1}
        for field, value, excerpt in facts
    ], "ambiguities": [], "customer_questions": []}
    if question:
        data["customer_questions"].append({"topic": "next_step", "source_excerpt": question, "confidence": 1})
    if control:
        data["preparation_intent"] = {"action": control[0], "source_excerpt": control[1], "confidence": 1}
    return CasePatch.model_validate(data)


class FixedModel:
    def __init__(self, proposal):
        self.proposal = proposal
        self.events = []

    def extract_case_patch(self, event):
        self.events.append(event.model_copy(deep=True))
        return self.proposal.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


class Capture(GmailAdapter):
    def __init__(self):
        self.requests = []

    def send_reply(self, **request):
        assert request["recipient"] == SENDER and request.get("attachment") is None
        self.requests.append(request)
        return {"id": f"captured-date-{len(self.requests)}"}


class Journey:
    def __init__(self, tmp_path):
        self.path = tmp_path / "undecided-date.db"
        self.gmail = Capture()
        self.number = 0
        self.today = TODAY

    def turn(self, body, proposal=None):
        self.number += 1
        event = InboundEvent(
            id=f"undecided-event-{self.number}", external_thread_id="undecided-thread",
            sender=SENDER, channel="gmail", subject="Fictional date enquiry", body=body,
            received_at=datetime(2026, 9, 5, 12, tzinfo=UTC) + timedelta(minutes=self.number),
            rfc_message_id=f"<undecided-event-{self.number}@example.test>",
        )
        model = FixedModel(proposal or patch())
        with closing(SQLiteStore(self.path)) as store:
            guard = GuardedLLM(model)
            case, duplicate, plan = WorkflowService(
                store, POLICY, guard, today_provider=lambda: self.today,
            ).process(event)
            assert not duplicate and plan != "ready" and len(model.events) == 1
            assert not guard.last_extraction_fallback
            sender = AutomaticGmailReplySender(self.gmail, store, SENDER)
            sender.withhold_obsolete_unsent()
            outcomes = OutboxDispatcher(store, sender, channel="gmail").dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["payload"] == self.gmail.requests[-1]["body"] and row["provider_message_id"]
            assert store.get_case(case.id).model_dump() == case.model_dump()
            return SimpleNamespace(case=case, body=row["payload"], model=model)

    def background(self):
        facts = [
            ("nationality_country", "China", "I hold a Chinese passport."),
            ("application_country", "Hong Kong", "I will apply in Hong Kong."),
            ("visit_purpose", "tourism", "I am visiting the UK for tourism."),
            ("occupation_status", "student", "I am a university student."),
            ("funding_source", "self", "I will pay for the trip myself."),
        ]
        return self.turn(" ".join(item[2] for item in facts) + " My travel dates are undecided.", patch(facts=facts))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("These synthetic tests must not access the network")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.socket.connect", deny)


def assert_no_authority(case):
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.confirmation_kind is None and case.confirmation_fingerprint is None
    assert case.delivery_path is None
    assert not evaluate_gate(case.model_copy(deep=True), POLICY, TODAY).allowed


@pytest.mark.parametrize(("body", "language"), [
    (ZH, "zh"),
    ("旅行日期还没有确定。我现在能做些什么？", "zh"),
    ("旅行日期还没有确定。那我可以从哪里开始？", "zh"),
    (EN, "en"),
    ("My travel dates are still undecided. Can I get started on anything now?", "en"),
    ("My travel dates are still undecided. Where can I start for now?", "en"),
])
def test_current_date_obstacle_gets_practical_action_before_name_question(tmp_path, body, language):
    journey = Journey(tmp_path)
    before = journey.background().case
    assert before.profile.full_name is None
    assert set(before.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    result = journey.turn(body, patch(question=body))
    case = result.case
    assert case.id == before.id and case.next_step_advice.kind == "document"
    assert case.next_step_advice.requirement_id == "status_evidence"
    assert case.next_step_advice.question_field is None and reply_items(case)[1] == []
    assert case.question_plan == case.last_requested_fields == []
    assert ("学校" if language == "zh" else "school") in result.body
    assert ("在读" if language == "zh" else "enrolment") in result.body
    assert ("日期确定后" if language == "zh" else "once the dates are known") in result.body
    assert ("计划旅行日期" if language == "zh" else "planned travel dates") in result.body
    assert case.profile == before.profile and case.evidence == before.evidence
    assert case.documents == before.documents and case.requirements == before.requirements
    assert case.deferred_fields == before.deferred_fields and case.stage == before.stage
    assert case.preparation_control_epoch == before.preparation_control_epoch
    assert not case.preparation_paused and case.status == CaseStatus.DRAFT
    assert_no_authority(case)


@pytest.mark.parametrize("body", [
    "如果旅行日期还没有确定。有什么能先做的？",
    "If my travel dates are still undecided; is there anything I can do in the meantime?",
    "朋友问：旅行日期还没有确定。有什么能先做的？",
    "A template says:\nMy travel dates are still undecided. Is there anything I can do in the meantime?",
    "日期不是没有确定。有什么能先做的？",
    "My dates are no longer undecided. Is there anything I can do in the meantime?",
    "旅行日期还没有确定。不要回答有什么能先做的。",
    "My travel dates are still undecided. Do not answer whether there is anything I can do in the meantime.",
    "我现在申请工作签证。旅行日期还没有确定。有什么能先做的？",
    "For a student visa, my travel dates are still undecided. Is there anything I can do in the meantime?",
    "旅行日期还没有确定。有什么能先做的才能保证获批？",
    "My travel dates are still undecided. Is there anything I can do in the meantime to guarantee approval?",
    "我以后会问：旅行日期还没有确定。有什么能先做的？",
    "Tomorrow I will ask: my travel dates are still undecided. Is there anything I can do in the meantime?",
    '"My travel dates are still undecided." Is there anything I can do in the meantime?',
    "> 旅行日期还没有确定。\n> 有什么能先做的？\n谢谢。",
])
def test_qualified_or_noncurrent_request_does_not_borrow_date_advice(tmp_path, body):
    journey = Journey(tmp_path)
    journey.background()
    result = journey.turn(body, patch(question=body))
    assert preparation_obstacle_kind(body) is None
    assert result.case.next_step_advice is None or result.case.next_step_advice.requirement_id is None
    assert "headed letter confirming current enrolment" not in result.body
    assert "先向学校索取确认当前在读情况" not in result.body
    assert_no_authority(result.case)


@pytest.mark.parametrize(("body", "suffix", "links"), [
    ("日期还没确定。我现在能先准备什么？", "这次不用给我链接。", False),
    ("My travel dates are still undecided. What can I prepare now?", "Please do not include any links in this reply.", False),
    ("日期还没确定。我现在能先准备什么？", "旧邮件里写着‘不用链接’。", True),
    ("My travel dates are still undecided. What can I prepare now?", "An old email contains the phrase 'no links'.", True),
])
def test_link_scope_does_not_remove_useful_action_or_apply_quoted_preferences(tmp_path, body, suffix, links):
    journey = Journey(tmp_path)
    journey.background()
    result = journey.turn(body + " " + suffix, patch(question=body))
    assert result.case.next_step_advice.requirement_id == "status_evidence"
    assert ("GOV.UK:" in result.body) is links
    assert result.case.last_requested_fields == []
    assert_no_authority(result.case)


def test_paused_request_is_information_only_and_does_not_resume(tmp_path):
    journey = Journey(tmp_path)
    journey.background()
    command = "Please pause my visa preparation."
    paused = journey.turn(command, patch(control=("pause", command))).case
    result = journey.turn(EN, patch(question=EN))
    assert result.case.preparation_paused
    assert result.case.preparation_control_epoch == paused.preparation_control_epoch
    assert result.case.next_step_advice.kind == "paused"
    assert result.case.next_step_advice.requirement_id is None and result.case.last_requested_fields == []
    assert "ask your school" not in result.body
    assert_no_authority(result.case)


@pytest.mark.parametrize(("arrival_day", "departure_day", "kind"), [(30, 20, "review"), (20, 30, "question")])
def test_existing_dates_are_not_erased_or_hidden_by_an_undecided_date_request(tmp_path, arrival_day, departure_day, kind):
    journey = Journey(tmp_path)
    journey.background()
    arrival = f"My arrival date is {arrival_day} September 2026."
    departure = f"My departure date is {departure_day} September 2026."
    recorded = journey.turn(arrival + " " + departure, patch(facts=[
        ("planned_arrival_date", f"2026-09-{arrival_day}", arrival),
        ("planned_departure_date", f"2026-09-{departure_day}", departure),
    ])).case
    assert recorded.profile.planned_arrival_date == date(2026, 9, arrival_day)
    assert recorded.profile.planned_departure_date == date(2026, 9, departure_day)
    result = journey.turn(EN, patch(question=EN))
    assert result.case.profile == recorded.profile
    assert result.case.next_step_advice.kind == kind
    assert result.case.next_step_advice.requirement_id is None
    assert "headed letter confirming current enrolment" not in result.body
    if kind == "review":
        assert "date order" in result.body and result.case.last_requested_fields == []
    assert_no_authority(result.case)


def test_expired_source_does_not_emit_current_operational_guidance(tmp_path):
    journey = Journey(tmp_path)
    journey.background()
    journey.today = date(2026, 10, 5)
    result = journey.turn(EN, patch(question=EN))
    assert result.case.next_step_advice.kind == "review"
    assert result.case.next_step_advice.requirement_id is None
    assert "official requirements" in result.body and "ask your school" not in result.body
    assert_no_authority(result.case)


def test_model_without_grounded_next_step_does_not_activate_obstacle_reply(tmp_path):
    journey = Journey(tmp_path)
    journey.background()
    result = journey.turn(ZH)
    assert result.case.next_step_advice is None
    assert_no_authority(result.case)


def test_old_deferred_dates_do_not_turn_personal_information_question_into_material_advice(tmp_path):
    journey = Journey(tmp_path)
    journey.background()
    question = "Which personal detail should I provide first?"
    result = journey.turn(question, patch(question=question))
    assert result.case.next_step_advice.kind == "question"
    assert result.case.last_requested_fields == ["full_name"]
    assert_no_authority(result.case)
