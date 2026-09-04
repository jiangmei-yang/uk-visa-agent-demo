from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from typing import Any

import pytest

from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.outbound import (
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
    UncertainDeliveryError,
)


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class Call:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result

    def execute(self) -> dict[str, Any]:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Attachments:
    def __init__(self, messages: Messages) -> None:
        self.messages = messages

    def get(self, **kwargs: Any) -> Call:
        self.messages.attachment_arguments = kwargs
        return Call(self.messages.attachment_result)


class Messages:
    def __init__(self) -> None:
        self.list_result: dict[str, Any] | Exception = {"messages": []}
        self.get_result: dict[str, Any] | Exception = {}
        self.send_result: dict[str, Any] | Exception = {"id": "gmail-sent-1"}
        self.attachment_result: dict[str, Any] | Exception = {"data": ""}
        self.list_arguments: dict[str, Any] = {}
        self.get_arguments: dict[str, Any] = {}
        self.send_arguments: dict[str, Any] = {}
        self.attachment_arguments: dict[str, Any] = {}

    def list(self, **kwargs: Any) -> Call:
        self.list_arguments = kwargs
        return Call(self.list_result)

    def get(self, **kwargs: Any) -> Call:
        self.get_arguments = kwargs
        return Call(self.get_result)

    def send(self, **kwargs: Any) -> Call:
        self.send_arguments = kwargs
        return Call(self.send_result)

    def attachments(self) -> Attachments:
        return Attachments(self)


class Service:
    def __init__(self, messages: Messages) -> None:
        self._messages = messages

    def users(self) -> Service:
        return self

    def messages(self) -> Messages:
        return self._messages


def test_complete_inbox_batch_follows_pagination() -> None:
    class PagedMessages(Messages):
        def list(self, **kwargs: Any) -> Call:
            if kwargs.get("pageToken") == "page-two":
                return Call({"messages": [{"id": "old"}]})
            return Call({"messages": [{"id": "new"}], "nextPageToken": "page-two"})

    adapter = GmailAdapter(Service(PagedMessages()))
    assert adapter.list_complete_message_ids("bounded", limit=2) == ["new", "old"]
    with pytest.raises(ValueError, match="exceeds"):
        adapter.list_complete_message_ids("bounded", limit=1)


def test_complete_inbox_batch_rejects_repeated_page_tokens() -> None:
    messages = Messages()
    messages.list_result = {"messages": [], "nextPageToken": "loop"}
    with pytest.raises(ValueError, match="repeated"):
        GmailAdapter(Service(messages)).list_complete_message_ids("bounded")


def _reply_request() -> ReplyRequest:
    return ReplyRequest(
        outbox_id="out-1",
        recipient="applicant@example.test",
        subject="Re: Application",
        body="Please review the summary.",
        thread_id="gmail-thread-1",
        in_reply_to="<inbound@example.test>",
        references="<first@example.test> <inbound@example.test>",
        rfc_message_id="<out-1@visa-agent.local>",
        attachment=("review-pack.zip", b"synthetic zip bytes"),
    )


def test_gmail_raw_message_and_attachment_decode_unpadded_base64url() -> None:
    messages = Messages()
    raw = b"From: applicant@example.test\r\n\r\nHello"
    messages.get_result = {"id": "gmail-1", "threadId": "thread-1", "raw": _encoded(raw)}
    messages.attachment_result = {"data": _encoded(b"attachment bytes")}
    adapter = GmailAdapter(Service(messages))

    result = adapter.get_raw_message("gmail-1")
    attachment = adapter.get_attachment("gmail-1", "attachment-1")

    assert result.raw == raw
    assert result.provider_thread_id == "thread-1"
    assert messages.get_arguments["format"] == "raw"
    assert attachment == b"attachment bytes"


