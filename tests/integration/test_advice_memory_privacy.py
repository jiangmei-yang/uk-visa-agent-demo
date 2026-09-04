"""Logical advice-memory export/deletion in an isolated offline state database.

The fixture transport captures real dispatcher sends; no model or mail network
is used. Deletion assertions concern active database records, not secure disk
erasure or recall of messages already delivered to a recipient.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 4)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
QUESTIONS = [
    ("application", "Where do I apply for my UK visitor visa?"),
    ("timing", "How early can I apply?"),
    ("fees", "What is the visitor visa application fee?"),
    ("translation", "How should I translate my Chinese supporting documents?"),
]
CONTINUE = "Please continue with the unanswered questions."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Advice-memory privacy tests must stay offline")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


class FixedModel:
    def __init__(self, questions: list[tuple[str, str]]) -> None:
        self.questions = questions

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        return CasePatch(updates=[], ambiguities=[], customer_questions=[
            CustomerQuestion(topic=topic, source_excerpt=text, confidence=1)
            for topic, text in self.questions
        ])

    render_message = staticmethod(deterministic_fallback_message)


class CapturedSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return f"offline-memory-receipt-{len(self.requests)}"


def turn(store: SQLiteStore, sender: CapturedSender, label: str, number: int,
         body: str, questions: list[tuple[str, str]]) -> tuple[Case, InboundEvent]:
    event = InboundEvent(id=f"{label}-provider-{number}", external_thread_id=f"{label}-thread",
        sender="fictional-memory@example.test", channel="gmail", subject="Fictional consultation",
        body=body, received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=number))
    workflow = WorkflowService(store, POLICY, FixedModel(questions), today_provider=lambda: TODAY)
    case, duplicate, plan = workflow.process(event)
    assert not duplicate and plan == "blocked"
    result = OutboxDispatcher(store, sender, channel="gmail").dispatch_due(event.received_at)
    assert len(result) == 1 and result[0].status == "SENT"
    row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
    assert row["provider_message_id"] and sender.requests[-1].body == row["payload"]
    assert sender.requests[-1].attachment is None
    return case, event


def pending_history(store: SQLiteStore, sender: CapturedSender, label: str) -> tuple[Case, InboundEvent]:
    body = " ".join(text for _, text in QUESTIONS) + f" Original question context marker: {label}-private-context."
    first, original = turn(store, sender, label, 1, body, QUESTIONS)
    assert first.pending_advice and first.pending_advice[0].offered_notice in sender.requests[-1].body
    second, _ = turn(store, sender, label, 2, CONTINUE, [])
    assert second.pending_advice and second.pending_advice[0].answer_attempts
    assert second.latest_customer_message == CONTINUE
    assert second.pending_advice[0].source_body == original.body
    return second, original


def test_old_snapshot_without_advice_memory_field_reopens_with_empty_default(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = {
        "id": "legacy-case", "external_thread_id": "legacy-thread",
        "applicant_contact": "legacy@example.test", "primary_channel": "gmail",
        "policy_version": POLICY.version, "profile": {"full_name": "Maya Chen"},
    }
    store = SQLiteStore(path)
    with store.connection:
        store.connection.execute("INSERT INTO cases(id,thread_id,snapshot_json,updated_at) VALUES (?,?,?,?)",
            (legacy["id"], legacy["external_thread_id"], json.dumps(legacy), datetime.now(UTC).isoformat()))
    store.close()
    store = SQLiteStore(path)
    try:
        case = store.get_case("legacy-case")
        assert case is not None and case.pending_advice == []
        assert case.profile.full_name == "Maya Chen"
        exported = store.export_case_data(case.id)
        assert exported is not None and exported["case"]["pending_advice"] == []
    finally:
        store.close()


def test_export_retains_original_pending_context_and_truthful_retention_note(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    sender = CapturedSender()
    case, original = pending_history(store, sender, "primary")
    store.close()
    store = SQLiteStore(path)
    try:
        exported = store.export_case_data(case.id)
        assert exported is not None
        assert exported["case"]["latest_customer_message"] == CONTINUE
        pending = exported["case"]["pending_advice"]
        assert pending[0]["source_event_id"] == original.id
        assert pending[0]["source_body"] == original.body
        assert pending[0]["source_questions"] and pending[0]["answer_attempts"]
        assert len(exported["outbound_messages"]) == 2
        assert all(row["status"] == "SENT" for row in exported["outbound_messages"])
        note = exported["data_note"].casefold()
        assert "retains the latest customer message and the original question context" in note
        assert "raw processed inbound messages are not retained" not in note
    finally:
        store.close()


def test_case_deletion_removes_its_advice_context_and_history_but_not_neighbor(tmp_path: Path) -> None:
    path = tmp_path / "neighbors.db"
    store = SQLiteStore(path)
    sender = CapturedSender()
    primary, original = pending_history(store, sender, "primary")
    neighbor, _ = pending_history(store, sender, "neighbor")
    neighbor_before = store.export_case_data(neighbor.id)
    removed = store.delete_case(primary.id)
    assert removed is not None and removed.pending_advice
    store.close()
    store = SQLiteStore(path)
    try:
        assert store.get_case(primary.id) is None
        assert store.get_case_by_thread(primary.external_thread_id) is None
        assert store.export_case_data(primary.id) is None
        assert not store.event_processed(original.id)
        assert not store.event_processed("primary-provider-2")
        for table in ("processed_events", "outbox", "held_inbound_events", "review_actions",
                      "processing_consent_events", "processing_deferred_events"):
            assert store.connection.execute(f"SELECT 1 FROM {table} WHERE case_id=?", (primary.id,)).fetchall() == []
        active_snapshots = " ".join(row[0] for row in store.connection.execute("SELECT snapshot_json FROM cases"))
        assert "primary-private-context" not in active_snapshots
        assert "neighbor-private-context" in active_snapshots
        assert store.export_case_data(neighbor.id) == neighbor_before
    finally:
        store.close()
