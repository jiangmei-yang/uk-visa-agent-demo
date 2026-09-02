from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from visa_agent.channels.outbound import (
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
)


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


@dataclass(frozen=True)
class GmailRawMessage:
    provider_message_id: str
    provider_thread_id: str
    raw: bytes


class GmailAdapter:
    """Thin Gmail API boundary; OAuth service creation lives in deployment setup."""

    def __init__(self, service: Any, user_id: str = "me") -> None:
        self.service = service
        self.user_id = user_id

    def list_message_ids(self, query: str, max_results: int = 20) -> list[str]:
        response = (
            self.service.users()
            .messages()
            .list(userId=self.user_id, q=query, maxResults=max_results)
            .execute()
        )
        return [str(item["id"]) for item in response.get("messages", [])]

    def get_message(self, message_id: str) -> dict[str, Any]:
        result = (
            self.service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="full")
            .execute()
        )
        return dict(result)

    def get_raw_message(self, message_id: str) -> GmailRawMessage:
        result = (
            self.service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="raw")
            .execute()
        )
        if not result.get("threadId") or not result.get("raw"):
            raise ValueError("Gmail raw message is missing threadId or raw content")
        return GmailRawMessage(
            provider_message_id=str(result.get("id", message_id)),
            provider_thread_id=str(result["threadId"]),
            raw=_decode_base64url(str(result["raw"])),
        )

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        result = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId=self.user_id, messageId=message_id, id=attachment_id)
            .execute()
        )
        return _decode_base64url(str(result["data"]))

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        matches = self.list_message_ids(f"in:sent rfc822msgid:{rfc_message_id}", max_results=2)
        return matches[0] if matches else None

    def send_reply(
        self,
        recipient: str,
        subject: str,
        body: str,
        thread_id: str,
        in_reply_to: str,
        references: str,
        message_id: str,
        attachment: tuple[str, bytes] | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message["In-Reply-To"] = in_reply_to
        message["References"] = references
        message["Message-ID"] = message_id
        message.set_content(body)
        if attachment:
            filename, content = attachment
            message.add_attachment(
                content, maintype="application", subtype="zip", filename=filename
            )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = (
            self.service.users()
            .messages()
            .send(userId=self.user_id, body={"raw": raw, "threadId": thread_id})
            .execute()
        )
        return dict(result)


class GmailReplySender:
    """Outbox sender that maps Gmail failures into finite channel semantics."""

    def __init__(self, adapter: GmailAdapter) -> None:
        self.adapter = adapter

    def send(self, request: ReplyRequest) -> str:
        try:
            result = self.adapter.send_reply(
                recipient=request.recipient,
                subject=request.subject,
                body=request.body,
                thread_id=request.thread_id,
                in_reply_to=request.in_reply_to,
                references=request.references,
                message_id=request.rfc_message_id,
                attachment=request.attachment,
            )
        except Exception as error:
            raise _map_gmail_error(error) from error
        provider_message_id = result.get("id")
        if not provider_message_id:
            raise PermanentChannelError("Gmail send response had no message ID")
        return str(provider_message_id)

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        try:
            return self.adapter.find_sent_message(rfc_message_id)
        except Exception as error:
            raise _map_gmail_error(error) from error


def _map_gmail_error(error: Exception) -> Exception:
    if isinstance(error, (TransientChannelError, PermanentChannelError)):
        return error
    status = getattr(getattr(error, "resp", None), "status", None)
    if status in {408, 429} or (isinstance(status, int) and 500 <= status < 600):
        return TransientChannelError(f"Gmail temporarily unavailable (HTTP {status})")
    if status is not None:
        return PermanentChannelError(f"Gmail rejected the operation (HTTP {status})")
    if isinstance(error, (TimeoutError, ConnectionError)):
        return TransientChannelError(f"Gmail transport failure: {type(error).__name__}")
    if isinstance(error, (ValueError, OSError)):
        return PermanentChannelError(
            f"Gmail request could not be completed: {type(error).__name__}"
        )
    return PermanentChannelError(f"Unclassified Gmail failure: {type(error).__name__}")
