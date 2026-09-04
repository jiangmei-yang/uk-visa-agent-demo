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
    phase: Literal["full", "history", "ready", "rescan"]
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
            CREATE TABLE IF NOT EXISTS recovery_actions (revision INTEGER PRIMARY KEY,
                actor TEXT NOT NULL, reason TEXT NOT NULL, previous_checkpoint TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS candidate_metadata_errors (id TEXT PRIMARY KEY,
                code TEXT NOT NULL, observations INTEGER NOT NULL DEFAULT 1,
                first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT);
            CREATE TABLE IF NOT EXISTS candidate_receipts (id TEXT PRIMARY KEY,
                received_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS candidate_threads (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL);
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

    def request_rescan(self, expected: SyncCheckpoint, *, actor: str, reason: str) -> SyncCheckpoint:
        """Audited operator request, never an acknowledgement or permission to send."""
        if not actor.strip() or not reason.strip():
            raise ValueError("Rescan requires an actor and reason")
        if expected.phase == "rescan":
            raise ValueError("A rescan is already requested")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._expect(expected)
            state = SyncCheckpoint(expected.revision + 1, "rescan", expected.history_id)
            self.connection.execute(
                "INSERT INTO recovery_actions (revision, actor, reason, previous_checkpoint) VALUES (?, ?, ?, ?)",
                (state.revision, actor.strip(), reason.strip(), json.dumps(asdict(expected))),
            )
            self._save(state)
        return state

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
            if expected.phase not in {"history", "ready"}:
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

    def record_receipt(self, identifier: str, received_ms: int) -> None:
        """Provider receipt time, never the sender-controlled MIME Date header."""
        with self.connection:
            self.connection.execute("INSERT INTO candidate_receipts VALUES (?,?) "
                "ON CONFLICT(id) DO UPDATE SET received_ms=excluded.received_ms", (identifier, received_ms))

    def receipt_ms(self, identifier: str) -> int:
        row = self.connection.execute("SELECT received_ms FROM candidate_receipts WHERE id=?",
                                      (identifier,)).fetchone()
        if row is None:
            raise ValueError("Gmail candidate lacks its verified provider receipt")
        return int(row[0])

    def record_thread(self, identifier: str, thread_id: str) -> None:
        with self.connection:
            existing = self.connection.execute("SELECT thread_id FROM candidate_threads WHERE id=?",
                                               (identifier,)).fetchone()
            if existing is not None and existing[0] != thread_id:
                raise ValueError("Gmail candidate provider thread changed")
            self.connection.execute("INSERT OR IGNORE INTO candidate_threads VALUES (?,?)", (identifier, thread_id))

    def thread_id(self, identifier: str) -> str:
        row = self.connection.execute("SELECT thread_id FROM candidate_threads WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Gmail candidate lacks its verified provider thread")
        return str(row[0])

    def consent_scanned_ids(self) -> list[str]:
        return [row[0] for row in self.connection.execute(
            "SELECT c.id FROM candidates c JOIN candidate_receipts r ON r.id=c.id "
            "WHERE c.status='consent_scanned' ORDER BY r.received_ms,c.id")]

    def resume_awaiting_consent(self, identifiers: list[str]) -> None:
        """Revisit original provider IDs after a current grant, never synthesize mail."""
        with self.connection:
            self.connection.executemany("UPDATE candidates SET status='pending',reason=NULL "
                "WHERE id=? AND status='awaiting_consent'", [(value,) for value in identifiers])

    def consent_scan_drained(self) -> bool:
        state = self.checkpoint()
        return state is not None and state.phase == "ready" and not self.pending_ids()

    def record_metadata_unavailable(self, identifier: str) -> None:
        """Retain a redacted observation, not an acknowledgement or disposition."""
        with self.connection:
            if self.connection.execute("SELECT status FROM candidates WHERE id=?", (identifier,)).fetchone() != ("pending",):
                raise ValueError("Metadata observation requires a pending Gmail candidate")
            self.connection.execute("""INSERT INTO candidate_metadata_errors(id,code)
                VALUES (?,'METADATA_NOT_FOUND') ON CONFLICT(id) DO UPDATE SET
                observations=observations+1, last_seen=CURRENT_TIMESTAMP, resolved_at=NULL""", (identifier,))

    def metadata_available(self, identifier: str) -> None:
        """Successful metadata lookup resolves the observation, not the candidate."""
        with self.connection:
            self.connection.execute("""UPDATE candidate_metadata_errors SET resolved_at=CURRENT_TIMESTAMP
                WHERE id=? AND resolved_at IS NULL""", (identifier,))

    def unavailable_metadata(self) -> list[dict[str, str | int]]:
        return [dict(zip(("id", "code", "observations", "first_seen", "last_seen"), row, strict=True))
                for row in self.connection.execute("""SELECT id,code,observations,first_seen,last_seen
                    FROM candidate_metadata_errors WHERE resolved_at IS NULL ORDER BY first_seen,id""")]

    def acknowledge(self, identifier: str, outcome: Literal[
        "processed", "ignored", "rejected", "awaiting_consent", "consent_scanned", "consent_control"],
                    reason: str | None = None) -> None:
        if outcome not in {"processed", "ignored", "rejected", "awaiting_consent", "consent_scanned", "consent_control"}:
            raise ValueError("Invalid candidate outcome")
        if outcome not in {"processed", "consent_scanned"} and not reason:
            raise ValueError("Ignored/rejected candidates require an auditable reason")
        with self.connection:
            row = self.connection.execute("SELECT status, reason FROM candidates WHERE id=?",
                                          (identifier,)).fetchone()
            if row is None:
                raise ValueError("Unknown Gmail candidate")
            if row[0] not in {"pending", "consent_scanned"}:
                if tuple(row) == (outcome, reason):
                    return
                raise ValueError("Candidate already acknowledged with another outcome")
            self.connection.execute("UPDATE candidates SET status=?, reason=? WHERE id=?",
                                    (outcome, reason, identifier))

    def discovery_drained(self) -> bool:
        return self.consent_scan_drained() and not self.consent_scanned_ids()
