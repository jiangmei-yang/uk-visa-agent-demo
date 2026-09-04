from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from visa_agent.channels.outbound import (
    OutboxDispatcher,
    PermanentChannelError,
    ReplyRequest,
    TransientChannelError,
    UncertainDeliveryError,
)
from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppSender
from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


class FakeSender:
    def __init__(
        self,
        outcomes: list[str | Exception],
        find_outcomes: list[str | None | Exception] | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.find_outcomes = find_outcomes or []
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def find_sent_message(self, rfc_message_id: str) -> str | None:
        assert rfc_message_id.startswith("<out-")
        outcome = self.find_outcomes.pop(0)
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


@pytest.mark.parametrize("channel", ["gmail", "whatsapp_twilio"])
def test_new_applicant_update_blocks_unsent_final_delivery(tmp_path, channel):
    store = _demo_store(tmp_path)
    case = store.list_cases()[0]
    now = datetime(2026, 9, 4, 10, tzinfo=UTC)
    with store.connection:
        store.connection.execute("UPDATE outbox SET channel=?, send_deadline=? WHERE message_type='ready'",
                                 (channel, (now + timedelta(hours=24)).isoformat()))
    workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    event = InboundEvent(id="late-correction", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="日期有变", body="离开日期有变，请先不要发旧材料包。",
        received_at=now)
    assert workflow.process(event)[2] == "finalized_case_held"
    sender = FakeSender(["must-not-be-sent"])
    result = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(now)
    assert result[0].status == "FAILED"
    assert sender.requests == []
    assert "review" in next(row for row in store.list_outbox() if row["message_type"] == "ready")["last_error"]
    store.close()


def test_late_hold_does_not_resend_or_erase_a_provider_accepted_delivery(tmp_path):
    store = _demo_store(tmp_path)
    case = store.list_cases()[0]
    now = datetime(2026, 9, 4, 10, tzinfo=UTC)
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='SENDING' WHERE message_type='ready'")
    event = InboundEvent(id="after-send-update", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="Change", body="My dates changed", received_at=now)
    workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    workflow.process(event)
    sender = FakeSender([], find_outcomes=["already-accepted"])
    dispatcher = OutboxDispatcher(store, sender, allowed_message_types=("ready",))
    assert dispatcher.reconcile_sending(sender, now)[0].status == "SENT"
    assert dispatcher.dispatch_due(now) == [] and sender.requests == []
    assert len(store.list_held_inbound(case.id)) == 1
    store.close()


def test_unrelated_sender_cannot_block_a_confirmed_pack(tmp_path):
    store = _demo_store(tmp_path)
    case = store.list_cases()[0]
    now = datetime(2026, 9, 4, 10, tzinfo=UTC)
    event = InboundEvent(id="outsider", external_thread_id=case.external_thread_id,
        sender="outsider@example.test", subject="Stop", body="Don't send this", received_at=now)
    workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), OfflineFixtureLLM())
    assert workflow.process(event)[2] == "sender_mismatch_rejected"
    sender = FakeSender(["confirmed-delivery"])
    outcome = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(now)
    assert outcome[0].status == "SENT" and len(sender.requests) == 1
    store.close()


@pytest.mark.parametrize("change", ["modified_bytes", "missing_registry", "different_path"])
@pytest.mark.parametrize("channel", ["gmail", "whatsapp_twilio"])
def test_unverified_final_archive_is_never_sent(tmp_path, change, channel):
    store = _demo_store(tmp_path)
    case = store.list_cases()[0]
    with store.connection:
        store.connection.execute("UPDATE outbox SET channel=? WHERE message_type='ready'", (channel,))
    if change == "modified_bytes":
        Path(case.delivery_path).write_bytes(b"not the confirmed archive")
    elif change == "missing_registry":
        with store.connection:
            store.connection.execute("DELETE FROM deliveries WHERE case_id=?", (case.id,))
    else:
        with store.connection:
            store.connection.execute("UPDATE deliveries SET path='another-pack.zip' WHERE case_id=?", (case.id,))
    sender = FakeSender(["must-not-send"])
    outcome = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(datetime.now(UTC))
    assert outcome[0].status == "FAILED" and sender.requests == []
    store.close()


def test_verified_whatsapp_ready_notice_does_not_attach_archive(tmp_path):
    store = _demo_store(tmp_path)
    with store.connection:
        store.connection.execute("UPDATE outbox SET channel='whatsapp_twilio' WHERE message_type='ready'")
    sender = FakeSender(["accepted-notice"])
    try:
        outcomes = OutboxDispatcher(store, sender, allowed_message_types=("ready",)).dispatch_due(datetime.now(UTC))
        assert outcomes[0].status == "SENT"
        assert len(sender.requests) == 1 and sender.requests[0].attachment is None
    finally:
        store.close()


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


