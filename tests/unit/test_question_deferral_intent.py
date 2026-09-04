from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.llm.ports import CasePatch, QuestionDeferral
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def message(body):
    return InboundEvent(id='e', external_thread_id='t', sender='fictional@example.test',
                        subject='UK visit', body=body, received_at=datetime.now(UTC))


@pytest.mark.parametrize('excerpt,confidence', [('invented quote', 1), ('还在等学校的放假安排', 0.2),
    ('日期尚未确定', 1)])
def test_ungrounded_low_confidence_or_quoted_deferral_is_ignored(excerpt, confidence):
    event = message('还在等学校的放假安排\n\nOn Friday, Adviser wrote:\n日期尚未确定')
    patch = CasePatch(updates=[], ambiguities=[], question_deferrals=[QuestionDeferral(
        field='planned_arrival_date', source_excerpt=excerpt, confidence=confidence)])
    accepted = validate_case_patch(event, patch)
    assert accepted.question_deferrals == []
    assert not accepted.requires_human_review


def test_deferral_cannot_target_confirmation_or_arbitrary_fields():
    with pytest.raises(ValidationError):
        QuestionDeferral(field='route_confirmed_standard_visitor', source_excerpt='skip it', confidence=1)


def test_semantic_deferral_survives_new_turn_without_filling_facts(tmp_path):
    class IntentModel(OfflineFixtureLLM):
        def extract_case_patch(self, event):
            intents = [] if event.id != 'e' else [QuestionDeferral(field=field,
                source_excerpt='还在等学校的放假安排', confidence=0.95)
                for field in ('planned_arrival_date', 'planned_departure_date')]
            return CasePatch(updates=[], ambiguities=[], question_deferrals=intents)

    path = tmp_path / 'db'
    store = SQLiteStore(path)
    try:
        workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')), IntentModel())
        case, _, _ = workflow.process(message('还在等学校的放假安排'))
        assert len(case.deferred_fields) == 2
        assert case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None
        assert not case.final_summary_confirmed and not case.delivery_path
        store.close()
        store = SQLiteStore(path)
        workflow = WorkflowService(store, workflow.policy, IntentModel())
        again, _, _ = workflow.process(message('其他信息稍后给你').model_copy(update={'id':'next'}))
        assert again.deferred_fields == case.deferred_fields
        assert not set(again.last_requested_fields) & set(case.deferred_fields)
    finally:
        store.close()


def test_deferral_does_not_erase_existing_exact_dates(tmp_path):
    from datetime import date

    class IntentModel(OfflineFixtureLLM):
        def extract_case_patch(self, event):
            return CasePatch(updates=[], ambiguities=[], question_deferrals=[QuestionDeferral(
                field='planned_arrival_date', source_excerpt=event.body, confidence=1)])

    store = SQLiteStore(tmp_path / 'db')
    case = Case(id='c', external_thread_id='t', applicant_contact='fictional@example.test', policy_version='v')
    case.profile.planned_arrival_date = date(2026, 11, 10)
    store.save_case(case)
    try:
        workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')), IntentModel())
        result, _, _ = workflow.process(message('还在等学校的放假安排'))
        assert result.profile.planned_arrival_date == date(2026, 11, 10)
        assert 'planned_arrival_date' not in result.deferred_fields
    finally:
        store.close()
