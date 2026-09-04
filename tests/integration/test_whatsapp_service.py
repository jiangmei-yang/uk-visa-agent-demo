from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from visa_agent.channels.whatsapp_service import CurrentWhatsAppSender, run_cycle
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def event(identifier, now):
    return InboundEvent(id=identifier, external_thread_id="whatsapp:+10000000000",
        sender="whatsapp:+10000000000", channel="whatsapp_twilio", subject="Documents",
        body="Please help me prepare.", received_at=now)


def setup(tmp_path, fail_send=False):
    store = SQLiteStore(tmp_path / "case.db")
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if fail_send:
            raise TimeoutError("Unknown provider outcome")
        return SimpleNamespace(sid="SM-captured")

    sender = CurrentWhatsAppSender(SimpleNamespace(messages=SimpleNamespace(create=send)),
        "whatsapp:+10000000001", "https://sandbox.example.test/webhooks/twilio/whatsapp/status", store)
    workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                               OfflineFixtureLLM())
    return store, workflow, sender, calls


def test_service_drains_intake_then_sends_only_latest_reply(tmp_path):
    store, workflow, sender, calls = setup(tmp_path)
    now = datetime.now(UTC)
    try:
        store.enqueue_inbound(event("first", now - timedelta(seconds=2)))
        store.enqueue_inbound(event("second", now - timedelta(seconds=1)))
        assert run_cycle(store, workflow, sender, now, inbound_limit=1)["phase"] == "intake_wait"
        assert calls == []
        assert run_cycle(store, workflow, sender, now, inbound_limit=1)["dispatch_outcomes"] == ["SENT"]
        assert len(calls) == 1
        assert run_cycle(store, workflow, sender, now)["dispatched"] == 0
        rows = store.list_outbox()
        assert next(row for row in rows if row["event_id"] == "first")["status"] == "FAILED"
        assert next(row for row in rows if row["event_id"] == "second")["status"] == "SENT"
    finally:
        store.close()


def test_failed_intake_keeps_prior_draft_unsent(tmp_path):
    store, workflow, sender, calls = setup(tmp_path)
    now = datetime.now(UTC)
    workflow.process(event("first", now - timedelta(seconds=2)))
    store.enqueue_inbound(event("new", now - timedelta(seconds=1)))

    class UnavailableWorkflow:
        def process(self, incoming):
            raise OSError("processing unavailable")

    try:
        assert run_cycle(store, UnavailableWorkflow(), sender, now)["phase"] == "intake_wait"
        assert calls == []
        assert store.list_outbox()[0]["status"] == "PENDING"
        assert store.list_inbound_queue()[0]["status"] == "RETRY"
    finally:
        store.close()


def test_uncertain_send_is_not_repeated_on_next_cycle(tmp_path):
    store, workflow, sender, calls = setup(tmp_path, fail_send=True)
    now = datetime.now(UTC)
    store.enqueue_inbound(event("first", now - timedelta(seconds=1)))
    try:
        assert run_cycle(store, workflow, sender, now)["dispatch_outcomes"] == ["SENDING"]
        assert run_cycle(store, workflow, sender, now + timedelta(seconds=10))["dispatched"] == 0
        assert len(calls) == 1 and store.list_outbox()[0]["status"] == "AMBIGUOUS"
    finally:
        store.close()


def test_service_does_not_claim_email_or_final_pack_outbox(tmp_path):
    store, workflow, sender, calls = setup(tmp_path)
    now = datetime.now(UTC)
    case = Case(id="case", external_thread_id="t", applicant_contact="fictional@example.test",
                policy_version="v")
    mail = event("mail", now).model_copy(update={"channel": "gmail", "external_thread_id": "t"})
    final = event("final", now).model_copy(update={"external_thread_id": "t"})
    store.commit_event(case, mail, "blocked", "email draft")
    store.commit_event(case, final, "ready", "final awaiting review")
    try:
        assert run_cycle(store, workflow, sender, now)["dispatched"] == 0
        assert calls == [] and all(row["status"] == "PENDING" for row in store.list_outbox())
    finally:
        store.close()


def test_service_rechecks_reply_window_after_intake(tmp_path):
    store, workflow, sender, calls = setup(tmp_path)
    # Model/document processing began inside the window, but dispatch happens outside it.
    cycle_started = datetime.now(UTC) - timedelta(minutes=2)
    incoming = event("near-deadline", cycle_started - timedelta(hours=24) + timedelta(minutes=1))
    store.enqueue_inbound(incoming)
    try:
        result = run_cycle(store, workflow, sender, cycle_started)
        assert result["dispatch_outcomes"] == ["FAILED"]
        assert calls == []
        row = store.list_outbox()[0]
        assert "window has expired" in row["last_error"]
        assert store.list_inbound_queue()[0]["status"] == "PROCESSED"
        assert store.get_case(row["case_id"]) is not None
        assert run_cycle(store, workflow, sender, datetime.now(UTC))["dispatched"] == 0
    finally:
        store.close()
