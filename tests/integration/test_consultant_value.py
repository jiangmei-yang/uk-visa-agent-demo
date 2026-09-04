"""Fictional consultant-value regressions, not a naturalness score or live trial.

Suggestions are grounded in the repository's reviewed GOV.UK source categories:
personal circumstances, available funds, sponsor support and purpose of visit.
They are not asserted to be a universal mandatory checklist or acceptance promise.
Only extraction/provider I/O is substituted; the workflow, guard, persisted reply,
automatic reviewed sender and dispatcher are real and the store reopens each turn.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL, ROUTE_CHECK_URL
from visa_agent.workflow.conversation import QUESTION_TEXT_ZH, reply_items
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 4)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
APPLICANT = "fictional-consultant-enquiry@example.test"
TRAVEL_FIELDS = {"planned_arrival_date", "planned_departure_date"}
DEFERRED = "旅行日期还没有确定。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Consultant-value regressions cannot access a live provider")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def _patch(*, updates=(), questions=(), deferred=False, control=None):
    return CasePatch.model_validate({
        "updates": [{"field": field, "value": value, "source_excerpt": text, "confidence": 1}
                    for field, value, text in updates],
        "ambiguities": [],
        "customer_questions": [{"topic": topic, "source_excerpt": text, "confidence": 1}
                               for topic, text in questions],
        "question_deferrals": [{"field": field, "source_excerpt": DEFERRED, "confidence": 1}
                               for field in sorted(TRAVEL_FIELDS)] if deferred else [],
        "preparation_intent": {"action": control[0], "source_excerpt": control[1], "confidence": 1}
                              if control else None,
    })


class Model:
    def __init__(self, patch):
        self.patch = patch
        self.events = []

    def extract_case_patch(self, event):
        self.events.append(event.model_copy(deep=True))
        return self.patch.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


class CaptureGmail(GmailAdapter):
    def __init__(self):
        self.calls = []

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == APPLICANT and kwargs.get("attachment") is None
        self.calls.append(kwargs)
        return {"id": f"fictional-consultant-send-{len(self.calls)}"}


class Conversation:
    def __init__(self, tmp_path):
        self.path = tmp_path / "consultant-value.db"
        self.gmail = CaptureGmail()
        self.turns = 0

    def turn(self, body, patch):
        self.turns += 1
        event = InboundEvent(id=f"consultant-{self.turns}", external_thread_id="fictional-consultant-thread",
            sender=APPLICANT, subject="英国签证准备", channel="gmail", body=body,
            received_at=datetime(2026, 9, 4, 10, tzinfo=UTC) + timedelta(minutes=self.turns),
            rfc_message_id=f"<consultant-{self.turns}@example.test>")
        model = Model(patch)
        store = SQLiteStore(self.path)
        try:
            guarded = GuardedLLM(model)
            workflow = WorkflowService(store, POLICY, guarded, today_provider=lambda: TODAY)
            case, duplicate, plan = workflow.process(event)
            assert not duplicate and plan == "blocked" and not guarded.last_extraction_fallback
            assert len(model.events) == 1 and model.events[0].body == body
            assert case.status == CaseStatus.DRAFT and not case.profile_confirmed
            assert not case.final_summary_confirmed and case.confirmation_kind is None
            assert case.delivery_path is None
            sender = AutomaticGmailReplySender(self.gmail, store, APPLICANT)
            sender.withhold_obsolete_unsent()
            sent = OutboxDispatcher(store, sender, channel="gmail",
                                    allowed_message_types=("blocked",)).dispatch_due(event.received_at)
            assert len(sent) == 1 and sent[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["payload"] == self.gmail.calls[-1]["body"] and row["status"] == "SENT"
            assert store.get_case(case.id).model_dump() == case.model_dump()
            return SimpleNamespace(case=case, body=row["payload"], event=event, model=model)
        finally:
            store.close()


def _context(kind):
    updates = [("nationality_country", "China", "我持中国护照。"),
               ("application_country", "Hong Kong", "我会在香港递交申请。")]
    if kind == "family":
        updates.append(("visit_purpose", "family_or_friends", "我去英国探望姐姐，计划住她家。"))
    else:
        updates.append(("visit_purpose", "tourism", "这次去英国旅游。"))
    updates.append(("occupation_status", "student", "我是在香港读大学的学生。") if kind == "student"
                   else ("occupation_status", "employed", "我目前受雇工作。"))
    updates.append(("funding_source", "personal_sponsor", "旅行费用由我父母资助。") if kind == "parents"
                   else ("funding_source", "self", "旅行费用全部由我自己的存款承担。"))
    body = " ".join([*(text for _, _, text in updates), DEFERRED, "请帮我准备英国访问签证。"])
    return body, _patch(updates=updates, deferred=True)


def _assert_limited_intake_and_dates_preserved(result):
    case, body = result.case, result.body
    assert len(reply_items(case)[1]) <= 1, body
    assert set(case.deferred_fields) == TRAVEL_FIELDS
    assert all(getattr(case.profile, field) is None for field in TRAVEL_FIELDS)
    assert not TRAVEL_FIELDS.intersection(case.last_requested_fields)
    assert all(QUESTION_TEXT_ZH[field] not in body for field in TRAVEL_FIELDS)
    assert "日期" in body and any(word in body for word in ("确定后", "先留空", "定下来", "以后"))
    assert not case.preparation_paused  # Deferring dates is not pausing the application.


def _assert_no_intake(result):
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert all(question not in result.body for question in QUESTION_TEXT_ZH.values())


@pytest.mark.parametrize("semantic_checklist", [False, True], ids=["no-topic", "accepted-checklist"])
def test_ordinary_first_enquiry_offers_useful_orientation_and_official_start_before_one_main_question(
    tmp_path, semantic_checklist,
):
    body = "我想办英国签证，需要什么？"
    result = Conversation(tmp_path).turn(body, _patch(
        questions=[("document_checklist", body)] if semantic_checklist else []))
    assert any(url in result.body for url in (ROUTE_CHECK_URL, APPLICATION_URL)), result.body
    assert any(word in result.body for word in ("是否需要", "申请类别", "ETA", "参考", "一般")), result.body
    assert len(reply_items(result.case)[1]) <= 1, result.body
    assert result.case.profile.visit_purpose is None and result.case.profile.nationality_country is None
    assert result.case.profile.application_country is None
    assert result.case.profile.route_confirmed_standard_visitor is not True


def test_hong_kong_self_funded_student_gets_relevant_preparation_not_an_identity_questionnaire(tmp_path):
    result = Conversation(tmp_path).turn(*_context("student"))
    assert result.case.profile.nationality_country == "China"
    assert result.case.profile.application_country == "Hong Kong"
    assert result.case.profile.occupation_status == "student" and result.case.profile.funding_source == "self"
    assert "在读证明" in result.body and "银行流水" in result.body, result.body
    assert "资金来源" in result.body and any(word in result.body for word in ("可用", "能够使用", "能否使用"))
    assert any(word in result.body for word in ("学校", "银行", "网银"))
    assert any(url in result.body for url in (APPLICATION_URL, DOCUMENTS_URL))
    _assert_limited_intake_and_dates_preserved(result)


@pytest.mark.parametrize("kind", ["employed", "parents", "family"])
def test_other_known_contexts_receive_their_own_actionable_advice_not_the_student_template(tmp_path, kind):
    result = Conversation(tmp_path).turn(*_context(kind))
    assert result.case.profile.occupation_status == "employed"
    assert "在读证明" not in result.body and "向学校" not in result.body and "你目前在读书" not in result.body
    if kind == "employed":
        assert any(word in result.body for word in ("在职证明", "雇主信", "雇主", "公司抬头纸")), result.body
        assert any(word in result.body for word in ("索取", "联系", "请雇主", "向公司")), result.body
        assert any(word in result.body for word in ("职位", "薪资", "任职", "工作情况")), result.body
    elif kind == "parents":
        assert result.case.profile.funding_source == "personal_sponsor"
        assert "资助" in result.body and any(word in result.body for word in ("关系", "亲属")), result.body
        assert any(word in result.body for word in ("承担", "资助范围", "资助内容")), result.body
        assert any(word in result.body for word in ("资金", "能力", "银行", "流水")), result.body
        assert any(word in result.body for word in ("父母", "资助人")), result.body
    else:
        assert result.case.profile.visit_purpose == "family_or_friends" and result.case.profile.funding_source == "self"
        assert "邀请" in result.body, result.body
        assert any(word in result.body for word in ("关系", "住宿", "住处", "访问安排")), result.body
        assert "姐姐承担费用" not in result.body and "由姐姐资助" not in result.body
    assert any(word in result.body for word in ("可以", "可先", "建议", "先向", "先请", "可向")), result.body
    _assert_limited_intake_and_dates_preserved(result)


def test_followup_faq_is_answered_without_appending_a_new_preparation_questionnaire(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(*_context("student"))
    question = "银行流水在访问签证申请里有什么作用？"
    result = dialogue.turn(question, _patch(questions=[("bank_period", question)]))
    assert "资金来源" in result.body and any(word in result.body for word in ("银行", "流水", "对账单"))
    _assert_no_intake(result)
    assert set(result.case.deferred_fields) == TRAVEL_FIELDS
    assert len(dialogue.gmail.calls) == 2


def test_explicit_next_step_alongside_faq_can_ask_one_missing_fact_without_dropping_the_answer(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(*_context("student"))
    faq = "银行流水在访问签证申请里有什么作用？"
    step = "另外，下一步先准备哪一项？"
    result = dialogue.turn(faq + step, _patch(questions=[("bank_period", faq), ("next_step", step)]))
    assert "资金来源" in result.body
    assert result.case.next_step_advice is not None and len(reply_items(result.case)[1]) == 1
    assert not TRAVEL_FIELDS.intersection(result.case.last_requested_fields)
    assert not result.case.preparation_paused and len(dialogue.gmail.calls) == 2


def test_paused_customer_can_get_faq_information_without_restarting_guidance_or_intake(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(*_context("student"))
    pause = "请先暂停我的英国签证材料准备。"
    paused = dialogue.turn(pause, _patch(control=("pause", pause)))
    _assert_no_intake(paused)
    faq = "银行流水在访问签证申请里有什么作用？"
    result = dialogue.turn(faq, _patch(questions=[("bank_period", faq)]))
    assert "资金来源" in result.body
    _assert_no_intake(result)
    assert result.case.preparation_paused and result.case.preparation_control_epoch == 1
    assert result.case.profile.occupation_status == "student" and result.case.profile.funding_source == "self"
    assert set(result.case.deferred_fields) == TRAVEL_FIELDS
    assert "Apply now" not in result.body and "可以先准备学校" not in result.body
    assert len(dialogue.gmail.calls) == 3


def test_explicit_next_step_combines_both_unknown_dates_in_one_sent_question_without_inventing_them(tmp_path):
    body, patch = _context("student")
    step = "另外，下一步先准备哪一项？"
    body = body.replace(DEFERRED, "") + step
    patch = patch.model_copy(update={"question_deferrals": [],
        "customer_questions": _patch(questions=[("next_step", step)]).customer_questions})
    result = Conversation(tmp_path).turn(body, patch)
    questions = reply_items(result.case)[1]
    assert len(questions) == 1 and "到英国" in questions[0] and "离开" in questions[0]
    assert result.case.last_requested_fields == ["planned_arrival_date", "planned_departure_date"]
    assert result.case.deferred_fields == []
    assert all(getattr(result.case.profile, field) is None for field in TRAVEL_FIELDS)
    assert all(result.case.active_evidence(field) == [] for field in TRAVEL_FIELDS)
    assert all(result.case.question_event_ids[field] == [result.event.id] for field in TRAVEL_FIELDS)
    assert result.case.next_step_advice is not None and not result.case.preparation_paused


def test_sent_name_question_then_supplied_name_keeps_same_case_and_asks_only_dob_after_reopen(tmp_path):
    dialogue = Conversation(tmp_path)
    initial = dialogue.turn(*_context("student"))
    assert initial.case.last_requested_fields == ["full_name"]
    assert len(reply_items(initial.case)[1]) == 1
    name = "我的姓名是示例安宁。"
    answered = dialogue.turn(name, _patch(updates=[("full_name", "示例安宁", name)]))
    assert answered.case.id == initial.case.id
    assert answered.model.events[0].requested_fields == ["full_name"]
    assert answered.case.profile.full_name == "示例安宁"
    assert answered.case.active_evidence("full_name")[0].source_event_id == answered.event.id
    assert answered.case.last_requested_fields == ["date_of_birth"]
    assert len(reply_items(answered.case)[1]) == 1
    assert QUESTION_TEXT_ZH["full_name"] not in answered.body
    assert QUESTION_TEXT_ZH["date_of_birth"] in answered.body
    assert set(answered.case.deferred_fields) == TRAVEL_FIELDS
    assert all(getattr(answered.case.profile, field) is None for field in TRAVEL_FIELDS)
    assert not TRAVEL_FIELDS.intersection(answered.case.last_requested_fields)
    assert not answered.case.preparation_paused
    store = SQLiteStore(dialogue.path)
    try:
        assert len(store.list_cases()) == 1
        rows = store.list_outbox()
        assert len(rows) == len(dialogue.gmail.calls) == 2
        assert {row["event_id"] for row in rows} == {initial.event.id, answered.event.id}
        assert all(row["status"] == "SENT" for row in rows)
        assert store.get_case(initial.case.id).model_dump() == answered.case.model_dump()
    finally:
        store.close()
