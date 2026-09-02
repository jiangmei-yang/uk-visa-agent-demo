from __future__ import annotations

import sqlite3
from pathlib import Path

from visa_agent.storage.sqlite import SQLiteStore


def test_existing_outbox_schema_gains_threading_and_delivery_columns(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE outbox (
               id TEXT PRIMARY KEY,
               case_id TEXT NOT NULL,
               event_id TEXT NOT NULL,
               message_type TEXT NOT NULL,
               payload TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               UNIQUE(event_id, message_type)
           )"""
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(database)
    try:
        columns = {
            str(row["name"])
            for row in store.connection.execute("PRAGMA table_info(outbox)").fetchall()
        }
        assert {
            "status",
            "attempt_count",
            "next_attempt_at",
            "last_error",
            "sent_at",
            "provider_message_id",
            "channel",
            "recipient",
            "external_thread_id",
            "reply_subject",
            "in_reply_to",
            "references_header",
        } <= columns
    finally:
        store.close()
