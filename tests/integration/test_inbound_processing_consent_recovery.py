"""Offline generic queue recovery, not a WhatsApp pre-download consent gate.

This queue already retains the raw provider event and attachment references.
The tests prove deferred recovery without pretending that earlier retention or
attachment download was prevented by the downstream processing-consent gate.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.privacy.consent import ConsentLedger, ProcessingScope
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


class CountedModel:
    def __init__(self) -> None:
        self.events: list[str] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event.id)
        updates = []
        if "My name is Alex Example." in event.body:
            updates.append(FactUpdate(field="full_name", value="Alex Example",
                                      source_excerpt="My name is Alex Example.", confidence=1))
        return CasePatch(updates=updates, ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CapturedSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return f"fictional-provider-receipt-{len(self.requests)}"


class Journey:
    def __init__(self, tmp_path: Path, *, channel: str = "whatsapp_twilio") -> None:
        self.path = tmp_path / "queue.db"
        self.store = SQLiteStore(self.path)
        self.scope = ProcessingScope("fictional-provider", "offline-model")
        self.policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
        self.ledger = ConsentLedger(self.store)
        self.ledger.configure(self.scope)
        self.model = CountedModel()
        self.reads: list[Path] = []
        self.sender = CapturedSender()
        self.channel = channel
        self.now = datetime.now(UTC)
        self.pdf = tmp_path / "fictional-student-letter.pdf"
        document = canvas.Canvas(str(self.pdf))
        document.drawString(50, 750, "Fictional University: Alex Example is enrolled as a student.")
        document.save()
        self.original = self.event("original-provider-id", "My name is Alex Example. Here is my student letter.")
        self.original.attachment_paths = [str(self.pdf)]

    def event(self, identifier: str, body: str) -> InboundEvent:
        self.now += timedelta(seconds=10)
        return InboundEvent(id=identifier,
            external_thread_id="fictional-email-thread" if self.channel == "email" else "whatsapp:+10000000000",
            sender="applicant@example.test" if self.channel == "email" else "whatsapp:+10000000000",
            channel=self.channel, subject="Fictional application",
            body=body, received_at=self.now)

    def read(self, path: Path) -> DocumentReadResult:
        self.reads.append(path)
        return DocumentReadResult("student_letter", "en", 1, {}, method="offline-test")

    def workflow(self) -> WorkflowService:
        return WorkflowService(self.store, self.policy, self.model, document_reader=self.read)

    def work(self):
        self.now += timedelta(seconds=10)
        return InboundEventWorker(self.store, self.workflow(), channel=self.channel).process_due(self.now)

    def reopen(self) -> None:
        self.store.close()
        self.store = SQLiteStore(self.path)
        self.ledger = ConsentLedger(self.store)

    def queue_row(self, identifier: str) -> dict:
        return next(row for row in self.store.list_inbound_queue() if row["id"] == identifier)

    def grant(self) -> None:
        self.now += timedelta(seconds=10)
        outcomes = OutboxDispatcher(self.store, self.sender, channel=self.channel,
            allowed_message_types=("processing_notice",)).dispatch_due(self.now)
        assert len(outcomes) == 1 and outcomes[0].status == "SENT"
        sent = self.sender.requests[-1]
        row = next(row for row in self.store.list_outbox() if row["id"] == sent.outbox_id)
        assert row["status"] == "SENT" and row["provider_message_id"]
        reference = re.search(r"PC-[A-F0-9]{12}", sent.body)
        assert reference is not None and sent.attachment is None
        accepted = self.event("applicant-grant", "I consent to the processing described in this notice "
                             f"(consent reference {reference.group()}).")
        self.store.enqueue_inbound(accepted)
        result = self.work()
        assert [(item.event_id, item.status) for item in result] == [(accepted.id, "PROCESSED")]
        case = self.store.get_case_by_thread(self.original.external_thread_id)
        assert case is not None and self.ledger.allowed(case)
        assert self.model.events == [] and self.reads == []


def test_defer_keeps_original_body_attachment_and_id_without_consuming_model_attempts(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    result = journey.work()
    assert [(item.event_id, item.status) for item in result] == [(journey.original.id, "AWAITING_CONSENT")]
    row = journey.queue_row(journey.original.id)
    assert InboundEvent.model_validate_json(row["payload_json"]) == journey.original
    assert row["attempt_count"] == 0 and row["lease_until"] is None
    assert not journey.store.event_processed(journey.original.id)
    assert journey.ledger.deferred_ids() == [journey.original.id]
    journey.reopen()
    assert journey.work() == []
    assert journey.model.events == [] and journey.reads == []
    assert journey.pdf.exists()
    journey.store.close()


@pytest.mark.parametrize("channel", ["whatsapp_twilio", "email"])
def test_real_sent_grant_reopens_and_processes_same_provider_event_once(tmp_path: Path, channel: str) -> None:
    journey = Journey(tmp_path, channel=channel)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.reopen()
    journey.grant()
    journey.reopen()
    result = journey.work()
    assert [(item.event_id, item.status) for item in result] == [(journey.original.id, "PROCESSED")]
    assert journey.model.events == [journey.original.id]
    assert journey.reads == [journey.pdf]
    case = journey.store.get_case_by_thread(journey.original.external_thread_id)
    assert case is not None and case.profile.full_name == "Alex Example"
    assert len(case.documents) == 1
    assert journey.store.event_processed(journey.original.id)
    assert journey.ledger.deferred_ids() == []
    assert journey.queue_row(journey.original.id)["payload_json"] == "{}"
    assert not journey.store.enqueue_inbound(journey.original)
    journey.reopen()
    assert journey.work() == [] and journey.model.events == [journey.original.id]
    assert {row["id"] for row in journey.store.list_inbound_queue()} == {journey.original.id, "applicant-grant"}
    journey.store.close()


@pytest.mark.parametrize("after_grant", [False, True])
@pytest.mark.parametrize("body", [
    "I do not consent to processing my information.",
    "I withdraw my consent to processing my information.",
])
def test_refusal_or_queued_withdrawal_preserves_waiting_source_without_model(
    tmp_path: Path, after_grant: bool, body: str,
) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    if after_grant:
        journey.grant()
    control = journey.event("applicant-refusal-or-withdrawal", body)
    journey.store.enqueue_inbound(control)
    result = journey.work()
    assert [(item.event_id, item.status) for item in result] == [(control.id, "PROCESSED")]
    journey.reopen()
    assert journey.work() == []
    row = journey.queue_row(journey.original.id)
    assert row["status"] == "AWAITING_CONSENT" and row["attempt_count"] == 0
    assert InboundEvent.model_validate_json(row["payload_json"]) == journey.original
    assert journey.ledger.deferred_ids() == [journey.original.id]
    assert not journey.store.event_processed(journey.original.id)
    assert journey.model.events == [] and journey.reads == [] and journey.pdf.exists()
    journey.store.close()


@pytest.mark.parametrize("change", [
    {"sender": "whatsapp:+19999999999"}, {"external_thread_id": "different-thread"},
    {"channel": "email"}, {"id": "substituted-provider-id"},
])
def test_recovery_rejects_changed_sender_thread_channel_or_source_id(tmp_path: Path, change: dict) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.grant()
    altered = journey.original.model_copy(update=change)
    with journey.store.connection:
        journey.store.connection.execute("UPDATE inbound_queue SET payload_json=? WHERE id=?",
                                         (altered.model_dump_json(), journey.original.id))
    journey.reopen()
    assert journey.work() == []
    assert journey.queue_row(journey.original.id)["status"] == "AWAITING_CONSENT"
    assert journey.model.events == [] and journey.reads == []
    assert not journey.store.event_processed(journey.original.id)
    journey.store.close()


@pytest.mark.parametrize("sender", [
    "applicant@example.test, intruder@example.test",
    "applicant@example.test, applicant@example.test",
    "intruder@example.test",
])
def test_email_recovery_requires_one_matching_applicant_address(tmp_path: Path, sender: str) -> None:
    journey = Journey(tmp_path, channel="email")
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.grant()
    changed = journey.original.model_copy(update={"sender": sender})
    with journey.store.connection:
        journey.store.connection.execute("UPDATE inbound_queue SET payload_json=? WHERE id=?",
                                         (changed.model_dump_json(), journey.original.id))
    assert journey.work() == []
    assert journey.queue_row(journey.original.id)["status"] == "AWAITING_CONSENT"
    assert journey.model.events == [] and journey.reads == []
    journey.store.close()


def test_different_case_grant_does_not_release_original_case(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    other = journey.event("other-applicant", "Please explain processing.").model_copy(update={
        "sender": "whatsapp:+12222222222", "external_thread_id": "whatsapp:+12222222222",
    })
    decision = journey.ledger.handle(other, journey.policy.version)
    journey.now += timedelta(seconds=10)
    OutboxDispatcher(journey.store, journey.sender, channel="whatsapp_twilio",
        allowed_message_types=("processing_notice",)).dispatch_due(journey.now)
    sent = next(item for item in journey.sender.requests if item.thread_id == other.external_thread_id)
    reference = re.search(r"PC-[A-F0-9]{12}", sent.body)
    assert reference is not None
    accepted = other.model_copy(update={"id": "other-applicant-grant",
        "received_at": journey.now + timedelta(seconds=10),
        "body": "I consent to the processing described in this notice "
                f"(consent reference {reference.group()})."})
    assert journey.ledger.handle(accepted, journey.policy.version).granted
    assert journey.ledger.allowed(journey.store.get_case(decision.case_id))
    assert journey.work() == []
    assert journey.queue_row(journey.original.id)["status"] == "AWAITING_CONSENT"
    assert journey.model.events == [] and journey.reads == []
    journey.store.close()


def test_scope_change_does_not_release_source_under_old_grant(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.grant()
    journey.ledger.configure(ProcessingScope("fictional-provider", "different-model"))
    journey.reopen()
    assert journey.work() == []
    assert journey.queue_row(journey.original.id)["status"] == "AWAITING_CONSENT"
    assert journey.model.events == [] and journey.reads == []
    journey.store.close()


def test_source_already_processed_by_workflow_is_never_replayed(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.grant()
    journey.workflow().process(journey.original)
    assert journey.store.event_processed(journey.original.id)
    assert journey.model.events == [journey.original.id]
    journey.reopen()
    assert journey.work() == [] and journey.model.events == [journey.original.id]
    journey.store.close()


def test_atomic_resume_rechecks_epoch_after_candidate_was_read(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.store.enqueue_inbound(journey.original)
    journey.work()
    journey.grant()
    candidate = journey.store.consent_resume_candidates("whatsapp_twilio")[0]
    case = journey.store.get_case(candidate["case_id"])
    assert case is not None
    journey.ledger.handle(journey.event("withdraw-between-read-and-resume",
        "I withdraw my consent to processing my information."), journey.policy.version)
    assert not journey.store.resume_inbound_after_consent(
        journey.original.id, case_id=case.id, channel="whatsapp_twilio", consent_epoch=candidate["epoch"],
        payload_json=candidate["payload_json"], case_snapshot_json=case.model_dump_json(),
    )
    assert journey.queue_row(journey.original.id)["status"] == "AWAITING_CONSENT"
    assert journey.model.events == [] and journey.reads == []
    journey.store.close()
