from __future__ import annotations

from datetime import date
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from visa_agent.channels.email_fixture import parse_email_bytes
from visa_agent.delivery.pack import generate_pack
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def _real_mime_bytes(fixture_path: Path, document_dir: Path) -> bytes:
    fixture = BytesParser(policy=policy.default).parsebytes(fixture_path.read_bytes())
    message = EmailMessage()
    for header in ("From", "To", "Date", "Message-ID", "Subject", "X-Demo-Thread-ID"):
        message[header] = fixture[header]
    body = fixture.get_body(preferencelist=("plain",))
    message.set_content(body.get_content() if body else "")
    attachment_names = [
        name.strip() for name in str(fixture["X-Demo-Attachments"] or "").split(",") if name.strip()
    ]
    for name in attachment_names:
        message.add_attachment(
            (document_dir / name).read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=name,
        )
    assert "X-Demo-Attachments" not in message
    return message.as_bytes()


def test_standard_mime_thread_reaches_pack_without_demo_attachment_header(tmp_path: Path) -> None:
    generated_documents = tmp_path / "generated"
    extracted_attachments = tmp_path / "inbound"
    output_dir = tmp_path / "output"
    generate_sample_documents(generated_documents)
    policy_snapshot = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    store = SQLiteStore(tmp_path / "visa.db")
    service = WorkflowService(store, policy_snapshot, OfflineFixtureLLM())
    events = [
        parse_email_bytes(
            _real_mime_bytes(path, generated_documents),
            extracted_attachments,
        )
        for path in sorted(Path("samples/emails").glob("*.eml"))
    ]
    try:
        plans: list[str] = []
        packages: list[Path | None] = []
        for event in events:
            case, duplicate, plan = service.process(event)
            package, _ = generate_pack(
                case,
                policy_snapshot,
                store,
                output_dir,
                date(2026, 9, 2),
            )
            assert duplicate is False
            plans.append(plan)
            packages.append(package)

        assert plans == ["blocked", "awaiting_confirmation", "ready"]
        assert packages[:2] == [None, None]
        assert packages[2] is not None and packages[2].is_file()
        assert len(list(extracted_attachments.glob("*.pdf"))) == 9

        counts_before_replay = store.counts()
        for event in events:
            _, duplicate, plan = service.process(event)
            assert duplicate is True
            assert plan == "duplicate_ignored"
        assert store.counts() == counts_before_replay
    finally:
        store.close()
