from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.channels.twilio_receiver import TwilioWebhookReceiver
from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppWebhook
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

ROOT = Path(__file__).resolve().parents[2]


class AcceptSignature:
    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(url and params and signature)


class FailingWorkflow:
    def process(self, event: InboundEvent) -> tuple[Case, bool, str]:
        del event
        raise TimeoutError("synthetic workflow timeout")


def _form() -> dict[str, str]:
    return {
        "MessageSid": "SM-queued-1",
        "From": "whatsapp:+85255550123",
        "To": "whatsapp:+14155238886",
        "Body": """My name is Lin Chen.
<!-- DEMO_FACTS
full_name=Lin Chen
-->
""",
        "NumMedia": "0",
    }


def _receiver(store: SQLiteStore, tmp_path: Path) -> TwilioWebhookReceiver:
    return TwilioWebhookReceiver(
        TwilioWhatsAppWebhook(
            "synthetic-token",
            "https://example.test/webhooks/twilio/whatsapp",
            tmp_path / "documents",
            signature_validator=AcceptSignature(),
        ),
        store,
    )


def test_webhook_is_durably_enqueued_once_then_worker_processes_and_redacts(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    receiver = _receiver(store, tmp_path)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    workflow = WorkflowService(
        store,
        load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
        OfflineFixtureLLM(),
    )
    try:
        first = receiver.receive(_form(), "valid", received_at=now)
        duplicate = receiver.receive(_form(), "valid", received_at=now)
        outcomes = InboundEventWorker(store, workflow, channel="whatsapp_twilio").process_due(now)

        assert first.queued is True
        assert duplicate.queued is False
        assert outcomes[0].status == "PROCESSED"
        queued = store.list_inbound_queue()[0]
        assert queued["status"] == "PROCESSED"
        assert queued["payload_json"] == "{}"
        assert store.counts() == {
            "cases": 1,
            "processed_events": 1,
            "outbox": 1,
            "deliveries": 0,
        }
    finally:
        store.close()


def test_expired_worker_lease_replays_safely(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    workflow = WorkflowService(
        store,
        load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
        OfflineFixtureLLM(),
    )
    try:
        _receiver(store, tmp_path).receive(_form(), "valid", received_at=now)
        claimed = store.claim_inbound(
            now,
            channel="whatsapp_twilio",
            lease_seconds=30,
        )
        worker = InboundEventWorker(store, workflow, channel="whatsapp_twilio")

        assert claimed[0]["id"] == "SM-queued-1"
        assert worker.process_due(now + timedelta(seconds=29)) == []
        recovered = worker.process_due(now + timedelta(seconds=31))

        assert recovered[0].status == "PROCESSED"
        assert store.counts()["processed_events"] == 1
        assert store.counts()["outbox"] == 1
    finally:
        store.close()


def test_worker_failures_retry_finitely_and_expose_dead_letter(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        _receiver(store, tmp_path).receive(_form(), "valid", received_at=now)
        worker = InboundEventWorker(
            store,
            FailingWorkflow(),
            channel="whatsapp_twilio",
            max_attempts=2,
            base_backoff_seconds=10,
        )

        first = worker.process_due(now)
        too_early = worker.process_due(now + timedelta(seconds=9))
        final = worker.process_due(now + timedelta(seconds=10))

        assert first[0].status == "RETRY"
        assert too_early == []
        assert final[0].status == "FAILED"
        queued = store.list_inbound_queue()[0]
        assert queued["status"] == "FAILED"
        assert queued["attempt_count"] == 2
        assert "private" not in queued["last_error"]
    finally:
        store.close()


def test_two_concurrent_workers_cannot_claim_the_same_inbound_event(tmp_path: Path) -> None:
    initial = SQLiteStore(tmp_path / "visa.db")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    _receiver(initial, tmp_path).receive(_form(), "valid", received_at=now)
    database = initial.path
    initial.close()
    barrier = Barrier(2)

    def claim() -> set[str]:
        store = SQLiteStore(database)
        try:
            barrier.wait()
            return {
                str(row["id"])
                for row in store.claim_inbound(now, channel="whatsapp_twilio", limit=1)
            }
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(claim)
        second_future = executor.submit(claim)
        first = first_future.result()
        second = second_future.result()

    assert first.isdisjoint(second)
    assert first | second == {"SM-queued-1"}
