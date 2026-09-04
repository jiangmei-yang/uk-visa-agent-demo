from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from visa_agent.storage.sqlite import SQLiteStore


class TransientChannelError(RuntimeError):
    """A retryable provider or network failure."""


class PermanentChannelError(RuntimeError):
    """A provider rejection that must not be retried automatically."""


class UncertainDeliveryError(RuntimeError):
    """The provider may have accepted the send; reconcile before any resend."""


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


class ReconciliableReplySender(ReplySender, Protocol):
    def find_sent_message(self, rfc_message_id: str) -> str | None: ...


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
        channel: str | None = None,
        allowed_message_types: tuple[str, ...] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.store = store
        self.sender = sender
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.channel = channel
        self.allowed_message_types = allowed_message_types

    def dispatch_due(self, now: datetime, limit: int = 20) -> list[DispatchOutcome]:
        outcomes: list[DispatchOutcome] = []
        for row in self.store.claim_pending_outbox(now, limit, self.channel, self.allowed_message_types):
            outbox_id = str(row["id"])
            attempt = int(row["attempt_count"]) + 1
            try:
                request = self._request_for(row, now)
                provider_message_id = self.sender.send(request)
            except UncertainDeliveryError as error:
                self.store.mark_outbox_uncertain(outbox_id, _safe_error(error))
                outcomes.append(DispatchOutcome(outbox_id, "SENDING"))
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

    def reconcile_sending(
        self,
        sender: ReconciliableReplySender,
        now: datetime,
        limit: int = 20,
    ) -> list[DispatchOutcome]:
        outcomes: list[DispatchOutcome] = []
        for row in self.store.list_sending_outbox(limit, self.channel):
            outbox_id = str(row["id"])
            rfc_message_id = f"<{outbox_id}@visa-agent.local>"
            try:
                provider_message_id = sender.find_sent_message(rfc_message_id)
            except TransientChannelError:
                outcomes.append(DispatchOutcome(outbox_id, "SENDING"))
                continue
            except Exception as error:
                self.store.mark_outbox_ambiguous(outbox_id, _safe_error(error))
                outcomes.append(DispatchOutcome(outbox_id, "AMBIGUOUS"))
                continue
            if provider_message_id:
                self.store.mark_outbox_sent(outbox_id, provider_message_id, now)
                outcomes.append(
                    DispatchOutcome(outbox_id, "SENT", provider_message_id=provider_message_id)
                )
            else:
                self.store.mark_outbox_ambiguous(
                    outbox_id,
                    "No matching provider message was found; manual retry approval is required.",
                )
                outcomes.append(DispatchOutcome(outbox_id, "AMBIGUOUS"))
        return outcomes

    def _request_for(self, row: dict[str, object], now: datetime) -> ReplyRequest:
        outbox_id = str(row["id"])
        case = self.store.get_case(str(row["case_id"]))
        if case is None:
            raise PermanentChannelError("Outbox case no longer exists")
        if int(str(row.get("preparation_control_epoch", 0))) != case.preparation_control_epoch:
            raise PermanentChannelError("Outbox reply belongs to a superseded preparation control epoch")
        if case.preparation_paused and str(row["message_type"]) not in {
            "blocked", "held_update_received",
        }:
            raise PermanentChannelError("Preparation is paused; only a safe receipt or FAQ reply may be sent")
        if int(str(row.get("case_revision", 1))) != case.delivery_revision:
            raise PermanentChannelError("Outbox reply belongs to a superseded delivery revision")
        if str(row["message_type"]) == "ready" and self.store.has_unreviewed_held_updates(case.id):
            raise PermanentChannelError("Final delivery withheld: newer or held applicant updates require review")
        raw_deadline = row.get("send_deadline")
        if raw_deadline and now > datetime.fromisoformat(str(raw_deadline)):
            raise PermanentChannelError("The channel's free-form reply window has expired")
        attachment: tuple[str, bytes] | None = None
        if str(row["message_type"]) == "ready":
            if not case.delivery_path:
                raise PermanentChannelError("Ready reply has no generated pack")
            registered = self.store.connection.execute(
                "SELECT path, sha256, case_revision FROM deliveries WHERE case_id=?", (case.id,),
            ).fetchone()
            if (registered is None or registered["path"] != case.delivery_path
                    or registered["case_revision"] != case.delivery_revision):
                raise PermanentChannelError("Final pack does not match the registered delivery")
            pack_path = Path(case.delivery_path)
            pack_bytes = pack_path.read_bytes()
            if hashlib.sha256(pack_bytes).hexdigest() != registered["sha256"]:
                raise PermanentChannelError("Final pack integrity check failed; review before sending")
            # Send these exact verified bytes, never reopen a mutable path after verification.
            if str(row.get("channel")) != "whatsapp_twilio":
                attachment = (pack_path.name, pack_bytes)
        return ReplyRequest(
            outbox_id=outbox_id,
            recipient=str(row["recipient"] or case.applicant_contact),
            subject=str(row["reply_subject"] or "Re: Standard Visitor application materials"),
            body=str(row["payload"]),
            thread_id=str(row["external_thread_id"] or case.external_thread_id),
            in_reply_to=str(row["in_reply_to"] or f"<{row['event_id']}>"),
            references=str(row["references_header"] or f"<{row['event_id']}>"),
            rfc_message_id=f"<{outbox_id}@visa-agent.local>",
            attachment=attachment,
        )
