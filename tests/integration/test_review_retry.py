import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import queue_review_retry, review_fingerprint
from visa_agent.workflow.service import WorkflowService


def setup_case(path):
    store = SQLiteStore(path)
    case = Case(id="c", external_thread_id="t", applicant_contact="user@example.test",
        primary_channel="gmail", policy_version="v", status=CaseStatus.HUMAN_REVIEW_REQUIRED,
        stage=WorkflowStage.HUMAN_REVIEW_REQUIRED, human_review_reason="Uncertain extraction",
        profile_confirmed=True, final_summary_confirmed=True,
        confirmation_fingerprint="old", confirmation_kind="final", confirmation_request_event_id="old")
    case.profile.has_serious_history = True
    store.save_case(case)
    event = InboundEvent(id="held", external_thread_id="t", sender=case.applicant_contact,
        channel="gmail", body="已核对无误。", subject="继续申请", received_at=datetime.now(UTC))
    store.record_rejected_event(event_id=event.id, case_id=case.id, thread_id="t",
        reason_code="HUMAN_REVIEW_CASE_NEW_EVENT", detail="held", held_event=event)
    return store, case, event


def retry(store, case, **overrides):
    values = dict(case_id=case.id, held_event_id="held", expected_fingerprint=review_fingerprint(case),
                  actor="Local reviewer", reason="Reviewed the correction; request normal extraction again.")
    values.update(overrides)
    return queue_review_retry(store, **values)


def test_review_queues_normal_validation_and_invalidates_all_old_consent(tmp_path):
    path = tmp_path / "db"
    store, case, event = setup_case(path)
    identifier = retry(store, case)
    resumed = store.get_case(case.id)
    assert resumed.status == CaseStatus.DRAFT
    assert not resumed.profile_confirmed and not resumed.final_summary_confirmed
    assert resumed.confirmation_fingerprint is None and resumed.confirmation_request_event_id is None
    assert resumed.profile == case.profile and resumed.documents == case.documents
    assert store.event_processed(event.id) and not store.event_processed(identifier)
    assert store.list_outbox() == []
    audit = store.export_case_data(case.id)["review_actions"]
    assert len(audit) == 1 and audit[0]["actor"] == "Local reviewer"
    assert Case.model_validate_json(audit[0]["before_json"]) == case
    store.close()
    store = SQLiteStore(path)
    workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    outcomes = InboundEventWorker(store, workflow, channel="gmail_review").process_due(datetime.now(UTC))
    assert outcomes[0].status == "PROCESSED"
    assert store.event_processed(event.id) and store.event_processed(identifier)
    assert len(store.list_held_inbound(case.id)) == 1
    assert not store.get_case(case.id).final_summary_confirmed
    assert store.get_case(case.id).profile.has_serious_history is True
    assert all(row["message_type"] != "ready" for row in store.list_outbox())
    assert InboundEventWorker(store, workflow, channel="gmail_review").process_due(datetime.now(UTC)) == []
    store.delete_case(case.id)
    assert store.connection.execute("SELECT COUNT(*) FROM review_actions").fetchone()[0] == 0
    store.close()


@pytest.mark.parametrize("overrides", [dict(actor=""), dict(reason="ok"), dict(expected_fingerprint="stale"),
                                     dict(held_event_id="missing")])
def test_invalid_operator_action_changes_nothing(tmp_path, overrides):
    store, case, _ = setup_case(tmp_path / "db")
    with pytest.raises(ValueError):
        retry(store, case, **overrides)
    assert store.get_case(case.id) == case
    assert not store.list_inbound_queue() and not store.export_case_data(case.id)["review_actions"]
    store.close()


@pytest.mark.parametrize("status", [CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION])
def test_finalized_case_cannot_be_reopened_by_intake_retry(tmp_path, status):
    store, case, _ = setup_case(tmp_path / "db")
    case.status = status
    store.save_case(case)
    with pytest.raises(ValueError, match="non-finalized"):
        retry(store, case)
    assert store.get_case(case.id) == case
    store.close()


def test_queue_storage_failure_rolls_back_case_and_audit(tmp_path):
    store, case, _ = setup_case(tmp_path / "db")
    store.connection.execute("""CREATE TRIGGER fail_review_queue BEFORE INSERT ON inbound_queue
        BEGIN SELECT RAISE(ABORT, 'injected queue failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        retry(store, case)
    assert store.get_case(case.id) == case
    assert not store.export_case_data(case.id)["review_actions"]
    store.close()


def test_uncertain_send_must_be_reconciled_before_retry(tmp_path):
    store, case, event = setup_case(tmp_path / "db")
    pending = event.model_copy(update={"id": "prior-send"})
    store.commit_event(case, pending, "blocked", "Prior reply")
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='SENDING'")
    with pytest.raises(ValueError, match="uncertain"):
        retry(store, case)
    assert store.get_case(case.id) == case
    assert store.list_outbox()[0]["status"] == "SENDING"
    assert not store.list_inbound_queue()
    store.close()
