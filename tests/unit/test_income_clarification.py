"""Explanations must not borrow a quoted, hypothetical or other-person request."""

import pytest

from visa_agent.workflow.advice_preferences import wants_brief_reply
from visa_agent.workflow.income_clarification import (
    guarantee_question,
    income_answer,
    income_question,
    only_income_evidence_question,
)


@pytest.mark.parametrize("wrapper", [
    'Please translate "{question}".',
    'My friend asked: "{question}".',
    "If I apply later, {question}",
    "Do not answer this: {question}",
])
@pytest.mark.parametrize("question", [
    "What should I use to explain my income?",
    "Can you guarantee this is enough?",
])
def test_not_a_current_own_request(wrapper, question):
    body = wrapper.format(question=question)
    assert income_question(body) is None
    assert not guarantee_question(body)


def test_no_self_employment_assumption():
    assert income_answer("income_evidence", "en", self_employed=False) is None


@pytest.mark.parametrize("body,covered", [
    ("I don't get payslips. What should I use to explain my income?", True),
    ("没有工资单我用什么说明收入？", True),
    ("我没有工资单。没有工资单用什么说明收入？", True),
    ("What should I use to explain my income? How can I avoid tax?", False),
    ("没有工资单用什么说明收入？怎么避税？", False),
    ('Please translate "What should I use to explain my income?"', False),
    ("If I apply later, what should I use to explain my income?", False),
])
def test_generic_boundary_removal_requires_entire_covered_request(body, covered):
    assert only_income_evidence_question(body) == covered


def test_transfers_need_own_transfer_context_and_a_question():
    assert income_question("Is the whole amount my income?") is None
    assert income_question("Those deposits include transfers from my own other account.") is None
    assert income_question("Those deposits include transfers from my own other account. "
                           "Is the whole amount my income?") == "own_transfers"


@pytest.mark.parametrize("text", [
    "Please keep it brief.", "你先简短告诉我眼下最值得做的一件事。",
])
def test_explicit_brief_pacing(text):
    assert wants_brief_reply(text)


@pytest.mark.parametrize("text", [
    'My friend said "Please keep it brief."',
    "If I ask tomorrow, please keep it brief.",
    "Do not keep it brief.", "不要简短回答。",
])
def test_brief_pacing_does_not_borrow_a_non_request(text):
    assert not wants_brief_reply(text)
