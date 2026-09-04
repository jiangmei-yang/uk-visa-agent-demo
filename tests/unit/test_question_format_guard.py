import pytest

from visa_agent.domain.models import Case
from visa_agent.llm.guarded import GuardedLLM, _question_format_key
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.workflow.conversation import reply_items


def example():
    case = Case(id='c', external_thread_id='t', applicant_contact='fictional@example.test', policy_version='v')
    case.profile.visit_purpose = 'tourism'
    case.profile.nationality_country = 'China'
    case.profile.application_country = 'Hong Kong'
    return case


def test_oxford_comma_does_not_reject_otherwise_identical_question():
    case = example()
    text = '\n'.join(reply_items(case)[1]).replace('studying or', 'studying, or')

    class Model(OfflineFixtureLLM):
        def render_message(self, case, plan):
            return text

    guarded = GuardedLLM(Model())
    assert guarded.render_message(case, 'blocked') == text
    assert not guarded.last_render_fallback


@pytest.mark.parametrize('first,second', [('10.00', '1000'), ('-100', '100'),
    ('2026-11-10','2026-11-17'), ('Do not confirm?', 'Do confirm?'),
    ('Which country will you apply from?', 'Which country were you born in?')])
def test_question_normalization_preserves_numbers_negation_and_meaning_words(first,second):
    assert _question_format_key(first) != _question_format_key(second)


@pytest.mark.parametrize('claim', ['No documents are needed from you at this stage.',
    "We’ll hold off on any further steps until you review the summary."])
def test_required_questions_do_not_authorize_extra_process_claims(claim):
    case = example()

    class Model(OfflineFixtureLLM):
        def render_message(self, case, plan):
            return '\n'.join(reply_items(case)[1]) + '\n' + claim

    guarded = GuardedLLM(Model())
    assert claim not in guarded.render_message(case, 'blocked')
    assert guarded.last_render_fallback
