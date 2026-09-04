from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore


class CaptureAdapter(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "accepted"}


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("plan", ["awaiting_profile_confirmation", "awaiting_confirmation"])
def test_automatic_confirmation_preserves_what_customer_is_confirming(
    tmp_path: Path, language: str, plan: str,
) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="user@example.test",
                policy_version="v", customer_language=language)
    case.profile.full_name = "Example Applicant"
    event = InboundEvent(id="e", external_thread_id="t", sender=case.applicant_contact,
                         subject="My application", body="Here are my details", channel="gmail",
                         received_at=now)
    store.commit_event(case, event, plan, "draft")
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "user@example.test"),
                                  channel="gmail", allowed_message_types=(plan,))
    try:
        assert dispatcher.dispatch_due(now)[0].status == "SENT"
        body = adapter.calls[0]["body"]
        assert "Example Applicant" in body
        assert store.list_outbox()[0]["payload"] == body
        document_heading = "这次整理使用的材料" if language == "zh" else "CURRENT DOCUMENTS"
        next_step = "继续准备所需材料" if language == "zh" else "continue with the supporting documents"
        if plan == "awaiting_profile_confirmation":
            assert next_step in body
            assert document_heading not in body
        else:
            assert document_heading in body
            assert next_step not in body
        assert dispatcher.dispatch_due(now) == []
    finally:
        store.close()


def test_backlog_withholds_old_drafts_without_delaying_latest_or_touching_uncertain(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="user@example.test", policy_version="v")
    for n in range(152):
        event = InboundEvent(id=f"e{n}", external_thread_id="t", sender=case.applicant_contact,
            subject="UK visit", body="Hello", channel="gmail", received_at=now)
        store.commit_event(case, event, "blocked", "draft")
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='SENDING' WHERE event_id='e0'")
    adapter = CaptureAdapter()
    sender = AutomaticGmailReplySender(adapter, store, "user@example.test")
    assert sender.withhold_obsolete_unsent() == 150
    rows = store.list_outbox()
    assert next(row for row in rows if row["event_id"] == "e0")["status"] == "SENDING"
    assert all(row["attempt_count"] == 0 for row in rows)
    dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",))
    assert dispatcher.dispatch_due(now, limit=1)[0].status == "SENT"
    assert len(adapter.calls) == 1 and sender.withhold_obsolete_unsent() == 0
    store.close()


def test_automatic_reply_replaces_model_wording_and_leaves_final_pack_pending(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="case-auto", external_thread_id="thread", applicant_contact="User Name <user@example.test>",
                policy_version="test", customer_language="zh")
    event = InboundEvent(id="first", external_thread_id="thread", sender=case.applicant_contact,
                         subject="普通咨询", body="需要什么资料", channel="gmail", received_at=now)
    store.commit_event(case, event, "blocked", "untrusted draft claims approval")
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "user@example.test"),
                                  channel="gmail", allowed_message_types=("blocked",))
    try:
        assert dispatcher.dispatch_due(now)[0].status == "SENT"
        assert "approval" not in adapter.calls[0]["body"]
        assert store.list_outbox()[0]["payload"] == adapter.calls[0]["body"]
        assert dispatcher.dispatch_due(now) == []
        final = event.model_copy(update={"id": "final"})
        store.commit_event(case, final, "ready", "Pack awaiting review")
        assert dispatcher.dispatch_due(now) == []
        assert next(r for r in store.list_outbox() if r["message_type"] == "ready")["status"] == "PENDING"
    finally:
        store.close()


def test_other_recipient_is_never_automatically_contacted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="other@example.test", policy_version="v")
    event = InboundEvent(id="e", external_thread_id="t", sender=case.applicant_contact,
                         subject="hello", body="help", channel="gmail", received_at=now)
    store.commit_event(case, event, "blocked", "draft")
    adapter = CaptureAdapter()
    try:
        dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "allowed@example.test"))
        assert dispatcher.dispatch_due(now)[0].status == "FAILED"
        assert adapter.calls == []
    finally:
        store.close()
