import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.privacy.consent import ConsentLedger, ProcessingScope
from visa_agent.storage.sqlite import SQLiteStore


def setup(tmp_path):
    store = SQLiteStore(tmp_path / "sandbox.db")
    now = datetime.now(UTC)
    for name in ("earlier", "selected", "later"):
        case = Case(id=name, external_thread_id=f"thread-{name}", applicant_contact=f"{name}@example.test",
                    primary_channel="gmail", policy_version="test")
        event = InboundEvent(id=f"event-{name}", external_thread_id=case.external_thread_id,
            channel="gmail", sender=case.applicant_contact, subject="UK visit", body="Hello", received_at=now)
        store.commit_event(case, event, "blocked", f"Reply for {name}")
    return store, now


class Sender:
    def __init__(self, *args):
        self.sent = []
        self.looked_up = []

    def send(self, request):
        self.sent.append(request)
        return "provider-selected"

    def find_sent_message(self, message_id):
        self.looked_up.append(message_id)
        return "provider-selected"


@pytest.mark.parametrize("action", ["send", "reconcile"])
def test_scoped_dispatch_selects_before_limit_and_never_mutates_other_cases(tmp_path, action):
    store, now = setup(tmp_path)
    try:
        if action == "reconcile":
            store.claim_pending_outbox(now, 3)
        others = [dict(row) for row in store.list_outbox() if row['case_id'] != 'selected']
        sender = Sender()
        dispatcher = OutboxDispatcher(store, sender, channel="gmail", case_id="selected")
        results = (dispatcher.dispatch_due(now, limit=1) if action == "send"
                   else dispatcher.reconcile_sending(sender, now, limit=1))
        assert len(results) == 1 and results[0].status == "SENT"
        selected = next(row for row in store.list_outbox() if row['case_id'] == 'selected')
        assert selected['id'] == results[0].outbox_id
        assert [dict(row) for row in store.list_outbox() if row['case_id'] != 'selected'] == others
        assert len(sender.sent) == (action == "send")
        assert len(sender.looked_up) == (action == "reconcile")
    finally:
        store.close()


def test_scoped_retirement_does_not_retire_other_customers_final_rows(tmp_path):
    store, now = setup(tmp_path)
    try:
        case = store.get_case('earlier')
        for number in range(2):
            event = InboundEvent(id=f"ready-{number}", external_thread_id=case.external_thread_id,
                channel="gmail", sender=case.applicant_contact, subject="Ready", body="Confirmed", received_at=now)
            store.commit_event(case, event, "ready", "Unsent final reply")
        others = [dict(row) for row in store.list_outbox() if row['case_id'] != 'selected']
        OutboxDispatcher(store, Sender(), channel="gmail", case_id="selected").dispatch_due(now, 1)
        assert [dict(row) for row in store.list_outbox() if row['case_id'] != 'selected'] == others
    finally:
        store.close()


@pytest.mark.parametrize("variant", ["valid", "wrong_sender", "unknown_case", "wrong_mode"])
def test_reviewed_runner_uses_open_binding_without_reconfiguring_or_touching_other_cases(tmp_path, monkeypatch, variant):
    store, _ = setup(tmp_path)
    ledger = ConsentLedger(store)
    ledger.configure(ProcessingScope(provider="DeepSeek", model="deepseek-v4-flash", interaction="service_request"))
    # New messages must be created under the registered scope epoch.
    case = store.get_case('selected')
    event = InboundEvent(id="current-selected", external_thread_id=case.external_thread_id,
        channel="gmail", sender=case.applicant_contact, subject="UK visit", body="Hello", received_at=datetime.now(UTC))
    store.commit_event(case, event, "blocked", "Current reply")
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='SENT' WHERE event_id='event-selected'")
    before = store.list_outbox()
    scope_id = ledger.scope().id
    store.close()
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps({"sender": None, "mailbox": "service@example.test", "subject": None, "after": 1}))
    binding = binding_path.read_bytes()
    spec = importlib.util.spec_from_file_location("scoped_runner", Path("scripts/gmail_sandbox.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    fake_service = SimpleNamespace(users=lambda: SimpleNamespace(getProfile=lambda **kwargs:
        SimpleNamespace(execute=lambda: {"emailAddress": "service@example.test"})))
    monkeypatch.setattr(runner, "build_gmail_service", lambda *a, **kw: fake_service)
    monkeypatch.setattr(runner, "GmailAdapter", lambda service: object())
    sent = []
    class FakeBase(Sender):
        def send(self, request):
            sent.append(request)
            return "provider-selected"
    monkeypatch.setattr(runner, "GmailReplySender", FakeBase)
    args = argparse.Namespace(action="send-reviewed", sender="selected@example.test", case="selected",
        mailbox="service@example.test", subject=None, after=None, state_dir=tmp_path,
        model="deepseek-v4-flash", processing_interaction="service_request", watch=True, crash_after_send=False)
    if variant == "wrong_sender":
        args.sender = "intruder@example.test"
    elif variant == "unknown_case":
        args.case = "missing"
    elif variant == "wrong_mode":
        args.processing_interaction = "explicit_consent"
    if variant == "valid":
        runner.run_once(args, argparse.ArgumentParser())
        assert len(sent) == 1 and sent[0].recipient == "selected@example.test"
    else:
        with pytest.raises(SystemExit):
            runner.run_once(args, argparse.ArgumentParser())
        assert not sent
    assert binding_path.read_bytes() == binding
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert ConsentLedger(store).scope().id == scope_id
        assert [r for r in store.list_outbox() if r['case_id'] != 'selected'] == [r for r in before if r['case_id'] != 'selected']
    finally:
        store.close()
