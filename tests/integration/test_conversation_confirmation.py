from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def setup_case(
    tmp_path: Path, channel: str = "email_fixture", *, stop_at_profile: bool = False,
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
    if channel == "gmail":
        assert plan == "awaiting_profile_confirmation"
        if stop_at_profile:
            return store, service, case, events[2]
        send_profile_summary(store)
        case, _, plan = service.process(events[2].model_copy(update={
            "id": "setup-profile-confirmation", "body": "I confirm the profile summary",
            "received_at": events[1].received_at + timedelta(minutes=1),
        }))
    assert plan == "awaiting_confirmation"
    return store, service, case, events[2]


def send_profile_summary(store: SQLiteStore) -> None:
    class Capture:
        def send(self, request):
            return "captured-" + request.outbox_id

    results = OutboxDispatcher(store, Capture(), allowed_message_types=("awaiting_profile_confirmation",)).dispatch_due(datetime.now(UTC))
    assert any(result.status == "SENT" for result in results)


@pytest.mark.parametrize("text", ["profile confirmed", "I confirm the profile summary",
                                 "我确认上述个人资料", "我确认个人资料摘要"])
def test_gmail_exact_profile_confirmation_without_a_previous_summary_has_no_authority(tmp_path, text):
    store = SQLiteStore(tmp_path / "case.db")
    service = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                              OfflineFixtureLLM(), today_provider=lambda: date(2026, 9, 4))
    try:
        inbound = InboundEvent(id="unrequested-confirmation", channel="gmail", external_thread_id="fictional-thread",
            sender="fictional@example.test", subject="Fictional confirmation", body=text,
            received_at=datetime(2026, 9, 4, tzinfo=UTC))
        case, _, plan = service.process(inbound)
        assert plan == "blocked" and not case.profile_confirmed and not case.final_summary_confirmed
        assert not case.delivery_path and not any(item.confirmed for item in case.evidence)
    finally:
        store.close()


@pytest.mark.parametrize("text", ["I confirm the profile summary", "我确认个人资料摘要",
                                 "Everything is correct, please proceed."])
def test_gmail_profile_confirmation_needs_sent_summary_and_survives_reopen(tmp_path, text):
    store, service, case, inbound = setup_case(tmp_path, "gmail", stop_at_profile=True)
    assert not case.profile_confirmed
    assert all(row["status"] != "SENT" for row in store.list_outbox())
    case, _, plan = service.process(inbound.model_copy(update={"body": text}))
    assert plan == "awaiting_profile_confirmation" and not case.profile_confirmed
    request_event_id = case.confirmation_request_event_id
    send_profile_summary(store)
    assert any(row["event_id"] == request_event_id and row["status"] == "SENT" for row in store.list_outbox())
    store.close()
    reopened = SQLiteStore(tmp_path / "case.db")
    try:
        fresh_service = WorkflowService(reopened, service.policy, OfflineFixtureLLM(),
                                       today_provider=lambda: date(2026, 9, 4))
        confirmed, _, plan = fresh_service.process(inbound.model_copy(update={
            "id": "after-actual-sent", "body": text, "received_at": inbound.received_at + timedelta(hours=1),
        }))
        assert confirmed.id == case.id and confirmed.profile_confirmed
        assert not confirmed.final_summary_confirmed and plan == "awaiting_confirmation"
        assert confirmed.delivery_path is None
    finally:
        reopened.close()


@pytest.mark.parametrize("text", ["I confirm the profile summary", "我确认个人资料摘要"])
def test_gmail_exact_confirmation_cannot_confirm_a_changed_profile(tmp_path, text):
    store, service, _, inbound = setup_case(tmp_path, "gmail", stop_at_profile=True)
    try:
        send_profile_summary(store)
        body = text + "\n<!-- DEMO_FACTS\nestimated_trip_cost_gbp=2345\n-->"
        case, _, _ = service.process(inbound.model_copy(update={"body": body}))
        assert case.profile.estimated_trip_cost_gbp == 2345
        assert not case.profile_confirmed and not case.final_summary_confirmed
    finally:
        store.close()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("资料都正确，可以继续。", "ready"),
        ("Everything is correct, please proceed.", "ready"),
        ("Yes, everything is correct.", "ready"),
        ("收到，我晚点看。", "awaiting_confirmation"),
        ("我还没看\nI CONFIRM THE FINAL SUMMARY", "awaiting_confirmation"),
        ("Thanks\nOn Friday, Visa wrote:\nI CONFIRM THE FINAL SUMMARY", "awaiting_confirmation"),
        ("收到\n\nFrom: Adviser <adviser@example.test>\nDate: 4 September 2026\n"
         "To: Applicant <applicant@example.test>\nSubject: Re: Enquiry\n\n"
         "Everything is correct, please proceed.", "awaiting_confirmation"),
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


def test_outlook_history_never_reaches_extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, service, _, event = setup_case(tmp_path)
    original = service.llm.delegate.extract_case_patch
    observed: list[str] = []

    def capture(inbound: InboundEvent):
        observed.append(inbound.body)
        return original(inbound)

    monkeypatch.setattr(service.llm.delegate, "extract_case_patch", capture)
    try:
        body = ("我晚点核对。\n\nFrom: Adviser <adviser@example.test>\nDate: 4 September 2026\n"
                "To: Applicant <applicant@example.test>\nSubject: Re: Enquiry\n\n"
                "<!-- DEMO_FACTS\nplanned_departure_date=2026-09-18\n-->\n"
                "Everything is correct, please proceed.")
        before = store.get_case_by_thread(event.external_thread_id)
        assert before is not None
        case, _, plan = service.process(event.model_copy(update={"body": body}))
        assert observed == ["我晚点核对。"]
        assert case.profile.planned_departure_date == before.profile.planned_departure_date
        assert not case.final_summary_confirmed and plan == "awaiting_confirmation"
        assert not case.delivery_path
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
