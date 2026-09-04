"""Fixture-only final-confirmation/pack/journal crash recovery. No Gmail or model network."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.channels.gmail import GmailHistoryPage, GmailMessagePage, GmailRawMessage
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.delivery.pack import generate_pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.llm.ports import CasePatch, FactUpdate, PreparationIntent
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import queue_finalized_revision, review_fingerprint
from visa_agent.workflow.service import WorkflowService

PROJECT = Path(__file__).resolve().parents[2]
SENDER = "lin.chen@example.test"
MAILBOX = "visa-agent@example.test"
THREAD = "demo-thread-lin-chen-001"
FINAL_ID = "isolated-gmail-final-confirmation"
FOLLOWUP_ID = "isolated-later-applicant-update"
PAUSE = "Please pause my preparation for now."
CORRECTION = "My trip budget should be GBP 2,600 instead."
PRIVATE_ERROR = "synthetic-private-pack-failure-detail"
NOW = datetime(2026, 9, 2, tzinfo=UTC)
POLICY = PROJECT / "knowledge/uk_standard_visitor_2026-02-25.yaml"


def _seed_before_confirmation(directory: Path) -> str:
    documents = directory / "source-documents"
    generate_sample_documents(documents)
    store = SQLiteStore(directory / "sandbox.db")
    try:
        class Capture:
            def send(self, request):
                assert request.attachment is None
                return "captured-" + request.outbox_id

        workflow = WorkflowService(store, load_policy(POLICY), OfflineFixtureLLM(),
                                   today_provider=lambda: DEMO_EVALUATION_DATE)
        for filename in ("01_initial_submission.eml", "02_correction_and_translation.eml"):
            event = parse_eml(PROJECT / "samples/emails" / filename, documents)
            event.channel = "gmail"
            case, _, plan = workflow.process(event)
            sent = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,)).dispatch_due(NOW)
            assert len(sent) == 1 and sent[0].status == "SENT"
        assert case.confirmation_kind == "profile" and not case.profile_confirmed
        case, _, plan = workflow.process(event.model_copy(update={
            "id": "seed-profile-confirmation", "body": "I confirm the profile summary",
            "attachment_paths": [], "received_at": event.received_at + timedelta(minutes=1),
        }))
        sent = OutboxDispatcher(store, Capture(), allowed_message_types=(plan,)).dispatch_due(NOW)
        assert len(sent) == 1 and sent[0].status == "SENT"
        assert case.primary_channel == "gmail" and case.confirmation_kind == "final"
        assert not case.final_summary_confirmed and case.delivery_path is None
        assert store.counts()["deliveries"] == 0
        return case.id
    finally:
        store.close()


def _configure(directory: Path, fault: str | None = None, followup: str | None = None,
               committed_backlog: int = 0):
    spec = importlib.util.spec_from_file_location("isolated_pack_runner", PROJECT / "scripts/gmail_sandbox.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    observations = {"raw_reads": [], "workflows": [], "pack_calls": [], "dispatches": [], "sends": []}
    control = SimpleNamespace(fault=fault)
    raw = (PROJECT / "samples/emails/03_final_confirmation.eml").read_bytes()
    raws = {FINAL_ID: raw}
    for index in range(committed_backlog):
        raws[f"isolated-committed-confirmation-{index:03d}"] = raw
    if followup is not None:
        message = EmailMessage()
        message["From"], message["To"] = SENDER, MAILBOX
        message["Date"] = "Tue, 01 Sep 2026 06:00:00 +0000"
        message["Message-ID"] = f"<{FOLLOWUP_ID}@example.test>"
        message["Subject"] = "Re: Standard Visitor documents for London conference"
        message.set_content(PAUSE if followup == "pause" else CORRECTION)
        raws[FOLLOWUP_ID] = message.as_bytes()

    class FollowupFixtureLLM(OfflineFixtureLLM):
        # Explicitly scripted understanding; these tests assess persistence, not language quality.
        def extract_case_patch(self, event):
            if event.body.strip() == PAUSE:
                return CasePatch(updates=[], ambiguities=[], preparation_intent=PreparationIntent(
                    action="pause", source_excerpt=PAUSE, confidence=1.0))
            if event.body.strip() == CORRECTION:
                return CasePatch(updates=[FactUpdate(field="estimated_trip_cost_gbp", value=2600,
                    source_excerpt=CORRECTION, confidence=1.0)], ambiguities=[])
            return super().extract_case_patch(event)

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"emailAddress": MAILBOX})

    class Adapter:
        def __init__(self, service):
            pass

        def current_history_id(self):
            return "100"

        def list_message_page(self, query, token):
            assert token is None
            return GmailMessagePage(tuple(raws), None)

        def list_added_history_page(self, start, token):
            return GmailHistoryPage(tuple(raws), None, "101")

        def get_intake_metadata(self, identifier):
            assert identifier in raws
            offset = list(raws).index(identifier) * 1000
            return {"id": identifier, "internalDate": str(int(NOW.timestamp() * 1000) + offset),
                "labelIds": ["INBOX"], "payload": {"headers": [
                    {"name": "From", "value": SENDER}, {"name": "To", "value": MAILBOX}]}}

        def get_raw_message(self, identifier):
            observations["raw_reads"].append(identifier)
            return GmailRawMessage(identifier, THREAD, raws[identifier])

        def send_reply(self, **kwargs):
            observations["sends"].append(kwargs)
            assert kwargs.get("attachment") is None, "No automatic final-pack release"
            return {"id": "simulated-followup-reply"}

    class ObservedWorkflow(WorkflowService):
        def __init__(self, store, policy, llm, **kwargs):
            super().__init__(store, policy, llm, today_provider=lambda: DEMO_EVALUATION_DATE)

        def process(self, event):
            if control.fault == "workflow_failure":
                raise OSError(PRIVATE_ERROR)
            result = super().process(event)
            observations["workflows"].append((result[1], result[2]))
            if control.fault == "after_workflow_commit":
                os._exit(75)
            return result

    class ObservedDispatcher(OutboxDispatcher):
        def dispatch_due(self, *args, **kwargs):
            observations["dispatches"].append(True)
            return super().dispatch_due(*args, **kwargs)

    class ObservedJournal(GmailSyncJournal):
        def acknowledge(self, identifier, outcome, reason=None):
            super().acknowledge(identifier, outcome, reason)
            if control.fault == "after_journal_ack":
                os._exit(75)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return DEMO_EVALUATION_DATE

    def materialize(case, policy, store, output_root, today):
        observations["pack_calls"].append(case.id)
        if control.fault == "refused":
            return None, [PRIVATE_ERROR]
        if control.fault == "io_failure":
            raise OSError(PRIVATE_ERROR)
        result = generate_pack(case, policy, store, output_root, today)
        if control.fault == "after_pack_commit":
            assert result[0] is not None
            os._exit(75)
        return result

    runner.build_gmail_service = lambda *args, **kwargs: Service()
    runner.GmailAdapter = Adapter
    runner.DeepSeekStructuredLLM = lambda *args, **kwargs: FollowupFixtureLLM()
    runner.read_secret = lambda *args, **kwargs: "unused-synthetic-key"
    runner.WorkflowService = ObservedWorkflow
    runner.OutboxDispatcher = ObservedDispatcher
    runner.GmailSyncJournal = ObservedJournal
    runner.generate_pack = materialize
    runner.date = FixedDate
    args = argparse.Namespace(action="serve", sender=SENDER, mailbox=MAILBOX, subject=None,
        after=1, state_dir=directory, model="offline", watch=True)
    return runner, args, observations, control


def _run(directory: Path, fault: str | None = None, followup: str | None = None):
    runner, args, observations, _ = _configure(directory, fault, followup)
    runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    return observations


def _state(directory: Path):
    store = SQLiteStore(directory / "sandbox.db")
    try:
        case = store.list_cases()[0]
        rows = store.list_outbox()
        result = {"case": case, "counts": store.counts(), "outbox": rows,
                  "delivery": store.connection.execute("SELECT * FROM deliveries").fetchone()}
    finally:
        store.close()
    binding = json.loads((directory / "binding.json").read_text())
    journal = GmailSyncJournal(directory / "sync.db", json.dumps(binding, sort_keys=True))
    try:
        result["pending"] = journal.pending_ids()
        result["drained"] = journal.discovery_drained()
    finally:
        journal.close()
    return result


@pytest.mark.parametrize("fault", ["refused", "io_failure"])
def test_pack_failure_keeps_candidate_pending_and_recovery_does_not_repeat_workflow(tmp_path, fault):
    case_id = _seed_before_confirmation(tmp_path)
    runner, args, observations, _ = _configure(tmp_path, fault)
    with pytest.raises((RuntimeError, OSError)):
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    before = _state(tmp_path)
    assert before["case"].id == case_id and before["case"].final_summary_confirmed
    assert before["case"].delivery_path is None
    assert before["pending"] == [FINAL_ID] and not before["drained"]
    assert before["counts"] == {"cases": 1, "processed_events": 4, "outbox": 4, "deliveries": 0}
    assert observations["workflows"] == [(False, "ready")]
    assert observations["dispatches"] == observations["sends"] == []
    assert [row["status"] for row in before["outbox"] if row["message_type"] == "ready"] == ["PENDING"]

    recovered = _run(tmp_path)
    after = _state(tmp_path)
    assert recovered["workflows"] == [(True, "duplicate_ignored")]
    assert after["pending"] == [] and after["drained"]
    assert after["counts"] == {**before["counts"], "deliveries": 1}
    assert after["outbox"] == before["outbox"]
    assert after["case"].profile == before["case"].profile
    assert after["case"].evidence == before["case"].evidence
    archive = Path(after["case"].delivery_path)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == after["delivery"]["sha256"]
    assert _run(tmp_path)["raw_reads"] == []
    assert _state(tmp_path)["counts"] == after["counts"]


@pytest.mark.parametrize("window", ["after_workflow_commit", "after_pack_commit", "after_journal_ack"])
def test_real_child_exit_recovers_final_pack_and_acknowledgement_once(tmp_path, window):
    _seed_before_confirmation(tmp_path)
    script = """
