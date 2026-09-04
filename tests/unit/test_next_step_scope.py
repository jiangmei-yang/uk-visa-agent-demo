"""Synthetic own-case scope regressions; no provider, evaluation file or mailbox."""

from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate
from visa_agent.workflow.customer_questions import validated_customer_questions


def next_step(excerpt: str) -> CustomerQuestion:
    return CustomerQuestion(topic="next_step", source_excerpt=excerpt, confidence=0.99)


@pytest.mark.parametrize(("body", "excerpt"), [
    ("What should she prepare next for her UK visa?", "What should she prepare next for her UK visa?"),
    ("What documents should he prepare next?", "What documents should he prepare next?"),
    ("How should they start preparing their application?", "How should they start preparing their application?"),
    ("她下一步要准备什么材料？", "她下一步要准备什么材料？"),
    ("接下来他应该怎么准备申请？", "接下来他应该怎么准备申请？"),
    ("我要帮她准备申请，下一步做什么？", "下一步做什么？"),
    ("I am asking for my sister. What comes next?", "What comes next?"),
    ("My brother's UK visitor application is the subject here. What comes next?", "What comes next?"),
    ("My sister wants to apply for a visa. What is the next step?", "What is the next step?"),
    ("This question is on behalf of a friend. What is the next step?", "What is the next step?"),
    ("这是替弟弟问的。下一步准备哪一份？", "下一步准备哪一份？"),
    ("这次帮一位朋友咨询申请。接下来该怎么做？", "接下来该怎么做？"),
    ("我弟弟想去英国旅游。下一步准备哪一份？", "下一步准备哪一份？"),
    ("我妹妹需要学生签证。下一步怎么准备？", "下一步怎么准备？"),
    ("Please discuss her application. If you can help, what is the next step?", "what is the next step?"),
    ("先不讨论我的旅游申请。下一步准备什么？", "下一步准备什么？"),
    ("This is not about my visitor application. What is the next step?", "What is the next step?"),
    ("I need a student visa. What should I prepare next?", "What should I prepare next?"),
    ("我现在想申请工作签证。接下来怎么准备？", "接下来怎么准备？"),
    ("I have a French visa application. What is the next step?", "What is the next step?"),
    ("加拿大签证的材料还没准备。下一步怎么做？", "下一步怎么做？"),
])
def test_foreign_applicant_or_application_cannot_borrow_current_case_step(body: str, excerpt: str) -> None:
    assert validated_customer_questions(body, [next_step(excerpt)]) == []


@pytest.mark.parametrize(("body", "excerpt"), [
    ("What should I prepare next?", "What should I prepare next?"),
    ("我下一步准备什么？", "我下一步准备什么？"),
    ("My mother is paying for my trip. What should I prepare next?", "What should I prepare next?"),
    ("My sister is helping me prepare my application. What should I prepare next?", "What should I prepare next?"),
    ("妈妈给我资助。接下来我准备什么？", "接下来我准备什么？"),
    ("妈妈打算帮我准备签证材料。接下来我准备什么？", "接下来我准备什么？"),
    ("My brother is in Britain on a student visa and I will stay with him during my UK holiday. What should I prepare next?",
     "What should I prepare next?"),
    ("My sister's student visa is current, but I am visiting Britain for a holiday. What should I prepare next?",
     "What should I prepare next?"),
    ("我弟弟持学生签证在英国读书，我这次是去英国旅游看他。下一步我准备什么？", "下一步我准备什么？"),
    ("妹妹在英国持有学生签证，而我是去英国探亲。下一步怎么准备？", "下一步怎么准备？"),
    ("I am not asking for my sister. What should I prepare next?", "What should I prepare next?"),
    ("不是替弟弟问的。下一步我准备什么？", "下一步我准备什么？"),
    ("Do not discuss my brother's application. What should I prepare next?", "What should I prepare next?"),
    ("弟弟的申请先不谈。下一步我准备什么？", "下一步我准备什么？"),
    ('The earlier note said "I am asking for my sister". What should I prepare next?', "What should I prepare next?"),
    ("之前有人写了“她下一步怎么申请”。我下一步准备什么？", "我下一步准备什么？"),
    ("> I am asking for my sister.\nWhat should I prepare next?", "What should I prepare next?"),
    ("Not a student visa. What should I prepare next for my visitor application?", "What should I prepare next for my visitor application?"),
    ("I am not applying for a student visa. What should I prepare next for my visitor application?", "What should I prepare next for my visitor application?"),
    ("不是学生签证。我的访客签证下一步准备什么？", "我的访客签证下一步准备什么？"),
    ("我不申请学生签证。我的访客签证下一步准备什么？", "我的访客签证下一步准备什么？"),
])
def test_own_step_survives_sponsors_and_quoted_or_declined_other_people(body: str, excerpt: str) -> None:
    proposed = next_step(excerpt)
    assert validated_customer_questions(body, [proposed]) == [proposed]