def test_gmail_reply_preserves_thread_headers_and_deterministic_message_id() -> None:
    messages = Messages()
    sender = GmailReplySender(GmailAdapter(Service(messages)))

    provider_id = sender.send(_reply_request())

    raw = base64.urlsafe_b64decode(messages.send_arguments["body"]["raw"])
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert provider_id == "gmail-sent-1"
    assert messages.send_arguments["body"]["threadId"] == "gmail-thread-1"
    assert str(parsed["Message-ID"]) == "<out-1@visa-agent.local>"
    assert str(parsed["X-Visa-Agent-Message-ID"]) == "<out-1@visa-agent.local>"
    assert str(parsed["In-Reply-To"]) == "<inbound@example.test>"
    assert str(parsed["References"]) == "<first@example.test> <inbound@example.test>"
    attachment = next(parsed.iter_attachments())
    assert attachment.get_filename() == "review-pack.zip"
    assert attachment.get_payload(decode=True) == b"synthetic zip bytes"


def test_gmail_reconciliation_searches_sent_mail_by_rfc_message_id() -> None:
    messages = Messages()
    messages.list_result = {"messages": [{"id": "gmail-found-1"}]}
    sender = GmailReplySender(GmailAdapter(Service(messages)))

    result = sender.find_sent_message("<out-1@visa-agent.local>")

    assert result == "gmail-found-1"
    assert messages.list_arguments["q"] == "in:sent rfc822msgid:<out-1@visa-agent.local>"
    assert messages.list_arguments["maxResults"] == 2


def test_gmail_send_without_provider_id_requires_reconciliation() -> None:
    messages = Messages()
    messages.send_result = {"threadId": "gmail-thread-1"}
    sender = GmailReplySender(GmailAdapter(Service(messages)))

    with pytest.raises(UncertainDeliveryError, match="no message ID"):
        sender.send(_reply_request())


@pytest.mark.parametrize("sent_label,expected", [(True, "gmail-found-1"), (False, None)])
def test_reconcile_rewritten_message_id(sent_label: bool, expected: str | None) -> None:
    class RewrittenMessages(Messages):
        def list(self, **kwargs: Any) -> Call:
            return Call(
                {"messages": []}
                if "rfc822msgid:" in kwargs["q"]
                else {"messages": [{"id": "gmail-found-1"}]}
            )

    messages = RewrittenMessages()
    messages.get_result = {
        "labelIds": ["SENT"] if sent_label else ["INBOX"],
        "payload": {
            "headers": [{"name": "x-visa-agent-message-id", "value": "<out-1@visa-agent.local>"}]
        },
    }
    assert GmailAdapter(Service(messages)).find_sent_message("<out-1@visa-agent.local>") == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [(503, UncertainDeliveryError), (429, TransientChannelError), (403, PermanentChannelError)],
)
def test_gmail_http_failures_map_to_finite_channel_semantics(
    status: int,
    expected: type[Exception],
) -> None:
    messages = Messages()
    error = RuntimeError("provider detail must not escape")
    error.resp = SimpleNamespace(status=status)  # type: ignore[attr-defined]
    messages.send_result = error
    sender = GmailReplySender(GmailAdapter(Service(messages)))

    with pytest.raises(expected, match=f"HTTP {status}"):
        sender.send(_reply_request())


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), OSError(), RuntimeError(), ValueError()])
def test_gmail_send_transport_uncertainty_does_not_allow_blind_retry(error: Exception) -> None:
    messages = Messages()
    messages.send_result = error
    with pytest.raises(UncertainDeliveryError):
        GmailReplySender(GmailAdapter(Service(messages))).send(_reply_request())


@pytest.mark.parametrize("reason,expected", [
    ("rateLimitExceeded", TransientChannelError),
    ("userRateLimitExceeded", TransientChannelError),
    ("domainPolicy", PermanentChannelError),
    ("dailyLimitExceeded", PermanentChannelError),
])
def test_gmail_403_distinguishes_quota_from_permissions(reason: str, expected: type[Exception]) -> None:
    import json

    messages = Messages()
    error = RuntimeError("sensitive provider detail")
    error.resp = SimpleNamespace(status=403)  # type: ignore[attr-defined]
    error.content = json.dumps({"error": {"errors": [{"reason": reason}]}})  # type: ignore[attr-defined]
    messages.send_result = error
    with pytest.raises(expected):
        GmailReplySender(GmailAdapter(Service(messages))).send(_reply_request())
