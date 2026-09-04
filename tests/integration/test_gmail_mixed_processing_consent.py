"""Mixed grants keep original Gmail IDs; only isolated, capture-only transports.

The shared harness reopens the real SQLite store and runner on every cycle. It
fakes Gmail and model I/O; no real mailbox, credentials or provider are used.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from test_gmail_processing_consent import MAILBOX, SENDER
from test_gmail_processing_consent import harness as harness  # Explicit shared fixture.

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate
from visa_agent.privacy.consent import ConsentLedger


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Mixed-consent regressions cannot access a network")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def mixed(harness, monkeypatch):
    class Model:
        def extract_case_patch(self, event):
            harness.extracted.append((event.id, event.body, event.received_at))
            updates, questions = [], []
            fact = "My full name is Synthetic Rowan."
            if fact in event.body:
                updates.append(FactUpdate(field="full_name", value="Synthetic Rowan",
                    source_excerpt=fact, confidence=1))
            for topic, question in [
                ("application", "Where is the official application page?"),
                ("booking", "Can I prepare without buying flight tickets?"),
            ]:
                if question in event.body:
                    questions.append(CustomerQuestion(topic=topic, source_excerpt=question, confidence=1))
            return CasePatch(updates=updates, ambiguities=[], customer_questions=questions)

        render_message = staticmethod(deterministic_fallback_message)

    monkeypatch.setattr(harness.runner, "DeepSeekStructuredLLM", lambda *args, **kwargs: Model())
    return harness


def _sent_notice(harness, *, notice_only=False):
    harness.add("initial", "I consent to the processing described in this notice." if notice_only
                else "Please help prepare my UK visitor application.")
    harness.run()
    notice = harness.sent[-1]
    reference = re.search(r"PC-[A-F0-9]{12}", notice["body"]).group()
    return notice, reference


def _grant(reference):
    return f"I consent to the processing described in this notice (consent reference {reference})."


def _journal(harness):
    return GmailSyncJournal(harness.path / "sync.db", json.dumps({
        "sender": SENDER, "mailbox": MAILBOX, "subject": None, "after": 1}, sort_keys=True))


def _state(harness):
    store = harness.open_store()
    try:
        case = store.list_cases()[0]
        ledger = ConsentLedger(store)
        audit = [tuple(row) for row in store.connection.execute(
            "SELECT event_id,action,epoch FROM processing_consent_events ORDER BY rowid")]
        return case, ledger.epoch(case.id), ledger.deferred_ids(case.id), audit, store.list_outbox()
    finally:
        store.close()


@pytest.mark.parametrize(("business", "attachment"), [
    ("My full name is Synthetic Rowan.", False),
    ("Where is the official application page?", False),
    ("Can I prepare without buying flight tickets?", False),
    ("", True),
])
def test_mixed_grant_replays_original_business_once_after_restart_without_control_text(
    mixed, monkeypatch, business, attachment,
):
    notice, reference = _sent_notice(mixed)
    mixed.add("mixed-grant", _grant(reference) + "\n" + business, attachment=attachment,
              references=notice["message_id"])
    # Even a valid mixed grant is only scanned in this first cycle. Metadata
    # presence is allowed, attachment filename/content access is not.
    with monkeypatch.context() as guarded:
        guarded.setattr(EmailMessage, "get_filename",
                        lambda *args, **kwargs: pytest.fail("Grant scan read attachment filename"))
        original = EmailMessage.get_payload

        def payload(message, *args, **kwargs):
            if kwargs.get("decode") and message.get_content_disposition() == "attachment":
                pytest.fail("Grant scan decoded attachment content")
            return original(message, *args, **kwargs)

        guarded.setattr(EmailMessage, "get_payload", payload)
        mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    case, epoch, deferred, audit, _ = _state(mixed)
    assert deferred == ["initial", "mixed-grant"]
    assert [(identifier, action) for identifier, action, _ in audit] == [("mixed-grant", "granted")]
    journal = _journal(mixed)
    try:
        assert journal.connection.execute("SELECT status FROM candidates WHERE id='mixed-grant'").fetchone() == ("pending",)
    finally:
        journal.close()
    mixed.add("later-business", "A later independent message.")
    mixed.run()
    assert [value[0] for value in mixed.extracted] == ["initial", "mixed-grant", "later-business"]
    extracted = next(value for value in mixed.extracted if value[0] == "mixed-grant")
    assert extracted[1].strip() == business and reference not in extracted[1]
    assert "I consent" not in extracted[1]
    # Gmail internalDate is authoritative and has millisecond precision; the
    # deliberately old MIME Date and the fixture clock's sub-millisecond tail
    # must not become the processed event's receipt time.
    receipt_ms = int(mixed.messages["mixed-grant"][1].timestamp() * 1000)
    assert extracted[2] == datetime.fromtimestamp(receipt_ms / 1000, UTC)
    assert len(mixed.documents) == int(attachment)
    after, after_epoch, deferred, after_audit, outbox = _state(mixed)
    assert after.id == case.id and after_epoch == epoch and after_audit == audit and deferred == []
    if business.startswith("My full name"):
        assert after.profile.full_name == "Synthetic Rowan"
    store = mixed.open_store()
    try:
        assert store.event_processed("mixed-grant") and store.counts()["processed_events"] == 3
        assert sum(row["event_id"] == "mixed-grant" and row["message_type"] == "processing_receipt"
                   for row in outbox) == 1
    finally:
        store.close()
    before = (list(mixed.extracted), list(mixed.documents), list(mixed.raw_reads), len(mixed.sent))
    mixed.run()
    assert (mixed.extracted, mixed.documents, mixed.raw_reads, len(mixed.sent)) == before
    assert _state(mixed)[1:4] == (after_epoch, [], audit)


def test_pure_grant_remains_a_control_not_an_empty_business_event(mixed):
    notice, reference = _sent_notice(mixed)
    mixed.add("pure-grant", _grant(reference), references=notice["message_id"])
    mixed.run()
    assert mixed.extracted == []
    mixed.run()
    assert [item[0] for item in mixed.extracted] == ["initial"]
    store = mixed.open_store()
    try:
        assert not store.event_processed("pure-grant")
        assert ConsentLedger(store).deferred_ids() == []
    finally:
        store.close()


@pytest.mark.parametrize(("question", "required"), [
    ("Where is the official application page?", "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"),
    ("Can I prepare without buying flight tickets?", "https://www.gov.uk"),
])
def test_mixed_grant_question_gets_an_actual_sent_useful_reply_without_a_followup_mail(
    mixed, question, required,
):
    notice, reference = _sent_notice(mixed)
    mixed.add("mixed-question", _grant(reference) + "\n" + question,
              references=notice["message_id"])
    mixed.run()
    assert mixed.extracted == []
    mixed.run()
    assert [item[0] for item in mixed.extracted] == ["initial", "mixed-question"]
    store = mixed.open_store()
    try:
        row = next(row for row in store.list_outbox()
            if row["event_id"] == "mixed-question" and row["message_type"] == "blocked")
        assert row["status"] == "SENT" and row["provider_message_id"] is not None
        captured = next(sent for index, sent in enumerate(mixed.sent, 1)
                        if row["provider_message_id"] == f"capture-{index}")
        assert captured["body"] == row["payload"] and required in captured["body"]
        assert captured["recipient"] == SENDER and captured.get("attachment") is None
        assert reference not in captured["body"] and "I consent to" not in captured["body"]
        if "flight" in question:
            assert "do not need to buy flights" in captured["body"].casefold()
        else:
            assert "application" in captured["body"].casefold()
    finally:
        store.close()


@pytest.mark.parametrize("withdraw_first", [False, True])
def test_same_email_withdrawal_beats_grant_and_business_in_either_order(mixed, withdraw_first):
    notice, reference = _sent_notice(mixed)
    grant = _grant(reference) + "\nMy full name is Synthetic Rowan."
    withdrawal = "I withdraw my consent to processing my information."
    body = withdrawal + "\n" + grant if withdraw_first else grant + "\n" + withdrawal
    mixed.add("conflicting-controls", body, attachment=True, references=notice["message_id"])
    mixed.run()
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    case, _, deferred, audit, _ = _state(mixed)
    assert deferred == ["initial"] and case.profile.full_name is None
    assert [(identifier, action) for identifier, action, _ in audit] == [
        ("conflicting-controls", "withdrawn")]


def test_chinese_business_before_explicit_grant_keeps_exact_business_not_authorization(mixed):
    notice, reference = _sent_notice(mixed)
    question = "英国签证的官方申请网页在哪里？"
    mixed.add("chinese-mixed", question + "\n"
              f"我同意按这份说明处理本线程信息和材料（授权参考码 {reference}）。",
              references=notice["message_id"])
    mixed.run()
    assert mixed.extracted == []
    mixed.run()
    assert [item[0] for item in mixed.extracted] == ["initial", "chinese-mixed"]
    assert mixed.extracted[-1][1].strip() == question
    assert reference not in mixed.extracted[-1][1] and "我同意" not in mixed.extracted[-1][1]


@pytest.mark.parametrize("same_cycle", [False, True])
def test_later_withdrawal_blocks_same_mixed_mail_before_attachment_or_model(mixed, same_cycle):
    notice, reference = _sent_notice(mixed)
    mixed.add("mixed-grant", _grant(reference) + "\nMy full name is Synthetic Rowan.",
              attachment=True, references=notice["message_id"])
    if not same_cycle:
        mixed.run()
        assert mixed.extracted == []
    mixed.add("withdraw", "I withdraw my consent to processing my information.")
    mixed.run()
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    case, _, deferred, audit, _ = _state(mixed)
    assert case.profile.full_name is None and not case.latest_customer_message
    assert deferred == ["initial", "mixed-grant"]
    assert [(identifier, action) for identifier, action, _ in audit] == [
        ("mixed-grant", "granted"), ("withdraw", "withdrawn")]
    store = mixed.open_store()
    try:
        assert not ConsentLedger(store).allowed(case) and store.counts()["processed_events"] == 0
    finally:
        store.close()


def test_101st_control_still_precedes_every_released_mixed_business_event(mixed):
    notice, reference = _sent_notice(mixed)
    mixed.add("mixed-grant", _grant(reference) + "\nMy full name is Synthetic Rowan.", attachment=True,
              references=notice["message_id"])
    mixed.run()
    for number in range(100):
        mixed.add(f"new-business-{number:03}", "Another ordinary preparation message.")
    mixed.add("last-withdraw", "I withdraw my consent to processing my information.")
    sent_before = len(mixed.sent)
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == [] and len(mixed.sent) == sent_before
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    case, _, deferred, audit, _ = _state(mixed)
    assert len(deferred) == 102 and "mixed-grant" in deferred
    assert audit[-1][0:2] == ("last-withdraw", "withdrawn")
    assert case.profile.full_name is None and not (mixed.path / "attachments").exists()


@pytest.mark.parametrize("persisted_awaiting", [False, True])
def test_scope_upgrade_between_mixed_grant_and_business_sends_new_notice_not_business(
    mixed, persisted_awaiting,
):
    # An ineffective first consent attempt receives a real notice without an
    # ordinary deferred email that could accidentally issue the upgrade notice.
    notice, reference = _sent_notice(mixed, notice_only=True)
    mixed.add("mixed-grant", _grant(reference) + "\nMy full name is Synthetic Rowan.",
              attachment=True, references=notice["message_id"])
    mixed.run()
    assert mixed.extracted == [] and _state(mixed)[2] == ["mixed-grant"]
    if persisted_awaiting:
        # Recreate the durable metadata checkpoint left by the former audited
        # mixed/no-current-scope branch. This neither changes consent nor makes
        # the business processed; recovery must not require a new customer mail.
        journal = _journal(mixed)
        try:
            journal.acknowledge("mixed-grant", "awaiting_consent", "PROCESSING_CONSENT_REQUIRED")
        finally:
            journal.close()
    mixed.args.model = "new-synthetic-model"
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    case, epoch, deferred, audit, rows = _state(mixed)
    notices = [row for row in rows if row["message_type"] == "processing_notice"]
    assert len(notices) == 2 and notices[-1]["status"] == "SENT"
    replacement = next(sent for sent in mixed.sent if "new-synthetic-model" in sent["body"])
    new_reference = re.search(r"PC-[A-F0-9]{12}", replacement["body"]).group()
    assert new_reference != reference and deferred == ["mixed-grant"]
    store = mixed.open_store()
    try:
        assert not ConsentLedger(store).allowed(case) and not store.event_processed("mixed-grant")
    finally:
        store.close()
    before = (list(mixed.raw_reads), len(mixed.sent))
    mixed.run()
    assert (mixed.raw_reads, len(mixed.sent)) == before  # No notice/scan loop.
    mixed.add("new-scope-grant", _grant(new_reference), references=replacement["message_id"])
    mixed.run()
    mixed.run()
    assert [item[0] for item in mixed.extracted] == ["mixed-grant"]
    assert mixed.extracted[0][1].strip() == "My full name is Synthetic Rowan."
    after, current_epoch, deferred, after_audit, _ = _state(mixed)
    assert after.id == case.id and after.profile.full_name == "Synthetic Rowan"
    assert current_epoch == epoch + 1 and deferred == [] and len(mixed.documents) == 1
    assert sum(item[:2] == ("mixed-grant", "granted") for item in after_audit) == 1
    assert after_audit[:-1] == audit


@pytest.mark.parametrize("control", ["I withdraw my consent to processing my information.",
                                     "I do not consent to processing my information."])
def test_withdrawn_or_declined_mixed_mail_is_not_rescanned_or_nagged_on_scope_upgrade(mixed, control):
    notice, reference = _sent_notice(mixed, notice_only=True)
    mixed.add("mixed-grant", _grant(reference) + "\nMy full name is Synthetic Rowan.",
              attachment=True, references=notice["message_id"])
    mixed.run()
    mixed.add("negative-control", control)
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    before = (list(mixed.raw_reads), len(mixed.sent))
    mixed.args.model = "new-synthetic-model"
    mixed.run()
    mixed.run()
    assert (mixed.raw_reads, len(mixed.sent)) == before
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    assert _state(mixed)[2] == ["mixed-grant"]


@pytest.mark.parametrize("restriction", ["Do not process my attachments.", "Do not read the attached PDF."])
def test_attachment_processing_restriction_does_not_become_mixed_consent(mixed, restriction):
    notice, reference = _sent_notice(mixed)
    mixed.add("restricted", _grant(reference) + "\n" + restriction, attachment=True,
              references=notice["message_id"])
    mixed.run()
    mixed.run()
    assert mixed.extracted == [] and mixed.documents == []
    assert not (mixed.path / "attachments").exists()
    case, _, _, audit, _ = _state(mixed)
    assert not any(action == "granted" for _, action, _ in audit)
    store = mixed.open_store()
    try:
        assert not ConsentLedger(store).allowed(case)
    finally:
        store.close()


def test_older_privacy_receipt_cannot_consume_the_only_slot_before_current_business(mixed):
    notice, reference = _sent_notice(mixed)
    mixed.add("mixed-question", _grant(reference) + "\nWhere is the official application page?",
              references=notice["message_id"])
    mixed.run()
    store = mixed.open_store()
    try:
        # Deterministically exercise the normal cross-second ordering. SQLite's
        # CURRENT_TIMESTAMP has second precision; sleeping for a boundary would
        # make this test unnecessarily flaky. Only a synthetic queue time changes.
        store.connection.execute("UPDATE outbox SET created_at='2000-01-01 00:00:00' "
            "WHERE event_id='mixed-question' AND message_type='processing_receipt'")
        store.connection.commit()
    finally:
        store.close()
    mixed.run()
    _, _, _, _, rows = _state(mixed)
    latest = next(row for row in rows if row["event_id"] == "mixed-question" and row["message_type"] == "blocked")
    receipt = next(row for row in rows if row["event_id"] == "mixed-question" and row["message_type"] == "processing_receipt")
    assert latest["status"] == "SENT", [(row["message_type"], row["status"], row["attempt_count"], row["last_error"])
                                        for row in rows]
    assert receipt["status"] == "FAILED" and receipt["attempt_count"] == 0
    before = len(mixed.sent)
    mixed.run()
    assert len(mixed.sent) == before


def test_current_pending_notice_is_not_retired_as_an_obsolete_control(mixed):
    mixed.args.action = "prepare"
    mixed.add("initial", "Please help prepare my UK visitor application.")
    mixed.run()
    assert mixed.sent == [] and mixed.extracted == []
    store = mixed.open_store()
    try:
        rows = store.list_outbox()
        assert len(rows) == 1 and rows[0]["message_type"] == "processing_notice"
        sender = AutomaticGmailReplySender(mixed.runner.GmailAdapter(None), store, SENDER)
        assert sender.withhold_obsolete_unsent() == 0
        current = store.list_outbox()[0]
        assert current["status"] == "PENDING" and current["attempt_count"] == 0
        assert current["last_error"] is None
    finally:
        store.close()
    mixed.args.action = "serve"
    mixed.run()
    rows = _state(mixed)[-1]
    assert rows[0]["status"] == "SENT" and len(mixed.sent) == 1 and mixed.extracted == []
