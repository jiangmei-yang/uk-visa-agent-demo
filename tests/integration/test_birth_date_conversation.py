"""Exercise guarded extraction through durable cases and the actual Gmail reply sender.

Only the model's proposed facts and Gmail network transport are replaced. These tests
do not assert that a live model will always extract the supplied birthday correctly.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent, ProvenanceState
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


class BirthdayExtractionModel:
    version = "birthday-lifecycle-test-double"

    def __init__(self, initial_excerpt: str, initial_model_value: str) -> None:
        self.initial_excerpt = initial_excerpt
        self.initial_model_value = initial_model_value
        self.events: list[InboundEvent] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event)
        values = {
            "birthday-provided": (self.initial_model_value, self.initial_excerpt),
            "birthday-corrected": ("1994-06-13", "1994年6月13日"),
        }
        if event.id not in values:
            return CasePatch(updates=[], ambiguities=[])
        value, excerpt = values[event.id]
        return CasePatch(updates=[FactUpdate(
            field="date_of_birth", value=value, source_excerpt=excerpt, confidence=0.99,
        )], ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CapturedGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": f"captured-reply-{len(self.calls)}"}


@pytest.mark.parametrize("birthday_excerpt,initial_model_value", [
    ("1994.6.12", "1994-06-12"),
    ("1994年6月12日", "1994-06-12"),
    ("1994.6.12", "1994.6.12"),
    ("1994年6月12日", "1994年6月12日"),
])
def test_birthday_is_not_reasked_and_survives_reopen_correction_and_replay(
    tmp_path: Path, birthday_excerpt: str, initial_model_value: str,
) -> None:
    db_path = tmp_path / "birthday-conversation.db"
    case = Case(
        id="birthday-case", external_thread_id="birthday-thread",
        applicant_contact="fictional-applicant@example.test", primary_channel="gmail",
        policy_version="test", customer_language="zh",
        last_requested_fields=["planned_arrival_date", "planned_departure_date", "date_of_birth"],
    )
    case.profile.full_name = "示例申请人"
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    store = SQLiteStore(db_path)
    try:
        store.save_case(case)
    finally:
        store.close()

    model = BirthdayExtractionModel(birthday_excerpt, initial_model_value)
    adapter = CapturedGmail()
    now = datetime.now(UTC)
    turns = [
        ("birthday-provided", f"我不是说了日期还没定吗？我的出生日期是{birthday_excerpt}", date(1994, 6, 12)),
        ("other-preparations", "我会整理一下其他材料。", date(1994, 6, 12)),
        ("birthday-corrected", "出生日期写错了，应为1994年6月13日。", date(1994, 6, 13)),
    ]
    deferred_dates = {"planned_arrival_date", "planned_departure_date"}

    for index, (event_id, body, expected_birthday) in enumerate(turns):
        # The process and store are reconstructed on every incoming message.
        store = SQLiteStore(db_path)
        try:
            guard = GuardedLLM(model)
            workflow = WorkflowService(
                store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), guard,
                today_provider=lambda: date(2026, 9, 4),
            )
            event = InboundEvent(
                id=event_id, external_thread_id=case.external_thread_id,
                sender=case.applicant_contact, subject="英国旅游材料咨询", body=body,
                channel="gmail", received_at=now + timedelta(seconds=index),
                rfc_message_id=f"<{event_id}@example.test>",
            )
            result, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == "blocked"
            assert not guard.last_extraction_fallback
            assert result.id == case.id
            assert len(store.list_cases()) == 1
            assert isinstance(result.profile.date_of_birth, date)
            assert result.profile.date_of_birth == expected_birthday
            assert result.profile.model_dump(mode="json")["date_of_birth"] == expected_birthday.isoformat()
            assert result.status == CaseStatus.DRAFT
            assert not result.profile_confirmed and not result.final_summary_confirmed
            assert result.profile.planned_arrival_date is None
            assert result.profile.planned_departure_date is None
            assert set(result.deferred_fields) == deferred_dates
            assert not ({"date_of_birth"} | deferred_dates) & set(result.last_requested_fields)

            if index:
                assert model.events[-1].known_profile["date_of_birth"] == "1994-06-12"
                assert not ({"date_of_birth"} | deferred_dates) & set(model.events[-1].requested_fields)

            active = result.active_evidence("date_of_birth")
            assert len(active) == 1
            assert active[0].value == expected_birthday.isoformat()
            assert active[0].provenance_state == ProvenanceState.EXTRACTED_UNVERIFIED
            assert not active[0].confirmed
            if index == 0:
                assert active[0].source_excerpt == birthday_excerpt
            if index == 2:
                assert result.latest_changes["date_of_birth"] == "1994-06-13"
                original = next(item for item in result.evidence if item.source_event_id == "birthday-provided")
                assert original.superseded
                assert active[0].source_event_id == "birthday-corrected"

            dispatcher = OutboxDispatcher(
                store, AutomaticGmailReplySender(adapter, store, case.applicant_contact),
                channel="gmail", allowed_message_types=("blocked",),
            )
            outcomes = dispatcher.dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            sent = adapter.calls[-1]["body"]
            assert "你的出生日期是什么" not in sent
            assert "计划哪天" not in sent
            assert "哪天离开" not in sent
            if index == 2:
                assert "1994-06-13" in sent
            row = next(item for item in store.list_outbox() if item["event_id"] == event_id)
            assert row["payload"] == sent

            before_replay = store.get_case(case.id).model_dump_json()
            extraction_count = len(model.events)
            assert workflow.process(event)[1]
            assert len(model.events) == extraction_count
            assert store.get_case(case.id).model_dump_json() == before_replay
            assert dispatcher.dispatch_due(event.received_at) == []
            assert len(adapter.calls) == index + 1
        finally:
            store.close()