@pytest.mark.parametrize("own_first", [False, True])
@pytest.mark.parametrize(("own", "third", "scope"), [
    ("For my own UK visitor application, what should I prepare next?",
     "What should she prepare next?", "I am asking for my sister about a student visa."),
    ("回到我自己的英国旅游申请，下一步准备什么？", "她下一步准备什么？", "我替妹妹问学生签证。"),
])
def test_independent_own_step_survives_other_applicants_in_either_order_before_dedup(
    own_first: bool, own: str, third: str, scope: str,
) -> None:
    foreign = scope + " " + third
    body = (own + " " + foreign) if own_first else (foreign + " " + own)
    proposed_own, proposed_third = next_step(own), next_step(third)
    # Also invert the proposal order: output ordering must not decide case ownership.
    for proposals in ([proposed_own, proposed_third], [proposed_third, proposed_own]):
        assert validated_customer_questions(body, proposals) == [proposed_own]


@pytest.mark.parametrize(("body", "excerpt"), [
    ("I am asking for my sister. For my own application, what should I prepare next?",
     "For my own application, what should I prepare next?"),
    ("My earlier question was about a French visa. Back to my UK visitor application, what comes next?",
     "Back to my UK visitor application, what comes next?"),
    ("我刚才问学生签证。说回我自己的英国旅游申请，我下一步需要什么？",
     "说回我自己的英国旅游申请，我下一步需要什么？"),
])
def test_explicit_own_case_reanchor_restores_the_correct_scope(body: str, excerpt: str) -> None:
    proposed = next_step(excerpt)
    assert validated_customer_questions(body, [proposed]) == [proposed]


def test_same_ambiguous_excerpt_in_two_people_contexts_is_not_bound_to_convenient_occurrence() -> None:
    repeated = "What should I prepare next?"
    body = "I am asking for my sister. " + repeated + " For my own UK visitor application: " + repeated
    assert validated_customer_questions(body, [next_step(repeated)]) == []


def test_scope_filter_preserves_independent_supported_question_and_own_fact() -> None:
    own_fact = "Please correct my date of birth to 21 May 1998."
    other = "I am asking for my sister about a student visa. Can you help with her application?"
    foreign_step = "What should she prepare next?"
    faq = "For my own UK visitor application, where is the official form?"
    body = " ".join([own_fact, other, foreign_step, faq])
    patch = CasePatch(updates=[FactUpdate(
        field="date_of_birth", value="1998-05-21", source_excerpt=own_fact, confidence=1,
    )], ambiguities=[], customer_questions=[
        CustomerQuestion(topic="unsupported", source_excerpt=other, confidence=0.99),
        next_step(foreign_step), CustomerQuestion(topic="application", source_excerpt=faq, confidence=0.99),
    ])
    event = InboundEvent(id="scope-regression", channel="gmail", external_thread_id="own-case",
                         sender="fictional@example.test", subject="Application enquiry", body=body,
                         received_at=datetime(2026, 9, 4, tzinfo=UTC))
    result = validate_case_patch(event, patch)
    assert [(update.field, update.value) for update in result.updates] == [("date_of_birth", "1998-05-21")]
    assert [question.topic for question in result.customer_questions] == ["unsupported", "application"]
    assert result.preparation_intent is None and not result.requires_human_review
    assert patch.customer_questions[1].topic == "next_step"  # Guard does not mutate raw evidence.


def test_own_trip_background_does_not_make_explicit_other_person_step_ours() -> None:
    body = "I am preparing my UK holiday. My sister needs a student visa. What should she prepare next?"
    assert validated_customer_questions(body, [next_step("What should she prepare next?")]) == []
