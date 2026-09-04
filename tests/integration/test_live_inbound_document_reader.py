import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent import cli
from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.config import Settings
from visa_agent.documents.natural import DocumentFact, DocumentProposal
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.privacy.consent import ConsentLedger, ProcessingScope
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_live_worker_uses_ordinary_pdf_reader_or_explicitly_holds(tmp_path, monkeypatch, provider):
    settings = Settings(database_path=tmp_path / "case.db", output_dir=tmp_path / "output",
                        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    pdf = tmp_path / "student-letter.pdf"
    document = canvas.Canvas(str(pdf))
    document.drawString(50, 760, "Example University - fictional test letter")
    document.drawString(50, 735, "Alex Example is currently enrolled as a student.")
    document.save()
    extracted = []

    class Model(OfflineFixtureLLM):
        def __init__(self, *args, **kwargs):
            pass

        def extract_document(self, pages):
            extracted.extend(pages)
            return DocumentProposal(kind="student_letter", language="en", classification_page=1,
                classification_excerpt="Alex Example is currently enrolled as a student.", confidence=1,
                facts=[DocumentFact(field="full_name", value="Alex Example", page=1,
                    excerpt="Alex Example", confidence=1)])

    monkeypatch.setattr("visa_agent.llm.deepseek_client.DeepSeekStructuredLLM", Model)
    monkeypatch.setattr("visa_agent.llm.openai_client.OpenAIStructuredLLM", Model)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "read_secret", lambda *a, **kw: "unused-local-stub")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-stub")
    monkeypatch.setattr("sys.argv", ["visa-agent", "inbound-worker", "--channel", "whatsapp_twilio",
                                    "--provider", provider, "--model", "local-stub"])
    store = SQLiteStore(settings.database_path)
    event = InboundEvent(id="ordinary-document", external_thread_id="whatsapp:+10000000000",
        sender="whatsapp:+10000000000", channel="whatsapp_twilio", subject="Documents",
        body="Here is my student letter.", attachment_paths=[str(pdf)], received_at=datetime.now(UTC))
    ledger = ConsentLedger(store)
    ledger.configure(ProcessingScope(provider=provider, model="local-stub"))
    policy_version = load_policy(settings.policy_path).version
    notice_request = event.model_copy(update={
        "id": "processing-notice-request", "body": "How will you process my information?",
        "attachment_paths": [], "received_at": event.received_at - timedelta(minutes=3),
    })
    decision = ledger.handle(notice_request, policy_version)
    assert decision.action == "defer" and not decision.granted
    sent_requests: list[ReplyRequest] = []

    class CapturedSender:
        def send(self, request: ReplyRequest) -> str:
            sent_requests.append(request)
            return "offline-provider-notice-receipt"

    outcomes = OutboxDispatcher(
        store, CapturedSender(), channel="whatsapp_twilio",
        allowed_message_types=("processing_notice",),
    ).dispatch_due(event.received_at - timedelta(minutes=2))
    assert len(outcomes) == len(sent_requests) == 1 and outcomes[0].status == "SENT"
    assert sent_requests[0].attachment is None
    assert sent_requests[0].recipient == event.sender
    notice_row = next(row for row in store.list_outbox() if row["id"] == sent_requests[0].outbox_id)
    assert notice_row["status"] == "SENT" and notice_row["provider_message_id"]
    reference = re.search(r"PC-[A-F0-9]{12}", sent_requests[0].body)
    assert reference is not None
    applicant_grant = event.model_copy(update={
        "id": "applicant-processing-grant",
        "body": "I consent to the processing described in this notice "
                f"(consent reference {reference.group()}).",
        "attachment_paths": [], "received_at": event.received_at - timedelta(minutes=1),
    })
    accepted = ledger.handle(applicant_grant, policy_version)
    assert accepted.action == "control" and accepted.granted
    authorized_case = store.get_case(decision.case_id)
    assert authorized_case is not None and ledger.allowed(authorized_case)
    store.enqueue_inbound(event)
    store.close()
    cli.main()
    store = SQLiteStore(settings.database_path)
    try:
        case = store.list_cases()[0]
        assert len(case.documents) == 1 and not case.delivery_path
        assert store.list_inbound_queue()[0]["status"] == "PROCESSED"
        if provider == "deepseek":
            assert len(extracted) == 1 and "Alex Example" in extracted[0]
            assert case.documents[0].kind == "student_letter"
            assert case.documents[0].status.value == "ACCEPTED_FOR_REVIEW"
        else:
            assert extracted == []
            assert case.documents[0].status.value == "HUMAN_REVIEW_REQUIRED"
            assert any("live PDF reader is not configured" in issue.detail for issue in case.open_blockers())
        assert pdf.exists()
    finally:
        store.close()
