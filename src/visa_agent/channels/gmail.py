from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from email.message import EmailMessage
from importlib import import_module
from typing import Any

from visa_agent.channels.outbound import (
    PermanentChannelError,
    ReconciliationAccessError,
    ReplyRequest,
    TransientChannelError,
    UncertainDeliveryError,
)


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


@dataclass(frozen=True)
class GmailRawMessage:
    provider_message_id: str
    provider_thread_id: str
    raw: bytes


@dataclass(frozen=True)
class GmailMessagePage:
    message_ids: tuple[str, ...]
    next_page_token: str | None


@dataclass(frozen=True)
class GmailHistoryPage:
    added_message_ids: tuple[str, ...]
    next_page_token: str | None
    history_id: str


class GmailHistoryExpiredError(Exception):
    """A full resync is required; never advance the cursor past unavailable history."""


def _page_token(response: dict[str, Any]) -> str | None:
    token = response.get("nextPageToken")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("Gmail returned an invalid pagination token")
    return token


def _history_id(value: Any) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("Gmail history ID must be a positive decimal string")
    return value


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

    def current_history_id(self) -> str:
        result = self.service.users().getProfile(userId=self.user_id).execute()
        return _history_id(result.get("historyId"))

    def get_intake_metadata(self, message_id: str) -> dict[str, Any]:
        result = self.service.users().messages().get(
            userId=self.user_id, id=message_id, format="metadata",
            metadataHeaders=["From", "To", "Subject", "Auto-Submitted", "List-Id", "Precedence"],
        ).execute()
        return dict(result)

    def list_message_page(self, query: str, page_token: str | None = None) -> GmailMessagePage:
        """One scoped discovery page; callers must persist and follow the continuation."""
        kwargs: dict[str, Any] = {"userId": self.user_id, "q": query, "maxResults": 100,
                                  "includeSpamTrash": False}
        if page_token:
            kwargs["pageToken"] = page_token
        response = self.service.users().messages().list(**kwargs).execute()
        ids = tuple(dict.fromkeys(str(item["id"]) for item in response.get("messages", [])))
        if any(not identifier for identifier in ids):
            raise ValueError("Gmail returned an empty message ID")
        return GmailMessagePage(ids, _page_token(response))

    def list_added_history_page(
        self, start_history_id: str, page_token: str | None = None
    ) -> GmailHistoryPage:
        """No body retrieval. Caller must scope each candidate before processing it.

        History has no sender query. Do not checkpoint response.history_id until the final
        page and its candidates have been durably recorded. IDs are opaque, not counters.
        """
        kwargs: dict[str, Any] = {"userId": self.user_id,
            "startHistoryId": _history_id(start_history_id), "maxResults": 100,
            "historyTypes": ["messageAdded"]}
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            response = self.service.users().history().list(**kwargs).execute()
        except Exception as error:
            if getattr(getattr(error, "resp", None), "status", None) == 404:
                raise GmailHistoryExpiredError("Gmail history unavailable; full sync required") from error
            raise _map_gmail_error(error) from error
        ids = tuple(dict.fromkeys(
            str(item["message"]["id"])
            for record in response.get("history", [])
            for item in record.get("messagesAdded", [])
        ))
        if any(not identifier for identifier in ids):
            raise ValueError("Gmail returned an empty added-message ID")
        return GmailHistoryPage(ids, _page_token(response), _history_id(response.get("historyId")))

    def list_complete_message_ids(self, query: str, limit: int = 100) -> list[str]:
        """Fetch a bounded complete result, never silently discard older conversation mail."""
        if not 1 <= limit <= 500:
            raise ValueError("Complete inbox batch limit must be between 1 and 500")
        identifiers: list[str] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {
                "userId": self.user_id,
                "q": query,
                "maxResults": min(100, limit - len(identifiers)),
            }
            if token:
                kwargs["pageToken"] = token
            response = self.service.users().messages().list(**kwargs).execute()
            identifiers.extend(str(item["id"]) for item in response.get("messages", []))
            token = response.get("nextPageToken")
            if len(identifiers) > limit or (token and len(identifiers) >= limit):
                raise ValueError("Inbox scope exceeds the bounded batch; narrow the query")
            if not token:
                return list(dict.fromkeys(identifiers))
            if token in seen_tokens:
                raise ValueError("Gmail returned a repeated pagination token")
            seen_tokens.add(token)

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
        if len(matches) > 1:
            raise PermanentChannelError("Multiple sent messages match the outbound identifier")
        if matches:
            return matches[0]
        # Gmail can rewrite Message-ID. A private correlation header survives that rewrite.
        # Search is deliberately bounded: an older/unlocated message stays AMBIGUOUS,
        # never becomes an automatic resend on the strength of a negative search.
        recovered = []
        for identifier in self.list_message_ids("in:sent", max_results=100):
            item = (
                self.service.users()
                .messages()
                .get(
                    userId=self.user_id,
                    id=identifier,
                    format="metadata",
                    metadataHeaders=["X-Visa-Agent-Message-ID"],
                )
                .execute()
            )
            values = [
                str(header.get("value", "")).strip()
                for header in item.get("payload", {}).get("headers", [])
                if str(header.get("name", "")).casefold() == "x-visa-agent-message-id"
            ]
            if "SENT" in item.get("labelIds", []) and values == [rfc_message_id]:
                recovered.append(identifier)
        if len(recovered) > 1:
            raise PermanentChannelError("Multiple sent messages match the outbound identifier")
        return recovered[0] if recovered else None

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
        message["X-Visa-Agent-Message-ID"] = message_id
        message["Auto-Submitted"] = "auto-replied"
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
            raise _map_gmail_error(error, sending=True) from error
        provider_message_id = result.get("id")
        if not provider_message_id:
            raise UncertainDeliveryError("Gmail send response had no message ID")
        return str(provider_message_id)

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        try:
            return self.adapter.find_sent_message(rfc_message_id)
        except Exception as error:
            raise _map_gmail_error(error, reconciling=True) from error


