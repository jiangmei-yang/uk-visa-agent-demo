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


@pytest.mark.parametrize(("language", "pause", "resume", "step"), [
    ("zh", "我最近忙，先暂停准备。", "现在恢复", "告诉我下一步做什么"),
    ("en", "Please pause the preparation for now.", "Resume now", "tell me what I should do next"),
])
def test_paused_case_accepts_a_natural_contextual_resume_before_next_step(
    tmp_path, language, pause, resume, step,
):
    dialogue = Conversation(tmp_path)
    first = (
        "中国护照，香港申请，旅游、在读、自费，日期还没定。"
        if language == "zh" else
        "Chinese passport, applying in Hong Kong, holiday, student, self-funded; dates not fixed."
    )
    dialogue.turn(first, _patch(updates=[
        ("nationality_country", "China", "中国护照" if language == "zh" else "Chinese passport"),
        ("application_country", "Hong Kong", "香港申请" if language == "zh" else "applying in Hong Kong"),
        ("visit_purpose", "tourism", "旅游" if language == "zh" else "holiday"),
        ("occupation_status", "student", "在读" if language == "zh" else "student"),
        ("funding_source", "self", "自费" if language == "zh" else "self-funded"),
    ], deferred=True))
    paused = dialogue.turn(pause, _patch(control=("pause", pause)))
    assert paused.case.preparation_paused

    body = f"{resume}，{step}。" if language == "zh" else f"{resume} and {step}."
    result = dialogue.turn(
        body,
        _patch(control=("resume", resume), questions=[("next_step", step)]),
    )

    assert not result.case.preparation_paused
    assert result.case.latest_preparation_action == "resume"
    assert "保持暂停" not in result.body and "keep the preparation on hold" not in result.body
    assert result.case.last_requested_fields or result.case.next_step_advice is not None


@pytest.mark.parametrize(
    ("body", "relationship", "name", "relationship_excerpt", "location_excerpt"),
    [
        ("是我父亲陈建国资助，他不在英国。", "father", "陈建国", "我父亲", "他不在英国"),
        (
            "My father Jian Chen is sponsoring my trip and he doesn't live in the UK.",
            "father", "Jian Chen", "My father", "he doesn't live in the UK",
        ),
    ],
)
def test_natural_single_sponsor_answer_is_saved_and_not_reasked(
    tmp_path, body, relationship, name, relationship_excerpt, location_excerpt,
):
    dialogue = Conversation(tmp_path)
    dialogue.turn(
        "我拿中国护照，在香港申请，去英国旅游。我在工作，旅行由家人资助，日期还没定。",
        _patch(updates=[
            ("nationality_country", "China", "中国护照"),
            ("application_country", "Hong Kong", "香港申请"),
            ("visit_purpose", "tourism", "去英国旅游"),
            ("occupation_status", "employed", "我在工作"),
            ("funding_source", "personal_sponsor", "旅行由家人资助"),
        ], deferred=True),
    )

    result = dialogue.turn(body, _patch(updates=[
        ("sponsor_relationship", relationship, relationship_excerpt),
        ("sponsor_name", name, name),
        ("sponsor_is_in_uk", False, location_excerpt),
    ]))

    assert result.case.profile.sponsor_relationship == relationship
    assert result.case.profile.sponsor_name == name
    assert result.case.profile.sponsor_is_in_uk is False
    assert not {"sponsor_relationship", "sponsor_name", "sponsor_is_in_uk"}.intersection(
        result.case.last_requested_fields
    )
    assert name in result.body


