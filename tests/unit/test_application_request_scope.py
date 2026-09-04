"""Independent current-request boundaries for reviewed application-entry help.

All inputs are synthetic. These tests call pure local compilers, not a model or
mailbox, and do not score naturalness or assert applicant eligibility.
"""

from datetime import date

import pytest

from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    _general_application_proposal,
    grounded_customer_answer_plan,
    reviewed_application_requests,
    validated_customer_questions,
)

TODAY = date(2026, 9, 5)


@pytest.mark.parametrize("change", [
    "Now I am changing to a spouse visa and need fee information.",
    "I am no longer applying for a UK visitor visa.",
])
def test_current_route_change_or_retraction_clears_preceding_visitor_context(change):
    question = "Which webpage should I use to apply?"
    body = "I am applying for a UK visitor visa. " + change + " " + question
    assert reviewed_application_requests(body) == []
    assert not _general_application_proposal(body, question)


@pytest.mark.parametrize("body,question,language", [
    ("I am applying for a five-year UK visitor visa. What is the visa fee?", "What is the visa fee?", "en"),
    ("我准备申请五年英国访问签证。签证费是多少？", "签证费是多少？", "zh"),
])
def test_preceding_validity_is_retained_for_a_short_fee_followup(body, question, language):
    plan = grounded_customer_answer_plan(body, language, TODAY, semantic_questions=[proposal("fees", question)])
    text = "\n".join(plan.answers)
    assert "135" not in text and APPLICATION_SOURCE in text
    assert "Visa fees" in text


@pytest.mark.parametrize("body,question,language", [
    ("I mentioned a five-year UK visitor visa. Now I am asking about the ordinary six-month UK visitor visa. "
     "What is the visa fee?", "What is the visa fee?", "en"),
    ("之前说的是五年英国访问签证。现在我问普通六个月英国访问签证。签证费是多少？", "签证费是多少？", "zh"),
])
def test_current_six_month_scope_does_not_keep_an_older_long_term_fee_qualifier(body, question, language):
    plan = grounded_customer_answer_plan(body, language, TODAY, semantic_questions=[proposal("fees", question)])
    assert "£135" in "\n".join(plan.answers)


def proposal(topic, excerpt, confidence=0.91):
    return CustomerQuestion.model_validate({
        "topic": topic, "source_excerpt": excerpt, "confidence": confidence,
    })


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Application-scope tests must stay offline")

    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.socket.connect", deny)


@pytest.mark.parametrize(("body", "excerpt"), [
    ("Please tell me which webpage to use to apply for a UK visitor visa and how to get started.",
     "Please tell me which webpage to use to apply for a UK visitor visa and how to get started."),
    ("I am applying for a UK visitor visa. Which webpage should I use to apply?",
     "Which webpage should I use to apply?"),
    ("I am applying for a UK visitor visa. How do I get started?", "How do I get started?"),
    ("我想申请英国访问签证。应该从哪个网页开始申请？", "应该从哪个网页开始申请？"),
])
def test_current_application_entry_is_answered_with_or_without_neighbouring_model_label(body, excerpt):
    assert reviewed_application_requests(body)
    empty = grounded_customer_answer_plan(body, "en", TODAY)
    assert "application" in empty.selected_topics
    assert APPLICATION_SOURCE in "\n".join(empty.answers)
    for label in ("unsupported", "next_step"):
        raw = proposal(label, excerpt)
        before = raw.model_dump_json()
        accepted = validated_customer_questions(body, [raw])
        assert len(accepted) == 1 and accepted[0].topic == "application"
        assert accepted[0].source_excerpt == excerpt and accepted[0].confidence == 0.91
        assert raw.model_dump_json() == before
        plan = grounded_customer_answer_plan(body, "en", TODAY, semantic_questions=[raw])
        assert plan.selected_topics == ["application"]
        assert APPLICATION_SOURCE in "\n".join(plan.answers)


@pytest.mark.parametrize("separator", [", ", "; ", "\n"])
def test_model_cannot_cut_a_condition_off_the_application_request(separator):
    excerpt = "which webpage should I use to apply for a UK visitor visa?"
    body = "If my application is eligible" + separator + excerpt
    raw = proposal("unsupported", excerpt)
    assert not _general_application_proposal(body, excerpt)
    assert not reviewed_application_requests(body)
    assert all(item.topic != "application" for item in validated_customer_questions(body, [raw]))


