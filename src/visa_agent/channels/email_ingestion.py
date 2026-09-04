from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from visa_agent.channels.email_fixture import parse_email_bytes
from visa_agent.domain.models import InboundEvent
from visa_agent.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class EmailIngestionResult:
    event: InboundEvent | None
    failure_code: str | None = None


def _failure_code(error: ValueError) -> str:
    detail = str(error)
    if "exceed" in detail or "more than" in detail:
        return "EMAIL_LIMIT_EXCEEDED"
    if "Unsupported email attachment" in detail:
        return "UNSUPPORTED_ATTACHMENT"
    return "MALFORMED_EMAIL"


class EmailIngestionBoundary:
    def __init__(self, store: SQLiteStore, document_dir: Path) -> None:
        self.store = store
        self.document_dir = document_dir

    def preview(
        self, raw: bytes, *, provider_message_id: str, provider_thread_id: str,
        channel: str = "email", received_at: datetime | None = None,
    ) -> EmailIngestionResult:
        """In-memory control preview: no attachment decode/write or failure-body logging."""
        try:
            event = parse_email_bytes(raw, self.document_dir,
                external_thread_id=provider_thread_id, provider_message_id=provider_message_id,
                channel=channel, materialize_attachments=False, received_at_override=received_at)
        except ValueError as error:
            return EmailIngestionResult(event=None, failure_code=_failure_code(error))
        return EmailIngestionResult(event=event)

    def ingest(
        self,
        raw: bytes,
        *,
        provider_message_id: str,
        provider_thread_id: str,
        channel: str = "email",
        received_at: datetime | None = None,
    ) -> EmailIngestionResult:
        try:
            event = parse_email_bytes(
                raw,
                self.document_dir,
                external_thread_id=provider_thread_id,
                provider_message_id=provider_message_id,
                channel=channel,
                received_at_override=received_at,
            )
        except ValueError as error:
            code = _failure_code(error)
            self.store.record_inbound_failure(
                event_id=provider_message_id,
                thread_id=provider_thread_id,
                reason_code=code,
                detail=f"{type(error).__name__}: {' '.join(str(error).split())[:200]}",
                retryable=False,
            )
            return EmailIngestionResult(event=None, failure_code=code)
        return EmailIngestionResult(event=event)
