from contextlib import closing

import pytest
from test_consultant_value import POLICY, TODAY

from visa_agent.delivery.pack import _preparation_control_rejection, generate_pack
from visa_agent.domain.models import Case, CaseStatus
from visa_agent.domain.rules import evaluate_gate
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("location", ["caller", "persisted", "both"])
@pytest.mark.parametrize("hold", ["status", "reason", "both"])
def test_unresolved_review_blocks_before_any_artifact_work(tmp_path, monkeypatch, location, hold):
    def forbidden(*args, **kwargs):
        raise AssertionError("A review hold must reject before artifact generation")
    monkeypatch.setattr("visa_agent.delivery.pack._generate_pack", forbidden)
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version=POLICY.version, profile_confirmed=True, final_summary_confirmed=True)
    saved = case.model_copy(deep=True)
    for target in ([case] if location == "caller" else [saved] if location == "persisted" else [case, saved]):
        if hold in {"status", "both"}:
            target.status = CaseStatus.HUMAN_REVIEW_REQUIRED
        if hold in {"reason", "both"}:
            target.human_review_reason = "Unresolved separate risk after sponsor review"
    with closing(SQLiteStore(tmp_path / "state.db")) as store:
        store.save_case(saved)
        before = store.get_case(case.id).model_dump_json()
        output = tmp_path / "artifacts"
        path, reasons = generate_pack(case, POLICY, store, output, TODAY)
        assert path is None and "Unresolved human review" in reasons[0]
        assert not output.exists()
        assert store.get_case(case.id).model_dump_json() == before
        assert store.list_outbox() == []


@pytest.mark.parametrize("hold", ["status", "reason"])
def test_gate_does_not_treat_customer_confirmation_as_review_resolution(hold):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version=POLICY.version, profile_confirmed=True, final_summary_confirmed=True)
    if hold == "status":
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    else:
        case.human_review_reason = "Review still required"
    assert not evaluate_gate(case, POLICY, TODAY).checks["no_unresolved_human_review"]


def test_draft_is_not_mistaken_for_human_hold(tmp_path):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version=POLICY.version)
    with closing(SQLiteStore(tmp_path / "state.db")) as store:
        store.save_case(case)
        assert _preparation_control_rejection(case, store) is None
        assert evaluate_gate(case, POLICY, TODAY).checks["no_unresolved_human_review"]
