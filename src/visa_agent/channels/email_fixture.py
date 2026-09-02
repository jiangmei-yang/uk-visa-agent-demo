from __future__ import annotations

from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

from visa_agent.domain.models import InboundEvent


def parse_eml(path: Path, document_dir: Path) -> InboundEvent:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    body = message.get_body(preferencelist=("plain",))
    body_text = body.get_content() if body else ""
    attachment_names = [
        name.strip()
        for name in str(message.get("X-Demo-Attachments", "")).split(",")
        if name.strip()
    ]
    parsed_date = message.get("Date")
    received_at = datetime.now(UTC)
    if parsed_date is not None:
        received_at = datetime.fromtimestamp(parsed_date.datetime.timestamp(), UTC)
    return InboundEvent(
        id=str(message["Message-ID"]).strip("<>"),
        external_thread_id=str(message["X-Demo-Thread-ID"]),
        sender=str(message["From"]),
        subject=str(message["Subject"]),
        body=str(body_text),
        attachment_paths=[str(document_dir / name) for name in attachment_names],
        received_at=received_at,
    )
