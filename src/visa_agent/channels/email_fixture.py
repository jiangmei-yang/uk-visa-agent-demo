from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from visa_agent.domain.models import InboundEvent

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 20
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_BYTES = 30 * 1024 * 1024


def _attachment_path(document_dir: Path, filename: str, content: bytes) -> Path:
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("Email attachment has no safe filename")
    if Path(safe_name).suffix.lower() != ".pdf":
        raise ValueError(f"Unsupported email attachment type: {safe_name}")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"Email attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {safe_name}")
    document_dir.mkdir(parents=True, exist_ok=True)
    target = document_dir / safe_name
    if target.exists() and target.read_bytes() != content:
        digest = hashlib.sha256(content).hexdigest()[:12]
        target = document_dir / f"{target.stem}-{digest}{target.suffix.lower()}"
    if not target.exists():
        target.write_bytes(content)
    return target


def _body_text(message: EmailMessage) -> str:
    body = message.get_body(preferencelist=("plain",))
    if body is not None:
        return str(body.get_content())
    if message.get_content_maintype() == "text":
        return str(message.get_content())
    return ""


def parse_email_bytes(
    raw: bytes,
    document_dir: Path,
    *,
    external_thread_id: str | None = None,
    provider_message_id: str | None = None,
) -> InboundEvent:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Email exceeds {MAX_MESSAGE_BYTES} bytes")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    raw_message_id = provider_message_id or message.get("Message-ID")
    raw_thread_id = external_thread_id or message.get("X-Demo-Thread-ID")
    if not raw_message_id:
        raise ValueError("Email has no provider message ID")
    if not raw_thread_id:
        raise ValueError("Email has no provider thread ID")
    sender = message.get("From")
    if not sender:
        raise ValueError("Email has no sender")

    parsed_date = message.get("Date")
    received_at = datetime.now(UTC)
    if parsed_date is not None:
        parsed_datetime = getattr(parsed_date, "datetime", None)
        if parsed_datetime is None:
            raise ValueError("Email Date header is invalid")
        received_at = datetime.fromtimestamp(parsed_datetime.timestamp(), UTC)

    attachment_paths: list[str] = []
    mime_attachments = list(message.iter_attachments())
    if mime_attachments:
        if len(mime_attachments) > MAX_ATTACHMENT_COUNT:
            raise ValueError(f"Email has more than {MAX_ATTACHMENT_COUNT} attachments")
        total_attachment_bytes = 0
        for attachment in mime_attachments:
            filename = attachment.get_filename()
            content = attachment.get_payload(decode=True)
            if not filename or not isinstance(content, bytes):
                raise ValueError("Email attachment is missing a filename or payload")
            total_attachment_bytes += len(content)
            if total_attachment_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
                raise ValueError(
                    f"Email attachments exceed {MAX_TOTAL_ATTACHMENT_BYTES} total bytes"
                )
            attachment_paths.append(str(_attachment_path(document_dir, filename, content)))
    else:
        attachment_names = [
            name.strip()
            for name in str(message.get("X-Demo-Attachments", "")).split(",")
            if name.strip()
        ]
        attachment_paths = [str(document_dir / name) for name in attachment_names]

    return InboundEvent(
        id=str(raw_message_id).strip("<>"),
        external_thread_id=str(raw_thread_id),
        sender=str(sender),
        subject=str(message.get("Subject", "")),
        body=_body_text(message),
        attachment_paths=attachment_paths,
        rfc_message_id=(str(message.get("Message-ID")) if message.get("Message-ID") else None),
        references=(str(message.get("References")) if message.get("References") else None),
        received_at=received_at,
    )


def parse_eml(path: Path, document_dir: Path) -> InboundEvent:
    return parse_email_bytes(path.read_bytes(), document_dir)