def test_accepted_send_with_lost_response_is_reconciled_without_resend(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    sender = FakeSender(
        [UncertainDeliveryError("Response lost after acceptance")], ["accepted-provider-id"]
    )
    dispatcher = OutboxDispatcher(store, sender)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        outcome = dispatcher.dispatch_due(now, limit=1)[0]
        assert outcome.status == "SENDING"
        row = next(r for r in store.list_outbox() if r["id"] == outcome.outbox_id)
        assert row["attempt_count"] == 1
        assert row["next_attempt_at"] is None
        for other in store.claim_pending_outbox(now, limit=20):
            store.mark_outbox_sent(str(other["id"]), "already-sent", now)
        assert dispatcher.dispatch_due(now + timedelta(days=1)) == []
        assert dispatcher.reconcile_sending(sender, now)[0].status == "SENT"
        assert len(sender.requests) == 1
        assert dispatcher.dispatch_due(now + timedelta(days=2)) == []
    finally:
        store.close()


def test_twilio_timeout_reaches_manual_investigation_without_second_send(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    calls = []

    def create(**kwargs: str) -> None:
        calls.append(kwargs)
        raise TimeoutError("response lost")

    sender = TwilioWhatsAppSender(
        SimpleNamespace(messages=SimpleNamespace(create=create)), "whatsapp:+14155238886"
    )
    dispatcher = OutboxDispatcher(store, sender)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        outcome = dispatcher.dispatch_due(now, limit=1)[0]
        assert outcome.status == "SENDING"
        for other in store.claim_pending_outbox(now, limit=20):
            store.mark_outbox_sent(str(other["id"]), "already-sent", now)
        assert dispatcher.dispatch_due(now + timedelta(days=1)) == []
        assert dispatcher.reconcile_sending(sender, now)[0].status == "AMBIGUOUS"
        row = next(r for r in store.list_outbox() if r["id"] == outcome.outbox_id)
        assert "unavailable" in str(row["last_error"])
        assert dispatcher.dispatch_due(now + timedelta(days=2)) == []
        assert len(calls) == 1
    finally:
        store.close()


def test_ambiguous_send_is_reconciled_or_requires_manual_retry(tmp_path: Path) -> None:
    found_store = _demo_store(tmp_path / "found")
    missing_store = _demo_store(tmp_path / "missing")
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        found_row = found_store.claim_pending_outbox(now, limit=1)[0]
        found_sender = FakeSender([], find_outcomes=["provider-found"])
        found = OutboxDispatcher(found_store, found_sender).reconcile_sending(found_sender, now)
        assert found[0].status == "SENT"
        assert found_store.list_outbox()[0]["provider_message_id"] == "provider-found"

        missing_row = missing_store.claim_pending_outbox(now, limit=1)[0]
        missing_sender = FakeSender(
            ["other-provider", "provider-after-approval"], find_outcomes=[None]
        )
        dispatcher = OutboxDispatcher(missing_store, missing_sender)
        missing = dispatcher.reconcile_sending(missing_sender, now)
        assert missing[0].status == "AMBIGUOUS"
        ambiguous = next(
            row for row in missing_store.list_outbox() if row["id"] == missing_row["id"]
        )
        assert ambiguous["status"] == "AMBIGUOUS"
        assert dispatcher.dispatch_due(now, limit=1)[0].outbox_id != missing_row["id"]

        missing_store.retry_ambiguous_outbox(str(missing_row["id"]), now)
        approved = dispatcher.dispatch_due(now, limit=1)
        assert approved[0].outbox_id == missing_row["id"]
        assert approved[0].status == "SENT"
        assert found_row["id"] != missing_row["id"] or found_store.path != missing_store.path
    finally:
        found_store.close()
        missing_store.close()


def test_transient_reconciliation_failure_leaves_message_sending(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        claimed = store.claim_pending_outbox(now, limit=1)[0]
        sender = FakeSender([], find_outcomes=[TransientChannelError("search unavailable")])
        outcomes = OutboxDispatcher(store, sender).reconcile_sending(sender, now)
        assert outcomes[0].status == "SENDING"
        row = next(item for item in store.list_outbox() if item["id"] == claimed["id"])
        assert row["status"] == "SENDING"
    finally:
        store.close()


def test_channel_workers_claim_only_their_own_outbox_rows(tmp_path: Path) -> None:
    store = _demo_store(tmp_path)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)
    try:
        rows = store.list_outbox()
        with store.connection:
            store.connection.execute(
                "UPDATE outbox SET channel = 'gmail' WHERE id = ?", (rows[0]["id"],)
            )
            store.connection.execute(
                "UPDATE outbox SET channel = 'whatsapp_twilio' WHERE id = ?", (rows[1]["id"],)
            )

        gmail = store.claim_pending_outbox(now, channel="gmail")
        whatsapp = store.claim_pending_outbox(now, channel="whatsapp_twilio")

        assert [row["id"] for row in gmail] == [rows[0]["id"]]
        assert [row["id"] for row in whatsapp] == [rows[1]["id"]]
        assert all(row["channel"] == "gmail" for row in gmail)
        assert all(row["channel"] == "whatsapp_twilio" for row in whatsapp)
    finally:
        store.close()


def test_two_concurrent_processes_cannot_claim_the_same_outbox_row(tmp_path: Path) -> None:
    initial = _demo_store(tmp_path)
    database = initial.path
    expected = {str(row["id"]) for row in initial.list_outbox()}
    initial.close()
    barrier = Barrier(2)
    now = datetime(2026, 9, 2, 9, tzinfo=UTC)

    def claim() -> set[str]:
        store = SQLiteStore(database)
        try:
            barrier.wait()
            return {str(row["id"]) for row in store.claim_pending_outbox(now, limit=3)}
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(claim)
        second_future = executor.submit(claim)
        first = first_future.result()
        second = second_future.result()

    assert first.isdisjoint(second)
    assert first | second == expected
