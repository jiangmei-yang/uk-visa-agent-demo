from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from visa_agent.domain.models import Case, InboundEvent

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
    channel TEXT NOT NULL DEFAULT 'email',
    recipient TEXT,
    external_thread_id TEXT,
    send_deadline TEXT,
    reply_subject TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    sent_at TEXT,
    provider_message_id TEXT,
    in_reply_to TEXT,
    references_header TEXT,
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
CREATE TABLE IF NOT EXISTS channel_delivery_receipts (
    outbox_id TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(outbox_id, provider_message_id, delivery_status, error_code)
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
CREATE TABLE IF NOT EXISTS held_inbound_events (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS review_actions (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    held_event_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    retry_event_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS delivery_versions (
    case_id TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(case_id, sha256)
);
CREATE TABLE IF NOT EXISTS inbound_queue (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT,
    lease_until TEXT,
    last_error TEXT,
    processed_at TEXT,
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
            "in_reply_to": "TEXT",
            "references_header": "TEXT",
            "reply_subject": "TEXT",
            "channel": "TEXT NOT NULL DEFAULT 'email'",
            "recipient": "TEXT",
            "external_thread_id": "TEXT",
            "send_deadline": "TEXT",
            "reply_render_mode": "TEXT",
            "reply_render_error": "TEXT",
        }
        with self.connection:
            for column, declaration in additions.items():
                if column not in existing:
                    self.connection.execute(f"ALTER TABLE outbox ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        self.connection.close()

    def reset(self) -> None:
        self.connection.executescript(
            """DELETE FROM review_actions; DELETE FROM held_inbound_events; DELETE FROM inbound_queue; DELETE FROM deliveries; DELETE FROM outbox;
               DELETE FROM processed_events; DELETE FROM cases;"""
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

    def export_case_data(self, case_id: str) -> dict[str, Any] | None:
        case = self.get_case(case_id)
        if case is None:
            return None
        outbox = self.connection.execute(
            """SELECT id, event_id, message_type, payload, channel, recipient,
                      external_thread_id, status, attempt_count, last_error, sent_at,
                      provider_message_id, created_at, reply_render_mode, reply_render_error
               FROM outbox WHERE case_id = ? ORDER BY created_at, id""",
            (case_id,),
        ).fetchall()
        failures = self.connection.execute(
            """SELECT id, thread_id, reason_code, detail, retryable, created_at
               FROM inbound_failures WHERE case_id = ? ORDER BY created_at, id""",
            (case_id,),
        ).fetchall()
        deliveries = self.connection.execute(
            """SELECT id, path, sha256, created_at
               FROM deliveries WHERE case_id = ? ORDER BY created_at, id""",
            (case_id,),
        ).fetchall()
        return {
            "case": case.model_dump(mode="json"),
            "outbound_messages": [dict(row) for row in outbox],
            "inbound_failures": [dict(row) for row in failures],
            "held_inbound_events": self.list_held_inbound(case_id),
            "review_actions": [dict(row) for row in self.connection.execute(
                "SELECT * FROM review_actions WHERE case_id=? ORDER BY created_at, id", (case_id,))],
            "deliveries": [dict(row) for row in deliveries],
            "data_note": (
                "Raw processed inbound messages are not retained. The snapshot keeps only "
                "bounded evidence excerpts and source identifiers needed for audit. "
                "Exceptions: unprocessed applicant updates held for human review retain their "
                "event body and attachment references for pending review; case deletion removes them."
            ),
        }

    def delete_case(self, case_id: str) -> Case | None:
        case = self.get_case(case_id)
        if case is None:
            return None
        queued_ids: list[str] = []
        for row in self.connection.execute(
            "SELECT id, payload_json FROM inbound_queue WHERE payload_json != '{}'"
        ).fetchall():
            try:
                event = InboundEvent.model_validate_json(str(row["payload_json"]))
            except ValueError:
                continue
            if event.external_thread_id == case.external_thread_id:
                queued_ids.append(str(row["id"]))
        with self.connection:
            if queued_ids:
                placeholders = ",".join("?" for _ in queued_ids)
                self.connection.execute(
                    f"DELETE FROM inbound_queue WHERE id IN ({placeholders})", queued_ids
                )
            self.connection.execute("DELETE FROM inbound_failures WHERE case_id = ?", (case_id,))
            self.connection.execute("DELETE FROM held_inbound_events WHERE case_id = ?", (case_id,))
            self.connection.execute("DELETE FROM review_actions WHERE case_id = ?", (case_id,))
            self.connection.execute("DELETE FROM deliveries WHERE case_id = ?", (case_id,))
            self.connection.execute(
                "DELETE FROM channel_delivery_receipts WHERE outbox_id IN "
                "(SELECT id FROM outbox WHERE case_id = ?)", (case_id,),
            )
            self.connection.execute("DELETE FROM outbox WHERE case_id = ?", (case_id,))
            self.connection.execute("DELETE FROM processed_events WHERE case_id = ?", (case_id,))
            self.connection.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        return case

    def commit_event(
        self,
        case: Case,
        event: InboundEvent,
        message_type: str,
        payload: str,
    ) -> None:
        in_reply_to = event.rfc_message_id or f"<{event.id}>"
        references = " ".join(dict.fromkeys(f"{event.references or ''} {in_reply_to}".split()))
        reply_subject = (
            event.subject if event.subject.lower().startswith("re:") else f"Re: {event.subject}"
        )
        send_deadline = (
            (event.received_at + timedelta(hours=24)).isoformat()
            if event.channel == "whatsapp_twilio"
            else None
        )
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
                    case.external_thread_id,
                    case.model_dump_json(),
                    case.updated_at.isoformat(),
                ),
            )
            self.connection.execute(
                "INSERT INTO processed_events(event_id, case_id) VALUES (?, ?)",
                (event.id, case.id),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO outbox(
                       id, case_id, event_id, message_type, payload, channel, recipient,
                       external_thread_id, send_deadline, reply_subject, in_reply_to,
                       references_header
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"out-{event.id}-{message_type}",
                    case.id,
                    event.id,
                    message_type,
                    payload,
                    event.channel,
                    event.sender,
                    event.external_thread_id,
                    send_deadline,
                    reply_subject,
                    in_reply_to,
                    references,
                ),
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
                    case.external_thread_id,
                    case.model_dump_json(),
                    case.updated_at.isoformat(),
                ),
            )

    def save_delivery(self, case_id: str, path: str, sha256: str) -> None:
        with self.connection:
            previous = self.connection.execute(
                "SELECT path, sha256 FROM deliveries WHERE case_id = ?", (case_id,)
            ).fetchone()
            if previous and (previous["path"] != path or previous["sha256"] != sha256):
                unsafe = self.connection.execute(
                    "SELECT 1 FROM outbox WHERE case_id = ? AND message_type = 'ready' "
                    "AND (attempt_count > 0 OR status IN ('SENDING', 'SENT', 'AMBIGUOUS'))",
                    (case_id,),
                ).fetchone()
                if unsafe:
                    raise ValueError("Cannot replace a delivery after a send attempt")
                self.connection.execute(
                    "INSERT OR IGNORE INTO delivery_versions(case_id, path, sha256) VALUES (?, ?, ?)",
                    (case_id, previous["path"], previous["sha256"]),
                )
            self.connection.execute(
                "INSERT INTO deliveries(id, case_id, path, sha256) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET path = excluded.path, sha256 = excluded.sha256",
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
        held_event: InboundEvent | None = None,
    ) -> None:
        if held_event is not None and (
            held_event.id != event_id or held_event.external_thread_id != thread_id
            or reason_code not in {"OUT_OF_ORDER_EVENT", "HUMAN_REVIEW_CASE_NEW_EVENT", "FINALIZED_CASE_NEW_EVENT"}
        ):
            raise ValueError("Held event must match an eligible applicant review hold")
        with self.connection:
            if held_event is not None:
                self.connection.execute(
                    "INSERT OR IGNORE INTO held_inbound_events(id, case_id, reason_code, payload_json) VALUES (?, ?, ?, ?)",
                    (event_id, case_id, reason_code, held_event.model_dump_json()),
                )
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

    def list_held_inbound(self, case_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, reason_code, payload_json, created_at FROM held_inbound_events "
            "WHERE case_id=? ORDER BY created_at, id", (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def has_unreviewed_held_updates(self, case_id: str) -> bool:
        row = self.connection.execute("""
            SELECT 1 FROM held_inbound_events held WHERE held.case_id=?
            AND NOT EXISTS (
                SELECT 1 FROM review_actions action
                JOIN processed_events processed ON processed.event_id=action.retry_event_id
                WHERE action.held_event_id=held.id AND action.case_id=held.case_id
            ) LIMIT 1
        """, (case_id,)).fetchone()
        return row is not None

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

    def enqueue_inbound(self, event: InboundEvent) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO inbound_queue(id, channel, payload_json)
                   VALUES (?, ?, ?)""",
                (event.id, event.channel, event.model_dump_json()),
            )
        return cursor.rowcount == 1

    def claim_inbound(
        self,
        now: datetime,
        *,
        channel: str,
        limit: int = 20,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.connection:
            rows = self.connection.execute(
                """WITH due AS (
                       SELECT id FROM inbound_queue
                       WHERE channel = ?
                         AND (
                           (status IN ('PENDING', 'RETRY')
                            AND (available_at IS NULL OR available_at <= ?))
                           OR (status = 'PROCESSING' AND lease_until <= ?)
                         )
                       ORDER BY created_at, id
                       LIMIT ?
                   )
                   UPDATE inbound_queue
                   SET status = 'PROCESSING', lease_until = ?,
                       attempt_count = attempt_count + 1
                   WHERE id IN (SELECT id FROM due)
                   RETURNING id, channel, payload_json, status, attempt_count, available_at,
                             lease_until, last_error, processed_at, created_at""",
                (
                    channel,
                    now.isoformat(),
                    now.isoformat(),
                    limit,
                    lease_until.isoformat(),
                ),
            ).fetchall()
        return sorted((dict(row) for row in rows), key=lambda row: (row["created_at"], row["id"]))

    def mark_inbound_processed(self, event_id: str, processed_at: datetime) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE inbound_queue
                   SET status = 'PROCESSED', payload_json = '{}', processed_at = ?, lease_until = NULL,
                       available_at = NULL, last_error = NULL
                   WHERE id = ? AND status = 'PROCESSING'""",
                (processed_at.isoformat(), event_id),
            )

    def mark_inbound_retry(
        self,
        event_id: str,
        error: str,
        available_at: datetime,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE inbound_queue
                   SET status = 'RETRY', available_at = ?, lease_until = NULL, last_error = ?
                   WHERE id = ? AND status = 'PROCESSING'""",
                (available_at.isoformat(), error, event_id),
            )

    def mark_inbound_failed(self, event_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE inbound_queue
                   SET status = 'FAILED', available_at = NULL, lease_until = NULL, last_error = ?
                   WHERE id = ? AND status = 'PROCESSING'""",
                (error, event_id),
            )

    def list_inbound_queue(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id, channel, payload_json, status, attempt_count, available_at,
                      lease_until, last_error, processed_at, created_at
               FROM inbound_queue ORDER BY created_at, id"""
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
            """SELECT id, case_id, event_id, message_type, payload, channel, recipient,
                      external_thread_id, send_deadline, reply_subject, status, attempt_count,
                      next_attempt_at, last_error, sent_at, provider_message_id, in_reply_to,
                      references_header, created_at, reply_render_mode, reply_render_error
               FROM outbox ORDER BY created_at, id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_pending_outbox(
        self,
        now: datetime,
        limit: int = 20,
        channel: str | None = None,
        allowed_message_types: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        with self.connection:
            channel_filter = " AND channel = ?" if channel is not None else ""
            type_filter = ""
            values: list[object] = [now.isoformat()]
            if channel is not None:
                values.append(channel)
            if allowed_message_types is not None:
                if not allowed_message_types:
                    return []
                type_filter = " AND message_type IN (" + ",".join("?" for _ in allowed_message_types) + ")"
                values.extend(allowed_message_types)
            parameters = (*values, limit)
            rows = self.connection.execute(
                f"""WITH due AS (
                       SELECT id FROM outbox
                       WHERE status IN ('PENDING', 'RETRY')
                         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                         {channel_filter}
                         {type_filter}
                       ORDER BY created_at, id
                       LIMIT ?
                   )
                   UPDATE outbox SET status = 'SENDING'
                   WHERE id IN (SELECT id FROM due)
                   RETURNING id, case_id, event_id, message_type, payload, channel, recipient,
                             external_thread_id, send_deadline, reply_subject, status, attempt_count,
                             next_attempt_at, last_error, sent_at, provider_message_id, in_reply_to,
                             references_header, created_at""",
                parameters,
            ).fetchall()
        return sorted((dict(row) for row in rows), key=lambda row: (row["created_at"], row["id"]))

    def record_delivery_receipt(
        self, outbox_id: str, provider_id: str, status: str, error_code: str, recipient: str,
    ) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT channel, recipient, status, provider_message_id FROM outbox WHERE id = ?",
                (outbox_id,),
            ).fetchone()
            if (row is None or row["channel"] != "whatsapp_twilio"
                    or row["recipient"] != recipient or row["status"] not in {"SENDING", "SENT", "AMBIGUOUS"}):
                raise ValueError("Receipt does not match an attempted WhatsApp send")
            if row["provider_message_id"] and row["provider_message_id"] != provider_id:
                raise ValueError("Receipt provider identifier mismatch")
            self.connection.execute(
                "INSERT OR IGNORE INTO channel_delivery_receipts "
                "(outbox_id, provider_message_id, delivery_status, error_code) VALUES (?, ?, ?, ?)",
                (outbox_id, provider_id, status, error_code),
            )

    def delivery_receipt_status(self, outbox_id: str) -> str:
        rows = self.connection.execute(
            "SELECT provider_message_id, delivery_status FROM channel_delivery_receipts WHERE outbox_id = ?",
            (outbox_id,),
        ).fetchall()
        if not rows:
            return "unconfirmed"
        statuses = {row["delivery_status"] for row in rows}
        outbound = self.connection.execute(
            "SELECT provider_message_id FROM outbox WHERE id = ?", (outbox_id,)
        ).fetchone()
        if outbound and outbound["provider_message_id"] and any(
            row["provider_message_id"] != outbound["provider_message_id"] for row in rows
        ):
            return "conflict"
        if len({row["provider_message_id"] for row in rows}) > 1:
            return "conflict"
        failed = statuses & {"failed", "undelivered", "canceled"}
        if failed and statuses & {"delivered", "read"}:
            return "conflict"
        if failed:
            return "failed"
        return next(s for s in ("read", "delivered", "sent", "sending", "queued") if s in statuses)

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

    def mark_outbox_uncertain(self, outbox_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE outbox SET attempt_count = attempt_count + 1,
                   next_attempt_at = NULL, last_error = ?
                   WHERE id = ? AND status = 'SENDING'""",
                (error, outbox_id),
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

    def list_sending_outbox(
        self, limit: int = 20, channel: str | None = None
    ) -> list[dict[str, Any]]:
        channel_filter = " AND channel = ?" if channel is not None else ""
        parameters: tuple[object, ...] = (channel, limit) if channel is not None else (limit,)
        rows = self.connection.execute(
            f"""SELECT id, case_id, event_id, message_type, payload, channel, recipient,
                      external_thread_id, send_deadline, reply_subject, status, attempt_count,
                      next_attempt_at, last_error, sent_at, provider_message_id, in_reply_to,
                      references_header, created_at
               FROM outbox WHERE status = 'SENDING' {channel_filter}
               ORDER BY created_at, id LIMIT ?""",
            parameters,
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
