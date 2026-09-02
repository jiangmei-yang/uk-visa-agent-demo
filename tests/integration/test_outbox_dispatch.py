from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from visa_agent.channels.outbound import (
    OutboxDispatcher,
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
)
from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.storage.sqlite import SQLiteStore


class FakeSender:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _demo_store(tmp_path: Path) -> SQLiteStore:
    settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    run_demo(settings, reset=True)
    return SQLiteStore(settings.database_path)


def test_outbox_dispatches_each_reply_once_and_attaches_ready_pack(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    sender = FakeSender(["provider-1", "provider-2", "provider-3"])
    dispatcher = OutboxDispatcher(store, sender)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        outcomes = dispatcher.dispatch_due(now)
        assert [item.status for item in outcomes] == ["SENT", "SENT", "SENT"]
        assert [item.provider_message_id for item in outcomes] == [
            "provider-1",
            "provider-2",
            "provider-3",
        ]
        assert sender.requests[0].attachment is None
        assert sender.requests[1].attachment is None
        assert sender.requests[2].attachment is not None
        assert sender.requests[2].attachment[0].endswith(".zip")
        assert sender.requests[2].rfc_message_id.startswith("<out-")
        assert dispatcher.dispatch_due(now) == []
        assert len(sender.requests) == 3
        assert {row["status"] for row in store.list_outbox()} == {"SENT"}
    finally:
        store.close()


def test_transient_failure_retries_after_backoff_then_succeeds(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    sender = FakeSender([TransientChannelError("provider temporarily unavailable"), "provider-ok"])
    dispatcher = OutboxDispatcher(store, sender, max_attempts=3, base_backoff_seconds=60)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        first = dispatcher.dispatch_due(now, limit=1)
        assert first[0].status == "RETRY"
        assert first[0].next_attempt_at == now + timedelta(seconds=60)
        for row in store.claim_pending_outbox(now, limit=20):
            store.mark_outbox_sent(str(row["id"]), f"pre-sent-{row['id']}", now)
        assert dispatcher.dispatch_due(now + timedelta(seconds=59), limit=1) == []
        second = dispatcher.dispatch_due(now + timedelta(seconds=60), limit=1)
        assert second[0].status == "SENT"
        row = store.list_outbox()[0]
        assert row["attempt_count"] == 1
        assert row["provider_message_id"] == "provider-ok"
        assert "provider temporarily unavailable" not in str(row["last_error"])
    finally:
        store.close()


def test_permanent_and_exhausted_transient_failures_stop_automatic_delivery(
    tmp_path: Path,
) -> None:
    permanent_store = _demo_store(tmp_path / "permanent")
    transient_store = _demo_store(tmp_path / "transient")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        permanent = OutboxDispatcher(
            permanent_store,
            FakeSender([PermanentChannelError("recipient rejected")]),
        ).dispatch_due(now, limit=1)
        assert permanent[0].status == "FAILED"
        assert permanent_store.list_outbox()[0]["status"] == "FAILED"

        transient_sender = FakeSender(
            [TransientChannelError("timeout one"), TransientChannelError("timeout two")]
        )
        dispatcher = OutboxDispatcher(
            transient_store,
            transient_sender,
            max_attempts=2,
            base_backoff_seconds=1,
        )
        assert dispatcher.dispatch_due(now, limit=1)[0].status == "RETRY"
        assert dispatcher.dispatch_due(now + timedelta(seconds=1), limit=1)[0].status == "FAILED"
        failed = transient_store.list_outbox()[0]
        assert failed["status"] == "FAILED"
        assert failed["attempt_count"] == 2
        assert failed["next_attempt_at"] is None
    finally:
        permanent_store.close()
        transient_store.close()


def test_claimed_sending_message_is_not_claimed_by_another_worker(tmp_path: Path) -> None:
    first_store = _demo_store(tmp_path)
    second_store = SQLiteStore(tmp_path / "visa.db")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        claimed = first_store.claim_pending_outbox(now, limit=1)
        assert len(claimed) == 1
        assert second_store.claim_pending_outbox(now, limit=1)[0]["id"] != claimed[0]["id"]
        second_claim = second_store.claim_pending_outbox(now, limit=20)
        assert all(row["id"] != claimed[0]["id"] for row in second_claim)
        ambiguous = next(row for row in first_store.list_outbox() if row["id"] == claimed[0]["id"])
        assert ambiguous["status"] == "SENDING"
    finally:
        first_store.close()
        second_store.close()
