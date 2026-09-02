from __future__ import annotations

import sqlite3
from pathlib import Path

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
"""


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

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
                (case.id, case.email_thread_id, case.model_dump_json(), case.updated_at.isoformat()),
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
                (case.id, case.email_thread_id, case.model_dump_json(), case.updated_at.isoformat()),
            )

    def save_delivery(self, case_id: str, path: str, sha256: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO deliveries(id, case_id, path, sha256) VALUES (?, ?, ?, ?)",
                (f"delivery-{case_id}", case_id, path, sha256),
            )

    def counts(self) -> dict[str, int]:
        names = ("cases", "processed_events", "outbox", "deliveries")
        return {
            name: int(self.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in names
        }

    def list_outbox(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT id, case_id, event_id, message_type, payload, created_at FROM outbox ORDER BY created_at, id"
        ).fetchall()
        return [
            {str(key): str(value) for key, value in dict(row).items()}
            for row in rows
        ]