def test_conference_follow_up_which_one_first_keeps_the_invitation_as_first_action(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(
        "我去伦敦参加行业会议，公司付机票酒店。我在深圳工作，持中国护照，在中国申请，日期还没定。",
        _patch(updates=[
            ("visit_purpose", "conference", "参加行业会议"),
            ("funding_source", "employer_or_school", "公司付机票酒店"),
            ("occupation_status", "employed", "在深圳工作"),
            ("nationality_country", "China", "中国护照"),
            ("application_country", "China", "在中国申请"),
        ], deferred=True),
    )
    dialogue.turn(
        "请一次说清楚我要准备的全部资料。",
        _patch(questions=[("document_checklist", "请一次说清楚我要准备的全部资料。")]),
    )

    result = dialogue.turn("先做哪一份？", _patch(questions=[("next_step", "先做哪一份？")]))

    assert result.case.next_step_advice is not None
    assert result.case.next_step_advice.kind == "document"
    assert result.case.next_step_advice.requirement_id == "purpose_evidence"
    assert "会议主办方的邀请函" in result.body
    assert "护照上的姓名" not in result.body
    assert result.case.last_requested_fields == []


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
    assert "日期" in body and any(
        word in body for word in ("确定后", "先留空", "定下来", "以后", "不追问", "正式提交前")
    )
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


@pytest.mark.parametrize("model_topic", ["document_checklist", "unsupported", None])
def test_explicit_full_list_gets_a_complete_personal_adviser_overview_even_when_model_mislabels_it(
    tmp_path, model_topic,
):
    dialogue = Conversation(tmp_path)
    initial = dialogue.turn(*_context("student"))
    profile_before = initial.case.profile.model_dump(mode="json")
    question = "总共需要哪些资料，可以一次性跟我说清楚吗？"
    questions = [] if model_topic is None else [(model_topic, question)]

    result = dialogue.turn(question, _patch(questions=questions))

    assert result.case.profile.model_dump(mode="json") == profile_before
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert set(result.case.deferred_fields) == TRAVEL_FIELDS
    assert all(QUESTION_TEXT_ZH[field] not in result.body for field in TRAVEL_FIELDS)
    assert all(term in result.body for term in ("中国护照", "香港递交", "在读", "自己承担", "日期还没定"))
    assert "在线申请表" in result.body and "证明材料" in result.body
    assert all(term in result.body for term in (
        "父母姓名", "近 10 年旅行记录", "香港合法居留证明", "在读证明",
        "旅游计划", "自费资金", "资金可用及来源", "威尔士文", "译者姓名和签名",
    ))
    assert "不需要为了提供证明而先买机票或订酒店" in result.body
    assert "最先做的一步：先向学校申请在读证明" in result.body
    assert all(url in result.body for url in (ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL))
    assert "资助说明" not in result.body and "固定 3 个月" not in result.body
    assert "保证获批" not in result.body and "已预订" in result.body  # It warns not to claim a booking.
    assert len(dialogue.gmail.calls) == 2


def test_full_personal_overview_respects_no_links_without_removing_the_practical_advice(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(*_context("student"))
    question = "总共需要哪些资料，请一次性说清楚，但不要发链接。"

    result = dialogue.turn(question, _patch(questions=[("document_checklist", question)]))

    assert "https://" not in result.body
    assert all(term in result.body for term in (
        "在线申请表", "香港合法居留证明", "自费资金", "机票或订酒店", "最先做的一步",
    ))
    assert "Check if you need a UK visa" in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize("model_topic", ["sponsor_support", "unsupported", "document_checklist", None])
def test_parent_sponsor_question_gets_a_practical_consultant_answer_across_model_labels(
    tmp_path, model_topic,
):
    dialogue = Conversation(tmp_path)
    initial = dialogue.turn(*_context("parents"))
    profile_before = initial.case.profile.model_dump(mode="json")
    question = "资助信具体应该写什么？父亲承担机票和住宿，父子关系用什么材料说明？"
    questions = [] if model_topic is None else [(model_topic, question)]

    result = dialogue.turn(question, _patch(questions=questions))

    assert result.case.profile.model_dump(mode="json") == profile_before
    assert result.case.customer_question_topics == ([] if model_topic is None else ["sponsor_support"])
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert all(term in result.body for term in (
        "父亲准备承担机票、住宿", "哪一次赴英访问", "具体承担哪些费用", "怎样支付",
        "资金和来源", "出生证明", "官方户籍记录", "不要为了凑材料而编造",
        "最先做的一步", "#if-you-have-a-sponsor",
    ))
    assert "固定余额或流水月数" in result.body
    assert "只能提供出生证明" not in result.body and "保证获批" not in result.body
    assert set(result.case.deferred_fields) == TRAVEL_FIELDS


@pytest.mark.parametrize(
    ("question", "wrong_excerpt", "conditional_text", "not_asserted"),
    [
        (
            "我住姐姐家。姐姐的邀请信要写什么？她不资助我，还要交她的银行流水吗？",
            "她不资助我",
            "如果费用由你自己承担",
            "费用由你自己承担。",
        ),
        (
            "I will stay with my sister. What should her invitation say? She is not paying for the trip; "
            "does she need to provide bank statements?",
            "She is not paying for the trip",
            "If you pay for the trip yourself",
            "You pay for the trip yourself.",
        ),
    ],
)
def test_host_only_negative_does_not_let_a_wrong_model_invent_self_funding(
    tmp_path, question, wrong_excerpt, conditional_text, not_asserted,
):
    result = Conversation(tmp_path).turn(
        question,
        _patch(
            updates=[("funding_source", "self", wrong_excerpt)],
            questions=[("unsupported", question)],
        ),
    )

    assert result.case.profile.funding_source is None
    assert conditional_text in result.body
    assert not_asserted not in result.body
    assert result.case.human_review_reason is None
    assert result.case.status == CaseStatus.DRAFT
    assert "没有核验过的依据" not in result.body
    assert "I don't have reviewed evidence" not in result.body


def test_english_full_overview_is_personal_and_separates_form_information_from_evidence(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn(*_context("student"))
    question = "Could you give me the complete list of documents and explain everything in one message?"

    result = dialogue.turn(question, _patch(questions=[("document_checklist", question)]))

    assert result.case.customer_language == "en"
    assert all(term in result.body for term in (
        "Chinese passport", "apply in Hong Kong", "currently studying", "pay for the trip yourself",
        "Information to prepare for the online form", "Evidence to prepare for your circumstances",
        "travel history for the past 10 years", "lawful residence in Hong Kong", "enrolment",
        "Self-funding", "English or Welsh", "do not need to buy flights or book hotels",
        "Your first practical step", "Apply online",
    ))
    assert all(url in result.body for url in (ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL))
    assert result.case.question_plan == result.case.last_requested_fields == []


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


def test_date_independent_preparation_request_is_answered_as_work_not_an_identity_form(tmp_path):
    dialogue = Conversation(tmp_path)
    initial = dialogue.turn(*_context("student"))

    result = dialogue.turn(
        "日期真的没定，我现在只能先准备不依赖日期的资料。",
        _patch(deferred=True),
    )

    assert result.case.id == initial.case.id
    assert result.case.customer_question_topics == ["next_step"]
    assert all(term in result.body for term in ("预计行程", "不需要", "机票", "酒店"))
    assert "方便告诉我护照上的姓名吗" not in result.body
    assert "你好" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert set(result.case.deferred_fields) == TRAVEL_FIELDS
    assert result.case.profile.model_dump() == initial.case.profile.model_dump()
    assert len(dialogue.gmail.calls) == 2


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
    assert initial.case.last_requested_fields == []
    next_step = "下一步我该准备什么？"
    itinerary_step = dialogue.turn(next_step, _patch(questions=[("next_step", next_step)]))
    assert "预计行程" in itinerary_step.body
    assert itinerary_step.case.last_requested_fields == []
    evidence_step = dialogue.turn(next_step, _patch(questions=[("next_step", next_step)]))
    assert "合法居留" in evidence_step.body
    assert evidence_step.case.last_requested_fields == []
    name_step = dialogue.turn(next_step, _patch(questions=[("next_step", next_step)]))
    assert name_step.case.last_requested_fields == ["full_name"]
    assert len(reply_items(name_step.case)[1]) == 1
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
        assert len(rows) == len(dialogue.gmail.calls) == 5
        assert {row["event_id"] for row in rows} == {
            initial.event.id, itinerary_step.event.id, evidence_step.event.id,
            name_step.event.id, answered.event.id,
        }
        assert all(row["status"] == "SENT" for row in rows)
        assert store.get_case(initial.case.id).model_dump() == answered.case.model_dump()
    finally:
        store.close()
