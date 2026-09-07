import json
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from test_sponsor_location_review import seed

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.sponsor_location_command import sponsor_location_command

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


@pytest.fixture(autouse=True)
def safe_clock_and_network(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 7)
    monkeypatch.setattr("visa_agent.workflow.sponsor_location_command.date", FixedDate)
    def deny(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr("socket.socket.connect", deny)


def setup(tmp_path):
    with closing(SQLiteStore(tmp_path / "sandbox.db")) as store:
        case = seed(store)
    plan = sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, case_id=case.id)
    return case, plan


def test_plan_cannot_approve_itself_and_explicit_review_never_sends(tmp_path):
    case, plan = setup(tmp_path)
    assert plan["context"]["current_values"] == {"residence": [False], "current_presence": [True]}
    assert len(plan["context"]["statements"]) == 2
    assert plan["decision"]["source_and_applicability_checked"] is False
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, decision_path=decision)
    plan["decision"].update(actor="Fictional reviewer", rationale="Checked both source statements and applicability.",
                            source_and_applicability_checked=True)
    # Informational context must not grant authority or override the real data.
    plan["context"]["proposed_uk_status_evidence_required"] = False
    decision.write_text(json.dumps(plan))
    result = sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, decision_path=decision)
    assert result["uk_status_evidence_required"] is True
    assert result["mail_sent"] is False and result["customer_confirmed"] is False
    with closing(SQLiteStore(tmp_path / "sandbox.db")) as store:
        saved = store.get_case(case.id)
        assert len(saved.sponsor_location_review_history) == 1
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert store.list_outbox() == [] and saved.delivery_path is None
    with pytest.raises(ValueError):
        sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, decision_path=decision)


@pytest.mark.parametrize("fault", ["fingerprint", "policy", "unchecked", "extra", "lock"])
def test_bad_decision_or_busy_worker_cannot_mutate_case(tmp_path, fault):
    case, plan = setup(tmp_path)
    plan["decision"].update(actor="Fictional reviewer", rationale="Checked both source statements and applicability.",
                            source_and_applicability_checked=True)
    if fault == "fingerprint":
        plan["decision"]["expected_fingerprint"] = "stale"
    elif fault == "policy":
        plan["decision"]["policy_digest"] = "stale"
    elif fault == "unchecked":
        plan["decision"]["source_and_applicability_checked"] = False
    elif fault == "extra":
        plan["decision"]["send_now"] = True
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(plan))
    with closing(SQLiteStore(tmp_path / "sandbox.db")) as store:
        before = store.get_case(case.id).model_dump_json()
    if fault == "lock":
        with exclusive_state(tmp_path), pytest.raises(RuntimeError):
            sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, decision_path=decision)
    else:
        with pytest.raises(ValueError):
            sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, decision_path=decision)
    with closing(SQLiteStore(tmp_path / "sandbox.db")) as store:
        assert store.get_case(case.id).model_dump_json() == before


def test_missing_database_is_not_created(tmp_path):
    with pytest.raises(ValueError):
        sponsor_location_command(state_dir=tmp_path, policy_path=POLICY_PATH, case_id="missing")
    assert not (tmp_path / "sandbox.db").exists()
