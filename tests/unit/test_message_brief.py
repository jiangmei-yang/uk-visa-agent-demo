import json

from visa_agent.domain.models import Case
from visa_agent.llm.openai_client import message_input


def test_model_brief_distinguishes_followup_and_exposes_grounded_context():
    case = Case(id='c', external_thread_id='t', applicant_contact='fictional@example.test', policy_version='v')
    first = message_input(case, 'blocked')
    assert json.loads(first[first.index('{'):])['is_follow_up'] is False
    case.outbound_message_ids = ['previous']
    case.latest_received_facts = {'occupation_status': 'student'}
    case.deferred_fields = ['planned_arrival_date', 'planned_departure_date']
    followup = message_input(case, 'blocked')
    brief = json.loads(followup[followup.index('{'):])
    assert brief['is_follow_up'] is True
    assert "studying" in brief['received_context_acknowledgement']
    assert brief['deferred_questions'] == case.deferred_fields
    assert 'only the selected next step' in followup
