"""Exercise the actual runner without credentials, network access or outgoing messages."""

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("failure", [ValueError("Inbox scope exceeds bounded batch"),
                                    TimeoutError("inbox unavailable")])
def test_uncertain_sends_reconcile_before_failed_intake(tmp_path, monkeypatch, failure):
    spec = importlib.util.spec_from_file_location("gmail_runner", Path("scripts/gmail_sandbox.py"))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls = []

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"emailAddress": "service@example.test"})

    class Adapter:
        def __init__(self, service):
            pass

        def current_history_id(self):
            return "111"

        def list_message_page(self, query, token):
            calls.append("intake")
            raise failure

    class Dispatcher:
        def __init__(self, *args, **kwargs):
            pass

        def reconcile_sending(self, *args):
            calls.append("reconcile")
            return []

        def dispatch_due(self, *args, **kwargs):
            pytest.fail("Failed intake must not dispatch potentially stale replies")

    monkeypatch.setattr(runner, "build_gmail_service", lambda *a, **kw: Service())
    monkeypatch.setattr(runner, "GmailAdapter", Adapter)
    monkeypatch.setattr(runner, "OutboxDispatcher", Dispatcher)
    monkeypatch.setattr(runner, "read_secret", lambda *a, **kw: "unused-local-test-key")
    monkeypatch.setattr(runner, "DeepSeekStructuredLLM", lambda *a, **kw: object())
    args = argparse.Namespace(action="serve", sender="applicant@example.test",
        mailbox="service@example.test", subject=None, after=1788508800,
        state_dir=tmp_path, model="unused", watch=False)
    with pytest.raises(type(failure), match=str(failure)):
        runner.run_once(args, argparse.ArgumentParser())
    assert calls == ["reconcile", "intake"]
