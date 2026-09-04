"""Natural application-link requests remain answerable after guidance was sent."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    SOURCE,
    grounded_customer_answers,
)
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("body", [
    "申请网页在哪？",
    "请把签证申请网页发我一下。",
    "英国签证申请网站在哪，怎么申请？",
])
def test_explicit_application_page_variants_are_answered(body: str) -> None:
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4))
    assert len(answers) == 1
    assert APPLICATION_SOURCE in answers[0]
    assert "Apply now" in answers[0]


@pytest.mark.parametrize(("body", "language"), [
    ("网址发我一下，怎么申请？", "zh"),
    ("再发我申请链接，并介绍一下申请流程。", "zh"),
    ("Could you send me that link again and explain how I apply?", "en"),
])
def test_requesting_process_again_still_receives_explanation(body: str, language: str) -> None:
    answers = grounded_customer_answers(
        body, language, date(2026, 9, 4), sent_application_guidance=True,
    )
    assert len(answers) == 1
    assert APPLICATION_SOURCE in answers[0] and "Apply now" in answers[0]
    assert ("在线填写申请" in answers[0] and "预约签证中心" in answers[0]) if language == "zh" else ("Apply online" in answers[0])


@pytest.mark.parametrize(("body", "language"), [
    ("网址发我一下", "zh"),
    ("那个链接再发我一遍。", "zh"),
    ("请把之前的网址发给我。", "zh"),
    ("麻烦再发我一下那个网页", "zh"),
    ("Could you send me that link again?", "en"),
    ("Please resend me the website.", "en"),
])
def test_short_link_followup_requires_previously_sent_application_guidance(
    body: str, language: str,
) -> None:
    assert grounded_customer_answers(body, language, date(2026, 9, 4)) == []
    answers = grounded_customer_answers(
        body, language, date(2026, 9, 4), sent_application_guidance=True,
    )
    assert len(answers) == 1
    assert APPLICATION_SOURCE in answers[0]
    assert "Apply now" in answers[0]
    assert len(answers[0].splitlines()) == 2
    assert "流程是" not in answers[0] and "Apply online" not in answers[0]
    assert "不代表" not in answers[0] and "does not yet confirm" not in answers[0]


@pytest.mark.parametrize("body", [
    "不用发申请网页。",
    "网址不要再发我了。",
    "那个链接不用发我。",
    "收到\n> 网址发我一下",
    "他说“申请网页在哪？”，我不问这个。",
    "学校网址发我一下。",
    "酒店的链接再发我一遍。",
    "网址发我一下，是学校那个。",
    "Send me the school website.",
    "Could you send me that hotel link again?",
    "No links please.",
    "That link worked, thanks.",
])
def test_previous_guidance_does_not_override_decline_quote_or_another_site(body: str) -> None:
    assert grounded_customer_answers(
        body, "zh", date(2026, 9, 4), sent_application_guidance=True,
    ) == []


def test_contextual_link_followup_still_obeys_source_review_window() -> None:
    answers = grounded_customer_answers(
        "网址发我一下", "zh", date(2026, 10, 5), sent_application_guidance=True,
    )
    assert len(answers) == 1
    assert "复核" in answers[0]
    assert APPLICATION_SOURCE not in answers[0]


@pytest.mark.parametrize(("body", "language"), [
    ("签证费是多少钱？", "zh"),
    ("申请费多少", "zh"),
    ("申请签证要多少钱", "zh"),
    ("How much is the visitor visa fee?", "en"),
    ("What does a visa application cost?", "en"),
])
def test_fee_answer_is_current_conditional_and_not_total_service_cost(body: str, language: str) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert "£135" in answer and APPLICATION_SOURCE in answer
    assert "6" in answer and "Standard Visitor" in answer
    assert ("不包含" in answer) if language == "zh" else ("cost extra" in answer)


@pytest.mark.parametrize(("body", "language"), [
    ("银行流水要提供几个月的？", "zh"),
    ("流水需要几个月", "zh"),
    ("三个月流水够吗？", "zh"),
    ("银行对账单要覆盖多长时间？", "zh"),
    ("How many months of bank statements do I need?", "en"),
    ("How far back should the bank statements go?", "en"),
])
def test_bank_statement_answer_does_not_invent_fixed_month_requirement(body: str, language: str) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert SOURCE in answer
    if language == "zh":
        assert "没有统一规定" in answer
        assert "资金来源" in answer and "可以使用" in answer
        assert "不能只凭" in answer
    else:
        assert "does not set one fixed number" in answer
        assert "come from" in answer and "access" in answer
        assert "months alone" in answer
    assert not any(phrase in answer for phrase in ["3 个月", "6 个月", "3 months", "6 months"])


@pytest.mark.parametrize("body", [
    "签证费多少？", "银行流水要提供几个月？",
])
def test_fees_and_bank_period_require_current_reviewed_source(body: str) -> None:
    answers = grounded_customer_answers(body, "zh", date(2026, 10, 5))
    assert len(answers) == 1
    assert "复核" in answers[0]
    assert "135" not in answers[0] and "没有统一规定" not in answers[0]


@pytest.mark.parametrize("body", [
    "学生签证费多少？",
    "工作签证要几个月的银行流水？",
    "How much is a transit visa?",
])
def test_other_routes_do_not_receive_visitor_fee_or_bank_period_answer(body: str) -> None:
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4))
    assert len(answers) == 1
    assert "路线" in answers[0]
    assert "135" not in answers[0] and "没有统一规定" not in answers[0]


@pytest.mark.parametrize("body", [
    "不用告诉我签证费多少。",
    "不想知道银行流水要提供几个月。",
    "谢谢\n> 银行流水需要几个月？",
    "朋友问‘签证费多少？’，不是我问的。",
    "Don't explain the visa fee. Don't explain how many months of bank statements I need.",
    "I have paid the visa fee and collected bank statements.",
])
def test_fee_and_bank_mentions_quotes_and_declines_are_not_fresh_requests(body: str) -> None:
    assert grounded_customer_answers(body, "zh", date(2026, 9, 4)) == []


@pytest.mark.parametrize(("body", "language", "reviewed_wording"), [
    ("必须先买机票订酒店吗？", "zh", "证明价值较低"),
    ("Must I buy flights and book hotels first?", "en", "less useful evidence"),
])
def test_booking_answer_matches_source_body_without_inventing_absolute_ban(
    body: str, language: str, reviewed_wording: str,
) -> None:
    answers = grounded_customer_answers(body, language, date(2026, 9, 4))
    assert len(answers) == 1
    answer = answers[0]
    assert reviewed_wording in answer and SOURCE in answer
    assert not any(phrase in answer for phrase in ["不应作为证据", "禁止", "documents not to use", "must not"])
    assert ("过境除外" in answer) if language == "zh" else ("except transit" in answer)


class LocalExtractedFacts:
    version = "no-network-adviser-query-test"

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        return CasePatch(updates=[], ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"offline-adviser-query-{len(self.calls)}"}


def seed_student(path: Path) -> Case:
    case = Case(
        id="fictional-query-case", external_thread_id="fictional-query-thread",
        applicant_contact="fictional@example.test", primary_channel="gmail",
        policy_version="test", customer_language="zh",
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    store = SQLiteStore(path)
    try:
        store.save_case(case)
    finally:
        store.close()
    return case


def captured_turn(
    path: Path, seed: Case, adapter: CaptureGmail, text: str, turn: int, *, send: bool = True,
) -> tuple[Case, str, list[dict[str, Any]]]:
    event = InboundEvent(
        id=f"fictional-query-{turn}", external_thread_id=seed.external_thread_id,
        sender=seed.applicant_contact, subject="虚构申请咨询", body=text, channel="gmail",
        received_at=datetime(2026, 9, 4, tzinfo=UTC) + timedelta(minutes=turn),
    )
    store = SQLiteStore(path)
    try:
        workflow = WorkflowService(
            store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
            LocalExtractedFacts(), today_provider=lambda: date(2026, 9, 4),
        )
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked"
        if send:
            sender = AutomaticGmailReplySender(adapter, store, seed.applicant_contact)
            sender.withhold_obsolete_unsent()
            dispatcher = OutboxDispatcher(
                store, sender, channel="gmail", allowed_message_types=("blocked",),
            )
            sent = dispatcher.dispatch_due(event.received_at)
            assert len(sent) == 1 and sent[0].status == "SENT"
            assert dispatcher.dispatch_due(event.received_at) == []
        rows = store.list_outbox()
        body = next(row["payload"] for row in rows if row["event_id"] == event.id)
        if send:
            assert body == adapter.calls[-1]["body"]
        assert workflow.process(event)[1] is True
        return case, body, rows
    finally:
        store.close()


@pytest.mark.parametrize(("followup", "answer_fragment"), [
    ("申请网页在哪？", APPLICATION_SOURCE),
    ("网址发我一下", APPLICATION_SOURCE),
    ("签证费是多少钱？", "£135"),
    ("银行流水要提供几个月的？", "没有统一规定"),
    ("材料要准备些什么？", "有效护照或旅行证件"),
    ("需要提供什么资料？", "在读证明"),
])
def test_real_reply_pipeline_answers_followup_before_resuming_intake(
    tmp_path: Path, followup: str, answer_fragment: str,
) -> None:
    path = tmp_path / "fictional-query.db"
    seed = seed_student(path)
    adapter = CaptureGmail()
    _, first, _ = captured_turn(path, seed, adapter, "我在读书，自己付钱，日期没定。我准备开始整理申请资料。", 1)
    assert APPLICATION_SOURCE in first and "在读证明" in first
    case, body, _ = captured_turn(path, seed, adapter, followup, 2)
    assert answer_fragment in body
    assert "等你方便补充资料" not in body
    assert "你的出生日期是什么" not in body and "方便告诉我护照上的姓名" not in body
    assert "计划哪天" not in body
    assert case.question_plan == []
    assert len(adapter.calls) == 2
    assert not case.profile_confirmed and not case.final_summary_confirmed


@pytest.mark.parametrize("send_first", [True, False])
def test_short_followup_uses_sent_context_not_unsent_draft(tmp_path: Path, send_first: bool) -> None:
    path = tmp_path / "sent-context.db"
    seed = seed_student(path)
    adapter = CaptureGmail()
    captured_turn(path, seed, adapter, "我在读书，自己付钱，日期没定。我准备开始整理申请资料。", 1, send=send_first)
    case, body, rows = captured_turn(path, seed, adapter, "网址发我一下", 2)
    if send_first:
        assert APPLICATION_SOURCE in body
        # Explicit short follow-up is resolved against delivered context; no fresh advice dump.
        assert len(case.customer_answers) == 1
        assert "在读证明" not in body
        assert rows[0]["status"] == "SENT"
    else:
        # Unsent guidance cannot resolve a bare link reference or trigger a brochure.
        assert case.customer_answers == []
        assert APPLICATION_SOURCE not in body
        assert "在读证明" not in body
        assert rows[0]["status"] == "FAILED"
    assert len(adapter.calls) == (2 if send_first else 1)


@pytest.mark.parametrize("followup", [
    "学校网址发我一下。",
    "不要发材料清单了。",
    "收到\n> 材料要准备些什么？",
    "他说“材料要准备些什么？”，我不问这个。",
])
def test_reply_pipeline_does_not_reinterpret_declined_quoted_or_unrelated_question(
    tmp_path: Path, followup: str,
) -> None:
    path = tmp_path / "unrelated-query.db"
    seed = seed_student(path)
    adapter = CaptureGmail()
    captured_turn(path, seed, adapter, "我在读书，自己付钱，日期没定。我准备开始整理申请资料。", 1)
    _, body, _ = captured_turn(path, seed, adapter, followup, 2)
    assert APPLICATION_SOURCE not in body
    assert "接下来还需要这些材料" not in body
