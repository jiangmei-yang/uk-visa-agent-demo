from contextlib import closing

import pytest
from test_consultant_value import POLICY, TODAY
from test_sponsor_location_review import seed

from visa_agent.domain.models import CaseStatus
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.domain.sponsor_location_review import (
    current_sponsor_location_values,
    sponsor_location_review_is_current,
)
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.sponsor_location_review import review_sponsor_location
from visa_agent.workflow.sponsor_location_summary import sponsor_location_snapshot


def conflict(store):
    case = seed(store, "My sponsor lives in the UK but is not in the UK now.")
    case.sponsor_location_statements.extend(parse_sponsor_location_statements(
        "My sponsor does not live in the UK.", source_event_id="clarification",
        sponsor_name=case.profile.sponsor_name, sponsor_relationship=case.profile.sponsor_relationship))
    with store.connection:
        store.connection.execute("INSERT INTO processed_events(event_id,case_id) VALUES (?,?)",
                                 ("clarification", case.id))
    store.save_case(case)
    return case


def resolve(store, case, selection):
    return review_sponsor_location(store, case_id=case.id, expected_fingerprint=review_fingerprint(case),
        actor="Fictional operator", rationale="Checked applicant clarification against the original emails.",
        policy=POLICY, today=TODAY, selected_source_event_ids=selection)


def test_explicit_resolution_retains_history_and_exports_selected_source(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("No network")
    monkeypatch.setattr("socket.socket.connect", deny)
    path = tmp_path / "case.db"
    with closing(SQLiteStore(path)) as store:
        case = conflict(store)
        originals = [row.model_dump() for row in case.sponsor_location_statements]
        assert current_sponsor_location_values(case)["residence"] == {True, False}
        review = resolve(store, case, {"residence": "clarification"})
        assert not review.uk_status_evidence_required
        saved = store.get_case(case.id)
        assert saved.status == CaseStatus.DRAFT
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert saved.delivery_path is None
        assert [row.model_dump() for row in saved.sponsor_location_statements] == originals
        assert current_sponsor_location_values(saved) == {"residence": {False}, "current_presence": {False}}
        assert sponsor_location_review_is_current(saved, POLICY)
        snapshot = sponsor_location_snapshot(saved)
        dimension = snapshot["dimensions"]["residence"]
        assert dimension["value"] is False and dimension["state"] == "reported"
        assert [source["source_event_id"] for source in dimension["sources"]] == ["clarification"]
        assert store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    with closing(SQLiteStore(path)) as store:
        saved = store.get_case("fictional-review")
        assert saved.sponsor_location_review_history[-1].selected_source_event_ids == {"residence": "clarification"}
        assert sponsor_location_review_is_current(saved, POLICY)


@pytest.mark.parametrize("selection", [
    {}, {"residence": "nonexistent"}, {"wrong_dimension": "clarification"},
    {"current_presence": "source"}, {"residence": "clarification", "current_presence": "source"},
])
def test_invalid_or_incomplete_resolution_is_atomic(tmp_path, selection):
    with closing(SQLiteStore(tmp_path / "case.db")) as store:
        case = conflict(store)
        before = case.model_dump_json()
        with pytest.raises(ValueError):
            resolve(store, case, selection)
        assert store.get_case(case.id).model_dump_json() == before


@pytest.mark.parametrize("fault", ["foreign_source", "ambiguous_source", "stale_epoch"])
def test_resolution_cannot_select_unbound_or_ambiguous_evidence(tmp_path, fault):
    with closing(SQLiteStore(tmp_path / "case.db")) as store:
        case = conflict(store)
        if fault == "foreign_source":
            with store.connection:
                store.connection.execute("UPDATE processed_events SET case_id='other' WHERE event_id='clarification'")
        elif fault == "ambiguous_source":
            case.sponsor_location_statements.append(case.sponsor_location_statements[-1].model_copy(update={"value": True}))
        else:
            case.sponsor_location_statements[-1] = case.sponsor_location_statements[-1].model_copy(update={"identity_epoch": 9})
        store.save_case(case)
        before = case.model_dump_json()
        with pytest.raises(ValueError):
            resolve(store, case, {"residence": "clarification"})
        assert store.get_case(case.id).model_dump_json() == before


def test_new_source_invalidates_resolution_and_restores_visible_conflict(tmp_path):
    with closing(SQLiteStore(tmp_path / "case.db")) as store:
        case = conflict(store)
        resolve(store, case, {"residence": "clarification"})
        saved = store.get_case(case.id)
        saved.sponsor_location_statements.append(saved.sponsor_location_statements[0].model_copy(update={"source_event_id": "new-mail"}))
        assert not sponsor_location_review_is_current(saved, POLICY)
        assert current_sponsor_location_values(saved)["residence"] == {False, True}
        assert sponsor_location_snapshot(saved)["dimensions"]["residence"]["state"] == "conflicting"


def test_resolution_does_not_remove_an_unrelated_review_hold(tmp_path):
    with closing(SQLiteStore(tmp_path / "case.db")) as store:
        case = conflict(store)
        case.human_review_reason = "Unresolved refusal history; " + case.human_review_reason
        store.save_case(case)
        resolve(store, case, {"residence": "clarification"})
        saved = store.get_case(case.id)
        assert saved.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert saved.human_review_reason == "Unresolved refusal history"
        assert saved.delivery_path is None


def test_source_resolution_never_substitutes_for_live_processing_consent(tmp_path):
    from visa_agent.privacy.consent import ConsentLedger, ProcessingConsentRequired, ProcessingScope

    with closing(SQLiteStore(tmp_path / "case.db")) as store:
        case = conflict(store)
        ConsentLedger(store).configure(ProcessingScope(provider="fictional", model="no-network"))
        case = store.get_case(case.id)
        before = case.model_dump_json()
        with pytest.raises(ProcessingConsentRequired):
            resolve(store, case, {"residence": "clarification"})
        assert store.get_case(case.id).model_dump_json() == before
        assert store.list_outbox() == []
