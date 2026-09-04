from datetime import date, timedelta
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def setup_case(
    tmp_path: Path, channel: str = "email_fixture"
) -> tuple[SQLiteStore, WorkflowService, Case, InboundEvent]:
    documents = tmp_path / "documents"
    generate_sample_documents(documents)
    store = SQLiteStore(tmp_path / "case.db")
    service = WorkflowService(
        store,
        load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
        OfflineFixtureLLM(),
        today_provider=lambda: date(2026, 9, 4),
    )
    events = [
        parse_eml(path, documents).model_copy(update={"channel": channel})
        for path in sorted(Path("samples/emails").glob("*.eml"))
    ]
    service.process(events[0])
    case, _, plan = service.process(events[1])
    assert plan == "awaiting_confirmation"
    return store, service, case, events[2]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("资料都正确，可以继续。", "ready"),
        ("Everything is correct, please proceed.", "ready"),
        ("Yes, everything is correct.", "ready"),
        ("收到，我晚点看。", "awaiting_confirmation"),
        ("我还没看\nI CONFIRM THE FINAL SUMMARY", "awaiting_confirmation"),
        ("Thanks\nOn Friday, Visa wrote:\nI CONFIRM THE FINAL SUMMARY", "awaiting_confirmation"),
    ],
)
def test_final_confirmation_depends_on_current_context(
    tmp_path: Path, text: str, expected: str
) -> None:
    store, service, _, event = setup_case(tmp_path)
    try:
        case, _, plan = service.process(event.model_copy(update={"body": text}))
        assert plan == expected
        assert case.final_summary_confirmed == (expected == "ready")
    finally:
        store.close()


def test_gmail_cannot_confirm_an_unsent_summary(tmp_path: Path) -> None:
    store, service, _, event = setup_case(tmp_path, "gmail")
    try:
        case, _, plan = service.process(
            event.model_copy(update={"body": "Everything is correct, please proceed."})
        )
        assert plan == "awaiting_confirmation"
        assert not case.final_summary_confirmed
    finally:
        store.close()


def test_profile_correction_invalidates_previous_summary_and_requests_reconfirmation(
    tmp_path: Path,
) -> None:
    store, service, _, event = setup_case(tmp_path)
    try:
        body = "Everything is correct, please proceed.\n<!-- DEMO_FACTS\nplanned_departure_date=2026-09-18\n-->"
        case, _, plan = service.process(event.model_copy(update={"body": body}))
        assert case.profile.planned_departure_date == date(2026, 9, 18)
        assert not case.final_summary_confirmed
        assert not case.profile_confirmed
        assert plan == "awaiting_profile_confirmation"
        next_event = event.model_copy(
            update={
                "id": "natural-profile-confirmation",
                "body": "资料都正确，可以继续。",
                "received_at": event.received_at + timedelta(hours=1),
            }
        )
        case, _, plan = service.process(next_event)
        assert case.profile_confirmed
        assert not case.final_summary_confirmed
        assert plan == "awaiting_confirmation"
        final_event = next_event.model_copy(
            update={
                "id": "natural-final-confirmation",
                "received_at": next_event.received_at + timedelta(hours=1),
            }
        )
        case, _, plan = service.process(final_event)
        assert plan == "ready"
    finally:
        store.close()


def test_ordinary_pdf_without_fixture_markers_is_not_falsely_accepted(tmp_path: Path) -> None:
    store, service, _, event = setup_case(tmp_path)
    document = tmp_path / "ordinary-letter.pdf"
    pdf = canvas.Canvas(str(document))
    pdf.drawString(50, 750, "A normal letter with no machine-readable fixture markers.")
    pdf.save()
    try:
        case, _, plan = service.process(
            event.model_copy(
                update={
                    "body": "Here is another supporting letter.",
                    "attachment_paths": [str(document)],
                }
            )
        )
        assert plan == "blocked"
        assert not case.final_summary_confirmed
        assert case.documents[-1].status.value == "HUMAN_REVIEW_REQUIRED"
        assert any(
            issue.code.startswith("UNCLASSIFIED_DOCUMENT_") for issue in case.open_blockers()
        )
    finally:
        store.close()


def test_email_name_correction_cannot_erase_conflicting_document_evidence(tmp_path: Path) -> None:
    store, service, _, event = setup_case(tmp_path)
    try:
        case, _, plan = service.process(
            event.model_copy(
                update={
                    "body": "My name should be Alex Chen.\n<!-- DEMO_FACTS\nfull_name=Alex Chen\n-->",
                }
            )
        )
        assert plan == "blocked"
        assert not case.final_summary_confirmed
        assert any(i.code == "EVIDENCE_CONFLICT_FULL_NAME" for i in case.open_blockers())
        assert any(e.source_document_id for e in case.active_evidence("full_name"))
        assert any(
            e.source_document_id is None and e.value == "Alex Chen"
            for e in case.active_evidence("full_name")
        )
        assert case.latest_changes == {"full_name": "Alex Chen"}
    finally:
        store.close()
