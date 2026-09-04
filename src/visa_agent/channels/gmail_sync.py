"""Durable discovery journal; no applicant processing, credentials or network operations.

Use inside the runner's exclusive state lock. A separate journal cannot atomically commit
workflow state: acknowledge only after workflow/pack success and rely on event deduplication
if a crash occurs before acknowledgement. Discovery alone never authorizes a send.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage


@dataclass(frozen=True)
class SyncCheckpoint:
    revision: int
    phase: Literal["full", "history", "ready"]
    history_id: str
    page_token: str | None = None
    seen_tokens: tuple[str, ...] = ()


class GmailSyncJournal:
    def __init__(self, path: Path, scope: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                                                scope TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoint (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                                                  snapshot TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending', reason TEXT);
        """)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO binding VALUES (1, ?)", (scope,))
        if self.connection.execute("SELECT scope FROM binding WHERE singleton=1").fetchone()[0] != scope:
            self.connection.close()
            raise ValueError("Sync journal belongs to a different mailbox/intake scope")

    def close(self) -> None:
        self.connection.close()

    def checkpoint(self) -> SyncCheckpoint | None:
        row = self.connection.execute("SELECT snapshot FROM checkpoint WHERE singleton=1").fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        data["seen_tokens"] = tuple(data["seen_tokens"])
        return SyncCheckpoint(**data)

    def _save(self, state: SyncCheckpoint) -> None:
        self.connection.execute("INSERT OR REPLACE INTO checkpoint VALUES (1, ?)",
                                (json.dumps(asdict(state)),))

    def start_full(self, history_id: str, expected: SyncCheckpoint | None) -> SyncCheckpoint:
        """Initial bootstrap or explicit expired-history resync, preserving every candidate."""
        self._validate_history(history_id)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expect(expected)
            state = SyncCheckpoint(0 if expected is None else expected.revision + 1,
                                   "full", history_id)
            self._save(state)
        return state

    def _expect(self, expected: SyncCheckpoint | None) -> None:
        if self.checkpoint() != expected:
            raise ValueError("Stale Gmail sync response; reload the checkpoint")

    @staticmethod
    def _validate_history(value: str) -> None:
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError("Invalid history cursor")

    def commit_page(
        self, expected: SyncCheckpoint, page: GmailMessagePage | GmailHistoryPage
    ) -> SyncCheckpoint:
        if isinstance(page, GmailMessagePage):
            if expected.phase != "full":
                raise ValueError("Full-sync response does not match checkpoint phase")
            ids, history_id = page.message_ids, expected.history_id
            phase: Literal["full", "history", "ready"] = "full" if page.next_page_token else "history"
        else:
            if expected.phase == "full":
                raise ValueError("History response does not match checkpoint phase")
            self._validate_history(page.history_id)
            if int(page.history_id) < int(expected.history_id):
                raise ValueError("History cursor moved backwards")
            ids = page.added_message_ids
            history_id = expected.history_id if page.next_page_token else page.history_id
            phase = "history" if page.next_page_token else "ready"
        token = page.next_page_token
        seen = expected.seen_tokens if expected.phase != "ready" else ()
        if token is not None and (not token or token in seen or token == expected.page_token):
            raise ValueError("Repeated or invalid Gmail pagination token")
        if any(not isinstance(identifier, str) or not identifier for identifier in ids):
            raise ValueError("Invalid Gmail candidate ID")
        new_seen = (*seen, token) if token else ()
        state = SyncCheckpoint(expected.revision + 1, phase, history_id, token, new_seen)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expect(expected)
            self.connection.executemany("INSERT OR IGNORE INTO candidates (id) VALUES (?)",
                                        [(identifier,) for identifier in ids])
            self._save(state)
        return state

    def pending_ids(self) -> list[str]:
        """Discovery order is NOT message chronology; runner must sort provider timestamps."""
        return [row[0] for row in self.connection.execute(
            "SELECT id FROM candidates WHERE status='pending' ORDER BY rowid")]

    def acknowledge(self, identifier: str, outcome: Literal["processed", "ignored", "rejected"],
                    reason: str | None = None) -> None:
        if outcome not in {"processed", "ignored", "rejected"}:
            raise ValueError("Invalid candidate outcome")
        if outcome != "processed" and not reason:
            raise ValueError("Ignored/rejected candidates require an auditable reason")
        with self.connection:
            row = self.connection.execute("SELECT status, reason FROM candidates WHERE id=?",
                                          (identifier,)).fetchone()
            if row is None:
                raise ValueError("Unknown Gmail candidate")
            if row[0] != "pending":
                if tuple(row) == (outcome, reason):
                    return
                raise ValueError("Candidate already acknowledged with another outcome")
            self.connection.execute("UPDATE candidates SET status=?, reason=? WHERE id=?",
                                    (outcome, reason, identifier))

    def discovery_drained(self) -> bool:
        state = self.checkpoint()
        return state is not None and state.phase == "ready" and not self.pending_ids()
