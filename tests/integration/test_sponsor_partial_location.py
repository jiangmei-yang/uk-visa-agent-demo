from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from test_consultant_value import APPLICANT, POLICY, TODAY, Model, _patch

from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import _profile_question_text
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("language,first,second,missing", [
    ("en", "My sponsor does not live in the UK.", "My sponsor is in the UK now.", "physically in the UK"),
    ("en", "My sponsor is in the UK now.", "My sponsor does not live in the UK.", "usually live in the UK"),
    ("zh", "我的资助人不住在英国。", "我的资助人现在在英国。", "目前人在英国"),
    ("zh", "我的资助人现在在英国。", "我的资助人不住在英国。", "平时住在英国"),
])
@pytest.mark.parametrize("with_question", [False, True])
def test_partial_location_continues_intake_then_reviews_complete_information(tmp_path, monkeypatch,
                                                                           language, first, second, missing, with_question):
    def deny(*args, **kwargs):
        raise AssertionError("No network in fictional regression")
    monkeypatch.setattr("socket.socket.connect", deny)
    if with_question:
        first += " 接下来需要什么材料？" if language == "zh" else " What should I prepare next?"
    path = tmp_path / "partial.db"
    case = Case(id="partial", external_thread_id="partial", applicant_contact=APPLICANT,
        primary_channel="gmail", policy_version=POLICY.version, customer_language=language,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina Example",
            sponsor_relationship="mother", sponsor_is_in_uk=False,
            nationality_country="China", application_country="Hong Kong", visit_purpose="tourism",
            occupation_status="student", route_confirmed_standard_visitor=True, has_serious_history=False))
    now = datetime.now(UTC)
    with closing(SQLiteStore(path)) as store:
        store.save_case(case)
        service = WorkflowService(store, POLICY, GuardedLLM(Model(_patch()), max_attempts=1), today_provider=lambda: TODAY)
        event = InboundEvent(id="first", external_thread_id=case.external_thread_id, sender=APPLICANT,
            channel="gmail", subject="Fictional", body=first, received_at=now)
        partial, _, _ = service.process(event)
        assert partial.status == CaseStatus.DRAFT and partial.human_review_reason is None
        assert len(partial.sponsor_location_statements) == 1
        assert partial.profile.sponsor_is_in_uk is None
        assert not evaluate_gate(partial, POLICY, TODAY).checks["sponsor_location_applicability_review_current"]
        assert missing in _profile_question_text(partial, "sponsor_is_in_uk")
        assert not partial.profile_confirmed and not partial.final_summary_confirmed
    # Persistence, not a model's transient chat memory, carries the first answer.
    with closing(SQLiteStore(path)) as store:
        service = WorkflowService(store, POLICY, GuardedLLM(Model(_patch()), max_attempts=1), today_provider=lambda: TODAY)
        event = InboundEvent(id="second", external_thread_id=case.external_thread_id, sender=APPLICANT,
            channel="gmail", subject="Fictional", body=second, received_at=now + timedelta(seconds=1))
        complete, _, _ = service.process(event)
        assert complete.status == CaseStatus.HUMAN_REVIEW_REQUIRED
        assert len(complete.sponsor_location_statements) == 2
        assert {item.source_event_id for item in complete.sponsor_location_statements} == {"first", "second"}
        assert complete.delivery_path is None
        assert not complete.profile_confirmed and not complete.final_summary_confirmed


def test_partial_input_does_not_clear_an_unrelated_hold():
    from visa_agent.workflow.sponsor_location import record_sponsor_location

    case = Case(id="held", external_thread_id="held", applicant_contact=APPLICANT, policy_version=POLICY.version,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina", sponsor_relationship="mother"),
        status=CaseStatus.HUMAN_REVIEW_REQUIRED, human_review_reason="Unresolved history")
    event = InboundEvent(id="partial", external_thread_id="held", sender=APPLICANT,
        channel="gmail", subject="Fictional", body="My sponsor does not live in the UK.", received_at=datetime.now(UTC))
    assert record_sponsor_location(case, event)
    assert case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert case.human_review_reason == "Unresolved history"
