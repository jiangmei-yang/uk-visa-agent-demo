"""Broken provider pagination -> audited rescan -> real runner restart, fully offline."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore

SENDER = "applicant@example.test"
MAILBOX = "service@example.test"
ACTIVATED_AT = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
BINDING = {"sender": SENDER, "mailbox": MAILBOX, "subject": None, "after": ACTIVATED_AT}
SCOPE = json.dumps(BINDING, sort_keys=True)
QUERY = f"from:{SENDER} to:{MAILBOX} after:{ACTIVATED_AT}"
ACTOR = "synthetic-recovery-operator"
REASON = "Provider rejected the continuation token after the first scoped page"


class BadPageToken(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Provider rejected the synthetic page token")
        self.resp = SimpleNamespace(status=400)
        self.content = json.dumps({"error": {"errors": [{"reason": "invalidArgument"}]}})


class FakeGmailProvider:
    """A persistent fake mailbox; each runner iteration constructs a fresh API service."""

    def __init__(self) -> None:
        self.mode = "broken"
        self.identifiers = [f"mail-{index:03d}" for index in range(103)]
        self.first_page = list(reversed(self.identifiers[30:90]))
        base = datetime(2026, 9, 3, 8, tzinfo=UTC)
        # Distinct receipt times deliberately disagree with provider IDs/discovery order.
        self.received_at = {identifier: base + timedelta(minutes=(index * 37) % 103)
                            for index, identifier in enumerate(self.identifiers)}
        self.chronological = sorted(self.identifiers, key=self.received_at.__getitem__)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raw_reads: list[str] = []
        self.metadata_reads: list[str] = []
        self.sends: list[dict[str, Any]] = []
        self.service_instances = 0
        self.fail_next_anchor = False

    def service(self) -> FakeGmailService:
        self.service_instances += 1
        return FakeGmailService(self)

    def raw_message(self, identifier: str) -> bytes:
        message = EmailMessage()
        message["From"] = SENDER
        message["To"] = MAILBOX
        message["Subject"] = "UK visitor enquiry"
        message["Message-ID"] = f"<{identifier}@example.test>"
        message["Date"] = format_datetime(self.received_at[identifier])
        message.set_content("Hello, where should I start with my UK visitor documents?")
        return message.as_bytes()


class FakeGmailService:
    def __init__(self, provider: FakeGmailProvider) -> None:
        self.provider = provider
        self.profile_calls = 0

    def users(self) -> FakeGmailService:
        return self

    def messages(self) -> FakeGmailService:
        return self

    def getProfile(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.profile_calls += 1
            self.provider.calls.append(("profile", dict(kwargs)))
            # The first profile verifies the mailbox; the second obtains the fresh anchor.
            if self.profile_calls == 2 and self.provider.fail_next_anchor:
                self.provider.fail_next_anchor = False
                raise TimeoutError("Synthetic fresh history anchor unavailable")
            return {"emailAddress": MAILBOX,
                    "historyId": "100" if self.provider.mode == "broken" else "300"}

        return SimpleNamespace(execute=execute)

    def list(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.provider.calls.append(("messages.list", dict(kwargs)))
            assert kwargs["userId"] == "me" and kwargs["q"] == QUERY
            assert kwargs["maxResults"] == 100 and kwargs["includeSpamTrash"] is False
            token = kwargs.get("pageToken")
            if self.provider.mode == "broken":
                if token is None:
                    return {"messages": [{"id": item} for item in self.provider.first_page],
                            "nextPageToken": "provider-rejected-page-two"}
                assert token == "provider-rejected-page-two"
                raise BadPageToken()
            if token is None:
                ids = list(reversed(self.provider.identifiers[:55]))
                return {"messages": [{"id": item} for item in ids],
                        "nextPageToken": "rescan-page-two"}
            assert token == "rescan-page-two", "A rescan must not reuse the rejected continuation"
            return {"messages": [{"id": item} for item in reversed(self.provider.identifiers[45:102])]}

        return SimpleNamespace(execute=execute)

    def history(self) -> SimpleNamespace:
        return SimpleNamespace(list=self._history_list)

    def _history_list(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.provider.calls.append(("history.list", dict(kwargs)))
            assert self.provider.mode == "recovered"
            assert kwargs["userId"] == "me" and kwargs["historyTypes"] == ["messageAdded"]
            start, token = kwargs["startHistoryId"], kwargs.get("pageToken")
            if start == "300":
                # One new message arrived during full discovery; other additions overlap.
                if token is None:
                    ids, next_token = ["mail-102", "mail-050"], "catchup-page-two"
                else:
                    assert token == "catchup-page-two"
                    ids, next_token = ["mail-002", "mail-102"], None
                history_id = "310"
            else:
                assert start in {"310", "311"} and token is None
                # Repeated provider additions must not re-run already processed events.
                ids = self.provider.identifiers
                next_token, history_id = None, str(int(start) + 1)
            result: dict[str, Any] = {"historyId": history_id, "history": [{
                "messagesAdded": [{"message": {"id": item}} for item in ids],
            }]}
            if next_token is not None:
                result["nextPageToken"] = next_token
            return result

        return SimpleNamespace(execute=execute)

    def get(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.provider.calls.append(("messages.get", dict(kwargs)))
            identifier = kwargs["id"]
            assert kwargs["userId"] == "me" and identifier in self.provider.identifiers
            if kwargs["format"] == "metadata":
                self.provider.metadata_reads.append(identifier)
                return {"id": identifier, "internalDate": str(int(self.provider.received_at[identifier].timestamp() * 1000)),
                        "labelIds": ["INBOX"], "payload": {"headers": [
                            {"name": "From", "value": SENDER}, {"name": "To", "value": MAILBOX},
                            {"name": "Subject", "value": "UK visitor enquiry"},
                        ]}}
            assert kwargs["format"] == "raw"
            self.provider.raw_reads.append(identifier)
            return {"id": identifier, "threadId": "synthetic-rescan-conversation",
                    "raw": base64.urlsafe_b64encode(self.provider.raw_message(identifier)).decode("ascii")}

        return SimpleNamespace(execute=execute)

    def send(self, **kwargs: Any) -> SimpleNamespace:
        def execute() -> dict[str, Any]:
            self.provider.calls.append(("messages.send", dict(kwargs)))
            self.provider.sends.append(dict(kwargs))
            return {"id": "captured-provider-send-once"}

        return SimpleNamespace(execute=execute)


def _module(path: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(state_dir: Path) -> tuple[Any, list[str], list[tuple[Any, ...]]]:
    journal = GmailSyncJournal(state_dir / "sync.db", SCOPE)
    try:
        return (journal.checkpoint(), journal.pending_ids(), journal.connection.execute(
            "SELECT revision, actor, reason, previous_checkpoint FROM recovery_actions ORDER BY revision",
        ).fetchall())
    finally:
        journal.close()


@pytest.mark.parametrize("interrupt_fresh_anchor", [False, True])
def test_broken_page_rescan_and_restarted_runner_preserve_and_drain_the_same_journey(
    tmp_path, monkeypatch, capsys, interrupt_fresh_anchor,
):
    provider = FakeGmailProvider()
    extractions: list[str] = []
    render_calls: list[str] = []

    class TracingOfflineLLM(OfflineFixtureLLM):
        def extract_case_patch(self, event: InboundEvent) -> CasePatch:
            extractions.append(event.id)
            return super().extract_case_patch(event)

        def render_message(self, case: Case, plan: str) -> str:
            render_calls.append(plan)
            return super().render_message(case, plan)

    args = argparse.Namespace(action="serve", sender=SENDER, mailbox=MAILBOX, subject=None,
        after=ACTIVATED_AT, state_dir=tmp_path, model="offline", watch=True)

    def iteration() -> None:
        # Reload the actual runner, recreating its services/stores just as a restart would.
        runner = _module("scripts/gmail_sandbox.py", "gmail_rescan_journey_runner")
        monkeypatch.setattr(runner, "build_gmail_service", lambda *a, **kw: provider.service())
        monkeypatch.setattr(runner, "read_secret", lambda *a, **kw: "unused-offline-test-key")
        monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *a, **kw: TracingOfflineLLM())
        runner.run_once(args, argparse.ArgumentParser())

    # Start with no checkpoint. Page one is really received and committed before page two fails.
    assert not (tmp_path / "sync.db").exists()
    with pytest.raises(BadPageToken, match="page token"):
        iteration()
    broken, pending, audit = _snapshot(tmp_path)
    assert broken.phase == "full" and broken.history_id == "100"
    assert broken.page_token == "provider-rejected-page-two" and broken.revision == 1
    assert pending == provider.first_page and audit == []
    assert provider.raw_reads == provider.metadata_reads == extractions == render_calls == provider.sends == []
    full_calls = [kwargs for kind, kwargs in provider.calls if kind == "messages.list"]
    assert [call.get("pageToken") for call in full_calls] == [None, "provider-rejected-page-two"]
    assert json.loads((tmp_path / "binding.json").read_text()) == BINDING
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.counts()["cases"] == store.counts()["processed_events"] == store.counts()["outbox"] == 0
    finally:
        store.close()

    # The real operator command consumes the reviewed revision and opens only the sync journal.
    operator = _module("scripts/gmail_sync_recover.py", "gmail_rescan_journey_operator")
    before_command_calls = list(provider.calls)
    case_bytes = (tmp_path / "sandbox.db").read_bytes()
    binding_bytes = (tmp_path / "binding.json").read_bytes()
    with monkeypatch.context() as command_patch:
        command_patch.setattr(sys, "argv", ["gmail_sync_recover.py", "rescan", "--state-dir", str(tmp_path),
            "--expected-revision", str(broken.revision), "--actor", ACTOR, "--reason", REASON])
        operator.main()
    requested, pending, audit = _snapshot(tmp_path)
    assert requested.phase == "rescan" and requested.revision == broken.revision + 1
    assert requested.history_id == "100" and requested.page_token is None
    assert pending == provider.first_page and len(audit) == 1
    assert audit[0][:3] == (requested.revision, ACTOR, REASON)
    previous = json.loads(audit[0][3])
    assert previous["phase"] == "full" and previous["page_token"] == broken.page_token
    assert previous["history_id"] == "100" and previous["revision"] == broken.revision
    assert provider.calls == before_command_calls
    assert (tmp_path / "sandbox.db").read_bytes() == case_bytes
    assert (tmp_path / "binding.json").read_bytes() == binding_bytes
    assert "No candidate cleared, case changed, mail read or message sent" in capsys.readouterr().out

    provider.mode = "recovered"
    if interrupt_fresh_anchor:
        provider.fail_next_anchor = True
        with pytest.raises(TimeoutError, match="fresh history anchor"):
            iteration()
        assert _snapshot(tmp_path) == (requested, pending, audit)
        assert provider.raw_reads == extractions == render_calls == provider.sends == []

    # A fresh anchor precedes two full pages and two history pages, with overlapping IDs.
    before_recovery = len(provider.calls)
    iteration()
    calls = provider.calls[before_recovery:]
    discovery = [(kind, kwargs) for kind, kwargs in calls if kind in {"messages.list", "history.list"}]
    assert [kind for kind, _ in discovery] == ["messages.list", "messages.list", "history.list", "history.list"]
    assert [kwargs.get("pageToken") for _, kwargs in discovery] == [None, "rescan-page-two", None, "catchup-page-two"]
    assert [kwargs["startHistoryId"] for kind, kwargs in discovery if kind == "history.list"] == ["300", "300"]
    assert [kind for kind, _ in calls[:2]] == ["profile", "profile"]
    assert provider.raw_reads == extractions == provider.chronological[:100]
    assert set(provider.metadata_reads) == set(provider.identifiers)
    assert provider.sends == []  # Real dispatcher remains behind the 100-body backlog gate.
    ready, remaining, audit_after_partial = _snapshot(tmp_path)
    assert ready.phase == "ready" and ready.history_id == "310"
    assert set(remaining) == set(provider.chronological[100:]) and audit_after_partial == audit
    assert "Intake backlog remains; no dispatch" in capsys.readouterr().out
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.counts()["cases"] == 1
        assert store.counts()["processed_events"] == store.counts()["outbox"] == 100
        assert {row["status"] for row in store.list_outbox()} == {"PENDING"}
    finally:
        store.close()

    # Another restart sees duplicate history additions, processes only the remaining three,
    # and the actual automatic sender sends only the newest reply, not 103 obsolete drafts.
    iteration()
    assert provider.raw_reads == extractions == provider.chronological
    assert Counter(extractions) == Counter({item: 1 for item in provider.identifiers})
    assert len(provider.sends) == 1
    drained, remaining, audit_after_drain = _snapshot(tmp_path)
    assert drained.phase == "ready" and drained.history_id == "311"
    assert remaining == [] and audit_after_drain == audit
    journal = GmailSyncJournal(tmp_path / "sync.db", SCOPE)
    try:
        assert journal.discovery_drained()
        statuses = journal.connection.execute("SELECT status, COUNT(*) FROM candidates GROUP BY status").fetchall()
        assert statuses == [("processed", 103)]
    finally:
        journal.close()
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.counts()["cases"] == 1
        assert store.counts()["processed_events"] == store.counts()["outbox"] == 103
        rows = store.list_outbox()
        assert len({row["id"] for row in rows}) == 103
        assert {row["event_id"] for row in rows} == set(provider.identifiers)
        sent = [row for row in rows if row["status"] == "SENT"]
        assert len(sent) == 1 and sent[0]["event_id"] == provider.chronological[-1]
        assert sent[0]["provider_message_id"] == "captured-provider-send-once"
        obsolete = [row for row in rows if row["status"] == "FAILED"]
        assert len(obsolete) == 102 and all(row["attempt_count"] == 0 for row in obsolete)
        assert all(row["last_error"] == "Obsolete unsent reply withheld" for row in obsolete)
        wire = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(provider.sends[0]["body"]["raw"]))
        assert str(wire["Message-ID"]) == f"<{sent[0]['id']}@visa-agent.local>"
        assert str(wire["To"]) == SENDER and str(wire["In-Reply-To"]) == f"<{provider.chronological[-1]}@example.test>"
        assert provider.sends[0]["body"]["threadId"] == "synthetic-rescan-conversation"
        final_case = store.list_cases()[0].model_dump_json()
        final_rows = rows
    finally:
        store.close()

    # A further idle restart receives repeated history IDs but does no body/model/send work.
    body_count, metadata_count, render_count = len(provider.raw_reads), len(provider.metadata_reads), len(render_calls)
    iteration()
    assert len(provider.raw_reads) == body_count and len(provider.metadata_reads) == metadata_count
    assert len(render_calls) == render_count and len(extractions) == 103 and len(provider.sends) == 1
    assert provider.service_instances == (5 if interrupt_fresh_anchor else 4)
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.list_outbox() == final_rows
        assert store.list_cases()[0].model_dump_json() == final_case
    finally:
        store.close()
