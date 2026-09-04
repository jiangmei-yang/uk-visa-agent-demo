import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage
from visa_agent.channels.gmail_intake import discover_messages
from visa_agent.channels.gmail_sync import GmailSyncJournal


def seeded(path):
    journal = GmailSyncJournal(path, "scope")
    state = journal.start_full("100", None)
    state = journal.commit_page(state, GmailMessagePage(("old", "pending"), "expired-token"))
    journal.acknowledge("old", "processed")
    return journal, state


def test_rescan_preserves_candidates_and_waits_for_a_fresh_provider_anchor(tmp_path):
    path = tmp_path / "sync.db"
    journal, prior = seeded(path)
    requested = journal.request_rescan(prior, actor="operator", reason="Provider rejected page token")
    assert requested.phase == "rescan" and not journal.discovery_drained()
    assert journal.pending_ids() == ["pending"]
    journal.close()
    journal = GmailSyncJournal(path, "scope")

    class Adapter:
        failing = True

        def current_history_id(self):
            if self.failing:
                raise OSError("provider unavailable")
            return "200"

        def list_message_page(self, query, token):
            assert query == "original-scope" and token is None
            return GmailMessagePage(("old", "pending", "new"), None)

        def list_added_history_page(self, start, token):
            assert start == "200"
            return GmailHistoryPage(("overlap",), None, "201")

    adapter = Adapter()
    try:
        with pytest.raises(OSError):
            discover_messages(adapter, journal, "original-scope")
        assert journal.checkpoint() == requested
        adapter.failing = False
        assert discover_messages(adapter, journal, "original-scope")
        assert journal.pending_ids() == ["pending", "new", "overlap"]
        assert not journal.discovery_drained()
        assert journal.connection.execute("SELECT status FROM candidates WHERE id='old'").fetchone()[0] == "processed"
        assert journal.connection.execute("SELECT actor, reason FROM recovery_actions").fetchone() == (
            "operator", "Provider rejected page token")
    finally:
        journal.close()


def test_stale_or_unauditable_rescan_never_changes_checkpoint(tmp_path):
    journal, prior = seeded(tmp_path / "sync.db")
    try:
        with pytest.raises(ValueError, match="actor and reason"):
            journal.request_rescan(prior, actor="", reason="reason")
        assert journal.checkpoint() == prior
        journal.connection.execute("""CREATE TRIGGER reject_audit BEFORE INSERT ON recovery_actions
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            journal.request_rescan(prior, actor="operator", reason="reason")
        assert journal.checkpoint() == prior
        journal.connection.execute("DROP TRIGGER reject_audit")
        requested = journal.request_rescan(prior, actor="operator", reason="reason")
        with pytest.raises(ValueError, match="Stale"):
            journal.request_rescan(prior, actor="operator", reason="reason")
        assert journal.checkpoint() == requested
        assert journal.connection.execute("SELECT COUNT(*) FROM recovery_actions").fetchone()[0] == 1
    finally:
        journal.close()


def test_operator_command_checks_revision_and_does_not_touch_case_database(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sync_recover", Path("scripts/gmail_sync_recover.py"))
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    binding = {"sender": "fictional@example.test", "mailbox": "service@example.test", "subject": None}
    (tmp_path / "binding.json").write_text(json.dumps(binding))
    case_db = tmp_path / "sandbox.db"
    case_db.write_bytes(b"untouched-case-database-sentinel")
    # The actual service uses sorted keys, unlike binding.json's insertion order.
    journal = GmailSyncJournal(tmp_path / "sync.db", json.dumps(binding, sort_keys=True))
    state = journal.start_full("100", None)
    journal.close()
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: argparse.Namespace(
        action="rescan", state_dir=tmp_path, expected_revision=state.revision,
        actor="operator", reason="Inspecting rejected pagination"))
    command.main()
    with pytest.raises(SystemExit):
        command.main()  # The reviewed revision is stale after the first request.
    assert case_db.read_bytes() == b"untouched-case-database-sentinel"
