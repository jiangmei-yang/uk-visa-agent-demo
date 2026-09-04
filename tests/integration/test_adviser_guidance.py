"""Preparation guidance must reach Gmail once, based on durable sent evidence."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL
from visa_agent.workflow.conversation import reply_items
from visa_agent.workflow.service import WorkflowService


class ExtractedStudentFacts:
    version = "guidance-test-extraction"

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        if event.id != "provided-details":
            return CasePatch(updates=[], ambiguities=[])
        return CasePatch(updates=[
            FactUpdate(field="date_of_birth", value="1994.6.12",
                       source_excerpt="1994.6.12", confidence=0.99),
            FactUpdate(field="estimated_trip_cost_gbp", value=6000,
                       source_excerpt="预算6000英镑", confidence=0.99),
        ], ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": f"captured-guidance-{len(self.calls)}"}


def create_case(tmp_path: Path) -> tuple[Path, Case]:
    path = tmp_path / "guidance.db"
    case = Case(
        id="guidance-case", external_thread_id="guidance-thread",
        applicant_contact="fictional-student@example.test", primary_channel="gmail",
        policy_version="test", customer_language="zh",
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )
    case.profile.full_name = "示例申请人"
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.profile.route_confirmed_standard_visitor = True
    store = SQLiteStore(path)
    try:
        store.save_case(case)
    finally:
        store.close()
    return path, case


def inbound(case: Case, event_id: str, text: str, offset: int = 0) -> InboundEvent:
    return InboundEvent(
        id=event_id, external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="英国旅游材料咨询", body=text,
        channel="gmail", received_at=datetime.now(UTC) + timedelta(seconds=offset),
        rfc_message_id=f"<{event_id}@example.test>",
    )


def process_turn(
    path: Path, event: InboundEvent, adapter: CaptureGmail, *, send: bool = True,
) -> tuple[Case, str, list[dict[str, Any]]]:
    # Each turn constructs a new store/workflow, proving advice memory is persisted.
    store = SQLiteStore(path)
    try:
        workflow = WorkflowService(
            store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
            ExtractedStudentFacts(), today_provider=lambda: date(2026, 9, 4),
        )
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked"
        assert case.status == CaseStatus.DRAFT
        assert not case.profile_confirmed and not case.final_summary_confirmed
        assert len(store.list_cases()) == 1
        if send:
            sender = AutomaticGmailReplySender(adapter, store, event.sender)
            sender.withhold_obsolete_unsent()
            dispatcher = OutboxDispatcher(
                store, sender, channel="gmail", allowed_message_types=("blocked",),
            )
            outcomes = dispatcher.dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            assert dispatcher.dispatch_due(event.received_at) == []
        rows = [dict(row) for row in store.list_outbox()]
        body = next(row["payload"] for row in rows if row["event_id"] == event.id)
        if send:
            assert body == adapter.calls[-1]["body"]
        return case, body, rows
    finally:
        store.close()


def assert_useful_guidance_before_questions(case: Case, body: str) -> None:
    assert APPLICATION_URL in body and DOCUMENTS_URL in body
    assert "在读证明" in body
    assert "银行流水" in body and "资金来源" in body
    assert "预算数字本身不能代替资金证明" in body
    assert case.profile.date_of_birth == date(1994, 6, 12)
    assert case.profile.estimated_trip_cost_gbp == 6000
    assert not {"date_of_birth", "estimated_trip_cost_gbp",
                "planned_arrival_date", "planned_departure_date"} & set(case.last_requested_fields)
    questions = reply_items(case)[1]
    assert 0 < len(questions) <= 2
    assert body.count("？") <= 2
    for question in questions:
        assert body.index(APPLICATION_URL) < body.index(question)
        assert body.index(DOCUMENTS_URL) < body.index(question)
    assert "你的出生日期是什么" not in body
    assert "计划哪天" not in body


def test_sent_guidance_survives_reopen_without_repetition_and_explicit_question_is_answered(
    tmp_path: Path,
) -> None:
    path, seed = create_case(tmp_path)
    adapter = CaptureGmail()
    first = inbound(seed, "provided-details", "出生日期是1994.6.12，预算6000英镑。")
    case, body, _ = process_turn(path, first, adapter)
    assert_useful_guidance_before_questions(case, body)
    assert case.guidance_events == {
        "application_overview_v1": first.id,
        "student_self_preparation_v1": first.id,
    }

    second = inbound(seed, "ordinary-followup", "我在整理其他资料。", 1)
    restored, followup_body, _ = process_turn(path, second, adapter)
    assert restored.id == case.id and restored.guidance_events == case.guidance_events
    assert APPLICATION_URL not in followup_body
    assert "预算数字本身不能代替资金证明" not in followup_body
    assert "先给你申请入口" not in followup_body

    third = inbound(seed, "explicit-website-question", "请把申请官网链接再发给我，可以吗？", 2)
    answered, answer_body, _ = process_turn(path, third, adapter)
    assert APPLICATION_URL in answer_body
    assert "Apply now" in answer_body
    assert "预算数字本身不能代替资金证明" not in answer_body
    assert answered.guidance_events["application_overview_v1"] == third.id
    assert len(adapter.calls) == 3


def test_unsent_guidance_is_not_treated_as_communicated(tmp_path: Path) -> None:
    path, seed = create_case(tmp_path)
    adapter = CaptureGmail()
    first = inbound(seed, "provided-details", "出生日期是1994.6.12，预算6000英镑。")
    _, draft, pending_rows = process_turn(path, first, adapter, send=False)
    assert APPLICATION_URL in draft and adapter.calls == []
    assert pending_rows[0]["status"] == "PENDING"

    second = inbound(seed, "before-first-reply", "其他资料我还在整理。", 1)
    case, body, rows = process_turn(path, second, adapter)
    assert_useful_guidance_before_questions(case, body)
    assert set(case.guidance_events.values()) == {second.id}
    assert len(adapter.calls) == 1
    assert next(row for row in rows if row["event_id"] == first.id)["status"] == "FAILED"
    assert next(row for row in rows if row["event_id"] == second.id)["status"] == "SENT"


@pytest.mark.parametrize("text", ["我晚点回复。", "I'll reply later."])
def test_pure_later_reply_does_not_trigger_proactive_guidance(tmp_path: Path, text: str) -> None:
    path, seed = create_case(tmp_path)
    adapter = CaptureGmail()
    case, body, _ = process_turn(path, inbound(seed, "later", text), adapter)
    assert case.customer_answers == [] and case.guidance_events == {}
    assert "https://" not in body and "？" not in body and "?" not in body
    assert len(body) < 120
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("text", ["先不用链接。", "No links for now, please."])
def test_declined_links_do_not_trigger_proactive_link_dump(tmp_path: Path, text: str) -> None:
    path, seed = create_case(tmp_path)
    adapter = CaptureGmail()
    case, body, _ = process_turn(path, inbound(seed, "declined-links", text), adapter)
    assert "https://" not in body
    assert case.guidance_events == {}
    assert len(adapter.calls) == 1
