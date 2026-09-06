from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from test_consultant_value import APPLICANT, POLICY, TODAY, Model, _patch

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import summary_fingerprint
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("body", [
    "My sponsor does not live in the UK but is in the UK now.",
    "我的资助人不住在英国，但现在在英国。",
])
@pytest.mark.parametrize("off_topic", [False, True])
def test_omitted_mixed_location_cannot_preserve_old_boolean_and_confirmation(tmp_path, monkeypatch, body, off_topic):
    def deny(*args, **kwargs):
        raise AssertionError("Fictional workflow cannot access live services")
    monkeypatch.setattr("socket.socket.connect", deny)
    path = tmp_path / "case.db"
    case = Case(id="fictional-location", external_thread_id="fictional-location",
        applicant_contact=APPLICANT, primary_channel="gmail", policy_version=POLICY.version,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina Example",
            sponsor_relationship="mother", sponsor_is_in_uk=False,
            nationality_country="China", application_country="Hong Kong", occupation_status="student",
            visit_purpose="tourism", route_confirmed_standard_visitor=True, has_serious_history=False),
        profile_confirmed=True, final_summary_confirmed=True)
    before = summary_fingerprint(case, include_documents=False)
    event = InboundEvent(id="original-location-email", external_thread_id=case.external_thread_id,
        sender=APPLICANT, channel="gmail", subject="Fictional", body=body, received_at=datetime.now(UTC))
    patch = _patch(questions=[("off_topic", body)] if off_topic else [])
    with closing(SQLiteStore(path)) as store:
        store.save_case(case)
        service = WorkflowService(store, POLICY, GuardedLLM(Model(patch), max_attempts=1),
                                  today_provider=lambda: TODAY)
        updated, duplicate, plan = service.process(event)
        assert not duplicate and plan == "blocked"
        assert updated.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert updated.profile.sponsor_is_in_uk is None
        assert not updated.profile_confirmed and not updated.final_summary_confirmed
        assert summary_fingerprint(updated, include_documents=False) != before
        assert [(row.dimension, row.value) for row in updated.sponsor_location_statements] == [
            ("residence", False), ("current_presence", True)]
        assert all(row.source_event_id == event.id and row.source_excerpt in body
                   for row in updated.sponsor_location_statements)
        reply = deterministic_fallback_message(updated, plan)
        assert ("分别记下" in reply if updated.customer_language == "zh" else "separate information" in reply)
        assert "refusal decision" not in reply
        again, duplicate, _ = service.process(event)
        assert duplicate and len(again.sponsor_location_statements) == 2
    with closing(SQLiteStore(path)) as store:
        saved = store.get_case(case.id)
        assert saved is not None and len(saved.sponsor_location_statements) == 2
        assert saved.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert saved.delivery_path is None
    assert not list(tmp_path.rglob("*.zip"))


@pytest.mark.parametrize("fault", ["sender", "old_event", "finalized"])
def test_location_capture_cannot_bypass_existing_inbound_boundaries(tmp_path, fault):
    now = datetime.now(UTC)
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact=APPLICANT,
        primary_channel="gmail", policy_version=POLICY.version, last_inbound_received_at=now,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_is_in_uk=False))
    if fault == "finalized":
        case.status = CaseStatus.READY_FOR_HUMAN_REVIEW
    event = InboundEvent(id="rejected-location", external_thread_id=case.external_thread_id,
        sender="someone-else@example.test" if fault == "sender" else APPLICANT,
        channel="gmail", subject="Fictional", body="My sponsor is in the UK.",
        received_at=now - timedelta(seconds=1) if fault == "old_event" else now + timedelta(seconds=1))
    model = Model(_patch())
    with closing(SQLiteStore(tmp_path / "boundary.db")) as store:
        store.save_case(case)
        updated, _, _ = WorkflowService(store, POLICY, GuardedLLM(model, max_attempts=1),
                                       today_provider=lambda: TODAY).process(event)
        assert updated.sponsor_location_statements == []
        assert updated.profile.sponsor_is_in_uk is False
        assert model.events == []
