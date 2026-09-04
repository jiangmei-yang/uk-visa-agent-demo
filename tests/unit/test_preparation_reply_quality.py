"""Reading-level regressions: no provider, corpus labels or recipient delivery."""

from datetime import date
from pathlib import Path

import pytest

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.adviser_guidance import preparation_guidance
from visa_agent.workflow.conversation import change_acknowledgement, preparation_control_receipt
from visa_agent.workflow.customer_questions import grounded_customer_answers

TODAY = date(2026, 9, 4)


def example(language: str = "en") -> Case:
    return Case(id="reply-quality", external_thread_id="quality-thread",
                applicant_contact="fictional@example.test", customer_language=language,
                policy_version=load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")).version)


@pytest.mark.parametrize(("language", "background", "question"), [
    ("zh", "我朋友的材料暂时不准备了。", "我的中文文件需要翻译吗？"),
    ("en", "My friend has delayed her application.", "Do my Chinese documents need a translation?"),
    ("zh", "我自己会去旅行，朋友不去。", "中文的证明要附译文吗？"),
    ("en", "I am funding the trip myself.", "Do I need a full translation of my Chinese letter?"),
])
def test_translation_does_not_borrow_translator_identity_from_independent_background(
    language: str, background: str, question: str,
) -> None:
    answers = grounded_customer_answers(background + "\n" + question, language, TODAY,
        semantic_questions=[CustomerQuestion(topic="translation", source_excerpt=question, confidence=0.99)])
    reply = "\n".join(answers)
    assert "GOV.UK:" in reply
    assert "仅凭是朋友" not in reply and "Who translated it" not in reply


@pytest.mark.parametrize(("language", "question", "expected"), [
    ("zh", "中文文件由我朋友翻译可以吗？", "不能判断译件是否合格"),
    ("en", "Can my friend translate my Chinese documents?", "cannot guarantee acceptance"),
])
def test_actual_translator_question_keeps_qualification(language: str, question: str, expected: str) -> None:
    answers = grounded_customer_answers(question, language, TODAY,
        semantic_questions=[CustomerQuestion(topic="translation", source_excerpt=question, confidence=0.99)])
    assert expected in "\n".join(answers)


@pytest.mark.parametrize(("language", "question"), [
    ("zh", "申请英国旅游签证前，需要先付机票和酒店的钱吗？"),
    ("en", "Must I buy flights and book a hotel for my visitor application?"),
])
def test_booking_answer_informs_without_instructing_preparation_to_start(language: str, question: str) -> None:
    answers = grounded_customer_answers(question, language, TODAY,
        semantic_questions=[CustomerQuestion(topic="booking", source_excerpt=question, confidence=0.99)])
    reply = "\n".join(answers)
    assert "GOV.UK:" in reply
    assert "我们先整理" not in reply and "We can first" not in reply


def test_validated_restart_is_a_reason_for_relevant_guidance_but_not_repeated_guidance() -> None:
    case = example()
    case.latest_customer_message = "Pick up where we left it."
    case.latest_preparation_action = "resume"
    case.profile.visit_purpose = "tourism"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    guidance = preparation_guidance(case, TODAY, set())
    assert any("enrolment" in text and "bank statements" in text for _, text in guidance)
    assert preparation_guidance(case, TODAY, {topic for topic, _ in guidance}) == []
    case.preparation_paused = True
    assert preparation_guidance(case, TODAY, set()) == []


@pytest.mark.parametrize("language", ["en", "zh"])
def test_retained_pause_is_not_worded_as_a_new_customer_decision(language: str) -> None:
    case = example(language)
    case.preparation_paused = True
    text = preparation_control_receipt(case)
    assert text is not None
    assert ("keep the preparation on hold" if language == "en" else "保持暂停") in text


@pytest.mark.parametrize("budget", [1750, 1900, 20000])
def test_budget_receipt_uses_natural_currency_not_internal_field_title(budget: int) -> None:
    case = example()
    case.latest_changes = {"estimated_trip_cost_gbp": str(budget)}
    text = change_acknowledgement(case)
    assert text is not None and f"your total trip budget to £{budget:,}" in text
    assert "Estimated Trip Cost" not in text


def test_human_review_still_acknowledges_independent_dob_correction_without_boolean_label() -> None:
    case = example()
    case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    case.preparation_paused = True
    case.latest_changes = {"date_of_birth": "1998-05-21", "has_serious_history": "True"}
    case.profile.has_serious_history = True
    reply = deterministic_fallback_message(case, "blocked")
    assert "21 May 1998" in reply and "human adviser" in reply
    assert "Has Serious History" not in reply and "True" not in reply
    assert "?" not in reply and "on hold" in reply
