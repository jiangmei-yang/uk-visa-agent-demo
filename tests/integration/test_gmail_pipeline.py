from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailRawMessage
from visa_agent.channels.gmail_pipeline import GmailInboxProcessor
from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

ROOT = Path(__file__).resolve().parents[2]


class FakeGmailAdapter:
    def __init__(self, raw_message: GmailRawMessage) -> None:
        self.raw_message = raw_message
        self.queries: list[tuple[str, int]] = []

    def list_message_ids(self, query: str, max_results: int = 20) -> list[str]:
        self.queries.append((query, max_results))
        return [self.raw_message.provider_message_id]

    def get_raw_message(self, message_id: str) -> GmailRawMessage:
        assert message_id == self.raw_message.provider_message_id
        return self.raw_message


class CaptureSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return "gmail-sent-1"


def _raw_email() -> bytes:
    message = EmailMessage()
    message["From"] = "Applicant <applicant@example.test>"
    message["To"] = "service@example.test"
    message["Subject"] = "Application details"
    message["Date"] = format_datetime(datetime(2026, 9, 2, 9, tzinfo=UTC))
    message["Message-ID"] = "<applicant-message-1@example.test>"
    message["References"] = "<earlier-message@example.test>"
    message.set_content(
        """Here are my details.
<!-- DEMO_FACTS
full_name=Ada Lovelace
date_of_birth=1992-12-10
visit_purpose=conference
occupation_status=student
funding_source=self
route_confirmed_standard_visitor=true
-->
"""
    )
    return message.as_bytes()


def test_gmail_raw_message_reaches_workflow_and_replay_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "visa.db")
    raw_message = GmailRawMessage(
        provider_message_id="gmail-provider-1",
        provider_thread_id="gmail-thread-1",
        raw=_raw_email(),
    )
    adapter = FakeGmailAdapter(raw_message)
    processor = GmailInboxProcessor(
        adapter,  # type: ignore[arg-type]
        EmailIngestionBoundary(store, tmp_path / "attachments"),
        WorkflowService(
            store,
            load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
            OfflineFixtureLLM(),
        ),
    )
    try:
        first = processor.process_query("label:visa-agent is:unread")
        counts = store.counts()
        sender = CaptureSender()
        dispatch = OutboxDispatcher(store, sender).dispatch_due(
            datetime(2026, 9, 2, 10, tzinfo=UTC)
        )
        second = processor.process_query("label:visa-agent is:unread")

        assert first[0].status == "blocked"
        assert second[0].status == "duplicate_ignored"
        assert store.counts() == counts
        assert dispatch[0].status == "SENT"
        assert sender.requests[0].subject == "Re: Application details"
        assert adapter.queries == [
            ("label:visa-agent is:unread", 20),
            ("label:visa-agent is:unread", 20),
        ]
        outbox = store.list_outbox()[0]
        assert outbox["reply_subject"] == "Re: Application details"
        assert outbox["in_reply_to"] == "<applicant-message-1@example.test>"
        assert outbox["references_header"] == (
            "<earlier-message@example.test> <applicant-message-1@example.test>"
        )
    finally:
        store.close()
