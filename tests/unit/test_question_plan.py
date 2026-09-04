from datetime import date

import pytest

from visa_agent.domain.models import Case
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.workflow.conversation import customer_requests_next_step, next_fact_questions


@pytest.mark.parametrize("body", [
    "现在可以继续了，下一步需要什么？", "接下来怎么准备？", "我已经准备好了。",
    "What is the next step?", "I'm ready to continue.", "What's next?",
])
def test_explicit_resumption_only_affects_question_pacing(body):
    assert customer_requests_next_step(body)


@pytest.mark.parametrize("body", [
    "我还没核对摘要，下一步先不要问。", "如果没问题，再告诉我下一步。", "先不要继续问。",
    "收到\n> 下一步是什么？", "你之前说“下一步是什么？”", "I'll reply later.",
    "I'm not ready to continue.", "If the summary is right, what's next?",
])
def test_negation_conditions_and_quotes_do_not_resume_questioning(body):
    assert not customer_requests_next_step(body)


def test_stored_plan_still_filters_newly_filled_and_deferred_fields():
    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test", policy_version="v")
    case.question_plan = ["date_of_birth", "planned_arrival_date", "full_name"]
    case.profile.date_of_birth = date(1998, 5, 12)
    case.deferred_fields = ["planned_arrival_date"]
    assert next_fact_questions(case) == ["full_name"]


def test_wording_model_cannot_reopen_paused_questions():
    class RepeatingModel:
        version = "test"

        def render_message(self, case, plan):
            raise AssertionError("Paused question flow must not invoke free wording")

    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test", policy_version="v")
    case.question_plan = []
    case.pending_question_fields = ["full_name", "date_of_birth"]
    reply = GuardedLLM(RepeatingModel()).render_message(case, "blocked")
    assert reply == deterministic_fallback_message(case, "blocked")
    assert '?' not in reply and '？' not in reply
    assert not case.profile_confirmed and not case.final_summary_confirmed
