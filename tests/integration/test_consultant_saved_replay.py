"""Replay exposed fictional provider proposals; never a new model observation."""

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

REPORTS = (
    Path("eval_output/consultant_value_provider_2026-09-04-v3.json"),
    Path("eval_output/application_information_deepseek_2026-09-05.json"),
)
ROWS = [row for report in REPORTS for row in json.loads(report.read_text())["results"]]
SPEC = importlib.util.spec_from_file_location("consultant_probe_replay", "scripts/consultant_value_probe.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_saved_proposals_through_current_guard_and_captured_sender(row, tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Saved proposal replay must not use any network")

    monkeypatch.setattr("socket.socket.connect", no_network)
    monkeypatch.setattr("socket.create_connection", no_network)

    class SavedModel:
        calls = 0

        def extract_case_patch(self, event):
            self.calls += 1
            assert event.body == row["input"]
            return CasePatch.model_validate(row["proposed_patch"])

        render_message = staticmethod(deterministic_fallback_message)

    model = SavedModel()
    store = SQLiteStore(tmp_path / "replay.db")
    try:
        workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
            GuardedLLM(model, max_attempts=1), today_provider=lambda: date(2026, 9, 4))
        event = InboundEvent(id=row["id"], channel="gmail", external_thread_id=row["id"],
            sender=probe.CONTACT, subject="Visitor application preparation", body=row["input"],
            received_at=datetime(2026, 9, 4, 15, tzinfo=UTC))
        case, _, _ = workflow.process(event)
        capture = probe.CapturedGmail()
        outcomes = OutboxDispatcher(store, AutomaticGmailReplySender(capture, store, probe.CONTACT),
            channel="gmail", allowed_message_types=("blocked",)).dispatch_due(event.received_at)
        actual = store.list_outbox()[-1]
        assert model.calls == 1 and not workflow.llm.last_extraction_fallback
        assert len(outcomes) == len(capture.bodies) == 1 and outcomes[0].status == "SENT"
        assert capture.bodies[0] == actual["payload"]
        expected = next(expected for kind, _, expected in [*probe.CASES, *probe.APPLICATION_CASES] if kind == row["id"])
        checks = probe.checks_for(row["id"], case, actual["payload"], expected)
        assert all(checks.values()), checks
        if row["id"] == "parents":
            # V3's printed checks missed the guard dropping this true statement.
            assert case.profile.sponsor_relationship == "parents"
        if row["id"] == "family":
            assert "费用按你说的由自己承担" in actual["payload"]
            assert "准备住在哪里" not in actual["payload"]
            assert "对方是否提供住宿或承担费用要单独确认" not in actual["payload"]
    finally:
        store.close()
