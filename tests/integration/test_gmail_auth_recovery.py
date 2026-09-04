"""Runner recovery simulation; no real token is loaded, revoked, or refreshed."""

import argparse
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage
from visa_agent.channels.outbound import PermanentChannelError
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("failure_point", ["credential_refresh", "profile", "discovery"])
def test_auth_failure_preserves_pending_reply_and_recovery_sends_once(tmp_path, monkeypatch, failure_point):
    spec = importlib.util.spec_from_file_location("auth_recovery_runner", Path("scripts/gmail_sandbox.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    failing = True
    sends = []

    def reject_if(point):
        if failing and failure_point == point:
            raise PermanentChannelError("Simulated authorization rejected; reconnect required")

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            def execute():
                reject_if("profile")
                return {"emailAddress": "service@example.test"}
            return SimpleNamespace(execute=execute)

    def build_service(*args, **kwargs):
        assert kwargs["interactive"] is False
        reject_if("credential_refresh")
        return Service()

    class Adapter:
        def __init__(self, service):
            pass

        def current_history_id(self):
            return "200"

        def list_message_page(self, query, page_token):
            reject_if("discovery")
            return GmailMessagePage((), None)

        def list_added_history_page(self, start, page_token):
            return GmailHistoryPage((), None, "201")

        def send_reply(self, **kwargs):
            sends.append(kwargs)
            return {"id": "one-provider-acceptance"}

    case = Case(id="case", external_thread_id="thread", applicant_contact="applicant@example.test",
                primary_channel="gmail", policy_version="v")
    event = InboundEvent(id="original", external_thread_id="thread", sender=case.applicant_contact,
        subject="UK visit", body="Where do I start?", channel="gmail", received_at=datetime.now(UTC))
    with_store = SQLiteStore(tmp_path / "sandbox.db")
    with_store.commit_event(case, event, "blocked", "existing draft")
    original_case = with_store.get_case(case.id).model_dump_json()
    original_outbox = with_store.list_outbox()
    with_store.close()
    monkeypatch.setattr(runner, "build_gmail_service", build_service)
    monkeypatch.setattr(runner, "GmailAdapter", Adapter)
    monkeypatch.setattr(runner, "read_secret", lambda *a, **kw: "unused")
    monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *a, **kw: OfflineFixtureLLM())
    args = argparse.Namespace(action="serve", sender=case.applicant_contact,
        mailbox="service@example.test", subject=None, after=1, state_dir=tmp_path,
        model="offline", watch=True)

    with pytest.raises(PermanentChannelError, match="authorization rejected"):
        runner.run_once(args, argparse.ArgumentParser())
    store = SQLiteStore(tmp_path / "sandbox.db")
    assert store.get_case(case.id).model_dump_json() == original_case
    assert store.list_outbox() == original_outbox
    assert store.counts()["processed_events"] == 1
    assert sends == []
    store.close()

    failing = False
    runner.run_once(args, argparse.ArgumentParser())
    runner.run_once(args, argparse.ArgumentParser())
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert len(sends) == 1
        assert sends[0]["recipient"] == case.applicant_contact
        assert store.get_case(case.id).model_dump_json() == original_case
        assert store.counts()["processed_events"] == 1
        rows = store.list_outbox()
        assert len(rows) == 1 and rows[0]["status"] == "SENT"
        assert rows[0]["provider_message_id"] == "one-provider-acceptance"
    finally:
        store.close()
