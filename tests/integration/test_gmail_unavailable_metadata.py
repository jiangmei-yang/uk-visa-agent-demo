"""Fictional metadata failure/recovery through the real Gmail intake runner."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.gmail import GmailAdapter, GmailMessageUnavailableError
from visa_agent.channels.gmail_intake import discover_messages, ordered_candidates
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.channels.outbound import PermanentChannelError, TransientChannelError
from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore

SENDER = "fictional-applicant@example.test"
MAILBOX = "fictional-service@example.test"
ACTIVATION = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
BINDING = {"sender": SENDER, "mailbox": MAILBOX, "subject": None, "after": ACTIVATION}
SCOPE = json.dumps(BINDING, sort_keys=True)
PRIVATE_ERROR = "fictional-provider-secret-DO-NOT-PERSIST"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unavailable-metadata tests must never use a real network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


class ProviderError(RuntimeError):
    def __init__(self, status, reason="notFound"):
        super().__init__(PRIVATE_ERROR)
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps({"error": {"message": PRIVATE_ERROR,
                                            "errors": [{"reason": reason}]}}).encode()


class Mailbox:
    def __init__(self, *, recovered_older=False):
        self.status = 404
        self.reason = "notFound"
        self.calls, self.raw_reads, self.sends, self.extractions = [], [], [], []
        self.service_instances = 0
        healthy_at = datetime(2026, 9, 3, 10, tzinfo=UTC)
        self.received = {"healthy": healthy_at,
                         "unavailable": healthy_at + timedelta(minutes=-1 if recovered_older else 1)}

    def service(self):
        self.service_instances += 1
        return Service(self)

    def raw(self, identifier):
        message = EmailMessage()
        message["From"], message["To"] = SENDER, MAILBOX
        message["Subject"] = "UK visitor preparation"
        message["Message-ID"] = f"<{identifier}@example.test>"
        message["Date"] = format_datetime(self.received[identifier])
        message.set_content(
            "My full name is Sample Rowan. Please pause my UK visa preparation for now."
            if identifier == "healthy" else "My date of birth is 1992-06-10."
        )
        return message.as_bytes()


class Service:
    def __init__(self, mailbox):
        self.mailbox = mailbox

    def users(self):
        return self

    def messages(self):
        return self

    def getProfile(self, **kwargs):
        self.mailbox.calls.append(("profile", kwargs))
        return SimpleNamespace(execute=lambda: {"emailAddress": MAILBOX, "historyId": "100"})

    def list(self, **kwargs):
        self.mailbox.calls.append(("list", kwargs))
        assert kwargs["q"] == f"from:{SENDER} to:{MAILBOX} after:{ACTIVATION}"
        return SimpleNamespace(execute=lambda: {"messages": [{"id": "unavailable"}, {"id": "healthy"}]})

    def history(self):
        return SimpleNamespace(list=self.history_list)

    def history_list(self, **kwargs):
        self.mailbox.calls.append(("history", kwargs))
        return SimpleNamespace(execute=lambda: {"historyId": str(int(kwargs["startHistoryId"]) + 1),
            "history": [{"messagesAdded": [{"message": {"id": item}} for item in ("healthy", "unavailable")]}]})

    def get(self, **kwargs):
        def execute():
            self.mailbox.calls.append(("get", kwargs))
            identifier = kwargs["id"]
            assert identifier in self.mailbox.received
            if kwargs["format"] == "metadata":
                if identifier == "unavailable" and self.mailbox.status is not None:
                    raise ProviderError(self.mailbox.status, self.mailbox.reason)
                return {"id": identifier, "labelIds": ["INBOX"],
                    "internalDate": str(int(self.mailbox.received[identifier].timestamp() * 1000)),
                    "payload": {"headers": [{"name": "From", "value": SENDER},
                                             {"name": "To", "value": MAILBOX},
                                             {"name": "Subject", "value": "UK visitor preparation"}]}}
            assert kwargs["format"] == "raw"
            self.mailbox.raw_reads.append(identifier)
            return {"id": identifier, "threadId": "fictional-metadata-thread",
                    "raw": base64.urlsafe_b64encode(self.mailbox.raw(identifier)).decode()}

        return SimpleNamespace(execute=execute)

    def send(self, **kwargs):
        def execute():
            self.mailbox.sends.append(kwargs)
            return {"id": "fictional-send-once"}

        return SimpleNamespace(execute=execute)


def _ordered(adapter, journal):
    return ordered_candidates(adapter, journal, sender=SENDER, mailbox=MAILBOX,
                              after=ACTIVATION, subject=None)


def _discovered(tmp_path, mailbox):
    journal = GmailSyncJournal(tmp_path / "sync.db", SCOPE)
    adapter = GmailAdapter(mailbox.service())
    assert discover_messages(adapter, journal, f"from:{SENDER} to:{MAILBOX} after:{ACTIVATION}")
    return adapter, journal


def test_metadata_404_is_typed_and_does_not_publish_original_provider_exception():
    with pytest.raises(GmailMessageUnavailableError) as caught:
        GmailAdapter(Mailbox().service()).get_intake_metadata("unavailable")
    assert str(caught.value) == "Gmail candidate metadata is unavailable"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert PRIVATE_ERROR not in str(caught.value)


@pytest.mark.parametrize("status,reason,error_type", [
    (401, "authError", PermanentChannelError),
    (403, "insufficientPermissions", PermanentChannelError),
    (403, "rateLimitExceeded", TransientChannelError),
    (429, "rateLimitExceeded", TransientChannelError),
    (500, "backendError", TransientChannelError),
    (503, "backendError", TransientChannelError),
])
def test_non_404_failure_stops_ordering_without_unavailable_or_ignored_disposition(tmp_path, status, reason, error_type):
    mailbox = Mailbox()
    mailbox.status, mailbox.reason = status, reason
    adapter, journal = _discovered(tmp_path, mailbox)
    try:
        with pytest.raises(error_type) as caught:
            _ordered(adapter, journal)
        assert not isinstance(caught.value, GmailMessageUnavailableError)
        assert PRIVATE_ERROR not in str(caught.value)
        assert journal.pending_ids() == ["unavailable", "healthy"]
        assert journal.unavailable_metadata() == []
        assert not journal.discovery_drained()
        assert PRIVATE_ERROR not in "\n".join(journal.connection.iterdump())
        assert mailbox.raw_reads == mailbox.sends == []
    finally:
        journal.close()


def test_404_observation_is_redacted_and_metadata_success_alone_is_not_acknowledgement(tmp_path):
    mailbox = Mailbox()
    adapter, journal = _discovered(tmp_path, mailbox)
    try:
        assert _ordered(adapter, journal) == ["healthy"]
        observed = journal.unavailable_metadata()
        assert len(observed) == 1
        assert set(observed[0]) == {"id", "code", "observations", "first_seen", "last_seen"}
        assert observed[0]["id"] == "unavailable" and observed[0]["code"] == "METADATA_NOT_FOUND"
        assert observed[0]["observations"] == 1
        assert journal.pending_ids() == ["unavailable", "healthy"]
        mailbox.status = None
        assert _ordered(adapter, journal) == ["healthy", "unavailable"]
        assert journal.unavailable_metadata() == []
        assert journal.pending_ids() == ["unavailable", "healthy"] and not journal.discovery_drained()
        record = journal.connection.execute("SELECT observations, resolved_at FROM candidate_metadata_errors").fetchone()
        assert record[0] == 1 and record[1] is not None
        assert PRIVATE_ERROR not in "\n".join(journal.connection.iterdump())
    finally:
        journal.close()


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(path):
    journal = GmailSyncJournal(path / "sync.db", SCOPE)
    try:
        return journal.checkpoint(), journal.pending_ids(), journal.unavailable_metadata(), list(journal.connection.iterdump())
    finally:
        journal.close()


@pytest.mark.parametrize("recovered_older", [False, True], ids=["fresh-update", "out-of-order-held"])
def test_actual_runner_keeps_unknown_pending_processes_healthy_and_recovers_without_loss_or_resend(
    tmp_path, monkeypatch, capsys, recovered_older,
):
    mailbox = Mailbox(recovered_older=recovered_older)

    class Model:
        def extract_case_patch(self, event):
            mailbox.extractions.append(event.id)
            if event.id == "healthy":
                return CasePatch.model_validate({"updates": [{"field": "full_name", "value": "Sample Rowan",
                    "source_excerpt": "My full name is Sample Rowan.", "confidence": 1}], "ambiguities": [],
                    "preparation_intent": {"action": "pause", "confidence": 1,
                        "source_excerpt": "Please pause my UK visa preparation for now."}})
            return CasePatch.model_validate({"updates": [{"field": "date_of_birth", "value": "1992-06-10",
                "source_excerpt": "My date of birth is 1992-06-10.", "confidence": 1}], "ambiguities": []})

        render_message = staticmethod(deterministic_fallback_message)

    args = argparse.Namespace(action="serve", sender=SENDER, mailbox=MAILBOX, subject=None,
                              after=ACTIVATION, state_dir=tmp_path, model="offline", watch=True)

    def iteration():
        runner = _module("scripts/gmail_sandbox.py", "gmail_unavailable_test_runner")
        monkeypatch.setattr(runner, "build_gmail_service", lambda *a, **kw: mailbox.service())
        monkeypatch.setattr(runner, "read_secret", lambda *a, **kw: "unused-fictional-model-key")
        monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *a, **kw: Model())
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)

    # Bootstrap from no checkpoint; the first persisted candidate is unavailable.
    assert not (tmp_path / "sync.db").exists()
    iteration()
    state, pending, errors, dump = _snapshot(tmp_path)
    assert state.phase == "ready" and pending == ["unavailable"]
    assert errors[0]["observations"] == 1 and PRIVATE_ERROR not in "\n".join(dump)
    assert mailbox.raw_reads == mailbox.extractions == ["healthy"] and mailbox.sends == []
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        case = store.list_cases()[0]
        assert case.profile.full_name == "Sample Rowan" and case.preparation_paused
        assert case.profile.date_of_birth is None and case.preparation_control_epoch == 1
        assert len(store.list_outbox()) == 1 and store.list_outbox()[0]["status"] == "PENDING"
        assert not store.event_processed("unavailable")
    finally:
        store.close()

    iteration()  # Reopening does not drop the unknown ID or re-extract healthy mail.
    assert _snapshot(tmp_path)[1] == ["unavailable"]
    assert _snapshot(tmp_path)[2][0]["observations"] == 2
    assert mailbox.raw_reads == mailbox.extractions == ["healthy"] and mailbox.sends == []
    initial_output = capsys.readouterr().out
    assert "Intake backlog remains; no dispatch" in initial_output and PRIVATE_ERROR not in initial_output

    # The real operator rescan command cannot turn an observation into ignored mail.
    state = _snapshot(tmp_path)[0]
    operator = _module("scripts/gmail_sync_recover.py", "gmail_unavailable_test_operator")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["gmail_sync_recover.py", "rescan", "--state-dir", str(tmp_path),
            "--expected-revision", str(state.revision), "--actor", "fictional-operator",
            "--reason", "Retry scoped discovery after unavailable metadata"])
        operator.main()
    assert _snapshot(tmp_path)[0].phase == "rescan"
    assert _snapshot(tmp_path)[1] == ["unavailable"]
    assert _snapshot(tmp_path)[2][0]["observations"] == 2
    iteration()
    assert _snapshot(tmp_path)[1] == ["unavailable"]
    assert _snapshot(tmp_path)[2][0]["observations"] == 3
    assert mailbox.raw_reads == mailbox.extractions == ["healthy"] and mailbox.sends == []

    mailbox.status = None
    iteration()
    state, pending, errors, dump = _snapshot(tmp_path)
    assert state.phase == "ready" and pending == [] and errors == []
    assert PRIVATE_ERROR not in "\n".join(dump)
    assert mailbox.raw_reads == ["healthy", "unavailable"]
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        case = store.list_cases()[0]
        assert case.preparation_paused and case.preparation_control_epoch == 1
        assert store.event_processed("healthy") and store.event_processed("unavailable")
        assert not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None
        held = store.list_held_inbound(case.id)
        if recovered_older:
            assert mailbox.extractions == ["healthy"] and case.profile.date_of_birth is None
            assert len(held) == 1 and held[0]["id"] == "unavailable"
            assert held[0]["reason_code"] == "OUT_OF_ORDER_EVENT"
            assert InboundEvent.model_validate_json(held[0]["payload_json"]).body.strip() == "My date of birth is 1992-06-10."
            assert store.has_unreviewed_held_updates(case.id)
        else:
            assert mailbox.extractions == ["healthy", "unavailable"] and held == []
            assert case.profile.date_of_birth == date(1992, 6, 10)
        rows = store.list_outbox()
        sent = [row for row in rows if row["status"] == "SENT"]
        assert len(sent) == len(mailbox.sends) == 1
        assert sent[0]["event_id"] == ("healthy" if recovered_older else "unavailable")
        assert all(row["attempt_count"] == 0 for row in rows if row["status"] == "FAILED")
        wire = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(mailbox.sends[0]["body"]["raw"]))
        assert wire.get_body(preferencelist=("plain",)).get_content().strip() == sent[0]["payload"].strip()
        assert str(wire["To"]) == SENDER and sent[0]["provider_message_id"] == "fictional-send-once"
        assert PRIVATE_ERROR not in "\n".join(store.connection.iterdump())
        snapshot = store.export_case_data(case.id)
    finally:
        store.close()

    calls_before = (list(mailbox.raw_reads), list(mailbox.extractions), list(mailbox.sends))
    iteration()
    assert (mailbox.raw_reads, mailbox.extractions, mailbox.sends) == calls_before
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.export_case_data(case.id) == snapshot
    finally:
        store.close()
    assert PRIVATE_ERROR not in capsys.readouterr().out
