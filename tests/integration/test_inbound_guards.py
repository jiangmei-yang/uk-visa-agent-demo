from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


def _event(
    event_id: str,
    *,
    sender: str = "Applicant <applicant@example.test>",
    received_at: datetime,
    thread_id: str = "guard-thread-1",
) -> InboundEvent:
    return InboundEvent(
        id=event_id,
        external_thread_id=thread_id,
        sender=sender,
        subject="Documents",
        body="Please review this update.",
        received_at=received_at,
    )


def test_sender_mismatch_is_recorded_without_mutating_case_or_replying(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    service = WorkflowService(store, load_policy(POLICY_PATH), OfflineFixtureLLM())
    first_time = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        original, _, _ = service.process(_event("event-1", received_at=first_time))
        evidence_count = len(original.evidence)
        outbox_count = store.counts()["outbox"]
        rejected, duplicate, plan = service.process(
            _event(
                "event-2",
                sender="Attacker <attacker@example.test>",
                received_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
            )
        )
        assert duplicate is False
        assert plan == "sender_mismatch_rejected"
        assert len(rejected.evidence) == evidence_count
        assert store.counts()["outbox"] == outbox_count
        assert store.list_inbound_failures()[0]["reason_code"] == "THREAD_SENDER_MISMATCH"

        _, duplicate_replay, replay_plan = service.process(
            _event(
                "event-2",
                sender="Attacker <attacker@example.test>",
                received_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
            )
        )
        assert duplicate_replay is True
        assert replay_plan == "duplicate_ignored"
        assert len(store.list_inbound_failures()) == 1
    finally:
        store.close()


def test_older_event_is_held_without_reversing_latest_state(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    service = WorkflowService(store, load_policy(POLICY_PATH), OfflineFixtureLLM())
    latest_time = datetime(2026, 9, 2, 10, tzinfo=UTC)
    try:
        case, _, _ = service.process(_event("event-latest", received_at=latest_time))
        stage_before = case.stage
        held, duplicate, plan = service.process(
            _event(
                "event-older",
                received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
            )
        )
        assert duplicate is False
        assert plan == "out_of_order_held"
        assert held.stage == stage_before
        assert held.last_inbound_received_at == latest_time
        assert store.list_inbound_failures()[0]["reason_code"] == "OUT_OF_ORDER_EVENT"
    finally:
        store.close()


def test_new_event_for_ready_case_is_held_for_controlled_revision(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=POLICY_PATH,
    )
    result = run_demo(settings, reset=True)
    store = SQLiteStore(settings.database_path)
    service = WorkflowService(store, load_policy(POLICY_PATH), OfflineFixtureLLM())
    try:
        case_before = store.get_case(result.case.id)
        assert case_before is not None
        held, duplicate, plan = service.process(
            _event(
                "late-finalized-event",
                sender=case_before.applicant_contact,
                thread_id=case_before.external_thread_id,
                received_at=datetime(2026, 9, 2, 14, tzinfo=UTC),
            )
        )
        assert duplicate is False
        assert plan == "finalized_case_held"
        assert held.model_dump() == case_before.model_dump()
        assert store.counts()["outbox"] == 3
        assert store.list_inbound_failures()[0]["reason_code"] == "FINALIZED_CASE_NEW_EVENT"
    finally:
        store.close()