import runpy
import sys
from pathlib import Path
namespace = runpy.run_path(sys.argv[1])
namespace["_run"](Path(sys.argv[2]), sys.argv[3])
"""
    child = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve()),
                            str(tmp_path), window], cwd=PROJECT, capture_output=True, timeout=20)
    assert child.returncode == 75, child.stderr.decode(errors="replace")
    before = _state(tmp_path)
    assert before["counts"]["processed_events"] == before["counts"]["outbox"] == 4
    assert before["counts"]["deliveries"] == (0 if window == "after_workflow_commit" else 1)
    old_bytes = Path(before["case"].delivery_path).read_bytes() if before["case"].delivery_path else None
    recovered = _run(tmp_path)
    after = _state(tmp_path)
    assert after["counts"] == {"cases": 1, "processed_events": 4, "outbox": 4, "deliveries": 1}
    assert after["outbox"] == before["outbox"] and after["drained"]
    assert after["pending"] == [] and recovered["sends"] == []
    assert recovered["workflows"] == ([] if window == "after_journal_ack" else [(True, "duplicate_ignored")])
    if old_bytes is not None:
        assert Path(after["case"].delivery_path).read_bytes() == old_bytes
    idle = _run(tmp_path)
    assert idle["raw_reads"] == idle["pack_calls"] == []
    assert _state(tmp_path)["counts"] == after["counts"]


@pytest.mark.parametrize("fault", ["refused", "io_failure"])
@pytest.mark.parametrize("followup", ["pause", "correction"])
def test_pack_failure_does_not_trap_later_customer_update_behind_old_ready_event(
    tmp_path, fault, followup,
):
    _seed_before_confirmation(tmp_path)
    runner, args, observations, _ = _configure(tmp_path, fault, followup)
    with pytest.raises(runner.PackPreparationError) as raised:
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    assert PRIVATE_ERROR not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    before = _state(tmp_path)
    assert observations["raw_reads"] == [FINAL_ID, FOLLOWUP_ID]
    assert observations["workflows"][0] == (False, "ready")
    assert observations["workflows"][1][0] is False
    assert observations["pack_calls"] == [before["case"].id]
    assert before["pending"] == [FINAL_ID] and before["counts"]["processed_events"] == 5
    assert not before["case"].final_summary_confirmed
    assert before["case"].delivery_path is None and before["counts"]["deliveries"] == 0
    assert observations["dispatches"] == observations["sends"] == []
    if followup == "pause":
        assert before["case"].preparation_paused
    else:
        assert before["case"].profile.estimated_trip_cost_gbp == 2600

    # Even if materialization remains unavailable, the old confirmation is superseded.
    recovered = _run(tmp_path, fault, followup)
    after = _state(tmp_path)
    assert recovered["workflows"] == [(True, "duplicate_ignored")]
    assert recovered["pack_calls"] == []
    assert after["case"] == before["case"] and after["counts"] == before["counts"]
    assert after["drained"] and after["pending"] == []
    assert len(recovered["sends"]) == 1  # Only the later safe receipt/summary, never an old ZIP.
    assert _run(tmp_path, fault, followup)["sends"] == []


def test_workflow_failure_is_not_swallowed_as_a_pack_failure(tmp_path):
    _seed_before_confirmation(tmp_path)
    runner, args, observations, _ = _configure(tmp_path, "workflow_failure", "pause")
    with pytest.raises(OSError, match=PRIVATE_ERROR):
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    state = _state(tmp_path)
    assert observations["raw_reads"] == [FINAL_ID]
    assert observations["pack_calls"] == observations["dispatches"] == observations["sends"] == []
    assert state["pending"] == [FINAL_ID, FOLLOWUP_ID]
    assert state["counts"]["processed_events"] == 3


def test_damaged_registered_archive_is_not_rebuilt_but_later_correction_is_retained(tmp_path):
    _seed_before_confirmation(tmp_path)
    with pytest.raises(RuntimeError):
        _run(tmp_path, "refused")
    before = _state(tmp_path)
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        archive, reasons = generate_pack(before["case"], load_policy(POLICY), store,
                                         tmp_path / "packs", DEMO_EVALUATION_DATE)
        assert archive is not None and reasons == []
    finally:
        store.close()
    original_bytes = archive.read_bytes()
    archive.write_bytes(b"synthetic corrupted archive; must not be regenerated")
    runner, args, observations, _ = _configure(tmp_path, followup="correction")
    with pytest.raises(runner.PackPreparationError):
        runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    after = _state(tmp_path)
    assert archive.read_bytes() == b"synthetic corrupted archive; must not be regenerated"
    assert after["pending"] == [FINAL_ID]
    assert after["counts"]["processed_events"] == 5 and after["counts"]["outbox"] == 4
    assert observations["raw_reads"] == [FOLLOWUP_ID, FINAL_ID]
    assert observations["dispatches"] == observations["sends"] == []
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.has_unreviewed_held_updates(after["case"].id)
        held = store.connection.execute("SELECT payload_json FROM held_inbound_events WHERE id=?",
                                        (FOLLOWUP_ID,)).fetchone()
        assert CORRECTION in held[0]
        with pytest.raises(ValueError, match="integrity check failed"):
            queue_finalized_revision(store, case_id=after["case"].id, held_event_id=FOLLOWUP_ID,
                expected_fingerprint=review_fingerprint(after["case"]), actor="Fixture operator",
                reason="Review the retained original customer correction")
        # An explicit trusted restore of this test's original bytes, not auto-regeneration.
        archive.write_bytes(original_bytes)
        retry_id = queue_finalized_revision(store, case_id=after["case"].id, held_event_id=FOLLOWUP_ID,
            expected_fingerprint=review_fingerprint(after["case"]), actor="Fixture operator",
            reason="Review retained correction after verified original archive restore")
    finally:
        store.close()
    recovered = _run(tmp_path, followup="correction")
    revised = _state(tmp_path)
    assert revised["case"].delivery_revision == 2
    assert revised["case"].profile.estimated_trip_cost_gbp == 2600
    assert not revised["case"].final_summary_confirmed and revised["case"].delivery_path is None
    assert revised["pending"] == [] and revised["drained"]
    assert recovered["pack_calls"] == [] and recovered["workflows"][-1] == (True, "duplicate_ignored")
    assert archive.read_bytes() == original_bytes
    old_ready = [row for row in revised["outbox"] if row["message_type"] == "ready"]
    assert len(old_ready) == 1 and old_ready[0]["status"] == "FAILED"
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        assert store.event_processed(retry_id)
        assert not store.has_unreviewed_held_updates(revised["case"].id)
    finally:
        store.close()


@pytest.mark.parametrize("followup", ["pause", "correction"])
def test_one_hundred_committed_pack_failures_do_not_starve_new_customer_update(tmp_path, followup):
    case_id = _seed_before_confirmation(tmp_path)
    with pytest.raises(RuntimeError):
        _run(tmp_path, "refused")
    store = SQLiteStore(tmp_path / "sandbox.db")
    try:
        # Synthetic committed-event backlog: scheduling evidence, not 100 model extractions.
        with store.connection:
            store.connection.executemany("INSERT INTO processed_events(event_id, case_id) VALUES (?, ?)",
                [(f"isolated-committed-confirmation-{index:03d}", case_id) for index in range(99)])
    finally:
        store.close()
    runner, args, observations, _ = _configure(tmp_path, "refused", followup, committed_backlog=99)
    runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    after = _state(tmp_path)
    assert observations["raw_reads"][0] == FOLLOWUP_ID
    assert len(observations["raw_reads"]) == 100
    assert observations["workflows"][0][0] is False
    assert all(duplicate for duplicate, _ in observations["workflows"][1:])
    assert observations["pack_calls"] == observations["sends"] == []
    assert len(after["pending"]) == 1 and not after["drained"]
    assert not after["case"].final_summary_confirmed and after["counts"]["deliveries"] == 0
    if followup == "pause":
        assert after["case"].preparation_paused
    else:
        assert after["case"].profile.estimated_trip_cost_gbp == 2600
    runner, args, recovered, _ = _configure(tmp_path, "refused", followup, committed_backlog=99)
    runner.run_once(args, argparse.ArgumentParser(), fixture_without_processing_consent=True)
    assert len(recovered["raw_reads"]) == 1 and len(recovered["sends"]) == 1
    assert recovered["pack_calls"] == []
    assert _state(tmp_path)["drained"]
