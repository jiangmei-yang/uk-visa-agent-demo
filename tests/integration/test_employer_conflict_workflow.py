"""An omitted source conflict must survive the real persisted workflow."""

from datetime import UTC, datetime

from test_consultant_value import APPLICANT, POLICY, TODAY, Model, _patch

from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.llm.guarded import GuardedLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def test_model_omission_does_not_allow_literal_completion_or_pack_release(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Fictional conflict test cannot contact network")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    path = tmp_path / "conflict.db"
    event = InboundEvent(id="fictional-conflict", external_thread_id="fictional-conflict",
        sender=APPLICANT, channel="gmail", subject="Fictional",
        body="My employer is Northstar Ltd. My employer is Southstar Ltd. "
             "My employer's phone number is +852 2000 1234.",
        received_at=datetime.now(UTC))
    store = SQLiteStore(path)
    try:
        service = WorkflowService(store, POLICY, GuardedLLM(Model(_patch()), max_attempts=1),
                                  today_provider=lambda: TODAY)
        case, duplicate, plan = service.process(event)
        assert not duplicate and plan == "blocked"
        assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert "employer_name" in case.human_review_reason
        assert case.profile.employer_name is None and case.profile.employer_phone is None
        assert not case.active_evidence("employer_name") and not case.active_evidence("employer_phone")
        assert not case.profile_confirmed and not case.final_summary_confirmed
        case_id = case.id
    finally:
        store.close()
    reopened = SQLiteStore(path)
    try:
        saved = reopened.get_case(case_id)
        assert saved.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert "employer_name" in saved.human_review_reason
        assert not list(tmp_path.rglob("*.zip"))
    finally:
        reopened.close()
