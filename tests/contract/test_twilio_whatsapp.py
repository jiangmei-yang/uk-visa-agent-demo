from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.outbound import (
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
)
from visa_agent.channels.twilio_whatsapp import (
    TwilioWhatsAppSender,
    TwilioWhatsAppWebhook,
)


class SignatureValidator:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls: list[tuple[str, dict[str, str], str]] = []

    def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
        self.calls.append((url, params, signature))
        return self.valid


class Downloader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.urls: list[str] = []

    def download(self, url: str) -> bytes:
        self.urls.append(url)
        return self.content


def _form(**overrides: str) -> dict[str, str]:
    result = {
        "MessageSid": "SM-synthetic-1",
        "From": "whatsapp:+85255550123",
        "To": "whatsapp:+14155238886",
        "Body": "Please help me prepare my visitor documents.",
        "NumMedia": "0",
    }
    result.update(overrides)
    return result


def _request() -> ReplyRequest:
    return ReplyRequest(
        outbox_id="out-whatsapp-1",
        recipient="whatsapp:+85255550123",
        subject="unused on WhatsApp",
        body="Please send the missing document.",
        thread_id="whatsapp:+85255550123",
        in_reply_to="<unused>",
        references="<unused>",
        rfc_message_id="<out-whatsapp-1@visa-agent.local>",
    )


def test_signed_text_webhook_becomes_channel_neutral_event(tmp_path: Path) -> None:
    validator = SignatureValidator()
    boundary = TwilioWhatsAppWebhook(
        "synthetic-auth-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path,
        signature_validator=validator,
    )
    form = _form()

    result = boundary.parse(
        form,
        "valid-signature",
        received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
    )

    assert result.event.id == "SM-synthetic-1"
    assert result.event.channel == "whatsapp_twilio"
    assert result.event.external_thread_id == "whatsapp:+85255550123"
    assert result.event.sender == "whatsapp:+85255550123"
    assert result.service_address == "whatsapp:+14155238886"
    assert validator.calls == [
        ("https://example.test/webhooks/twilio/whatsapp", form, "valid-signature")
    ]


def test_invalid_webhook_signature_is_rejected_before_parsing(tmp_path: Path) -> None:
    boundary = TwilioWhatsAppWebhook(
        "synthetic-auth-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path,
        signature_validator=SignatureValidator(valid=False),
    )

    with pytest.raises(PermissionError, match="signature"):
        boundary.parse(_form(From="not-a-whatsapp-address"), "invalid")


def test_one_pdf_media_is_downloaded_from_allowlisted_twilio_host(tmp_path: Path) -> None:
    downloader = Downloader(b"%PDF-1.4 synthetic")
    boundary = TwilioWhatsAppWebhook(
        "synthetic-auth-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path,
        media_downloader=downloader,
        signature_validator=SignatureValidator(),
    )
    media_url = "https://api.twilio.com/2010-04-01/Accounts/AC/Messages/SM/Media/ME"

    result = boundary.parse(
        _form(NumMedia="1", MediaContentType0="application/pdf", MediaUrl0=media_url),
        "valid",
    )

    assert downloader.urls == [media_url]
    assert Path(result.event.attachment_paths[0]).read_bytes() == b"%PDF-1.4 synthetic"


@pytest.mark.parametrize(
    "overrides",
    [
        {"NumMedia": "2"},
        {
            "NumMedia": "1",
            "MediaContentType0": "image/jpeg",
            "MediaUrl0": "https://api.twilio.com/x",
        },
        {
            "NumMedia": "1",
            "MediaContentType0": "application/pdf",
            "MediaUrl0": "https://attacker.test/x",
        },
    ],
)
def test_unsupported_or_untrusted_media_fails_closed(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    boundary = TwilioWhatsAppWebhook(
        "synthetic-auth-token",
        "https://example.test/webhooks/twilio/whatsapp",
        tmp_path,
        media_downloader=Downloader(b"content"),
        signature_validator=SignatureValidator(),
    )

    with pytest.raises(ValueError):
        boundary.parse(_form(**overrides), "valid")


def test_twilio_sender_sends_text_and_returns_message_sid() -> None:
    create_calls: list[dict[str, str]] = []

    def create(**kwargs: str) -> SimpleNamespace:
        create_calls.append(kwargs)
        return SimpleNamespace(sid="SM-sent-1")

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    sender = TwilioWhatsAppSender(client, "whatsapp:+14155238886")

    result = sender.send(_request())

    assert result == "SM-sent-1"
    assert create_calls == [
        {
            "body": "Please send the missing document.",
            "from_": "whatsapp:+14155238886",
            "to": "whatsapp:+85255550123",
        }
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(503, TransientChannelError), (429, TransientChannelError), (400, PermanentChannelError)],
)
def test_twilio_send_errors_map_to_finite_channel_semantics(
    status: int,
    expected: type[Exception],
) -> None:
    error = RuntimeError("provider detail")
    error.status = status  # type: ignore[attr-defined]

    def create(**kwargs: str) -> None:
        del kwargs
        raise error

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    sender = TwilioWhatsAppSender(client, "whatsapp:+14155238886")

    with pytest.raises(expected, match=f"HTTP {status}"):
        sender.send(_request())
