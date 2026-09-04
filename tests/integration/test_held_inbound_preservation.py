import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("status", [CaseStatus.HUMAN_REVIEW_REQUIRED, CaseStatus.READY_FOR_HUMAN_REVIEW])
@pytest.mark.parametrize("queued", [False, True])
def test_held_update_survives_replay_export_and_queue_cleanup(tmp_path, status, queued):
    path = tmp_path / "case.db"
    store = SQLiteStore(path)
    case = Case(id="case", external_thread_id="thread", applicant_contact="person@example.test",
                policy_version="v", status=status)
    store.save_case(case)
    now = datetime(2026, 9, 4, tzinfo=UTC)
    event = InboundEvent(id="correction", external_thread_id="thread", sender=case.applicant_contact,
        subject="补充材料", body="日期改了，请先不要用旧资料。", received_at=now,
        attachment_paths=["private-upload/corrected-letter.pdf"], channel="gmail")
    service = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    if queued:
        store.enqueue_inbound(event)
        InboundEventWorker(store, service, channel="gmail").process_due(now)
        assert store.list_inbound_queue()[0]["payload_json"] == "{}"
    else:
        service.process(event)
    assert service.process(event)[1]
    store.close()
    store = SQLiteStore(path)
    held = store.export_case_data(case.id)["held_inbound_events"]
    assert len(held) == 1
    restored = InboundEvent.model_validate_json(held[0]["payload_json"])
    assert restored == event
    assert store.get_case(case.id).status == status
    assert not store.list_outbox()
    store.delete_case(case.id)
    assert store.list_held_inbound(case.id) == []
    store.close()


def test_sender_mismatch_body_is_not_preserved_as_an_applicant_update(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    case = Case(id="c", external_thread_id="t", applicant_contact="owner@example.test", policy_version="v")
    store.save_case(case)
    service = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    event = InboundEvent(id="wrong", external_thread_id="t", sender="other@example.test", subject="x",
        body="untrusted unrelated body", received_at=datetime.now(UTC))
    assert service.process(event)[2] == "sender_mismatch_rejected"
    assert store.list_held_inbound(case.id) == []
    store.close()


def test_held_payload_failure_cannot_mark_event_processed(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    store.connection.execute("""CREATE TRIGGER fail_hold BEFORE INSERT ON held_inbound_events
        BEGIN SELECT RAISE(ABORT, 'injected hold write failure'); END""")
    event = InboundEvent(id="e", external_thread_id="t", sender="owner@example.test", subject="x",
        body="retain me", received_at=datetime.now(UTC))
    with pytest.raises(sqlite3.IntegrityError):
        store.record_rejected_event(event_id="e", case_id="c", thread_id="t",
            reason_code="HUMAN_REVIEW_CASE_NEW_EVENT", detail="paused", held_event=event)
    assert not store.event_processed("e")
    assert store.list_inbound_failures() == []
    store.close()
