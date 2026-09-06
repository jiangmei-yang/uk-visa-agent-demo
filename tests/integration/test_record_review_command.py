"""Local operator command tests on copied fictional SQLite state; no network."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from test_operator_record_review import setup

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.record_review_command import record_review_command

POLICY = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


@pytest.fixture(autouse=True)
def fixed_review_clock(monkeypatch):
    class FictionalDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 7)
    monkeypatch.setattr("visa_agent.workflow.record_review_command.date", FictionalDate)


def prepared(tmp_path):
    store, case, kwargs = setup(tmp_path)
    state = tmp_path / "gmail-state"
    state.mkdir()
    destination = sqlite3.connect(state / "sandbox.db")
    store.connection.backup(destination)
    destination.close()
    store.close()
    return state, case, kwargs


def test_plan_is_unapproved_and_apply_changes_no_outbox_or_customer_consent(tmp_path):
    state, case, kwargs = prepared(tmp_path)
    plan = record_review_command(state_dir=state, policy_path=POLICY, case_id=case.id)
    assert plan["context"]["source_issues"] == []
    assert all(plan["context"]["intake_checks"].values())
    assert plan["context"]["missing_details"] == []
    assert plan["decision"]["actor"] == "" and not plan["decision"]["travel_history_scope_checked"]
    assert plan["decision"]["assessments"][0]["relationship_category"] is None
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        record_review_command(state_dir=state, policy_path=POLICY, decision_path=path)
    plan["decision"].update(actor=kwargs["actor"], rationale=kwargs["rationale"],
        travel_history_scope_checked=True, assessments=[item.model_dump(mode="json") for item in kwargs["assessments"]])
    path.write_text(json.dumps(plan), encoding="utf-8")
    store = SQLiteStore(state / "sandbox.db")
    before = store.list_outbox()
    result = record_review_command(state_dir=state, policy_path=POLICY, decision_path=path)
    assert result["status"] == "review_saved" and not result["mail_sent"] and not result["customer_confirmed"]
    assert store.list_outbox() == before
    assert store.get_case(case.id).application_record_review is not None
    assert not store.get_case(case.id).final_summary_confirmed
    store.close()


def test_plan_reports_missing_family_detail_without_treating_context_as_approval(tmp_path):
    from test_application_record_workflow import patch, record
    from test_consultant_value import Conversation

    dialogue = Conversation(tmp_path)
    statement = "My sister Example Doe lives at 1 Example Road, London, UK."
    turn = dialogue.turn(statement, patch(records=[record(statement, {
        "name": "Example Doe", "relationship": "sister", "address": "1 Example Road, London, UK",
    }, kind="uk_contact")]))
    state = tmp_path / "gmail-state"
    state.mkdir()
    with sqlite3.connect(dialogue.path) as source, sqlite3.connect(state / "sandbox.db") as destination:
        source.backup(destination)
    plan = record_review_command(state_dir=state, policy_path=POLICY, case_id=turn.case.id)
    assert plan["context"]["missing_details"] == [{
        "record_id": next(iter(turn.case.application_records.current())), "field": "passport_number",
    }]
    assert not plan["context"]["intake_checks"]["application_record_descriptive_fields_complete"]
    assert not plan["decision"]["assessments"][0]["applicable_details_checked"]
    # Editing informational output cannot confer authority or repair missing intake.
    plan["context"]["intake_checks"] = {"application_record_descriptive_fields_complete": True}
    plan["context"]["missing_details"] = []
    path = tmp_path / "unapproved.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        record_review_command(state_dir=state, policy_path=POLICY, decision_path=path)


def test_existing_worker_lock_cannot_be_bypassed_by_inspection(tmp_path):
    state, case, _ = prepared(tmp_path)
    with exclusive_state(state), pytest.raises(RuntimeError, match="Another worker"):
        record_review_command(state_dir=state, policy_path=POLICY, case_id=case.id)


def test_missing_state_is_not_silently_initialized(tmp_path):
    missing = tmp_path / "missing-state"
    with pytest.raises(FileNotFoundError):
        record_review_command(state_dir=missing, policy_path=POLICY, case_id="fictional")
    assert not missing.exists()


def test_empty_existing_database_is_not_initialized_by_plan(tmp_path):
    database = tmp_path / "sandbox.db"
    database.touch()
    with pytest.raises(ValueError, match="refusing to initialize"):
        record_review_command(state_dir=tmp_path, policy_path=POLICY, case_id="fictional")
    assert database.read_bytes() == b""
    assert not (tmp_path / "worker.lock").exists()


@pytest.mark.parametrize("failure", ["policy", "stale", "unknown_field"])
def test_changed_or_malformed_decision_cannot_approve(tmp_path, failure):
    state, case, kwargs = prepared(tmp_path)
    plan = record_review_command(state_dir=state, policy_path=POLICY, case_id=case.id)
    plan["decision"].update(actor=kwargs["actor"], rationale=kwargs["rationale"],
        travel_history_scope_checked=True, assessments=[item.model_dump(mode="json") for item in kwargs["assessments"]])
    if failure == "policy":
        plan["decision"]["policy_digest"] = "wrong-policy"
    elif failure == "stale":
        plan["decision"]["expected_fingerprint"] = "stale-case"
    else:
        plan["decision"]["send_pack"] = True
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        record_review_command(state_dir=state, policy_path=POLICY, decision_path=path)
    store = SQLiteStore(state / "sandbox.db")
    assert store.get_case(case.id).application_record_review is None
    store.close()
