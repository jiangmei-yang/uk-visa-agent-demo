from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppWebhook
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

ROOT = Path(__file__).resolve().parents[2]


class AcceptSignature:
    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        return bool(url and params and signature)


class CaptureSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return "SM-sent-1"


def _form(message_sid: str = "SM-inbound-1") -> dict[str, str]:
    return {
        "MessageSid": message_sid,
        "From": "whatsapp:+85255550123",
        "To": "whatsapp:+14155238886",
        "Body": """My details are below.
<!-- DEMO_FACTS
full_name=Lin Chen
date_of_birth=1997-04-18
visit_purpose=conference
occupation_status=student
funding_source=self
route_confirmed_standard_visitor=true
-->
""",
        "NumMedia": "0",
    }


def test_whatsapp_event_reuses_workflow_outbox_and_idempotency(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    boundary = TwilioWhatsAppWebhook(
        "synthetic-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path / "documents",
        signature_validator=AcceptSignature(),
    )
    service = WorkflowService(
        store,
        load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
        OfflineFixtureLLM(),
    )
    received_at = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        event = boundary.parse(_form(), "valid", received_at=received_at).event
        case, duplicate, plan = service.process(event)
        counts = store.counts()
        replayed, replay_duplicate, replay_plan = service.process(event)

        assert duplicate is False
        assert plan == "awaiting_confirmation"
        assert replay_duplicate is True
        assert replay_plan == "duplicate_ignored"
        assert replayed == case
        assert store.counts() == counts
        assert case.primary_channel == "whatsapp_twilio"
        assert case.applicant_contact == "whatsapp:+85255550123"
        outbox = store.list_outbox()[0]
        assert outbox["channel"] == "whatsapp_twilio"
        assert outbox["recipient"] == "whatsapp:+85255550123"
        assert outbox["send_deadline"] == (received_at + timedelta(hours=24)).isoformat()
    finally:
        store.close()


def test_whatsapp_worker_sends_within_window_and_stops_after_expiry(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    boundary = TwilioWhatsAppWebhook(
        "synthetic-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path / "documents",
        signature_validator=AcceptSignature(),
    )
    service = WorkflowService(
        store,
        load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
        OfflineFixtureLLM(),
    )
    received_at = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        first = boundary.parse(_form("SM-within"), "valid", received_at=received_at).event
        second = boundary.parse(
            _form("SM-expired"), "valid", received_at=received_at
        ).event.model_copy(
            update={
                "external_thread_id": "whatsapp:+85255550999",
                "sender": "whatsapp:+85255550999",
            }
        )
        service.process(first)
        service.process(second)
        rows = store.list_outbox()
        expired_row = next(row for row in rows if row["event_id"] == "SM-expired")
        with store.connection:
            store.connection.execute(
                "UPDATE outbox SET send_deadline = ? WHERE id = ?",
                ((received_at - timedelta(seconds=1)).isoformat(), expired_row["id"]),
            )
        sender = CaptureSender()
        outcomes = OutboxDispatcher(
            store,
            sender,
            channel="whatsapp_twilio",
        ).dispatch_due(received_at)

        assert sorted(outcome.status for outcome in outcomes) == ["FAILED", "SENT"]
        assert len(sender.requests) == 1
        assert sender.requests[0].recipient == "whatsapp:+85255550123"
        expired = next(row for row in store.list_outbox() if row["id"] == expired_row["id"])
        assert "reply window has expired" in expired["last_error"]
    finally:
        store.close()
