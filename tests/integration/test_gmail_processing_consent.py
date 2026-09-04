"""Actual Gmail runner and consent ledger with isolated SQLite and capture-only transport."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage, GmailRawMessage
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.privacy.consent import ConsentLedger
from visa_agent.storage.sqlite import SQLiteStore

SENDER = "synthetic-applicant@example.test"
MAILBOX = "synthetic-service@example.test"
THREAD = "synthetic-consent-thread"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("consent_gmail_runner", Path("scripts/gmail_sandbox.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    state = SimpleNamespace(messages={}, sent=[], extracted=[], documents=[], raw_reads=[],
                            clock=datetime.now(UTC) + timedelta(minutes=2), unavailable=set())

    class Adapter:
        def __init__(self, service):
            pass

        def current_history_id(self):
            return "100"

        def list_message_page(self, query, token):
            return GmailMessagePage(tuple(state.messages), None)

        def list_added_history_page(self, start, token):
            return GmailHistoryPage(tuple(state.messages), None, str(int(start) + 1))

        def get_intake_metadata(self, identifier):
            if identifier in state.unavailable:
                from visa_agent.channels.gmail import GmailMessageUnavailableError
                raise GmailMessageUnavailableError("Synthetic metadata unavailable")
            raw, received_at = state.messages[identifier]
            return {"id": identifier, "internalDate": str(int(received_at.timestamp() * 1000)),
                    "labelIds": ["INBOX"], "payload": {"headers": [
                        {"name": "From", "value": SENDER}, {"name": "To", "value": MAILBOX}]}}

        def get_raw_message(self, identifier):
            state.raw_reads.append(identifier)
            return GmailRawMessage(identifier, THREAD, state.messages[identifier][0])

        def send_reply(self, **kwargs):
            assert kwargs.get("attachment") is None
            state.sent.append(kwargs)
            return {"id": f"capture-{len(state.sent)}"}

        def find_sent_message(self, rfc_message_id):
            return next((f"capture-{index}" for index, value in enumerate(state.sent, 1)
                         if value["message_id"] == rfc_message_id), None)

    class Model(OfflineFixtureLLM):
        def extract_case_patch(self, event):
            state.extracted.append((event.id, event.body, event.received_at))
            return super().extract_case_patch(event)

    def reader(path):
        state.documents.append((path.name, path.read_bytes()))
        return DocumentReadResult("other_supporting_document", "en", 1, {})

    service = SimpleNamespace(users=lambda: SimpleNamespace(
        getProfile=lambda **kwargs: SimpleNamespace(execute=lambda: {"emailAddress": MAILBOX})))
    monkeypatch.setattr(runner, "build_gmail_service", lambda *args, **kwargs: service)
    monkeypatch.setattr(runner, "GmailAdapter", Adapter)
    monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *args, **kwargs: Model())
    monkeypatch.setattr(runner, "NaturalPDFReader", lambda model: reader)
    monkeypatch.setattr(runner, "read_secret", lambda *args, **kwargs: "synthetic-unused")
    args = argparse.Namespace(action="serve", sender=SENDER, mailbox=MAILBOX, subject=None,
        after=1, state_dir=tmp_path, model="synthetic-capture-model", watch=True)

    def add(identifier, body, *, attachment=False, references=None, received_at=None):
        message = EmailMessage()
        message["From"], message["To"] = SENDER, MAILBOX
        message["Subject"] = "UK visitor enquiry"
        message["Message-ID"] = f"<{identifier}@example.test>"
        # The provider receipt, not this deliberately obsolete header, controls order.
        message["Date"] = "Wed, 01 Jan 2020 00:00:00 +0000"
        if references:
            message["References"] = references
            message["In-Reply-To"] = references
        message.set_content(body)
        if attachment:
            message.add_attachment(b"%PDF-1.4 SYNTHETIC ONLY", maintype="application",
                                   subtype="pdf", filename="synthetic-support.pdf")
        state.clock += timedelta(seconds=1)
        state.messages[identifier] = (message.as_bytes(), received_at or state.clock)

    state.add = add
    state.run = lambda: runner.run_once(args, argparse.ArgumentParser())
    state.args, state.runner, state.path = args, runner, tmp_path
    state.open_store = lambda: SQLiteStore(tmp_path / "sandbox.db")
    return state


def test_preview_never_decodes_or_saves_attachments_or_logs_body(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "state.db")
    try:
        message = EmailMessage()
        message["From"] = SENDER
        message.set_content("SYNTHETIC PERSONAL BODY")
        message.add_attachment(b"SYNTHETIC", maintype="application", subtype="pdf", filename="private.pdf")
        monkeypatch.setattr("visa_agent.channels.email_fixture.save_pdf_attachment",
                            lambda *args: pytest.fail("Preview must not materialize any attachment"))
        original_payload = EmailMessage.get_payload

        def no_attachment_decode(message, *args, **kwargs):
            if message.get_content_disposition() == "attachment" and kwargs.get("decode"):
                pytest.fail("Preview must not decode attachment payloads")
            return original_payload(message, *args, **kwargs)

        monkeypatch.setattr(EmailMessage, "get_payload", no_attachment_decode)
        result = EmailIngestionBoundary(store, tmp_path / "attachments").preview(
            message.as_bytes(), provider_message_id="preview", provider_thread_id=THREAD)
        assert result.event is not None and result.event.attachment_paths == []
        assert not (tmp_path / "attachments").exists()
        assert store.counts()["cases"] == 0 and store.list_inbound_failures() == []
    finally:
        store.close()


def test_first_email_defers_original_attachment_without_model_or_disk(harness):
    harness.add("original", "I would like help preparing my UK visitor application.", attachment=True)
    harness.run()
    assert harness.extracted == [] and harness.documents == []
    assert not (harness.path / "attachments").exists()
    store = harness.open_store()
    try:
        assert store.counts()["processed_events"] == 0
        cases = store.list_cases()
        assert len(cases) == 1 and not cases[0].latest_customer_message
        assert ConsentLedger(store).deferred_ids(cases[0].id) == ["original"]
        rows = store.list_outbox()
        assert len(rows) == 1 and rows[0]["message_type"] == "processing_notice"
        assert rows[0]["status"] == "SENT"
    finally:
        store.close()
    journal = GmailSyncJournal(harness.path / "sync.db", json.dumps({
        "sender": SENDER, "mailbox": MAILBOX, "subject": None, "after": 1}, sort_keys=True))
    try:
        assert journal.connection.execute("SELECT status FROM candidates WHERE id='original'").fetchone() == (
            "awaiting_consent",)
        assert journal.discovery_drained()
    finally:
        journal.close()
    harness.run()
    assert len(harness.sent) == 1 and harness.raw_reads == ["original"]


def test_unseen_metadata_candidate_holds_notice_without_losing_healthy_defer(harness):
    harness.add("unavailable", "Synthetic metadata-only candidate")
    harness.add("healthy", "I need help with a UK visitor application.", attachment=True)
    harness.unavailable.add("unavailable")
    harness.run()
    assert harness.sent == [] and harness.extracted == []
    assert not (harness.path / "attachments").exists()
    store = harness.open_store()
    try:
        assert ConsentLedger(store).deferred_ids() == ["healthy"]
        assert store.list_outbox()[0]["status"] == "PENDING"
    finally:
        store.close()


def test_prepare_uses_same_consent_gate_and_durable_deferred_journal(harness):
    harness.args.action = "prepare"
    harness.args.after = None
    harness.add("prepare-original", "Please help me prepare my visit.", attachment=True)
    harness.run()
    assert harness.sent == [] and harness.extracted == [] and harness.documents == []
    assert not (harness.path / "attachments").exists()
    store = harness.open_store()
    try:
        assert ConsentLedger(store).deferred_ids() == ["prepare-original"]
        assert store.counts()["processed_events"] == 0
    finally:
        store.close()


def _grant_after_sent_notice(harness):
    harness.add("original", "Please help prepare my UK visitor application.", attachment=True)
    harness.run()
    notice = harness.sent[-1]
    reference = re.search(r"PC-[A-F0-9]{12}", notice["body"]).group()
    harness.add("grant", f"I consent to the processing described in this notice (consent reference {reference}).",
                references=notice["message_id"])
    harness.run()
    assert harness.extracted == []  # Earlier deferred original is pending for next cycle.


def test_sent_consent_restores_original_bytes_and_provider_order_before_new_business(harness):
    _grant_after_sent_notice(harness)
    harness.add("newer", "I have not chosen the travel dates yet.")
    harness.run()
    assert [item[0] for item in harness.extracted] == ["original", "newer"]
    assert harness.documents == [("synthetic-support.pdf", b"%PDF-1.4 SYNTHETIC ONLY")]
    assert all(received.year != 2020 for _, _, received in harness.extracted)
    store = harness.open_store()
    try:
        assert store.counts()["processed_events"] == 2
        assert not store.event_processed("grant")
        assert ConsentLedger(store).deferred_ids() == []
        assert store.list_held_inbound(store.list_cases()[0].id) == []
    finally:
        store.close()
    before = (list(harness.extracted), list(harness.documents), list(harness.raw_reads))
    harness.run()
    assert (harness.extracted, harness.documents, harness.raw_reads) == before


def test_101st_withdrawal_is_scanned_before_any_new_business_processing(harness):
    _grant_after_sent_notice(harness)
    harness.run()
    baseline = list(harness.extracted)
    for number in range(100):
        harness.add(f"business-{number:03}", "Here is a further synthetic update.", attachment=True)
    harness.add("withdraw-101", "I withdraw my consent to processing my information.")
    sent_before = len(harness.sent)
    harness.run()
    assert harness.extracted == baseline and len(harness.sent) == sent_before
    harness.run()
    assert harness.extracted == baseline
    store = harness.open_store()
    try:
        case = store.list_cases()[0]
        assert not ConsentLedger(store).allowed(case)
        assert len(ConsentLedger(store).deferred_ids()) == 100
        assert not store.event_processed("withdraw-101")
        assert store.counts()["processed_events"] == 1
    finally:
        store.close()


def test_failed_review_queue_cannot_hide_new_withdrawal(harness):
    _grant_after_sent_notice(harness)
    harness.run()
    store = harness.open_store()
    try:
        event = InboundEvent(id="queued-review", channel="gmail_review", external_thread_id=THREAD,
            sender=SENDER, subject="UK visitor enquiry", body="SYNTHETIC RETAINED CONTENT", received_at=harness.clock)
        store.enqueue_inbound(event)
        store.connection.execute("UPDATE inbound_queue SET status='FAILED',attempt_count=3")
        store.connection.commit()
    finally:
        store.close()
    harness.add("withdraw", "I withdraw my consent to processing my information.")
    baseline = list(harness.extracted)
    harness.run()
    assert harness.extracted == baseline
    store = harness.open_store()
    try:
        assert not ConsentLedger(store).allowed(store.list_cases()[0])
        assert store.list_inbound_queue()[0]["attempt_count"] == 3
        assert store.list_inbound_queue()[0]["status"] == "FAILED"
        assert store.connection.execute("SELECT action FROM processing_consent_events WHERE event_id='withdraw'").fetchone()[0] == "withdrawn"
    finally:
        store.close()


def test_historically_processed_replay_does_not_issue_a_new_notice_or_touch_attachment(harness):
    harness.add("historical", "Synthetic already processed mail", attachment=True)
    store = harness.open_store()
    try:
        case = Case(id="existing-case", external_thread_id=THREAD, primary_channel="gmail",
                    applicant_contact=SENDER, policy_version="synthetic-policy")
        event = InboundEvent(id="historical", channel="gmail", external_thread_id=THREAD,
                            sender=SENDER, subject="UK visitor enquiry", body="synthetic", received_at=harness.clock)
        store.commit_event(case, event, "blocked", "synthetic prior draft")
    finally:
        store.close()
    harness.run()
    assert not (harness.path / "attachments").exists()
    assert harness.extracted == [] and harness.sent == []
    store = harness.open_store()
    try:
        assert store.counts()["processed_events"] == 1
        assert not any(row["message_type"] == "processing_notice" for row in store.list_outbox())
    finally:
        store.close()


def test_152_message_backlog_scans_then_recovers_every_original_without_duplicates(harness):
    for number in range(152):
        harness.add(f"original-{number:03}", "Please help me prepare a UK visitor application.")
    harness.run()
    assert not harness.sent and not harness.extracted
    harness.run()
    assert len(harness.sent) == 1 and not harness.extracted
    reference = re.search(r"PC-[A-F0-9]{12}", harness.sent[0]["body"]).group()
    harness.add("backlog-grant", f"I consent to the processing described in this notice (consent reference {reference}).",
                references=harness.sent[0]["message_id"])
    harness.run()  # Grant releases the original 152 IDs for a new bounded scan.
    assert not harness.extracted
    harness.run()  # First 100 controls scanned; remaining 52 are still unknown.
    assert not harness.extracted
    harness.run()  # All controls scanned, first 100 business events may now run.
    assert len(harness.extracted) == 100
    harness.run()
    expected = [f"original-{number:03}" for number in range(152)]
    assert [item[0] for item in harness.extracted] == expected
    harness.run()
    assert [item[0] for item in harness.extracted] == expected
    store = harness.open_store()
    try:
        assert store.counts()["processed_events"] == 152
        assert ConsentLedger(store).deferred_ids() == []
    finally:
        store.close()


@pytest.mark.parametrize("changed", ["id", "thread"])
def test_materialization_cannot_switch_identity_after_consent_preview(harness, monkeypatch, changed):
    _grant_after_sent_notice(harness)
    original = harness.runner.GmailAdapter.get_raw_message
    calls = 0

    def changed_response(adapter, identifier):
        nonlocal calls
        calls += 1
        raw = original(adapter, identifier)
        if calls == 2:
            return GmailRawMessage("different-id" if changed == "id" else raw.provider_message_id,
                "different-thread" if changed == "thread" else raw.provider_thread_id, raw.raw)
        return raw

    monkeypatch.setattr(harness.runner.GmailAdapter, "get_raw_message", changed_response)
    with pytest.raises(ValueError, match="candidate|thread"):
        harness.run()
    assert not harness.extracted and not harness.documents
    assert not (harness.path / "attachments").exists()


def test_changed_model_requires_new_notice_before_any_new_material_is_saved(harness):
    _grant_after_sent_notice(harness)
    harness.run()
    baseline = (list(harness.extracted), list(harness.documents))
    harness.args.model = "different-synthetic-model"
    harness.add("new-model-message", "Please inspect this additional material.", attachment=True)
    harness.run()
    assert (harness.extracted, harness.documents) == baseline
    store = harness.open_store()
    try:
        assert not ConsentLedger(store).allowed(store.list_cases()[0])
        assert ConsentLedger(store).deferred_ids() == ["new-model-message"]
        notices = [row for row in store.list_outbox() if row["message_type"] == "processing_notice"]
        assert len(notices) == 2
        current = [row for row in notices if "different-synthetic-model" in row["payload"]]
        assert len(current) == 1 and current[0]["status"] == "SENT"
    finally:
        store.close()


def test_file_materialization_is_locked_but_external_workflow_is_not(harness, monkeypatch):
    _grant_after_sent_notice(harness)
    ingest = harness.runner.EmailIngestionBoundary.ingest
    process = harness.runner.WorkflowService.process
    observations = []

    def locked_ingest(boundary, *args, **kwargs):
        observations.append(("files", boundary.store.connection.in_transaction))
        return ingest(boundary, *args, **kwargs)

    def unlocked_workflow(workflow, event):
        observations.append(("workflow", workflow.store.connection.in_transaction))
        return process(workflow, event)

    monkeypatch.setattr(harness.runner.EmailIngestionBoundary, "ingest", locked_ingest)
    monkeypatch.setattr(harness.runner.WorkflowService, "process", unlocked_workflow)
    harness.run()
    assert observations == [("files", True), ("workflow", False)]
