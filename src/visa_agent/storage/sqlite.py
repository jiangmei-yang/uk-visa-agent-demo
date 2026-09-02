from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from visa_agent.domain.models import Case

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    sent_at TEXT,
    provider_message_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, message_type)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inbound_failures (
    id TEXT PRIMARY KEY,
    case_id TEXT,
    thread_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    detail TEXT NOT NULL,
    retryable INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_outbox()

    def _migrate_outbox(self) -> None:
        existing = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(outbox)").fetchall()
        }
        additions = {
            "status": "TEXT NOT NULL DEFAULT 'PENDING'",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "TEXT",
            "last_error": "TEXT",
            "sent_at": "TEXT",
            "provider_message_id": "TEXT",
        }
        with self.connection:
            for column, declaration in additions.items():
                if column not in existing:
                    self.connection.execute(f"ALTER TABLE outbox ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        self.connection.close()

    def reset(self) -> None:
        self.connection.executescript(
            "DELETE FROM deliveries; DELETE FROM outbox; DELETE FROM processed_events; DELETE FROM cases;"
        )
        self.connection.commit()

    def event_processed(self, event_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    def get_case_by_thread(self, thread_id: str) -> Case | None:
        row = self.connection.execute(
            "SELECT snapshot_json FROM cases WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return None if row is None else Case.model_validate_json(row["snapshot_json"])

    def get_case(self, case_id: str) -> Case | None:
        row = self.connection.execute(
            "SELECT snapshot_json FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        return None if row is None else Case.model_validate_json(row["snapshot_json"])

    def list_cases(self) -> list[Case]:
        rows = self.connection.execute(
            "SELECT snapshot_json FROM cases ORDER BY updated_at DESC"
        ).fetchall()
        return [Case.model_validate_json(row["snapshot_json"]) for row in rows]

    def commit_event(self, case: Case, event_id: str, message_type: str, payload: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO cases(id, thread_id, snapshot_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                (
                    case.id,
                    case.email_thread_id,
                    case.model_dump_json(),
                    case.updated_at.isoformat(),
                ),
            )
            self.connection.execute(
                "INSERT INTO processed_events(event_id, case_id) VALUES (?, ?)",
                (event_id, case.id),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO outbox(id, case_id, event_id, message_type, payload) VALUES (?, ?, ?, ?, ?)",
                (f"out-{event_id}-{message_type}", case.id, event_id, message_type, payload),
            )

    def save_case(self, case: Case) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO cases(id, thread_id, snapshot_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                (
                    case.id,
                    case.email_thread_id,
                    case.model_dump_json(),
                    case.updated_at.isoformat(),
                ),
            )

    def save_delivery(self, case_id: str, path: str, sha256: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO deliveries(id, case_id, path, sha256) VALUES (?, ?, ?, ?)",
                (f"delivery-{case_id}", case_id, path, sha256),
            )

    def record_rejected_event(
        self,
        *,
        event_id: str,
        case_id: str,
        thread_id: str,
        reason_code: str,
        detail: str,
        retryable: bool = False,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO inbound_failures(
                       id, case_id, thread_id, reason_code, detail, retryable
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, case_id, thread_id, reason_code, detail, int(retryable)),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO processed_events(event_id, case_id) VALUES (?, ?)",
                (event_id, case_id),
            )

    def record_inbound_failure(
        self,
        *,
        event_id: str,
        thread_id: str,
        reason_code: str,
        detail: str,
        retryable: bool,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO inbound_failures(
                       id, case_id, thread_id, reason_code, detail, retryable
                   ) VALUES (?, NULL, ?, ?, ?, ?)""",
                (event_id, thread_id, reason_code, detail, int(retryable)),
            )

    def list_inbound_failures(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id, case_id, thread_id, reason_code, detail, retryable, created_at
               FROM inbound_failures ORDER BY created_at, id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        names = ("cases", "processed_events", "outbox", "deliveries")
        return {
            name: int(self.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in names
        }

    def list_outbox(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id, case_id, event_id, message_type, payload, status, attempt_count,
                      next_attempt_at, last_error, sent_at, provider_message_id, created_at
               FROM outbox ORDER BY created_at, id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_pending_outbox(self, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection:
            rows = self.connection.execute(
                """SELECT id, case_id, event_id, message_type, payload, status, attempt_count,
                          next_attempt_at, last_error, sent_at, provider_message_id, created_at
                   FROM outbox
                   WHERE status IN ('PENDING', 'RETRY')
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                   ORDER BY created_at, id
                   LIMIT ?""",
                (now.isoformat(), limit),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.connection.execute(
                    f"UPDATE outbox SET status = 'SENDING' WHERE id IN ({placeholders})",
                    ids,
                )
        return [dict(row) for row in rows]

    def mark_outbox_sent(
        self,
        outbox_id: str,
        provider_message_id: str,
        sent_at: datetime,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE outbox
                   SET status = 'SENT', provider_message_id = ?, sent_at = ?,
                       next_attempt_at = NULL, last_error = NULL
                   WHERE id = ? AND status = 'SENDING'""",
                (provider_message_id, sent_at.isoformat(), outbox_id),
            )

    def mark_outbox_retry(
        self,
        outbox_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE outbox
                   SET status = 'RETRY', attempt_count = attempt_count + 1,
                       next_attempt_at = ?, last_error = ?
                   WHERE id = ? AND status = 'SENDING'""",
                (next_attempt_at.isoformat(), error, outbox_id),
            )

    def mark_outbox_failed(self, outbox_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE outbox
                   SET status = 'FAILED', attempt_count = attempt_count + 1,
                       next_attempt_at = NULL, last_error = ?
                   WHERE id = ? AND status = 'SENDING'""",
                (error, outbox_id),
            )

    def list_sending_outbox(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id, case_id, event_id, message_type, payload, status, attempt_count,
                      next_attempt_at, last_error, sent_at, provider_message_id, created_at
               FROM outbox WHERE status = 'SENDING' ORDER BY created_at, id LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_outbox_ambiguous(self, outbox_id: str, detail: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE outbox SET status = 'AMBIGUOUS', last_error = ?
                   WHERE id = ? AND status = 'SENDING'""",
                (detail, outbox_id),
            )

    def retry_ambiguous_outbox(self, outbox_id: str, next_attempt_at: datetime) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE outbox
                   SET status = 'RETRY', next_attempt_at = ?, last_error = NULL
                   WHERE id = ? AND status = 'AMBIGUOUS'""",
                (next_attempt_at.isoformat(), outbox_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Only an AMBIGUOUS outbox row can be manually retried")