@pytest.mark.parametrize("body", [
    "Which webpage should I use to apply for a UK visitor visa and guarantee approval?",
    "Which webpage should I use to apply for a UK visitor visa after my previous refusal?",
    "How much does it cost to get started with a UK visitor visa?",
    "How early should I apply for a UK visitor visa?",
    "Do not tell me which webpage to use to apply for a UK visitor visa.",
    "My sister asked which webpage to use to apply for a UK visitor visa.",
    "Tomorrow I will ask which webpage to use to apply for a UK visitor visa.",
    'The example says: "Which webpage should I use to apply for a UK visitor visa?"',
])
def test_nonentry_or_noncurrent_question_is_not_normalised_to_application(body):
    raw = proposal("unsupported", body)
    before = raw.model_dump_json()
    assert not reviewed_application_requests(body)
    assert not _general_application_proposal(body, body)
    assert all(item.topic != "application" for item in validated_customer_questions(body, [raw]))
    assert raw.model_dump_json() == before


@pytest.mark.parametrize("route", ["Graduate visa", "spouse visa", "visa for medical treatment", "transit visa"])
def test_other_route_in_previous_sentence_is_not_replaced_by_visitor_entry(route):
    excerpt = "Which webpage should I use to apply, and how do I get started?"
    body = f"I need a UK {route}. {excerpt}"
    raw = proposal("unsupported", excerpt)
    assert not reviewed_application_requests(body)
    assert not _general_application_proposal(body, excerpt)
    assert all(item.topic != "application" for item in validated_customer_questions(body, [raw]))


def test_complete_proposal_containing_link_and_approval_question_is_not_laundered():
    body = ("Which webpage should I use to apply for a UK visitor visa? "
            "Also, can you guarantee that my application will be approved?")
    raw = proposal("unsupported", body)
    assert not _general_application_proposal(body, body)
    accepted = validated_customer_questions(body, [raw])
    assert [item.topic for item in accepted] == ["unsupported"]
    assert accepted[0].source_excerpt == body and raw.topic == "unsupported"
    plan = grounded_customer_answer_plan(body, "en", TODAY, semantic_questions=[raw])
    assert "unsupported" in plan.selected_topics


def test_independent_link_and_fee_requests_keep_their_separate_authority():
    entry = "Which webpage should I use to apply for a UK visitor visa?"
    fee = "What is the fee for a six-month UK visitor visa?"
    body = entry + " Separately, " + fee
    questions = [proposal("unsupported", entry), proposal("fees", fee, 0.88)]
    before = [item.model_dump_json() for item in questions]
    accepted = validated_customer_questions(body, questions)
    assert [(item.topic, item.source_excerpt, item.confidence) for item in accepted] == [
        ("application", entry, 0.91), ("fees", fee, 0.88),
    ]
    plan = grounded_customer_answer_plan(body, "en", TODAY, semantic_questions=questions)
    assert set(plan.selected_topics) == {"application", "fees"}
    assert len(plan.answers) == 2 and APPLICATION_SOURCE in "\n".join(plan.answers)
    assert [item.model_dump_json() for item in questions] == before


def test_expired_source_produces_recheck_not_an_application_instruction():
    body = "Which webpage should I use to apply for a UK visitor visa?"
    plan = grounded_customer_answer_plan(body, "en", date(2026, 10, 5))
    answer = "\n".join(plan.answers)
    assert plan.answers and "recheck" in answer.lower()
    assert APPLICATION_SOURCE not in answer and "Apply now" not in answer


def test_current_no_links_changes_format_not_the_application_answer():
    body = "Which webpage should I use to apply for a UK visitor visa? Please answer without links."
    plan = grounded_customer_answer_plan(body, "en", TODAY)
    answer = "\n".join(plan.answers)
    assert plan.selected_topics == ["application"]
    assert "Apply now" in answer and "save" in answer
    assert "http" not in answer and "GOV.UK:" not in answer


def test_literal_grounding_and_confidence_still_gate_neighbouring_proposals():
    body = "Which webpage should I use to apply for a UK visitor visa?"
    unrelated = proposal("unsupported", "Where do I apply for a UK visitor visa?")
    uncertain = proposal("next_step", body, 0.79)
    assert validated_customer_questions(body, [unrelated, uncertain]) == []
