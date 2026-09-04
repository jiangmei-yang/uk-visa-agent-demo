"""Typed semantic questions reach reviewed Gmail replies without expanding authority.

All applicants and provider IDs below are fictional. The extraction adapter returns
explicit typed proposals; no network, PDF generation, or real email is involved.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, Evidence, InboundEvent, ProvenanceState
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate, QuestionDeferral
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import reply_items
from visa_agent.workflow.customer_questions import APPLICATION_SOURCE, SOURCE
from visa_agent.workflow.service import WorkflowService

POLICY_PATH = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
TODAY = date(2026, 9, 4)


class TypedQuestionModel:
    version = "fictional-semantic-intent-test"

    def __init__(self, patches: dict[str, CasePatch]) -> None:
        self.patches = patches
        self.calls: list[str] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.calls.append(event.id)
        return self.patches.get(event.id, CasePatch(updates=[], ambiguities=[]))

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"fictional-semantic-sent-{len(self.calls)}"}


def seed_case(store: SQLiteStore, language: str = "zh") -> Case:
    case = Case(
        id="fictional-semantic-case", external_thread_id="fictional-semantic-thread",
        applicant_contact="fictional-semantic@example.test", primary_channel="gmail",
        policy_version=load_policy(POLICY_PATH).version, customer_language=language,
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.profile.date_of_birth = date(1994, 6, 12)
    case.evidence = [Evidence(
        id="fictional-existing-birthday", fact_key="date_of_birth", value="1994-06-12",
        source_event_id="fictional-earlier-turn", source_excerpt="1994.6.12",
        extraction_method="bounded_structured_extraction", model_version="fictional-test",
        confidence=0.99, provenance_state=ProvenanceState.EXTRACTED_UNVERIFIED,
    )]
    evaluate_gate(case, load_policy(POLICY_PATH), TODAY)
    store.save_case(case)
    return case


def event_for(case: Case, event_id: str, body: str, offset: int = 0) -> InboundEvent:
    return InboundEvent(
        id=event_id, channel="gmail", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, subject="英国旅行咨询", body=body,
        received_at=datetime(2026, 9, 4, 12, tzinfo=UTC) + timedelta(minutes=offset),
        rfc_message_id=f"<{event_id}@example.test>",
    )


def patch_for(topic: str, text: str) -> CasePatch:
    return CasePatch(updates=[], ambiguities=[], customer_questions=[
        CustomerQuestion.model_validate({"topic": topic, "source_excerpt": text, "confidence": 0.99}),
    ])


def process_and_capture(
    store: SQLiteStore, model: TypedQuestionModel, adapter: CaptureGmail,
    event: InboundEvent, *, today: date = TODAY,
) -> tuple[Case, str]:
    workflow = WorkflowService(store, load_policy(POLICY_PATH), model, today_provider=lambda: today)
    case, duplicate, plan = workflow.process(event)
    assert not duplicate and plan == "blocked"
    sender = AutomaticGmailReplySender(adapter, store, event.sender)
    sender.withhold_obsolete_unsent()
    dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",))
    outcomes = dispatcher.dispatch_due(event.received_at)
    assert len(outcomes) == 1 and outcomes[0].status == "SENT"
    row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
    assert row["payload"] == adapter.calls[-1]["body"]
    assert row["reply_render_mode"] == "reviewed"
    before_repeat = len(adapter.calls)
    assert workflow.process(event)[1] is True
    assert dispatcher.dispatch_due(event.received_at) == []
    assert len(adapter.calls) == before_repeat
    assert model.calls.count(event.id) == 1
    return case, row["payload"]


def assert_no_added_authority(before: Case, after: Case, *, today: date = TODAY) -> None:
    assert after.profile == before.profile
    assert after.evidence == before.evidence
    assert after.documents == before.documents
    assert after.profile_confirmed == before.profile_confirmed
    assert after.final_summary_confirmed == before.final_summary_confirmed
    assert after.profile.route_confirmed_standard_visitor is False
    assert after.confirmation_fingerprint is None and after.confirmation_kind is None
    assert after.delivery_path is None
    assert after.status == CaseStatus.DRAFT
    policy = load_policy(POLICY_PATH)
    assert evaluate_gate(after, policy, today).checks == evaluate_gate(before, policy, today).checks
    assert not evaluate_gate(after, policy, today).allowed


@pytest.mark.parametrize(("topic", "text", "source", "fragments"), [
    ("application", "我还没弄明白应该从哪个页面开始这件事。",
     APPLICATION_SOURCE, ("Apply now", "保存")),
    ("timing", "等到结果出来一般得留出多大空档？",
     APPLICATION_SOURCE, ("3 个月", "3 周")),
    ("translation", "这些纸都是母语写的，对面看得懂吗？",
     SOURCE, ("翻译", "译者", "联系方式")),
    ("booking", "出结果前就把交通和住处的钱付掉有必要吗？",
     SOURCE, ("机票", "酒店", "过境除外")),
    ("fees", "政府那边这一笔大概要收多少？",
     APPLICATION_SOURCE, ("£135", "Standard Visitor")),
    ("bank_period", "账户的进出记录得往前追溯到什么程度？",
     SOURCE, ("没有统一规定", "资金来源")),
])
def test_indirect_questions_select_reviewed_answers_and_reach_actual_auto_sender(
    tmp_path: Path, topic: str, text: str, source: str, fragments: tuple[str, ...],
) -> None:
    store = SQLiteStore(tmp_path / "semantic.db")
    try:
        before = seed_case(store)
        event = event_for(before, "semantic-question", text)
        model = TypedQuestionModel({event.id: patch_for(topic, text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert topic in case.customer_question_topics
        assert case.customer_answers
        assert source in body
        for fragment in fragments:
            assert fragment in body
        assert all(url.startswith("https://www.gov.uk/") for url in re.findall(r"https?://\S+", body))
        assert not any(claim in body for claim in ["保证获批", "已经递交", "保证通过"])
        assert_no_added_authority(before, case)
    finally:
        store.close()


def test_indirect_document_request_uses_the_known_student_context_without_gate_release(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "semantic-checklist.db")
    try:
        before = seed_case(store)
        text = "我周末想先整理一批东西，手头该找些什么？"
        event = event_for(before, "semantic-checklist", text)
        model = TypedQuestionModel({event.id: patch_for("document_checklist", text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert "document_checklist" in case.customer_question_topics
        assert "护照" in body and "在读" in body and "银行" in body
        assert "在职证明" not in body
        assert_no_added_authority(before, case)
    finally:
        store.close()


@pytest.mark.parametrize(("text", "language", "acknowledgements"), [
    ("我之前被拒过一次，这次是不是就没戏了？", "zh", ("核实", "核对", "复核", "核查")),
    ("Would a previous refusal mean this is hopeless?", "en", ("check", "review", "verify")),
])
def test_unsupported_question_is_acknowledged_without_inventing_law_or_fees(
    tmp_path: Path, text: str, language: str, acknowledgements: tuple[str, ...],
) -> None:
    store = SQLiteStore(tmp_path / "semantic-unsupported.db")
    try:
        before = seed_case(store, language)
        event = event_for(before, "unsupported-question", text)
        model = TypedQuestionModel({event.id: patch_for("unsupported", text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert "unsupported" in case.customer_question_topics
        assert case.customer_answers and any(word in body.lower() for word in acknowledgements)
        assert "£" not in body and "135" not in body
        assert not re.search(r"\b\d+\s*(?:years?|months?|weeks?)\b|\d+\s*[年月周]", body)
        assert not any(claim in body for claim in ["保证获批", "一定拒签", "一定通过", "guaranteed approval"])
        assert_no_added_authority(before, case)
    finally:
        store.close()


def test_semantic_topic_and_answer_do_not_leak_into_the_next_customer_turn(tmp_path: Path) -> None:
    path = tmp_path / "semantic-reopen.db"
    adapter = CaptureGmail()
    text = "政府那边这一笔大概要收多少？"
    model = TypedQuestionModel({"fee-question": patch_for("fees", text)})
    store = SQLiteStore(path)
    try:
        before = seed_case(store)
        case, body = process_and_capture(store, model, adapter, event_for(before, "fee-question", text))
        assert "£135" in body and case.customer_question_topics == ["fees"]
    finally:
        store.close()
    store = SQLiteStore(path)
    try:
        later = event_for(before, "later", "我晚点回复。", 1)
        resumed, body = process_and_capture(store, model, adapter, later)
        assert resumed.customer_question_topics == []
        assert resumed.customer_answers == []
        assert "£135" not in body and "https://" not in body
        assert len(adapter.calls) == 2
        assert_no_added_authority(before, resumed)
    finally:
        store.close()


def test_semantic_intent_cannot_bypass_review_expiry(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "semantic-stale.db")
    try:
        before = seed_case(store)
        text = "政府那边这一笔大概要收多少？"
        event = event_for(before, "expired-source-question", text)
        model = TypedQuestionModel({event.id: patch_for("fees", text)})
        after, body = process_and_capture(store, model, CaptureGmail(), event, today=date(2026, 10, 5))
        assert "复核" in body and "£135" not in body
        assert APPLICATION_SOURCE not in body
        assert_no_added_authority(before, after, today=date(2026, 10, 5))
    finally:
        store.close()


def test_semantic_fee_intent_cannot_apply_visitor_fees_to_another_explicit_route(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "semantic-other-route.db")
    try:
        before = seed_case(store)
        text = "如果改成学生签证，政府那边这一笔大概要收多少？"
        event = event_for(before, "other-route-fee-question", text)
        model = TypedQuestionModel({event.id: patch_for("fees", text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert "路线" in body and "£135" not in body
        assert_no_added_authority(before, case)
    finally:
        store.close()


@pytest.mark.parametrize(("text", "language"), [
    ("十年访问签证的申请费是多少？", "zh"),
    ("What does a ten-year visitor visa cost?", "en"),
])
def test_unsupported_duration_does_not_fall_back_to_six_month_fee(
    tmp_path: Path, text: str, language: str,
) -> None:
    store = SQLiteStore(tmp_path / "unsupported-duration.db")
    try:
        before = seed_case(store, language)
        event = event_for(before, "long-term-fees", text)
        model = TypedQuestionModel({event.id: patch_for("unsupported", text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert case.customer_question_topics == ["unsupported"]
        assert case.customer_answers
        assert "£135" not in body and "6-month" not in body and "6 个月" not in body
        assert ("核实" in body) if language == "zh" else ("check" in body)
        assert_no_added_authority(before, case)
    finally:
        store.close()


@pytest.mark.parametrize(("text", "language"), [
    ("我问的是学生签证，机票必须提前买好吗？", "zh"),
    ("For a student visa, do I need to buy flights before the decision?", "en"),
])
def test_booking_intent_cannot_generalise_visitor_advice_to_a_student_route(
    tmp_path: Path, text: str, language: str,
) -> None:
    store = SQLiteStore(tmp_path / "semantic-other-route-booking.db")
    try:
        before = seed_case(store, language)
        event = event_for(before, "other-route-booking", text)
        model = TypedQuestionModel({event.id: patch_for("booking", text)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert "booking" in case.customer_question_topics
        assert ("路线" in body) if language == "zh" else ("route" in body.lower())
        assert "不需要为了提供这些预订证明而先购买" not in body
        assert "do not need to buy flights" not in body
        assert "less useful evidence" not in body and "证明价值较低" not in body
        assert_no_added_authority(before, case)
    finally:
        store.close()


@pytest.mark.parametrize(("body", "topic_excerpts", "expected", "absent"), [
    (
        "What does  a ten-year visitor visa cost?",
        [("unsupported", "What does a ten-year visitor visa cost?")],
        "separate check", "£135",
    ),
    (
        "十年访问签证的申请费是多少？",
        [("unsupported", "十年访问签证"), ("fees", "申请费是多少？")],
        "另行核实", "£135",
    ),
    (
        "不用回答申请费多少。中文材料需要翻译吗？",
        [("translation", "中文材料需要翻译吗？")],
        "完整翻译", "£135",
    ),
    (
        "不是学生签证，我只是去旅游。政府那边这一笔要多少钱？",
        [("fees", "政府那边这一笔要多少钱？")],
        "£135", "需要先按对应路线核实",
    ),
    (
        "官网怎么申请？多久有结果？需要翻译吗？过去有拒签怎么办？",
        [("application", "官网怎么申请？"), ("timing", "多久有结果？"),
         ("translation", "需要翻译吗？"), ("unsupported", "过去有拒签怎么办？")],
        "另行核实", "£135",
    ),
])
def test_semantic_fallback_boundary_fixes_reach_the_reviewed_gmail_body(
    tmp_path: Path, body: str, topic_excerpts: list[tuple[str, str]], expected: str, absent: str,
) -> None:
    store = SQLiteStore(tmp_path / "semantic-boundary-fixes.db")
    try:
        before = seed_case(store)
        event = event_for(before, "semantic-boundary-fix", body)
        patch = CasePatch(updates=[], ambiguities=[], customer_questions=[
            CustomerQuestion.model_validate({"topic": topic, "source_excerpt": excerpt, "confidence": 0.99})
            for topic, excerpt in topic_excerpts
        ])
        model = TypedQuestionModel({event.id: patch})
        case, delivered_body = process_and_capture(store, model, CaptureGmail(), event)
        assert expected in delivered_body and absent not in delivered_body
        if len(topic_excerpts) == 4:
            assert len(case.customer_answers) == 3
            assert "还没有展开" in delivered_body
        assert_no_added_authority(before, case)
    finally:
        store.close()


def seed_only_dates_missing(store: SQLiteStore, language: str) -> Case:
    """No remaining intake questions: this exposes unsolicited checklist regressions."""
    case = seed_case(store, language)
    case.profile.full_name = "Fictional Applicant"
    case.profile.uk_accommodation = "Planned stay in London; booking not made"
    case.profile.estimated_trip_cost_gbp = 2500
    case.profile.current_address = "Fictional address, Hong Kong"
    case.profile.has_serious_history = False
    case.profile.route_confirmed_standard_visitor = True
    evaluate_gate(case, load_policy(POLICY_PATH), TODAY)
    store.save_case(case)
    return case


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize(("text", "language"), [
    ("普通旅游签证的申请费是多少钱？", "zh"),
    ("What is the visitor visa application fee?", "en"),
])
def test_pure_faq_does_not_append_missing_documents_or_repeat_deferred_dates(
    tmp_path: Path, text: str, language: str, semantic: bool,
) -> None:
    store = SQLiteStore(tmp_path / "faq-without-unrelated-requests.db")
    try:
        before = seed_only_dates_missing(store, language)
        event = event_for(before, "pure-fee-faq", text)
        patch = patch_for("fees", text) if semantic else CasePatch(updates=[], ambiguities=[])
        model = TypedQuestionModel({event.id: patch})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        assert "£135" in body
        assert case.latest_deferred_fields == [] and case.deferred_fields == before.deferred_fields
        assert case.last_requested_fields == []
        assert sum(item.applicable and item.blocker and not item.satisfied for item in case.requirements) >= 3
        assert reply_items(case)[2] == []
        assert all(phrase not in body for phrase in [
            "接下来还需要这些材料", "We'll also need these documents", "日期确定后再告诉我",
            "具体日期补齐前", "Let me know when your dates are decided", "final check will remain on hold",
        ])
        assert case.profile == before.profile and case.evidence == before.evidence
        assert not case.profile_confirmed and not case.final_summary_confirmed
        assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed
    finally:
        store.close()


@pytest.mark.parametrize(("text", "language"), [
    ("今天有点忙。", "zh"),
    ("Thanks, I am taking a break.", "en"),
    ("收到\n> 请把材料清单发我", "zh"),
])
def test_quiet_turn_without_facts_or_topics_does_not_start_a_document_checklist(
    tmp_path: Path, text: str, language: str,
) -> None:
    store = SQLiteStore(tmp_path / "quiet-no-checklist.db")
    try:
        before = seed_only_dates_missing(store, language)
        event = event_for(before, "quiet-turn", text)
        case, body = process_and_capture(store, TypedQuestionModel({}), CaptureGmail(), event)
        assert case.customer_question_topics == [] and case.customer_answers == []
        assert case.latest_received_facts == {} and case.latest_changes == {}
        assert case.latest_document_names == [] and case.last_requested_fields == []
        assert sum(item.applicable and item.blocker and not item.satisfied for item in case.requirements) >= 3
        assert reply_items(case)[2] == []
        assert "接下来还需要这些材料" not in body and "We'll also need these documents" not in body
        assert "GOV.UK:" not in body and "银行流水" not in body and "bank statements" not in body
        assert case.profile == before.profile and case.evidence == before.evidence
    finally:
        store.close()


@pytest.mark.parametrize("decline_links", [False, True])
@pytest.mark.parametrize(("checklist_request", "language", "decline"), [
    ("请把材料清单发我。", "zh", "不用发链接。"),
    ("Please send me a document checklist.", "en", "No links please."),
])
def test_explicit_checklist_explains_multiple_items_and_uses_requirement_sources(
    tmp_path: Path, checklist_request: str, language: str, decline: str, decline_links: bool,
) -> None:
    store = SQLiteStore(tmp_path / "requested-explained-checklist.db")
    try:
        before = seed_only_dates_missing(store, language)
        text = checklist_request + ("\n" + decline if decline_links else "")
        event = event_for(before, "explicit-checklist", text)
        model = TypedQuestionModel({event.id: patch_for("document_checklist", checklist_request)})
        case, body = process_and_capture(store, model, CaptureGmail(), event)
        documents = reply_items(case)[2]
        assert len(documents) >= 3
        assert all(" — " in item and item in body for item in documents)
        if language == "zh":
            assert "核对身份" in body and "向学校索取" in body and "资金从哪里来" in body
            assert "不是所有申请人通用的强制清单" in body
        else:
            assert "to check your identity" in body and "ask your school" in body
            assert "where the money comes from" in body and "not a universal mandatory checklist" in body
        assert "Apply now" not in body
        sources = {source for item in case.requirements
                   if item.applicable and item.blocker and not item.satisfied for source in item.source_urls}
        assert sources
        if decline_links:
            assert "http://" not in body and "https://" not in body
        else:
            assert all(source in body for source in sources)
            assert all(source.startswith("https://www.gov.uk/") for source in sources)
        assert case.profile == before.profile and case.evidence == before.evidence
        assert case.documents == before.documents and case.delivery_path is None
        assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed
    finally:
        store.close()


def test_single_employment_correction_uses_a_natural_english_acknowledgement(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "natural-employment-correction.db")
    try:
        before = seed_only_dates_missing(store, "en")
        text = "I am employed now; that is a correction to my student status."
        event = event_for(before, "employment-correction", text)
        patch = CasePatch(updates=[FactUpdate(
            field="occupation_status", value="employed", source_excerpt="I am employed now", confidence=0.99,
        )], ambiguities=[])
        case, body = process_and_capture(store, TypedQuestionModel({event.id: patch}), CaptureGmail(), event)
        assert case.latest_changes == {"occupation_status": "employed"}
        assert case.profile.occupation_status == "employed"
        assert "Thanks for clarifying—I've noted that you're employed." in body
        assert "Occupation Status:" not in body and "occupation_status" not in body
        assert case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None
        assert not case.profile_confirmed and not case.final_summary_confirmed
        assert not evaluate_gate(case, load_policy(POLICY_PATH), TODAY).allowed
    finally:
        store.close()


@pytest.mark.parametrize(("deferral", "faq", "language"), [
    ("行程还在斟酌。", "最早什么时候申请？", "zh"),
    ("The trip is still up in the air.", "How long does a visitor visa decision take?", "en"),
])
def test_timing_answer_takes_priority_over_a_repeated_typed_date_deferral(
    tmp_path: Path, deferral: str, faq: str, language: str,
) -> None:
    store = SQLiteStore(tmp_path / "timing-with-typed-date-deferral.db")
    try:
        before = seed_only_dates_missing(store, language)
        event = event_for(before, "timing-and-deferral", deferral + "\n" + faq)
        patch = patch_for("timing", faq)
        patch.question_deferrals = [
            QuestionDeferral(field="planned_arrival_date", source_excerpt=deferral, confidence=0.99),
            QuestionDeferral(field="planned_departure_date", source_excerpt=deferral, confidence=0.99),
        ]
        case, body = process_and_capture(store, TypedQuestionModel({event.id: patch}), CaptureGmail(), event)
        assert case.latest_received_facts == {} and case.latest_changes == {}
        assert set(case.latest_deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
        assert case.deferred_fields == before.deferred_fields
        assert case.last_requested_fields == [] and reply_items(case)[2] == []
        assert APPLICATION_SOURCE in body
        if language == "zh":
            assert body.startswith("如果你需要申请 Standard Visitor")
            assert "日期先留空" not in body and "日期确定后再告诉我" not in body
        else:
            assert body.startswith("If you need a Standard Visitor visa")
            assert "leave the dates open" not in body and "when your dates are decided" not in body
        assert case.profile == before.profile and case.evidence == before.evidence
        assert not case.profile_confirmed and not case.final_summary_confirmed
        policy = load_policy(POLICY_PATH)
        assert evaluate_gate(case, policy, TODAY).checks == evaluate_gate(before, policy, TODAY).checks
        assert not evaluate_gate(case, policy, TODAY).allowed
    finally:
        store.close()
