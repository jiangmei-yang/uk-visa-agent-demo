"""Obstacle-aware preparation from synthetic state, never an evidence waiver."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.domain.models import (
    Case,
    CaseProfile,
    CaseStatus,
    Document,
    DocumentStatus,
    Evidence,
    InboundEvent,
    Issue,
    IssueSeverity,
)
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.next_step import select_next_step
from visa_agent.workflow.preparation_obstacles import preparation_obstacle_kind
from visa_agent.workflow.service import WorkflowService

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 4)
DATES = "日期还是定不下来，我现在能先准备什么？"
LETTER = "公司现在不给我开在职证明，我下一步可以先准备什么？"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Synthetic obstacle regressions cannot access a network")
    monkeypatch.setattr("socket.socket.connect", reject)
    monkeypatch.setattr("socket.create_connection", reject)


def _case(body: str, language: str = "zh") -> Case:
    return Case(id="synthetic-obstacle", external_thread_id="synthetic-obstacle-thread",
        applicant_contact="synthetic@example.test", primary_channel="gmail", policy_version=POLICY.version,
        customer_language=language, latest_customer_message=body, customer_question_topics=["next_step"],
        profile=CaseProfile(nationality_country="China", application_country="Hong Kong",
            visit_purpose="tourism", occupation_status="employed", funding_source="self"),
        deferred_fields=["planned_arrival_date", "planned_departure_date"])


@pytest.mark.parametrize(("body", "language", "terms"), [
    ("公司现在不给我开在职证明，我下一步可以先准备什么？", "zh", ["在职证明", "银行", "替代"]),
    ("My employer will not issue my employment letter. What can I prepare next?", "en", ["employer", "bank", "replace"]),
    ("日期还是定不下来，我现在能先准备什么？", "zh", ["日期", "在职", "确定"]),
    ("My travel dates are still undecided. What can I prepare now?", "en", ["dates", "employer", "confirmed"]),
])
def test_exposed_obstacles_get_a_concrete_step_not_an_unrelated_name_question(body, language, terms):
    case = _case(body, language)
    gate = evaluate_gate(case, POLICY, TODAY)
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, gate)
    assert result.question_field is None
    assert result.kind in {"document", "waiting"}
    assert all(term in result.message for term in terms)
    assert "GOV.UK:" in result.message
    assert case.model_dump_json() == before
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None


@pytest.mark.parametrize("body", [
    "如果公司不给我开在职证明，我下一步可以先准备什么？",
    "假设日期还是定不下来，我现在能先准备什么？",
    "My employer will not issue my employment letter. If I apply, what can I prepare next?",
    "Suppose my travel dates are still undecided. What can I prepare now?",
    '朋友问：“公司现在不给我开在职证明，我下一步可以先准备什么？”',
    'The customer wrote: "My travel dates are still undecided. What can I prepare now?"',
    "朋友的公司现在不给她开在职证明，我下一步可以先准备什么？",
    "I am asking on behalf of my sister. My travel dates are still undecided. What can I prepare now?",
    "My friend's travel dates are still undecided. What can I prepare next?",
    "不是公司不给我开在职证明。我下一步可以先准备什么？",
    "日期不是没定，我现在能先准备什么？",
    "公司没有说不给我开在职证明，我下一步可以先准备什么？",
    "我朋友日期没定，我现在能先准备什么？",
    "It is not true that my employer will not issue my employment letter. What can I prepare next?",
    "日期还是定不下来，不要再告诉我准备什么。",
    "My travel dates are still undecided. Do not suggest what I can prepare next.",
    "公司现在不给我开在职证明，我下一步可以先准备什么才能保证获批？",
    "My employer will not issue my employment letter. What can I prepare next to guarantee approval?",
    "公司现在不给我开在职证明，我下一步可以先准备什么？可以伪造一份吗？",
    "My employer will not issue my employment letter. What can I prepare next? Can I fake it?",
    "我申请工作签证。公司现在不给我开在职证明，我下一步可以先准备什么？",
    "For a Canadian visa, my travel dates are still undecided. What can I prepare now?",
    "公司现在不给我开在职证明。",  # No current request to advance preparation.
    "我现在能先准备什么？",  # No particular obstacle.
    "> 公司现在不给我开在职证明，我下一步可以先准备什么？\n谢谢。",
])
def test_non_current_or_qualified_obstacles_do_not_borrow_own_case_advice(body):
    assert preparation_obstacle_kind(body) is None


@pytest.mark.parametrize(("occupation", "term"), [
    ("employed", "任职时间"), ("student", "在读"), ("self_employed", "经营登记"),
])
def test_date_obstacle_uses_actual_occupation_without_requiring_identity(occupation, term):
    case = _case(DATES)
    case.profile.occupation_status = occupation
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.requirement_id == "status_evidence" and term in result.message
    assert "如果按普通访客路线" in result.message
    assert result.question_field is None


@pytest.mark.parametrize(("funding", "term"), [
    ("self", "银行"), ("employer_or_school", "资助单位或学校"), ("personal_sponsor", "资助人"),
])
def test_letter_obstacle_uses_actual_funding_without_assuming_a_substitute(funding, term):
    case = _case(LETTER)
    case.profile.funding_source = funding
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.requirement_id == "funding_evidence" and term in result.message
    assert "不能自动替代" in result.message and "在职情况这项检查仍保留" in result.message
    assert "可以先向公司" not in result.message


def _document(kind, status=DocumentStatus.RECEIVED):
    return Document(id="synthetic-" + kind, filename=kind + ".pdf", kind=kind, sha256="a" * 64,
        mime_type="application/pdf", status=status, source_event_id="synthetic-source",
        path="/synthetic-never-opened/" + kind + ".pdf")


@pytest.mark.parametrize("status", [DocumentStatus.RECEIVED, DocumentStatus.PROCESSING,
    DocumentStatus.ACCEPTED_FOR_REVIEW])
@pytest.mark.parametrize(("body", "kind", "expected"), [
    (DATES, "employment_letter", "funding_evidence"), (LETTER, "bank_statement", "passport"),
])
def test_received_or_in_processing_material_is_not_requested_again(body, kind, expected, status):
    case = _case(body)
    case.documents = [_document(kind, status)]
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.requirement_id == expected and "不用重发" in result.message
    assert result.question_field is None


def test_request_placeholder_is_not_falsely_reported_as_received():
    case = _case(LETTER)
    case.documents = [_document("bank_statement", DocumentStatus.REQUESTED)]
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.requirement_id == "funding_evidence"
    assert "已经收到" not in result.message


@pytest.mark.parametrize("body", [LETTER, DATES])
def test_when_early_material_is_already_received_offer_an_actual_organising_task(body):
    case = _case(body)
    case.documents = [_document(kind) for kind in
                      ["employment_letter", "bank_statement", "passport", "residence_permit"]]
    gate = evaluate_gate(case, POLICY, TODAY)
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, gate)
    assert result.kind == "waiting" and result.requirement_id is None and result.question_field is None
    assert ("公司的实际答复" if body == LETTER else "拟访问地点草稿") in result.message
    assert "重发已收到" in result.message
    assert before == case.model_dump_json()


@pytest.mark.parametrize("reason", ["paused", "human_review", "history", "blocker", "age",
    "expired", "replacement", "clarification", "held", "outside_purpose"])
def test_existing_safety_boundaries_remain_ahead_of_obstacle_help(reason):
    case = _case(LETTER)
    if reason == "paused":
        case.preparation_paused = True
    elif reason == "human_review":
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    elif reason == "history":
        case.profile.has_serious_history = True
    elif reason == "blocker":
        case.issues = [Issue(id="synthetic-blocker", code="CONFLICT", title="Conflict",
            detail="Synthetic unresolved conflict", severity=IssueSeverity.BLOCKER)]
    elif reason == "age":
        case.profile.date_of_birth = date(2015, 1, 1)
    elif reason == "replacement":
        case.documents = [_document("unknown", DocumentStatus.NEEDS_REPLACEMENT)]
    elif reason == "clarification":
        case.documents = [_document("unknown", DocumentStatus.NEEDS_CLARIFICATION)]
    elif reason == "outside_purpose":
        case.profile.visit_purpose = "paid_work"
    gate = evaluate_gate(case, POLICY, TODAY)
    if reason == "expired":
        gate.checks["policy_snapshot_is_current"] = False
    elif reason == "held":
        gate.checks["all_held_updates_reviewed"] = False
    result = select_next_step(case, POLICY, gate)
    assert result.kind in {"review", "paused"} and result.requirement_id is None
    assert result.question_field is None and "可以先从网银" not in result.message


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("missing", ["funding_source", "application_country"])
def test_incomplete_background_asks_needed_fact_instead_of_review_or_reasking_name(language, missing):
    body = DATES if language == "zh" else "My travel dates are still undecided. What can I prepare now?"
    case = _case(body, language)
    case.profile.full_name = "Synthetic Rowan"
    case.profile.date_of_birth = date(1993, 8, 19)
    setattr(case.profile, missing, None)
    gate = evaluate_gate(case, POLICY, TODAY)
    before = case.model_dump_json()
    result = select_next_step(case, POLICY, gate)
    assert result.kind == "question" and result.question_field == missing
    assert result.requirement_id is None
    assert "appropriate entry route" not in result.message and "核对适用的入境路线" not in result.message
    assert case.model_dump_json() == before


def test_unknown_requirement_rule_cannot_use_the_reviewed_obstacle_path():
    case = _case(LETTER)
    gate = evaluate_gate(case, POLICY, TODAY)
    case.requirements[0].rule_version = "unreviewed"
    result = select_next_step(case, POLICY, gate)
    assert result.kind == "review" and "官方依据" in result.message


@pytest.mark.parametrize("suffix", ["不要发链接。", "No links please."])
def test_no_links_is_not_no_useful_advice(suffix):
    case = _case(LETTER + suffix)
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.requirement_id == "funding_evidence" and "银行" in result.message
    assert "http" not in result.message


def test_negative_route_evidence_stays_distinct_from_false_default_after_snapshot_reload():
    case = _case(LETTER)
    case.evidence = [Evidence(id="synthetic-route", fact_key="route_confirmed_standard_visitor", value=False,
        source_event_id="synthetic-route-event", source_excerpt="我不按标准访客签证申请。",
        extraction_method="synthetic", model_version="synthetic", confidence=1)]
    case = Case.model_validate_json(case.model_dump_json())
    result = select_next_step(case, POLICY, evaluate_gate(case, POLICY, TODAY))
    assert result.kind == "review" and result.requirement_id is None


class Model:
    def __init__(self, patch):
        self.patch = patch

    def extract_case_patch(self, event):
        return self.patch.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


@pytest.mark.parametrize("explicit_other_route", [False, True])
@pytest.mark.parametrize("body", [LETTER, DATES])
def test_actual_workflow_and_reopened_sqlite_keep_sourced_route_distinction(
    tmp_path, explicit_other_route, body,
):
    background = [("nationality_country", "China", "我持中国护照"),
        ("application_country", "Hong Kong", "在香港申请"),
        ("visit_purpose", "tourism", "去英国旅游"),
        ("occupation_status", "employed", "我在香港上班"),
        ("funding_source", "self", "费用自己承担")]
    initial = "，".join(excerpt for _, _, excerpt in background) + "。日期还没有确定，想先了解准备过程。"
    updates = [FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=1)
               for field, value, excerpt in background]
    if explicit_other_route:
        denial = "我不按标准访客签证申请。"
        initial += denial
        updates.append(FactUpdate(field="route_confirmed_standard_visitor", value=False,
            source_excerpt=denial, confidence=1))
    stamp = datetime(2026, 9, 4, 12, tzinfo=UTC)
    first = InboundEvent(id="synthetic-first", channel="gmail", external_thread_id="synthetic-reopen",
        sender="synthetic@example.test", subject="英国签证咨询", body=initial, received_at=stamp)
    store = SQLiteStore(tmp_path / "obstacle.db")
    try:
        workflow = WorkflowService(store, POLICY, Model(CasePatch(updates=updates, ambiguities=[])),
            today_provider=lambda: TODAY)
        case, duplicate, _ = workflow.process(first)
        assert not duplicate and case.profile.route_confirmed_standard_visitor is False
        identifier = case.id
    finally:
        store.close()
    store = SQLiteStore(tmp_path / "obstacle.db")
    try:
        reloaded = store.get_case(identifier)
        assert reloaded is not None and reloaded.profile.route_confirmed_standard_visitor is False
        assert any(e.fact_key == "route_confirmed_standard_visitor" and e.value is False
                   for e in reloaded.evidence) == explicit_other_route
        second = first.model_copy(update={"id": "synthetic-second", "body": body,
            "received_at": stamp + timedelta(minutes=1)})
        patch = CasePatch(updates=[], ambiguities=[], customer_questions=[
            CustomerQuestion(topic="next_step", source_excerpt=body, confidence=1)])
        workflow = WorkflowService(store, POLICY, Model(patch), today_provider=lambda: TODAY)
        after, duplicate, _ = workflow.process(second)
        assert not duplicate and after.id == identifier
        if explicit_other_route:
            assert after.status == CaseStatus.HUMAN_REVIEW_REQUIRED
            assert not any("可以先从网银" in answer or "可以先向公司" in answer
                           for answer in after.customer_answers)
        else:
            assert after.status == CaseStatus.DRAFT and after.next_step_advice is not None
            assert after.next_step_advice.question_field is None and after.last_requested_fields == []
            assert "如果按普通访客路线" in after.next_step_advice.message
            assert ("银行" if body == LETTER else "在职证明") in after.next_step_advice.message
            assert after.profile.full_name is None and after.profile.date_of_birth is None
        assert not after.profile_confirmed and not after.final_summary_confirmed and not after.delivery_path
        assert after.profile.planned_arrival_date is None and after.profile.planned_departure_date is None
        assert after.profile.route_confirmed_standard_visitor is False
    finally:
        store.close()
