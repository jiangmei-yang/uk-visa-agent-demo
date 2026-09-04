"""Synthetic content-gap regressions, independent of model evaluation corpora.

These tests intentionally specify the missing advice rather than adapting to the
current output. They use reviewed answer selection and one guarded local workflow;
no provider API, mailbox, delivery, holdout data or persistent pause state is used.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate, QuestionDeferral
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    SOURCE,
    grounded_customer_answers,
    validated_customer_questions,
)
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 4)
POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")


def bank_answer(body: str, language: str, *, excerpt: str | None = None) -> str:
    """Supply a grounded topic so these are answer-content, not classifier tests."""
    questions = [CustomerQuestion(
        topic="bank_period", source_excerpt=body if excerpt is None else excerpt, confidence=0.99,
    )]
    assert validated_customer_questions(body, questions) == questions
    answers = grounded_customer_answers(body, language, TODAY, semantic_questions=questions)
    assert len(answers) == 1
    assert SOURCE in answers[0]
    return answers[0]


def assert_no_acceptance_sufficiency_or_approval_promise(answer: str) -> None:
    claims = (
        r"\b(?:will|is|are)\s+(?:definitely\s+|automatically\s+)?accepted\b|"
        r"\bguaranteed\s+(?:acceptance|approval|success)\b|"
        r"\b(?:evidence|documents|records)\s+(?:is|are)\s+sufficient\b|"
        r"(?:一定|保证|肯定).{0,8}(?:接受|通过|获批)|"
        r"(?:材料|证据|记录)(?:已经|已)?(?:足够|充分)(?:获批|通过|了)?"
    )
    for clause in re.split(r"[。.!?;；\n]", answer):
        for match in re.finditer(claims, clause, re.I):
            prefix = clause[:match.end()]
            assert re.search(r"\b(?:not|no|cannot|can't)\b|不|不能|无法|尚未", prefix, re.I), (
                f"Unqualified acceptance, sufficiency or approval promise: {clause}"
            )


@pytest.mark.parametrize(("language", "body"), [
    ("en", "The bank statements will support my visitor application, so where can I obtain them?"),
    ("zh", "这次访问申请会用到银行流水，它们要去哪里拿？"),
])
def test_statement_then_pronoun_obtaining_question_gets_practical_collection_advice(
    language: str, body: str,
) -> None:
    answer = bank_answer(body, language)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    if language == "zh":
        assert re.search(r"网银|网上银行|银行\s*App|银行应用", answer, re.I), answer
        assert re.search(r"(?:向|找|联系)银行.{0,12}(?:索取|申请|获取|提供)|银行.{0,8}(?:索取|开具)", answer), answer
    else:
        assert re.search(r"online banking|bank(?:ing)? (?:app|portal)", answer, re.I), answer
        assert re.search(r"(?:ask|contact|request).{0,45}(?:bank|statements)", answer, re.I), answer


@pytest.mark.parametrize(("language", "body"), [
    ("en", "My travel savings are in two accounts in my own name; how should I organise the records to show that I can use this money?"),
    ("zh", "旅行的钱分在我自己名下的两个账户里，要怎样整理记录才能看清这些钱可供我使用？"),
])
def test_two_personal_accounts_receive_ownership_source_access_and_organisation_advice(
    language: str, body: str,
) -> None:
    answer = bank_answer(body, language)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    if language == "zh":
        assert re.search(r"账户持有人|户名|账户.{0,12}(?:本人|姓名|名下|归属)", answer), answer
        assert re.search(r"资金来源|钱.{0,6}(?:来源|哪里来)", answer), answer
        assert re.search(r"可用|可以使用|能够使用|可支取|可动用", answer), answer
        assert re.search(
            r"(?:分别|各自|每个|两个).{0,30}(?:对账单|流水|交易记录|账户记录)|"
            r"(?:对账单|流水|记录).{0,20}(?:分别|逐一|按账户)", answer,
        ), answer
    else:
        assert re.search(r"account holder|ownership|accounts?.{0,20}(?:your|applicant's) name", answer, re.I), answer
        assert re.search(r"source|origin|come from", answer, re.I), answer
        assert re.search(r"access|availab|withdraw|use the (?:money|funds)", answer, re.I), answer
        assert re.search(
            r"(?:each|both|separate|account-by-account).{0,55}(?:statements?|records?)|"
            r"(?:statements?|records?).{0,55}(?:each|both|separately|per account)",
            answer, re.I,
        ), answer


@pytest.mark.parametrize(("language", "current_request", "inactive"), [
    ("en", "Which records explain that my savings are available?",
     'My previous message said "Where can I obtain them?"'),
    ("zh", "哪些记录能说明我的存款可以使用？", "以前的消息里写了“它们要去哪里拿？”"),
    ("en", "Which records explain that my savings are available?",
     "Do not explain where I can obtain them."),
    ("zh", "哪些记录能说明我的存款可以使用？", "不要解释它们在哪里获取。"),
])
def test_quoted_or_declined_statement_collection_is_not_a_fresh_subquestion(
    language: str, current_request: str, inactive: str,
) -> None:
    answer = bank_answer(current_request + "\n" + inactive, language, excerpt=current_request)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert not re.search(r"网银|银行\s*App|向银行索取|online banking|bank app|request statements", answer, re.I), answer


@pytest.mark.parametrize(("language", "decline", "current_request"), [
    ("en", "I am not asking how many months of statements I need.", "Where can I obtain them?"),
    ("zh", "不用解释银行流水要几个月。", "它们去哪里拿？"),
])
def test_declined_period_question_does_not_cancel_an_independent_collection_request(
    language: str, decline: str, current_request: str,
) -> None:
    answer = bank_answer(decline + "\n" + current_request, language, excerpt=current_request)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert re.search(r"网银|银行\s*App|online banking|bank app", answer, re.I), answer
    assert "没有统一规定" not in answer and "fixed number of months" not in answer, answer


@pytest.mark.parametrize(("language", "bank_request", "other_request"), [
    ("en", "Which records show that my savings are available for the visit?",
     "Where can I obtain my employment letter?"),
    ("zh", "哪些记录能说明我的存款可以用于这次旅行？", "我的在职证明要去哪里拿？"),
])
def test_obtaining_an_independent_document_does_not_trigger_bank_statement_collection_advice(
    language: str, bank_request: str, other_request: str,
) -> None:
    body = bank_request + "\n" + other_request
    questions = [
        CustomerQuestion(topic="bank_period", source_excerpt=bank_request, confidence=0.99),
        CustomerQuestion(topic="unsupported", source_excerpt=other_request, confidence=0.99),
    ]
    assert validated_customer_questions(body, questions) == questions
    answers = grounded_customer_answers(body, language, TODAY, semantic_questions=questions)
    bank = next(answer for answer in answers if re.search(r"账户持有人|account holder", answer, re.I))
    assert_no_acceptance_sufficiency_or_approval_promise(bank)
    assert not re.search(r"网银|银行\s*App|online banking|bank app", bank, re.I), bank


@pytest.mark.parametrize(("language", "period_question", "collection_question"), [
    ("en", "What period should my bank statements cover?",
     "Where can I request the bank statements?"),
    ("zh", "银行流水的时间跨度怎么选？", "银行流水能在网银里拿到吗？"),
])
def test_deduplicated_bank_topic_keeps_an_independent_explicit_statement_collection_clause(
    language: str, period_question: str, collection_question: str,
) -> None:
    body = period_question + "\n" + collection_question
    proposals = [CustomerQuestion(topic="bank_period", source_excerpt=excerpt, confidence=0.99)
                 for excerpt in (period_question, collection_question)]
    accepted = validated_customer_questions(body, proposals)
    assert accepted == proposals[:1]  # Production deduplicates the ordinary topic.
    answers = grounded_customer_answers(body, language, TODAY, semantic_questions=accepted)
    assert len(answers) == 1 and SOURCE in answers[0]
    answer = answers[0]
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert re.search(r"没有统一规定|fixed number of months", answer, re.I), answer
    assert re.search(r"网银|银行\s*App|online banking|bank app", answer, re.I), answer
    assert re.search(r"向银行索取|request statements from your bank", answer, re.I), answer


@pytest.mark.parametrize(("language", "bank_question", "application_question"), [
    ("en", "What period should my bank statements cover?",
     "Where can I get the official visitor visa application form?"),
    ("zh", "银行流水的时间跨度怎么选？", "英国访问签证的申请表在哪里获取？"),
])
def test_application_form_collection_does_not_cross_into_bank_collection_advice(
    language: str, bank_question: str, application_question: str,
) -> None:
    body = bank_question + "\n" + application_question
    proposals = [
        CustomerQuestion(topic="bank_period", source_excerpt=bank_question, confidence=0.99),
        CustomerQuestion(topic="application", source_excerpt=application_question, confidence=0.99),
    ]
    assert validated_customer_questions(body, proposals) == proposals
    answers = grounded_customer_answers(body, language, TODAY, semantic_questions=proposals)
    assert len(answers) == 2
    bank = next(answer for answer in answers if SOURCE in answer)
    application = next(answer for answer in answers if APPLICATION_SOURCE in answer)
    assert "Apply now" in application
    assert re.search(r"没有统一规定|fixed number of months", bank, re.I), bank
    assert not re.search(r"网银|银行\s*App|online banking|bank app", bank, re.I), bank
    assert_no_acceptance_sufficiency_or_approval_promise(bank)


@pytest.mark.parametrize(("language", "current_request", "inactive"), [
    ("en", "Which records explain that my savings are available?",
     'Someone else wrote "I have two accounts"; I only have one.'),
    ("zh", "哪些记录能说明我的存款可以使用？", "别人写的是“我有两个账户”，我只有一个。"),
])
def test_quoted_multiple_accounts_do_not_become_the_applicants_current_financial_facts(
    language: str, current_request: str, inactive: str,
) -> None:
    answer = bank_answer(current_request + "\n" + inactive, language, excerpt=current_request)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert "As the money is in different accounts" not in answer
    assert "既然钱在不同账户" not in answer


@pytest.mark.parametrize(("language", "body"), [
    ("en", "I do not have two accounts; how can I show the money in my sole account is available?"),
    ("zh", "我不是有两个账户，我只有一个账户，要怎样说明里面的钱可以使用？"),
])
def test_negated_multiple_accounts_do_not_become_positive_financial_facts(
    language: str, body: str,
) -> None:
    answer = bank_answer(body, language)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert "As the money is in different accounts" not in answer, answer
    assert "既然钱在不同账户" not in answer, answer


@pytest.mark.parametrize(("language", "body"), [
    ("en", "How should the records show that my funds can cover the costs of this visit?"),
    ("zh", "怎样用账户记录说明这笔钱可以覆盖旅行费用？"),
])
def test_covering_trip_costs_is_not_a_question_about_statement_date_coverage(
    language: str, body: str,
) -> None:
    answer = bank_answer(body, language)
    assert_no_acceptance_sufficiency_or_approval_promise(answer)
    assert "没有统一规定" not in answer and "fixed number of months" not in answer, answer


class DateCorrectionOnlyLLM:
    version = "fictional-dob-content-gap"

    def __init__(self, excerpt: str) -> None:
        self.excerpt = excerpt
        self.calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.calls += 1
        assert self.excerpt in event.body
        return CasePatch(updates=[FactUpdate(
            field="date_of_birth", value="1993-02-18",
            source_excerpt=self.excerpt, confidence=0.99,
        )], ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


@pytest.mark.parametrize(("language", "body", "excerpt"), [
    ("en", "A quick correction: my birth date is 18 February 1993. That is all for now.",
     "my birth date is 18 February 1993"),
    ("zh", "只更正一下，我的出生日期是1993年2月18日。暂时就这些。", "我的出生日期是1993年2月18日"),
])
def test_dob_only_correction_with_quiet_close_does_not_restart_application_or_material_guidance(
    tmp_path: Path, language: str, body: str, excerpt: str,
) -> None:
    seed = Case(
        id="fictional-dob-gap-case", external_thread_id="fictional-dob-gap-thread",
        applicant_contact="fictional-dob-gap@example.test", primary_channel="gmail",
        customer_language=language, policy_version=load_policy(POLICY_PATH).version,
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )
    seed.profile.full_name = "Fictional Applicant"
    seed.profile.date_of_birth = date(1993, 2, 17)
    seed.profile.visit_purpose = "tourism"
    seed.profile.nationality_country = "China"
    seed.profile.application_country = "Hong Kong"
    seed.profile.occupation_status = "student"
    seed.profile.funding_source = "self"
    event = InboundEvent(
        id="fictional-dob-gap-correction", external_thread_id=seed.external_thread_id,
        sender=seed.applicant_contact, subject="A small correction", body=body, channel="gmail",
        received_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )
    model = DateCorrectionOnlyLLM(excerpt)
    store = SQLiteStore(tmp_path / "dob-content-gap.db")
    try:
        store.save_case(seed)
        workflow = WorkflowService(store, load_policy(POLICY_PATH), model, today_provider=lambda: TODAY)
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked" and model.calls == 1
        assert case.profile.date_of_birth == date(1993, 2, 18)
        assert case.latest_changes == {"date_of_birth": "1993-02-18"}
        assert case.latest_received_facts == {} and case.customer_question_topics == []
        assert case.status == CaseStatus.DRAFT
        assert not case.profile_confirmed and not case.final_summary_confirmed
        assert case.delivery_path is None
        evidence = case.active_evidence("date_of_birth")
        assert len(evidence) == 1 and evidence[0].source_event_id == event.id
        assert evidence[0].source_excerpt == excerpt and not evidence[0].confirmed
        row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
        assert row["status"] == "PENDING"
        persisted = store.get_case(case.id)
        assert persisted is not None
        # The automatic sender's default reviewed renderer, without a send side effect.
        reply = deterministic_fallback_message(persisted, plan)
        assert reply == row["payload"]
        assert re.search(r"出生日期|生日|birth|DOB", reply, re.I), reply

        # This is a real correction acknowledgement, not an empty/no-fact quiet turn.
        assert "Apply now" not in reply and APPLICATION_SOURCE not in reply, reply
        assert not re.search(
            r"材料方面|可以先准备|接下来还需要这些材料|在读证明|银行流水|"
            r"you can start with|letter confirming your enrolment|bank statements|"
            r"we'll also need these documents", reply, re.I,
        ), reply
        assert not case.proactive_guidance_offered
    finally:
        store.close()


class FixedSyntheticPatchLLM:
    version = "fictional-information-gap-boundaries"

    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        return self.patch.model_copy(deep=True)

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


def synthetic_reviewed_turn(
    tmp_path: Path, language: str, body: str, patch: CasePatch,
) -> tuple[Case, str]:
    seed = Case(
        id="fictional-boundary-case", external_thread_id="fictional-boundary-thread",
        applicant_contact="fictional-boundary@example.test", primary_channel="gmail",
        customer_language=language, policy_version=load_policy(POLICY_PATH).version,
    )
    seed.profile.full_name = "Fictional Applicant"
    seed.profile.date_of_birth = date(1993, 2, 17)
    seed.profile.visit_purpose = "tourism"
    seed.profile.nationality_country = "China"
    seed.profile.application_country = "Hong Kong"
    seed.profile.occupation_status = "student"
    seed.profile.funding_source = "self"
    event = InboundEvent(
        id="fictional-boundary-turn", external_thread_id=seed.external_thread_id,
        sender=seed.applicant_contact, subject="A follow-up", body=body, channel="gmail",
        received_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )
    store = SQLiteStore(tmp_path / "information-boundary.db")
    try:
        store.save_case(seed)
        workflow = WorkflowService(
            store, load_policy(POLICY_PATH), FixedSyntheticPatchLLM(patch), today_provider=lambda: TODAY,
        )
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked"
        assert not workflow.llm.last_extraction_fallback
        assert not case.profile_confirmed and not case.final_summary_confirmed
        assert case.delivery_path is None and case.status == CaseStatus.DRAFT
        reply = deterministic_fallback_message(case, plan)
        row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
        assert row["status"] == "PENDING" and reply == row["payload"]
        return case, reply
    finally:
        store.close()


@pytest.mark.parametrize(("language", "correction", "resume"), [
    ("en", "My birthday should be 18 February 1993.",
     "Please continue preparing my application and tell me the next step."),
    ("zh", "生日请改成1993年2月18日。", "请继续准备申请，告诉我下一步怎么做。"),
])
def test_identity_correction_does_not_suppress_an_independent_explicit_continue_request(
    tmp_path: Path, language: str, correction: str, resume: str,
) -> None:
    patch = CasePatch(updates=[FactUpdate(
        field="date_of_birth", value="1993-02-18", source_excerpt=correction, confidence=0.99,
    )], ambiguities=[])
    case, reply = synthetic_reviewed_turn(tmp_path, language, correction + "\n" + resume, patch)
    assert case.latest_changes == {"date_of_birth": "1993-02-18"}
    assert case.profile.date_of_birth == date(1993, 2, 18)
    assert case.proactive_guidance_offered and "Apply now" in reply
    assert APPLICATION_SOURCE in reply


@pytest.mark.parametrize(("language", "body"), [
    ("en", "My travel dates are still undecided; that is the only update for now."),
    ("zh", "旅行日期还没定，暂时只补充这一点。"),
])
def test_date_deferral_receipt_does_not_claim_other_preparation_is_continuing(
    tmp_path: Path, language: str, body: str,
) -> None:
    patch = CasePatch(updates=[], ambiguities=[], question_deferrals=[
        QuestionDeferral(field=field, source_excerpt=body, confidence=0.99)
        for field in ("planned_arrival_date", "planned_departure_date")
    ])
    case, reply = synthetic_reviewed_turn(tmp_path, language, body, patch)
    assert case.latest_changes == {} and case.latest_received_facts == {}
    assert set(case.latest_deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None
    assert not case.proactive_guidance_offered and "Apply now" not in reply
    assert re.search(r"日期先留空|leave the dates open", reply, re.I), reply
    assert not re.search(
        r"先整理其他|继续准备|继续整理|collect the other details first|"
        r"continue (?:preparing|preparation)|carry on preparing", reply, re.I,
    ), reply
