from contextlib import closing
from datetime import UTC, datetime

import pytest
from test_consultant_value import POLICY, TODAY, Model, _patch

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.domain.rules import evaluate_gate, required_profile_facts
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.domain.sponsor_location_review import sponsor_location_review_is_current
from visa_agent.llm.guarded import GuardedLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import summary_fingerprint
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.service import WorkflowService
from visa_agent.workflow.sponsor_location import LOCATION_REVIEW_REASON
from visa_agent.workflow.sponsor_location_review import review_sponsor_location


def seed(store, body="My sponsor does not live in the UK but is in the UK now."):
    case = Case(id="fictional-review", external_thread_id="fictional-review",
        applicant_contact="fictional@example.test", primary_channel="gmail", policy_version=POLICY.version,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina Example",
                            sponsor_relationship="mother"), status=CaseStatus.HUMAN_REVIEW_REQUIRED,
        human_review_reason=LOCATION_REVIEW_REASON, profile_confirmed=True, final_summary_confirmed=True)
    case.sponsor_location_statements = parse_sponsor_location_statements(body, source_event_id="source",
        sponsor_name=case.profile.sponsor_name, sponsor_relationship=case.profile.sponsor_relationship)
    store.save_case(case)
    with store.connection:
        store.connection.execute("INSERT INTO processed_events(event_id,case_id) VALUES (?,?)", ("source", case.id))
    return case


def apply(store, case):
    return review_sponsor_location(store, case_id=case.id, expected_fingerprint=review_fingerprint(case),
        actor="Fictional reviewer", rationale="Read both source dimensions and checked applicability.",
        policy=POLICY, today=TODAY)


@pytest.mark.parametrize("presence", [True, False])
def test_review_resumes_without_legacy_conversion_or_customer_confirmation(tmp_path, monkeypatch, presence):
    def deny(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr("socket.socket.connect", deny)
    path = tmp_path / "test.db"
    with closing(SQLiteStore(path)) as store:
        case = seed(store, "My sponsor does not live in the UK but " + ("is" if presence else "is not") + " in the UK now.")
        before = summary_fingerprint(case, include_documents=False)
        review = apply(store, case)
        assert review.uk_status_evidence_required == presence
        saved = store.get_case(case.id)
        assert saved.status == CaseStatus.DRAFT and saved.human_review_reason is None
        assert saved.profile.sponsor_is_in_uk is None
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert saved.confirmation_fingerprint is None and saved.delivery_path is None
        assert sponsor_location_review_is_current(saved, POLICY)
        assert "sponsor_is_in_uk" not in required_profile_facts(saved)
        assert summary_fingerprint(saved, include_documents=False) != before
        assert evaluate_gate(saved, POLICY, TODAY).checks["sponsor_location_applicability_review_current"]
        assert store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    with closing(SQLiteStore(path)) as store:
        saved = store.get_case(case.id)
        assert len(saved.sponsor_location_review_history) == 1
        assert sponsor_location_review_is_current(saved, POLICY)


def test_other_risk_is_not_removed(tmp_path):
    with closing(SQLiteStore(tmp_path / "test.db")) as store:
        case = seed(store)
        case.human_review_reason = "Unresolved refusal history; " + LOCATION_REVIEW_REASON
        store.save_case(case)
        apply(store, case)
        saved = store.get_case(case.id)
        assert saved.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert saved.human_review_reason == "Unresolved refusal history"
        gate = evaluate_gate(saved, POLICY, TODAY)
        assert gate.checks["sponsor_location_applicability_review_current"]
        assert not gate.checks["no_unresolved_human_review"]
        assert not gate.allowed


def test_normal_intake_can_continue_after_review_without_reasking_legacy_location(tmp_path):
    with closing(SQLiteStore(tmp_path / "test.db")) as store:
        case = seed(store)
        case.profile.nationality_country = "China"
        case.profile.application_country = "Hong Kong"
        case.profile.visit_purpose = "tourism"
        case.profile.route_confirmed_standard_visitor = True
        case.profile.has_serious_history = False
        store.save_case(case)
        apply(store, case)
        event = InboundEvent(id="continue", external_thread_id=case.external_thread_id,
            sender=case.applicant_contact, channel="gmail", subject="Fictional continuation",
            body="What should I prepare next?", received_at=datetime.now(UTC))
        service = WorkflowService(store, POLICY, GuardedLLM(Model(_patch(
            questions=[("next_step", event.body)])), max_attempts=1),
                                  today_provider=lambda: TODAY)
        saved, duplicate, plan = service.process(event)
        assert not duplicate and plan == "blocked"  # Incomplete pack remains blocked, not intake.
        assert store.event_processed(event.id)
        assert saved.status == CaseStatus.DRAFT
        assert "sponsor_is_in_uk" not in saved.last_requested_fields
        assert sponsor_location_review_is_current(saved, POLICY)
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert saved.delivery_path is None


@pytest.mark.parametrize("fault", ["stale", "source", "paused", "finalized", "incomplete", "conflicting", "epoch"])
def test_review_rejects_unsafe_inputs_atomically(tmp_path, fault):
    with closing(SQLiteStore(tmp_path / "test.db")) as store:
        case = seed(store)
        if fault == "stale":
            changed = case.model_copy(deep=True)
            changed.profile.sponsor_address = "Changed address"
            store.save_case(changed)
        elif fault == "source":
            with store.connection:
                store.connection.execute("UPDATE processed_events SET case_id='different-case'")
        else:
            if fault == "paused":
                case.preparation_paused = True
                case.preparation_control_epoch += 1
            elif fault == "finalized":
                case.delivery_path = "fictional.zip"
            elif fault == "incomplete":
                case.sponsor_location_statements.pop()
            elif fault == "conflicting":
                row = case.sponsor_location_statements[0]
                case.sponsor_location_statements.append(row.model_copy(update={"value": not row.value}))
            elif fault == "epoch":
                case.sponsor_location_epoch += 1
            store.save_case(case)
        before = store.get_case(case.id).model_dump_json()
        with pytest.raises(ValueError):
            apply(store, case)
        assert store.get_case(case.id).model_dump_json() == before


@pytest.mark.parametrize("change", ["source", "identity", "address", "epoch", "control", "policy"])
def test_review_cannot_authorize_changed_case_or_policy(tmp_path, change):
    with closing(SQLiteStore(tmp_path / "test.db")) as store:
        case = seed(store)
        apply(store, case)
        case = store.get_case(case.id)
        policy = POLICY.model_copy(deep=True)
        if change == "source":
            row = case.sponsor_location_statements[0]
            case.sponsor_location_statements[0] = row.model_copy(update={"source_excerpt": "Altered source"})
        elif change == "identity":
            case.profile.sponsor_name = "Another Person"
        elif change == "address":
            case.profile.sponsor_address = "New address"
        elif change == "epoch":
            case.sponsor_location_epoch += 2  # Changed away and back is not old authority.
        elif change == "control":
            case.preparation_control_epoch += 1
        else:
            policy.version += "-changed"
        assert not sponsor_location_review_is_current(case, policy)
        assert not evaluate_gate(case, policy, TODAY).checks["sponsor_location_applicability_review_current"]
