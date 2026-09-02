from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from visa_agent.storage.sqlite import SQLiteStore


class TransientChannelError(RuntimeError):
    """A retryable provider or network failure."""


class PermanentChannelError(RuntimeError):
    """A provider rejection that must not be retried automatically."""


@dataclass(frozen=True)
class ReplyRequest:
    outbox_id: str
    recipient: str
    subject: str
    body: str
    thread_id: str
    in_reply_to: str
    references: str
    rfc_message_id: str
    attachment: tuple[str, bytes] | None = None


class ReplySender(Protocol):
    def send(self, request: ReplyRequest) -> str: ...


@dataclass(frozen=True)
class DispatchOutcome:
    outbox_id: str
    status: str
    provider_message_id: str | None = None
    next_attempt_at: datetime | None = None


def _safe_error(error: Exception) -> str:
    message = " ".join(str(error).split())[:200]
    return f"{type(error).__name__}: {message or 'no provider detail'}"


class OutboxDispatcher:
    def __init__(
        self,
        store: SQLiteStore,
        sender: ReplySender,
        *,
        max_attempts: int = 3,
        base_backoff_seconds: int = 60,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.store = store
        self.sender = sender
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds

    def dispatch_due(self, now: datetime, limit: int = 20) -> list[DispatchOutcome]:
        outcomes: list[DispatchOutcome] = []
        for row in self.store.claim_pending_outbox(now, limit):
            outbox_id = str(row["id"])
            attempt = int(row["attempt_count"]) + 1
            try:
                request = self._request_for(row)
                provider_message_id = self.sender.send(request)
            except TransientChannelError as error:
                if attempt >= self.max_attempts:
                    self.store.mark_outbox_failed(outbox_id, _safe_error(error))
                    outcomes.append(DispatchOutcome(outbox_id, "FAILED"))
                    continue
                next_attempt_at = now + timedelta(
                    seconds=self.base_backoff_seconds * (2 ** (attempt - 1))
                )
                self.store.mark_outbox_retry(outbox_id, _safe_error(error), next_attempt_at)
                outcomes.append(
                    DispatchOutcome(outbox_id, "RETRY", next_attempt_at=next_attempt_at)
                )
            except (PermanentChannelError, OSError, ValueError) as error:
                self.store.mark_outbox_failed(outbox_id, _safe_error(error))
                outcomes.append(DispatchOutcome(outbox_id, "FAILED"))
            except Exception as error:
                wrapped = PermanentChannelError(
                    f"Unclassified sender failure: {type(error).__name__}"
                )
                self.store.mark_outbox_failed(outbox_id, _safe_error(wrapped))
                outcomes.append(DispatchOutcome(outbox_id, "FAILED"))
            else:
                self.store.mark_outbox_sent(outbox_id, provider_message_id, now)
                outcomes.append(
                    DispatchOutcome(outbox_id, "SENT", provider_message_id=provider_message_id)
                )
        return outcomes

    def _request_for(self, row: dict[str, object]) -> ReplyRequest:
        outbox_id = str(row["id"])
        case = self.store.get_case(str(row["case_id"]))
        if case is None:
            raise PermanentChannelError("Outbox case no longer exists")
        attachment: tuple[str, bytes] | None = None
        if str(row["message_type"]) == "ready":
            if not case.delivery_path:
                raise PermanentChannelError("Ready reply has no generated pack")
            pack_path = Path(case.delivery_path)
            attachment = (pack_path.name, pack_path.read_bytes())
        return ReplyRequest(
            outbox_id=outbox_id,
            recipient=case.applicant_email,
            subject="Re: Standard Visitor application materials",
            body=str(row["payload"]),
            thread_id=case.email_thread_id,
            in_reply_to=f"<{row['event_id']}>",
            references=f"<{row['event_id']}>",
            rfc_message_id=f"<{outbox_id}@visa-agent.local>",
            attachment=attachment,
        )
