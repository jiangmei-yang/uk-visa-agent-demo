import argparse
import importlib.util
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.gmail import (
    GmailHistoryExpiredError,
    GmailHistoryPage,
    GmailMessagePage,
    GmailRawMessage,
)
from visa_agent.channels.gmail_intake import discover_messages, ordered_candidates, scope_rejection
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore


def metadata(identifier, timestamp, sender="applicant@example.test", mailbox="service@example.test"):
    return {"id": identifier, "internalDate": str(timestamp), "labelIds": ["INBOX"], "payload": {
        "headers": [{"name": "From", "value": sender}, {"name": "To", "value": mailbox}]}}


class Adapter:
    def __init__(self):
        self.metadata = {}
        self.expire = False
        self.pages = []

    def current_history_id(self):
        return "200"

    def list_message_page(self, query, token):
        self.pages.append(token)
        if token is None:
            return GmailMessagePage(tuple(f"id-{n}" for n in range(100)), "second")
        return GmailMessagePage(tuple(f"id-{n}" for n in range(100, 151)), None)

    def list_added_history_page(self, start, token):
        if self.expire:
            self.expire = False
            raise GmailHistoryExpiredError()
        return GmailHistoryPage(("id-0", "during-bootstrap"), None, "300")

    def get_intake_metadata(self, identifier):
        return self.metadata[identifier]


def test_page_budget_resumes_bootstrap_without_losing_overlap(tmp_path):
    adapter = Adapter()
    path = tmp_path / "sync.db"
    journal = GmailSyncJournal(path, "scope")
    assert not discover_messages(adapter, journal, "registered-query", max_pages=1)
    journal.close()
    journal = GmailSyncJournal(path, "scope")
    assert discover_messages(adapter, journal, "registered-query", max_pages=2)
    assert adapter.pages == [None, "second"]
    assert len(journal.pending_ids()) == 152
    journal.close()


def test_expired_cursor_performs_full_sync_without_resetting_old_queue(tmp_path):
    adapter = Adapter()
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    assert discover_messages(adapter, journal, "query")
    journal.acknowledge("id-0", "processed")
    adapter.expire = True
    assert discover_messages(adapter, journal, "query")
    assert "id-0" not in journal.pending_ids() and len(journal.pending_ids()) == 151
    journal.close()


def test_scopes_history_candidates_and_orders_by_provider_receipt_time(tmp_path):
    adapter = Adapter()
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    ids = ("new", "old", "outsider", "wrong-recipient", "pre-activation", "spam", "auto")
    state = journal.commit_page(state, GmailMessagePage(ids, None))
    journal.commit_page(state, GmailHistoryPage((), None, "200"))
    adapter.metadata = {key: metadata(key, 2000) for key in ids}
    adapter.metadata["old"]["internalDate"] = "1500"
    adapter.metadata["outsider"] = metadata("outsider", 2000, sender="other@example.test")
    adapter.metadata["wrong-recipient"] = metadata("wrong-recipient", 2000, mailbox="elsewhere@example.test")
    adapter.metadata["pre-activation"]["internalDate"] = "1000"
    adapter.metadata["spam"]["labelIds"] = ["SPAM"]
    adapter.metadata["auto"]["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-replied"})
    assert ordered_candidates(adapter, journal, sender="applicant@example.test",
        mailbox="service@example.test", after=1, subject=None) == ["old", "new"]
    assert journal.pending_ids() == ["new", "old"]
    journal.close()


def test_missing_timestamp_never_discards_a_scoped_candidate(tmp_path):
    adapter = Adapter()
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    state = journal.commit_page(state, GmailMessagePage(("bad",), None))
    journal.commit_page(state, GmailHistoryPage((), None, "200"))
    adapter.metadata["bad"] = metadata("bad", 1000)
    adapter.metadata["bad"].pop("internalDate")
    with pytest.raises(ValueError, match="timestamp"):
        ordered_candidates(adapter, journal, sender="applicant@example.test",
                           mailbox="service@example.test", after=1, subject=None)
    assert journal.pending_ids() == ["bad"]
    journal.close()


def test_duplicate_sender_headers_cannot_bypass_scope():
    message = Message()
    message["From"] = "applicant@example.test"
    message["From"] = "attacker@example.test"
    message["To"] = "service@example.test"
    assert scope_rejection(message, "applicant@example.test", "service@example.test", None)


def test_actual_runner_drains_over_100_messages_before_dispatch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("gmail_runner", Path("scripts/gmail_sandbox.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    reads, sends = [], []

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"emailAddress": "service@example.test"})

    class LiveAdapter(Adapter):
        def __init__(self, service):
            super().__init__()

        def get_intake_metadata(self, identifier):
            offset = 151 if identifier == "during-bootstrap" else int(identifier.split("-")[1])
            return metadata(identifier, 2000 + offset)

        def get_raw_message(self, identifier):
            reads.append(identifier)
            raw = (f"From: applicant@example.test\r\nTo: service@example.test\r\n"
                   f"Message-ID: <{identifier}@example.test>\r\nSubject: UK visit\r\n"
                   "Date: Fri, 04 Sep 2026 08:00:00 +0000\r\n\r\nHello, where do I start?").encode()
            return GmailRawMessage(identifier, "one-conversation", raw)

    class Dispatcher:
        def __init__(self, *args, **kwargs):
            pass

        def reconcile_sending(self, *args):
            return []

        def dispatch_due(self, *args, **kwargs):
            sends.append("dispatch")
            return []

    monkeypatch.setattr(runner, "build_gmail_service", lambda *a, **kw: Service())
    monkeypatch.setattr(runner, "GmailAdapter", LiveAdapter)
    monkeypatch.setattr(runner, "OutboxDispatcher", Dispatcher)
    monkeypatch.setattr(runner, "read_secret", lambda *a, **kw: "unused")
    monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *a, **kw: OfflineFixtureLLM())
    args = argparse.Namespace(action="serve", sender="applicant@example.test",
        mailbox="service@example.test", subject=None, after=1, state_dir=tmp_path,
        model="offline", watch=True)
    runner.run_once(args, argparse.ArgumentParser())
    assert len(reads) == 100 and not sends
    runner.run_once(args, argparse.ArgumentParser())
    assert reads == [f"id-{n}" for n in range(151)] + ["during-bootstrap"]
    assert len(sends) == 1
    runner.run_once(args, argparse.ArgumentParser())
    assert len(reads) == 152  # No repeated raw fetch or workflow processing on an idle cycle.
    store = SQLiteStore(tmp_path / "sandbox.db")
    assert store.counts()["processed_events"] == 152
    store.close()
