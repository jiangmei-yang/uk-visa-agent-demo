"""Current, source-bounded process guidance cannot be invented or erased by a model."""

from datetime import date

import pytest

from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.customer_questions import (
    AFTER_APPLY_SOURCE,
    APPLICATION_SOURCE,
    CANCEL_SOURCE,
    CONTACT_UKVI_SOURCE,
    PROCESSING_TIMES_SOURCE,
    ROUTE_CHECK_SOURCE,
    STANDARD_VISITOR_SOURCE,
    STUDENT_SOURCE,
    VAC_SOURCE,
    grounded_customer_answer_plan,
    grounded_customer_answers,
)

TODAY = date(2026, 9, 5)


def question(topic: str, excerpt: str) -> CustomerQuestion:
    return CustomerQuestion(topic=topic, source_excerpt=excerpt, confidence=.99)


@pytest.mark.parametrize(("body", "expected", "label"), [
    (
        "我是否符合 Standard Visitor 的一般资格要求？",
        STANDARD_VISITOR_SOURCE,
        "GOV.UK: Standard Visitor 一般要求 —",
    ),
    (
        "生物信息预约要做什么？在哪里预约？",
        VAC_SOURCE,
        "GOV.UK: 查询签证申请中心 —",
    ),
    (
        "提交以后怎么查进度，怎么收到结果？",
        AFTER_APPLY_SOURCE,
        "GOV.UK: 递交后与决定通知 —",
    ),
])
def test_obvious_current_process_question_has_deterministic_reviewed_answer(
    body: str,
    expected: str,
    label: str,
) -> None:
    answer = "\n".join(grounded_customer_answers(body, "zh", TODAY))

    assert expected in answer
    assert label in answer


@pytest.mark.parametrize(("body", "topic", "source"), [
    ("我是否符合 Standard Visitor 的一般资格要求？", "eligibility_overview", STANDARD_VISITOR_SOURCE),
    ("生物信息预约要做什么？", "biometrics", APPLICATION_SOURCE),
    ("提交后怎么查申请进度？", "after_apply", PROCESSING_TIMES_SOURCE),
])
def test_model_unsupported_cannot_erase_an_obvious_reviewed_question(
    body: str,
    topic: str,
    source: str,
) -> None:
    plan = grounded_customer_answer_plan(
        body,
        "zh",
        TODAY,
        semantic_questions=[question("unsupported", body)],
    )
    answer = "\n".join(plan.answers)

    assert source in answer
    assert "没有核验过的依据" not in answer
    assert topic in plan.selected_topics


def test_after_apply_change_and_cancel_are_bounded_separate_actions() -> None:
    body = "申请已经递交但填错了，怎么修改或取消？"
    answer = "\n".join(grounded_customer_answers(body, "zh", TODAY))

    assert CONTACT_UKVI_SOURCE in answer and CANCEL_SOURCE in answer
    assert "不要假定再发一封说明就会自动改表" in answer
    assert "不是撤回就一定退费" in answer
    assert "UKVI 收到撤回后不能再叫停撤回" in answer


@pytest.mark.parametrize(("body", "proposal"), [
    ("我已经完成生物信息预约。", "biometrics"),
    ("I already attended my biometrics appointment.", "biometrics"),
])
def test_completed_action_statement_is_not_turned_into_process_advice(
    body: str,
    proposal: str,
) -> None:
    language = "en" if body.startswith("I ") else "zh"
    answers = grounded_customer_answers(
        body,
        language,
        TODAY,
        semantic_questions=[question(proposal, body)],
    )

    assert answers == []


@pytest.mark.parametrize("body", [
    "收到\n> 我是否符合 Standard Visitor 资格？",
    "朋友问“生物信息预约要做什么？”我没有这个问题。",
])
def test_quoted_or_reported_question_is_not_current(body: str) -> None:
    answer = "\n".join(grounded_customer_answers(body, "zh", TODAY))

    assert APPLICATION_SOURCE not in answer
    assert STANDARD_VISITOR_SOURCE not in answer
    assert VAC_SOURCE not in answer


def test_third_party_biometrics_does_not_get_own_case_process_answer() -> None:
    body = "我替朋友问，她的 Standard Visitor 生物信息预约在哪里？"
    answer = "\n".join(grounded_customer_answers(
        body,
        "zh",
        TODAY,
        semantic_questions=[question("biometrics", body)],
    ))

    assert APPLICATION_SOURCE not in answer and VAC_SOURCE not in answer


def test_other_route_biometrics_gets_route_boundary_not_visitor_instructions() -> None:
    body = "学生签证录指纹在哪里？"
    answer = "\n".join(grounded_customer_answers(
        body,
        "zh",
        TODAY,
        semantic_questions=[question("biometrics", body)],
    ))

    assert ROUTE_CHECK_SOURCE in answer
    assert APPLICATION_SOURCE not in answer and VAC_SOURCE not in answer
    assert STUDENT_SOURCE not in answer


def test_hypothetical_cancellation_is_not_bound_to_current_case() -> None:
    body = "如果我以后递交了，怎么取消？"
    answer = "\n".join(grounded_customer_answers(
        body,
        "zh",
        TODAY,
        semantic_questions=[question("after_apply", body)],
    ))

    assert CANCEL_SOURCE not in answer


def test_current_no_link_preference_keeps_answer_but_removes_urls() -> None:
    body = "不用发链接。提交后怎么查进度？"
    answer = "\n".join(grounded_customer_answers(body, "zh", TODAY))

    assert "通常在 3 周内收到决定" in answer
    assert "https://" not in answer and "GOV.UK:" not in answer


@pytest.mark.parametrize(("topic", "body"), [
    ("eligibility_overview", "我是否符合 Standard Visitor 的一般资格要求？"),
    ("biometrics", "生物信息预约要做什么？"),
    ("after_apply", "提交后怎么查进度？"),
])
def test_process_guidance_expires_without_leaking_old_details(topic: str, body: str) -> None:
    answer = "\n".join(grounded_customer_answers(
        body,
        "zh",
        date(2026, 10, 5),
        semantic_questions=[question(topic, body)],
    ))

    assert "复核最新 GOV.UK" in answer
    assert "https://" not in answer and "3 周" not in answer


def test_student_application_page_is_specific_but_does_not_change_visitor_scope() -> None:
    body = "Where can I get the official Student visa application form?"
    answer = "\n".join(grounded_customer_answers(
        body,
        "en",
        TODAY,
        semantic_questions=[question("unsupported", body)],
    ))

    assert STUDENT_SOURCE in answer and ROUTE_CHECK_SOURCE in answer
    assert "Student visa official page" in answer
    assert "cannot be taken from this case's Standard Visitor workflow" in answer
    assert APPLICATION_SOURCE not in answer


@pytest.mark.parametrize(("body", "language", "required"), [
    (
        "我是自雇，个人账户和公司账户流水应该交哪个，要多少个月？",
        "zh",
        ("不宜只按“二选一”", "个人账户", "对应转账", "没有统一规定"),
    ),
    (
        "I am self-employed. Which statements should I use, my personal account or company account, and how many months?",
        "en",
        ("rather than being a simple either-or choice", "Personal-account", "matching transfers", "does not set one fixed number"),
    ),
])
def test_self_employed_accounts_are_explained_by_purpose_not_a_fixed_period(
    body: str,
    language: str,
    required: tuple[str, ...],
) -> None:
    answer = "\n".join(grounded_customer_answers(body, language, TODAY))

    assert all(part in answer for part in required)
    assert "#demonstrating-personal-circumstances" in answer
    assert "must provide 6 months" not in answer
