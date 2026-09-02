from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from visa_agent.channels import email_fixture
from visa_agent.channels.email_fixture import parse_email_bytes, parse_eml


def test_fixture_preserves_thread_and_provider_id(tmp_path: Path) -> None:
    event = parse_eml(Path("samples/emails/01_initial_submission.eml"), tmp_path)
    assert event.id == "demo-message-001@example.test"
    assert event.external_thread_id == "demo-thread-lin-chen-001"
    assert event.sender == "Lin Chen <lin.chen@example.test>"
    assert len(event.attachment_paths) == 7


def _mime_message(*, filename: str = "evidence.pdf", content: bytes = b"%PDF-demo") -> bytes:
    message = EmailMessage()
    message["From"] = "Applicant <applicant@example.test>"
    message["To"] = "visa-agent@example.test"
    message["Date"] = "Tue, 01 Sep 2026 09:00:00 +0800"
    message["Message-ID"] = "<provider-message-1@example.test>"
    message["Subject"] = "Documents"
    message["X-Demo-Thread-ID"] = "provider-thread-1"
    message.set_content("Please find my document attached.")
    message.add_attachment(
        content,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )
    return message.as_bytes()


def test_standard_mime_attachment_is_extracted_with_safe_filename(tmp_path: Path) -> None:
    event = parse_email_bytes(
        _mime_message(filename="../../passport.pdf"),
        tmp_path,
    )
    assert event.id == "provider-message-1@example.test"
    assert event.external_thread_id == "provider-thread-1"
    assert event.body == "Please find my document attached.\n"
    assert event.attachment_paths == [str(tmp_path / "passport.pdf")]
    assert (tmp_path / "passport.pdf").read_bytes() == b"%PDF-demo"


def test_mime_parser_rejects_unsupported_or_oversized_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="Unsupported email attachment type"):
        parse_email_bytes(_mime_message(filename="instructions.txt"), tmp_path)

    monkeypatch.setattr(email_fixture, "MAX_ATTACHMENT_BYTES", 3)
    with pytest.raises(ValueError, match="exceeds 3 bytes"):
        parse_email_bytes(_mime_message(content=b"four"), tmp_path)


def test_mime_parser_requires_provider_identifiers(tmp_path: Path) -> None:
    message = EmailMessage()
    message["From"] = "Applicant <applicant@example.test>"
    message.set_content("Hello")
    with pytest.raises(ValueError, match="provider message ID"):
        parse_email_bytes(message.as_bytes(), tmp_path)
