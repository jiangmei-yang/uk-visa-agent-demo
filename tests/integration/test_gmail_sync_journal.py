import sqlite3
import subprocess
import sys

import pytest

from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage
from visa_agent.channels.gmail_sync import GmailSyncJournal


def test_more_than_100_candidates_survive_restart_and_bootstrap_catchup(tmp_path):
    path = tmp_path / "sync.db"
    journal = GmailSyncJournal(path, "registered-scope")
    state = journal.start_full("1000", None)
    for batch in range(3):
        ids = tuple(f"mail-{n}" for n in range(batch * 100, min(251, (batch + 1) * 100)))
        state = journal.commit_page(state, GmailMessagePage(ids, f"page-{batch + 1}" if batch < 2 else None))
        journal.close()
        journal = GmailSyncJournal(path, "registered-scope")
        assert journal.checkpoint() == state
    assert len(journal.pending_ids()) == 251
    assert state.phase == "history" and state.history_id == "1000"
    assert not journal.discovery_drained()
    # A message arriving during bootstrap overlaps with full results; both paths deduplicate.
    state = journal.commit_page(state, GmailHistoryPage(("mail-250", "during-bootstrap"), None, "2000"))
    assert len(journal.pending_ids()) == 252 and state.phase == "ready"
    for identifier in journal.pending_ids():
        journal.acknowledge(identifier, "processed")
    assert journal.discovery_drained()
    journal.close()


def test_partial_history_never_advances_cursor_and_completed_ids_do_not_requeue(tmp_path):
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    state = journal.commit_page(state, GmailMessagePage(("old",), None))
    journal.acknowledge("old", "processed")
    state = journal.commit_page(state, GmailHistoryPage(("old", "new"), "next", "999"))
    assert state.history_id == "111" and journal.pending_ids() == ["new"]
    state = journal.commit_page(state, GmailHistoryPage(("new",), None, "1001"))
    assert state.history_id == "1001" and journal.pending_ids() == ["new"]
    journal.close()


def test_page_and_checkpoint_rollback_together_on_storage_failure(tmp_path):
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    journal.connection.execute("""CREATE TRIGGER fail_candidate BEFORE INSERT ON candidates
        WHEN NEW.id='fail' BEGIN SELECT RAISE(ABORT, 'injected disk write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        journal.commit_page(state, GmailMessagePage(("first", "fail"), "next"))
    assert journal.checkpoint() == state and journal.pending_ids() == []
    journal.connection.execute("DROP TRIGGER fail_candidate")
    state = journal.commit_page(state, GmailMessagePage(("first", "fail"), "next"))
    assert state.page_token == "next" and journal.pending_ids() == ["first", "fail"]
    journal.close()


def test_stale_replayed_response_cannot_overwrite_newer_progress(tmp_path):
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    old = journal.start_full("111", None)
    new = journal.commit_page(old, GmailMessagePage(("a",), "next"))
    with pytest.raises(ValueError, match="Stale"):
        journal.commit_page(old, GmailMessagePage(("b",), "other"))
    assert journal.checkpoint() == new and journal.pending_ids() == ["a"]
    journal.close()


def test_expired_history_resync_retains_unprocessed_and_processed_candidates(tmp_path):
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    state = journal.commit_page(state, GmailMessagePage(("done", "pending"), None))
    journal.acknowledge("done", "processed")
    state = journal.start_full("999", state)
    state = journal.commit_page(state, GmailMessagePage(("done", "pending", "new"), None))
    assert journal.pending_ids() == ["pending", "new"]
    assert state.history_id == "999" and state.phase == "history"
    journal.close()


def test_pagination_cycle_is_rejected_without_recording_partial_page(tmp_path):
    journal = GmailSyncJournal(tmp_path / "sync.db", "scope")
    state = journal.start_full("111", None)
    state = journal.commit_page(state, GmailMessagePage(("a",), "one"))
    state = journal.commit_page(state, GmailMessagePage(("b",), "two"))
    with pytest.raises(ValueError, match="Repeated"):
        journal.commit_page(state, GmailMessagePage(("lost",), "one"))
    assert journal.checkpoint() == state and journal.pending_ids() == ["a", "b"]
    journal.close()


def test_new_scope_cannot_reuse_an_existing_journal(tmp_path):
    path = tmp_path / "sync.db"
    GmailSyncJournal(path, "sender-one").close()
    with pytest.raises(ValueError, match="different"):
        GmailSyncJournal(path, "sender-two")


def test_acknowledgement_survives_crash_and_conflicting_outcomes_are_rejected(tmp_path):
    path = tmp_path / "sync.db"
    journal = GmailSyncJournal(path, "scope")
    state = journal.start_full("111", None)
    journal.commit_page(state, GmailMessagePage(("a",), None))
    journal.close()  # Workflow may have committed; unacknowledged ID must be replayed.
    journal = GmailSyncJournal(path, "scope")
    assert journal.pending_ids() == ["a"]
    with pytest.raises(ValueError, match="reason"):
        journal.acknowledge("a", "rejected")
    journal.acknowledge("a", "rejected", "UNSUPPORTED_ATTACHMENT")
    journal.close()
    journal = GmailSyncJournal(path, "scope")
    journal.acknowledge("a", "rejected", "UNSUPPORTED_ATTACHMENT")
    assert not journal.pending_ids()
    with pytest.raises(ValueError, match="another"):
        journal.acknowledge("a", "processed")
    journal.close()


@pytest.mark.parametrize("crash_window", ["before_commit", "after_commit"])
def test_actual_process_exit_preserves_atomic_page_checkpoint(tmp_path, crash_window):
    path = tmp_path / "sync.db"
    journal = GmailSyncJournal(path, "scope")
    initial = journal.start_full("111", None)
    journal.close()
    script = """
import os
import sys
from pathlib import Path
from visa_agent.channels.gmail import GmailMessagePage
from visa_agent.channels.gmail_sync import GmailSyncJournal
journal = GmailSyncJournal(Path(sys.argv[1]), 'scope')
state = journal.checkpoint()
if sys.argv[2] == 'before_commit':
    journal._save = lambda state: os._exit(75)
journal.commit_page(state, GmailMessagePage(('new',), None))
os._exit(75)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), crash_window],
                            capture_output=True, timeout=15)
    assert result.returncode == 75, result.stderr.decode()
    journal = GmailSyncJournal(path, "scope")
    if crash_window == "before_commit":
        assert journal.checkpoint() == initial and journal.pending_ids() == []
    else:
        assert journal.checkpoint().phase == "history" and journal.pending_ids() == ["new"]
    journal.close()
