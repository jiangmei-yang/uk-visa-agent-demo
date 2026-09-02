from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from visa_agent.channels.email_fixture import MAX_ATTACHMENT_BYTES, save_pdf_attachment
from visa_agent.channels.outbound import (
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
)
from visa_agent.domain.models import InboundEvent


class MediaDownloader(Protocol):
    def download(self, url: str) -> bytes: ...


class SignatureValidator(Protocol):
    def validate(self, url: str, params: dict[str, str], signature: str) -> bool: ...


@dataclass(frozen=True)
class TwilioWebhookResult:
    event: InboundEvent
    service_address: str


class TwilioWhatsAppWebhook:
    def __init__(
        self,
        auth_token: str,
        public_url: str,
        document_dir: Path,
        media_downloader: MediaDownloader | None = None,
        signature_validator: SignatureValidator | None = None,
    ) -> None:
        if not auth_token:
            raise ValueError("Twilio auth token is required")
        if not public_url.startswith("https://"):
            raise ValueError("Twilio webhook public URL must use HTTPS")
        self.auth_token = auth_token
        self.public_url = public_url
        self.document_dir = document_dir
        self.media_downloader = media_downloader
        self.signature_validator = signature_validator

    def parse(
        self,
        form: dict[str, str],
        signature: str,
        *,
        received_at: datetime | None = None,
    ) -> TwilioWebhookResult:
        validator = self.signature_validator
        if validator is None:
            validator_type = import_module("twilio.request_validator").RequestValidator
            validator = validator_type(self.auth_token)
        if not signature or not validator.validate(self.public_url, form, signature):
            raise PermissionError("Invalid Twilio webhook signature")

        message_sid = form.get("MessageSid", "").strip()
        sender = form.get("From", "").strip()
        recipient = form.get("To", "").strip()
        if (
            not message_sid
            or not sender.startswith("whatsapp:+")
            or not recipient.startswith("whatsapp:+")
        ):
            raise ValueError("Twilio webhook is missing a valid message SID or WhatsApp address")
        try:
            media_count = int(form.get("NumMedia", "0"))
        except ValueError as error:
            raise ValueError("Twilio NumMedia is invalid") from error
        if media_count < 0 or media_count > 1:
            raise ValueError("WhatsApp intake supports at most one media attachment per message")

        attachment_paths: list[str] = []
        if media_count:
            if self.media_downloader is None:
                raise ValueError("WhatsApp media downloader is not configured")
            media_type = form.get("MediaContentType0", "").split(";", 1)[0].strip().lower()
            media_url = form.get("MediaUrl0", "").strip()
            if media_type != "application/pdf":
                raise ValueError("WhatsApp intake accepts PDF media only")
            _validate_twilio_media_url(media_url)
            content = self.media_downloader.download(media_url)
            if len(content) > MAX_ATTACHMENT_BYTES:
                raise ValueError(f"WhatsApp PDF exceeds {MAX_ATTACHMENT_BYTES} bytes")
            path = save_pdf_attachment(
                self.document_dir,
                f"whatsapp-{message_sid}.pdf",
                content,
            )
            attachment_paths.append(str(path))

        return TwilioWebhookResult(
            event=InboundEvent(
                id=message_sid,
                channel="whatsapp_twilio",
                external_thread_id=sender,
                sender=sender,
                subject="WhatsApp conversation",
                body=form.get("Body", ""),
                attachment_paths=attachment_paths,
                received_at=received_at or datetime.now(UTC),
            ),
            service_address=recipient,
        )


class TwilioWhatsAppSender:
    def __init__(self, client: Any, service_address: str) -> None:
        if not service_address.startswith("whatsapp:+"):
            raise ValueError("Twilio WhatsApp service address is invalid")
        self.client = client
        self.service_address = service_address

    def send(self, request: ReplyRequest) -> str:
        if request.attachment is not None:
            raise PermanentChannelError(
                "WhatsApp ZIP delivery is disabled; use the secure email/review handoff"
            )
        try:
            result = self.client.messages.create(
                body=request.body,
                from_=self.service_address,
                to=request.recipient,
            )
        except Exception as error:
            raise _map_twilio_error(error) from error
        provider_id = getattr(result, "sid", None)
        if not provider_id:
            raise PermanentChannelError("Twilio send response had no message SID")
        return str(provider_id)

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        del rfc_message_id
        return None


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Any,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


class TwilioMediaDownloader:
    """Authenticated, size-bounded downloader with redirects disabled to prevent SSRF."""

    def __init__(self, account_sid: str, auth_token: str, timeout_seconds: float = 10) -> None:
        if not account_sid or not auth_token:
            raise ValueError("Twilio media credentials are required")
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

    def download(self, url: str) -> bytes:
        _validate_twilio_media_url(url)
        credentials = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode()
        ).decode("ascii")
        request = urllib_request.Request(
            url,
            headers={"Authorization": f"Basic {credentials}", "Accept": "application/pdf"},
        )
        opener = urllib_request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                content_type = str(response.headers.get_content_type()).casefold()
                if content_type != "application/pdf":
                    raise ValueError("Twilio media response was not a PDF")
                content = bytes(response.read(MAX_ATTACHMENT_BYTES + 1))
        except urllib_error.HTTPError as error:
            raise OSError(f"Twilio media download failed with HTTP {error.code}") from error
        except urllib_error.URLError as error:
            raise OSError("Twilio media download transport failed") from error
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"WhatsApp PDF exceeds {MAX_ATTACHMENT_BYTES} bytes")
        return content


def _validate_twilio_media_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "api.twilio.com" or host.endswith(".twilio.com") or host.endswith(".twiliocdn.com")
    ):
        raise ValueError("WhatsApp media URL is not an approved Twilio HTTPS host")


def _map_twilio_error(error: Exception) -> Exception:
    if isinstance(error, (TransientChannelError, PermanentChannelError)):
        return error
    status = getattr(error, "status", None)
    if status in {408, 429} or (isinstance(status, int) and 500 <= status < 600):
        return TransientChannelError(f"Twilio temporarily unavailable (HTTP {status})")
    if status is not None:
        return PermanentChannelError(f"Twilio rejected the operation (HTTP {status})")
    if isinstance(error, (TimeoutError, ConnectionError)):
        return TransientChannelError(f"Twilio transport failure: {type(error).__name__}")
    return PermanentChannelError(f"Unclassified Twilio failure: {type(error).__name__}")
