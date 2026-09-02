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


def test_mime_parser_rejects_invalid_date_and_total_attachment_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed_date = (
        b"From: Applicant <applicant@example.test>\r\n"
        b"Date: definitely-not-a-date\r\n"
        b"Message-ID: <bad-date@example.test>\r\n"
        b"X-Demo-Thread-ID: thread-1\r\n\r\nHello"
    )
    with pytest.raises(ValueError, match="Date header is invalid"):
        parse_email_bytes(malformed_date, tmp_path)

    monkeypatch.setattr(email_fixture, "MAX_TOTAL_ATTACHMENT_BYTES", 5)
    message = EmailMessage()
    message["From"] = "Applicant <applicant@example.test>"
    message["Message-ID"] = "<too-large@example.test>"
    message["X-Demo-Thread-ID"] = "thread-1"
    message.set_content("Two files")
    for filename in ("one.pdf", "two.pdf"):
        message.add_attachment(
            b"123",
            maintype="application",
            subtype="pdf",
            filename=filename,
        )
    with pytest.raises(ValueError, match="exceed 5 total bytes"):
        parse_email_bytes(message.as_bytes(), tmp_path)


def test_same_attachment_name_with_different_bytes_does_not_overwrite(tmp_path: Path) -> None:
    first = parse_email_bytes(_mime_message(content=b"first"), tmp_path)
    second_raw = _mime_message(content=b"second").replace(
        b"provider-message-1@example.test",
        b"provider-message-2@example.test",
    )
    second = parse_email_bytes(second_raw, tmp_path)
    first_path = Path(first.attachment_paths[0])
    second_path = Path(second.attachment_paths[0])
    assert first_path != second_path
    assert first_path.read_bytes() == b"first"
    assert second_path.read_bytes() == b"second"


def test_mime_parser_limits_attachment_count_and_total_message_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = EmailMessage()
    message["From"] = "Applicant <applicant@example.test>"
    message["Message-ID"] = "<too-many@example.test>"
    message["X-Demo-Thread-ID"] = "thread-1"
    message.set_content("Two files")
    for filename in ("one.pdf", "two.pdf"):
        message.add_attachment(
            b"pdf",
            maintype="application",
            subtype="pdf",
            filename=filename,
        )
    raw = message.as_bytes()
    monkeypatch.setattr(email_fixture, "MAX_ATTACHMENT_COUNT", 1)
    with pytest.raises(ValueError, match="more than 1 attachments"):
        parse_email_bytes(raw, tmp_path)

    monkeypatch.setattr(email_fixture, "MAX_ATTACHMENT_COUNT", 20)
    monkeypatch.setattr(email_fixture, "MAX_MESSAGE_BYTES", len(raw) - 1)
    with pytest.raises(ValueError, match="Email exceeds"):
        parse_email_bytes(raw, tmp_path)