def _is_google_refresh_error(error: Exception) -> bool:
    # Gmail is optional: offline deployments must not need the Google SDK. Match the
    # actual exception type, never provider text that could misclassify an ordinary bug.
    try:
        refresh_error = import_module("google.auth.exceptions").RefreshError
    except ImportError:
        return False
    return isinstance(error, refresh_error)


def _map_gmail_error(
    error: Exception, *, sending: bool = False, reconciling: bool = False,
) -> Exception:
    if isinstance(error, (TransientChannelError, PermanentChannelError, UncertainDeliveryError)):
        return error
    status = getattr(getattr(error, "resp", None), "status", None)
    if reconciling and (status == 401 or _is_google_refresh_error(error)):
        return ReconciliationAccessError("Gmail sent evidence unavailable; restore authorization")
    if sending and (status == 408 or (isinstance(status, int) and 500 <= status < 600)):
        return UncertainDeliveryError(f"Gmail send outcome requires reconciliation (HTTP {status})")
    if status == 403:
        try:
            payload = json.loads(getattr(error, "content", b"{}"))
            reasons = {item.get("reason") for item in payload["error"]["errors"]}
        except (ValueError, TypeError, KeyError, AttributeError):
            reasons = set()
        if reasons and reasons <= {"rateLimitExceeded", "userRateLimitExceeded"}:
            return TransientChannelError("Gmail rate limit exceeded (HTTP 403)")
        if reconciling and reasons and reasons <= {
            "authError", "insufficientPermissions", "domainPolicy", "forbidden",
        }:
            return ReconciliationAccessError("Gmail sent evidence unavailable; restore access (HTTP 403)")
    if status in {408, 429} or (isinstance(status, int) and 500 <= status < 600):
        return TransientChannelError(f"Gmail temporarily unavailable (HTTP {status})")
    if status is not None:
        return PermanentChannelError(f"Gmail rejected the operation (HTTP {status})")
    if isinstance(error, (TimeoutError, ConnectionError)):
        if sending:
            return UncertainDeliveryError("Gmail send transport outcome requires reconciliation")
        return TransientChannelError(f"Gmail transport failure: {type(error).__name__}")
    if sending:
        return UncertainDeliveryError("Unclassified Gmail send outcome requires reconciliation")
    if isinstance(error, (ValueError, OSError)):
        return PermanentChannelError(
            f"Gmail request could not be completed: {type(error).__name__}"
        )
    return PermanentChannelError(f"Unclassified Gmail failure: {type(error).__name__}")
