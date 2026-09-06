"""Captured-Gmail acceptance review for the bounded human-adviser experience.

This is a deterministic workflow evaluation, not a claim that prose naturalness
has been proved objectively.  The fixed patches stand in for model extraction;
the guarded workflow, reopened SQLite state, reviewed Gmail renderer, outbox and
captured transport are the production implementations.  Nothing is sent to a
real mailbox and no paid model is called.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL, ROUTE_CHECK_URL
from visa_agent.workflow.conversation import QUESTION_TEXT_EN, QUESTION_TEXT_ZH, reply_items
from visa_agent.workflow.customer_questions import (
    AFTER_APPLY_SOURCE,
    CANCEL_SOURCE,
    CONTACT_UKVI_SOURCE,
    ROUTE_CHECK_SOURCE,
    VAC_SOURCE,
)
from visa_agent.workflow.guidance_freshness import CHECKED_AT, REVIEW_AFTER
from visa_agent.workflow.service import WorkflowService
from visa_agent.workflow.sponsor_guidance import SPONSOR_SOURCE

TODAY = date(2026, 9, 5)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
APPLICANT = "fictional-acceptance-applicant@example.test"
DEFERRED_TEXT = "旅行日期还没有确定。"
TRAVEL_FIELDS = {"planned_arrival_date", "planned_departure_date"}
LEGACY_ROBOTIC_PHRASES = (
    "还想跟你确认一下：",
    "收到，我再了解一下你的情况。",
    "Could you help me with these details first?",
)
INTERNAL_TERMS = (
    "question_plan",
    "customer_questions",
    "route_confirmed_standard_visitor",
    "source_excerpt",
    "requires_human_review",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The captured-Gmail acceptance review cannot use the network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def patch(*, updates=(), questions=(), deferred=False, requires_human_review=False):
    return CasePatch.model_validate({
        "updates": [
            {"field": field, "value": value, "source_excerpt": excerpt, "confidence": 1}
            for field, value, excerpt in updates
        ],
        "ambiguities": [],
        "requires_human_review": requires_human_review,
        "customer_questions": [
            {"topic": topic, "source_excerpt": excerpt, "confidence": 1}
            for topic, excerpt in questions
        ],
        "question_deferrals": [
            {"field": field, "source_excerpt": DEFERRED_TEXT, "confidence": 1}
            for field in sorted(TRAVEL_FIELDS)
        ] if deferred else [],
    })


class FixedExtraction:
    version = "acceptance-fixed-extraction"

    def __init__(self, proposal: CasePatch) -> None:
        self.proposal = proposal
        self.events: list[InboundEvent] = []
        self.render_calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event.model_copy(deep=True))
        return self.proposal.model_copy(deep=True)

    def render_message(self, case, plan):
        self.render_calls += 1
        return deterministic_fallback_message(case, plan)


class CapturedGmail(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == APPLICANT
        assert kwargs.get("attachment") is None
        self.calls.append(kwargs)
        return {"id": f"captured-acceptance-{len(self.calls)}"}


class CapturedJourney:
    def __init__(self, tmp_path: Path, *, today: date = TODAY) -> None:
        self.path = tmp_path / "consultant-acceptance.db"
        self.gmail = CapturedGmail()
        self.today = today
        self.turns = 0

    def turn(self, body: str, proposal: CasePatch):
        self.turns += 1
        event = InboundEvent(
            id=f"acceptance-turn-{self.turns}",
            external_thread_id="fictional-acceptance-thread",
            sender=APPLICANT,
            subject="英国访问签证准备",
            channel="gmail",
            body=body,
            received_at=datetime(2026, 9, 5, 9, tzinfo=UTC) + timedelta(minutes=self.turns),
            rfc_message_id=f"<acceptance-{self.turns}@example.test>",
        )
        delegate = FixedExtraction(proposal)
        store = SQLiteStore(self.path)
        try:
            guarded = GuardedLLM(delegate, allow_model_rendering=False)
            service = WorkflowService(
                store,
                POLICY,
                guarded,
                today_provider=lambda: self.today,
            )
            case, duplicate, plan = service.process(event)
            assert not duplicate
            assert len(delegate.events) == 1 and delegate.events[0].body == body
            assert not guarded.last_extraction_fallback

            sender = AutomaticGmailReplySender(self.gmail, store, APPLICANT)
            sender.withhold_obsolete_unsent()
            outcomes = OutboxDispatcher(
                store,
                sender,
                channel="gmail",
                allowed_message_types=(
                    "blocked",
                    "awaiting_profile_confirmation",
                    "awaiting_confirmation",
                ),
            ).dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(item for item in store.list_outbox() if item["event_id"] == event.id)
            assert row["status"] == "SENT"
            assert row["payload"] == self.gmail.calls[-1]["body"]
            assert row["reply_render_mode"] == "reviewed"
            assert delegate.render_calls == 0
            assert store.get_case(case.id).model_dump() == case.model_dump()
            _assert_minimum_human_surface(row["payload"], case)
            return SimpleNamespace(case=case, body=row["payload"], event=event, plan=plan)
        finally:
            store.close()


def _urls(text: str) -> list[str]:
    return [match.rstrip(".,;:，。；：") for match in re.findall(r"https://[^\s)]+", text)]


def _assert_minimum_human_surface(body: str, case) -> None:
    """Check observable anti-robot regressions, not subjective human likeness."""
    assert body.strip()
    assert len(reply_items(case)[1]) <= 1, body
    assert all(phrase not in body for phrase in LEGACY_ROBOTIC_PHRASES), body
    assert all(term not in body for term in INTERNAL_TERMS), body
    assert not re.search(r"\b(?:AI|LLM)\b|大模型|工作流引擎|内部字段", body, re.I), body
    assert all(
        url.startswith(("https://www.gov.uk/", "https://visas-immigration.service.gov.uk/"))
        for url in _urls(body)
    ), body


def _known_student_context(*, funding="self"):
    funding_text = "旅行费用由我自己承担。" if funding == "self" else "旅行费用由我父亲承担。"
    funding_value = "self" if funding == "self" else "personal_sponsor"
    updates = [
        ("nationality_country", "China", "我持中国护照。"),
        ("application_country", "Hong Kong", "我会在香港递交申请。"),
        ("visit_purpose", "tourism", "这次去英国旅游。"),
        ("occupation_status", "student", "我是在读大学生。"),
        ("funding_source", funding_value, funding_text),
    ]
    if funding == "sponsor":
        updates.append(("sponsor_relationship", "father", "资助人是我父亲。"))
    body = " ".join([*(item[2] for item in updates), DEFERRED_TEXT])
    return body, patch(updates=updates, deferred=True)


def test_vague_first_enquiry_gives_orientation_sources_and_one_easy_question(tmp_path):
    journey = CapturedJourney(tmp_path)
    question = "我想办英国签证，需要什么？"

    result = journey.turn(question, patch())

    assert all(term in result.body for term in (
        "Standard Visitor", "有效护照或旅行证件", "可用资金和真实来源",
        "完整、可核验的翻译", "合法居留证明", "这次去英国主要是",
        "先用官方查询入口", "选择 Apply now", "决定后面该按旅游",
    )), result.body
    assert "你问了材料" not in result.body and "我记下的是" not in result.body
    assert set(_urls(result.body)) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}
    assert result.case.last_requested_fields == ["visit_purpose"]
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert len(journey.gmail.calls) == 1


@pytest.mark.parametrize(
    ("language", "body", "expected"),
    [
        (
            "zh",
            "我第一次申请英国旅游签证，不知道从哪里开始、要准备什么。",
            ("先用官方查询入口", "有效护照或旅行证件", "决定后面该按旅游"),
        ),
        (
            "en",
            "This is my first UK visitor visa application. I don't know where to start or what to prepare.",
            ("Use the official checker", "valid passport or travel document", "determines whether we prepare"),
        ),
    ],
)
def test_natural_first_application_wording_gets_advice_before_one_question(
    tmp_path, language, body, expected,
):
    result = CapturedJourney(tmp_path).turn(
        body,
        patch(questions=[("document_checklist", body)]),
    )

    assert result.case.customer_language == language
    assert result.case.last_requested_fields == ["visit_purpose"]
    assert len(reply_items(result.case)[1]) == 1
    assert all(term in result.body for term in expected), result.body
    assert set(_urls(result.body)) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}


@pytest.mark.parametrize(
    ("language", "body", "route_excerpt", "question", "acknowledgement"),
    [
        (
            "zh",
            "我已确认按 Standard Visitor 准备，正式申请从哪个官网开始？",
            "已确认按 Standard Visitor 准备",
            "正式申请从哪个官网开始？",
            "已经按你确认的 Standard Visitor 路线继续准备",
        ),
        (
            "en",
            "I have confirmed the Standard Visitor route. Which official website should I use to apply?",
            "confirmed the Standard Visitor route",
            "Which official website should I use to apply?",
            "Thanks for confirming the Standard Visitor route",
        ),
    ],
)
def test_route_confirmation_and_application_entry_read_like_consultant_advice(
    tmp_path, language, body, route_excerpt, question, acknowledgement,
):
    result = CapturedJourney(tmp_path).turn(body, patch(
        updates=[("route_confirmed_standard_visitor", True, route_excerpt)],
        questions=[("application", question)],
    ))

    assert result.case.customer_language == language
    assert result.case.profile.route_confirmed_standard_visitor is True
    assert acknowledgement in result.body
    assert "Standard Visitor" in result.body and "Apply now" in result.body
    assert APPLICATION_URL in result.body
    assert "：True" not in result.body and ": True" not in result.body
    assert result.case.last_requested_fields == []


@pytest.mark.parametrize(
    ("language", "body", "question"),
    [
        (
            "zh",
            "这封回复不要任何链接。请只告诉我正式申请怎么开始。",
            "请只告诉我正式申请怎么开始。",
        ),
        (
            "en",
            "Please do not include any links in this reply. Just tell me how to start the formal application.",
            "Just tell me how to start the formal application.",
        ),
    ],
)
def test_no_link_preference_keeps_a_named_entry_and_action_without_dangling_below(
    tmp_path, language, body, question,
):
    result = CapturedJourney(tmp_path).turn(
        body,
        patch(questions=[("application", question)]),
    )

    assert result.case.customer_language == language
    assert "Standard Visitor" in result.body and "Apply now" in result.body
    assert "在线申请页" in result.body if language == "zh" else "online application page" in result.body
    assert "https://" not in result.body and "GOV.UK:" not in result.body
    assert "下方" not in result.body and "below" not in result.body.casefold()
    assert result.case.last_requested_fields == []


@pytest.mark.parametrize(
    ("language", "body", "topic", "expected", "sources"),
    [
        (
            "zh",
            "在线申请交了以后，去哪里录指纹，要带什么？",
            "biometrics",
            ("预约签证申请中心", "指纹和照片", "确认自己实际能够到场"),
            (APPLICATION_URL, VAC_SOURCE),
        ),
        (
            "en",
            "After I submit the online form, where do I give fingerprints and what should I bring?",
            "biometrics",
            ("book a visa application centre appointment", "fingerprints and a photograph", "confirm that you can attend"),
            (APPLICATION_URL, VAC_SOURCE),
        ),
        (
            "zh",
            "我已经递交和录完指纹了，之后怎么查进度？",
            "after_apply",
            ("通常在 3 周内", "通常不需联系 UKVI", "查看垃圾邮件箱"),
            (AFTER_APPLY_SOURCE,),
        ),
        (
            "en",
            "I submitted the application and gave biometrics. What happens next and how do I track it?",
            "after_apply",
            ("usually takes up to 3 weeks", "do not need to contact UKVI", "check the spam or junk folder"),
            (AFTER_APPLY_SOURCE,),
        ),
        (
            "zh",
            "我已经递交申请，但地址写错了。怎么更正，或者撤回申请？费用会退吗？",
            "after_apply",
            ("不要假定再发一封说明就会自动改表", "退款取决于", "不能再叫停撤回"),
            (CONTACT_UKVI_SOURCE, CANCEL_SOURCE),
        ),
        (
            "en",
            "I submitted the application but my address is wrong. How can I correct it or withdraw the "
            "application? Will the fee be refunded?",
            "after_apply",
            ("automatically changes the form", "refund depends on the processing stage", "cannot be stopped"),
            (CONTACT_UKVI_SOURCE, CANCEL_SOURCE),
        ),
    ],
)
def test_process_questions_give_a_judgement_one_action_and_official_entry(
    tmp_path, language, body, topic, expected, sources,
):
    result = CapturedJourney(tmp_path).turn(
        body,
        patch(questions=[(topic, body)]),
    )

    assert result.case.customer_language == language
    assert all(term in result.body for term in expected), result.body
    assert all(source in result.body for source in sources)
    assert result.case.last_requested_fields == []


def test_same_turn_known_context_is_not_described_as_unknown(tmp_path):
    journey = CapturedJourney(tmp_path)
    body = (
        "我持中国护照，在香港递交，这次去英国旅游，是在读学生且费用自理。"
        "请告诉我需要哪些材料。"
    )

    result = journey.turn(body, patch(
        updates=[
            ("nationality_country", "China", "我持中国护照"),
            ("application_country", "Hong Kong", "在香港递交"),
            ("visit_purpose", "tourism", "这次去英国旅游"),
            ("occupation_status", "student", "是在读学生"),
            ("funding_source", "self", "费用自理"),
        ],
        questions=[("document_checklist", "需要哪些材料")],
    ))

    assert all(term in result.body for term in (
        "中国护照", "香港递交", "在读", "费用由你自己承担",
    )), result.body
    assert "还不知道你的护照、赴英目的和申请地点" not in result.body
    assert all(term in result.body for term in (
        "在读证明", "真实的计划行程", "资金从哪里来", "合法居留",
    ))
    assert all(term in result.body for term in (
        "建议从这里开始", "网银", "官方入口和材料依据", "在线申请",
    ))
    assert "我记下的是" not in result.body
    assert result.case.last_requested_fields == []


@pytest.mark.parametrize(
    ("language", "body", "updates", "expected"),
    [
        (
            "zh",
            "我持中国护照，会在香港申请，去英国旅游。我目前在读书，费用由自己承担。"
            "计划2026年10月10日到英国，2026年10月17日离开。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "tourism", "去英国旅游"),
                ("occupation_status", "student", "目前在读书"),
                ("funding_source", "self", "费用由自己承担"),
                ("planned_arrival_date", "2026-10-10", "2026年10月10日到英国"),
                ("planned_departure_date", "2026-10-17", "2026年10月17日离开"),
            ],
            ("你目前在读书", "在读证明", "银行流水", "资金来源"),
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong for a UK holiday. I am a student and "
            "will pay for the trip myself. I plan to arrive on 10 October 2026 and leave on 17 October 2026.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "tourism", "UK holiday"),
                ("occupation_status", "student", "I am a student"),
                ("funding_source", "self", "pay for the trip myself"),
                ("planned_arrival_date", "2026-10-10", "arrive on 10 October 2026"),
                ("planned_departure_date", "2026-10-17", "leave on 17 October 2026"),
            ],
            ("self-funded student", "letter confirming your enrolment", "bank statements", "accessible funds"),
        ),
        (
            "zh",
            "我持中国护照，会在香港申请，去英国旅游。我目前受雇工作，费用由自己承担。"
            "计划2026年10月10日到英国，2026年10月17日离开。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "tourism", "去英国旅游"),
                ("occupation_status", "employed", "目前受雇工作"),
                ("funding_source", "self", "费用由自己承担"),
                ("planned_arrival_date", "2026-10-10", "2026年10月10日到英国"),
                ("planned_departure_date", "2026-10-17", "2026年10月17日离开"),
            ],
            ("你现在在职", "向公司人事索取", "职位、薪资和入职时间"),
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong for a UK holiday. I am employed and "
            "will pay for the trip myself. I plan to arrive on 10 October 2026 and leave on 17 October 2026.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "tourism", "UK holiday"),
                ("occupation_status", "employed", "I am employed"),
                ("funding_source", "self", "pay for the trip myself"),
                ("planned_arrival_date", "2026-10-10", "arrive on 10 October 2026"),
                ("planned_departure_date", "2026-10-17", "leave on 17 October 2026"),
            ],
            ("your employment and income", "asking HR", "role, salary and how long"),
        ),
        (
            "zh",
            "我持中国护照，会在香港申请，去英国旅游。我在读书，父亲陈建国资助这次旅行，他不住在英国。"
            "计划2026年10月10日到英国，2026年10月17日离开。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "tourism", "去英国旅游"),
                ("occupation_status", "student", "在读书"),
                ("funding_source", "personal_sponsor", "父亲陈建国资助这次旅行"),
                ("sponsor_relationship", "father", "父亲陈建国"),
                ("sponsor_name", "陈建国", "父亲陈建国"),
                ("sponsor_is_in_uk", False, "他不住在英国"),
                ("planned_arrival_date", "2026-10-10", "2026年10月10日到英国"),
                ("planned_departure_date", "2026-10-17", "2026年10月17日离开"),
            ],
            ("由父亲资助", "资助说明", "具体费用", "资金材料与关系材料分开"),
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong for a UK holiday. I am a student. My "
            "father Jian Chen will sponsor this trip and he does not live in the UK. I plan to arrive on "
            "10 October 2026 and leave on 17 October 2026.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "tourism", "UK holiday"),
                ("occupation_status", "student", "I am a student"),
                ("funding_source", "personal_sponsor", "father Jian Chen will sponsor this trip"),
                ("sponsor_relationship", "father", "father Jian Chen"),
                ("sponsor_name", "Jian Chen", "father Jian Chen"),
                ("sponsor_is_in_uk", False, "he does not live in the UK"),
                ("planned_arrival_date", "2026-10-10", "arrive on 10 October 2026"),
                ("planned_departure_date", "2026-10-17", "leave on 17 October 2026"),
            ],
            ("father is funding this trip", "short statement", "exact costs", "relationship evidence separate"),
        ),
        (
            "zh",
            "我持中国护照，会在香港申请，去英国探望姐姐。我会住在姐姐家，但旅行费用由我自己承担。"
            "我目前受雇工作，计划2026年10月10日到英国，2026年10月17日离开。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "family_or_friends", "探望姐姐"),
                ("uk_accommodation", "姐姐家", "住在姐姐家"),
                ("occupation_status", "employed", "目前受雇工作"),
                ("funding_source", "self", "旅行费用由我自己承担"),
                ("planned_arrival_date", "2026-10-10", "2026年10月10日到英国"),
                ("planned_departure_date", "2026-10-17", "2026年10月17日离开"),
            ],
            ("探亲访友", "简短的邀请说明", "住宿安排", "不用把接待写成经济资助"),
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong to visit my sister. I will stay at her "
            "home but will pay for the trip myself. I am employed. I plan to arrive on 10 October 2026 and "
            "leave on 17 October 2026.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "family_or_friends", "visit my sister"),
                ("uk_accommodation", "my sister's home", "stay at her home"),
                ("occupation_status", "employed", "I am employed"),
                ("funding_source", "self", "pay for the trip myself"),
                ("planned_arrival_date", "2026-10-10", "arrive on 10 October 2026"),
                ("planned_departure_date", "2026-10-17", "leave on 17 October 2026"),
            ],
            ("visit to family or friends", "short invitation", "accommodation plans", "should not describe your host as funding"),
        ),
        (
            "zh",
            "我持中国护照，会在香港申请，去伦敦参加行业会议。我目前受雇工作，公司承担机票和酒店。"
            "计划2026年10月10日到英国，2026年10月17日离开。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "conference", "参加行业会议"),
                ("occupation_status", "employed", "目前受雇工作"),
                ("funding_source", "employer_or_school", "公司承担机票和酒店"),
                ("planned_arrival_date", "2026-10-10", "2026年10月10日到英国"),
                ("planned_departure_date", "2026-10-17", "2026年10月17日离开"),
            ],
            ("最先只做一件事", "主办方出具邀请函", "不能代替单位资助证明"),
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong to attend an industry conference in "
            "London. I am employed and my company will cover flights and the hotel. I plan to arrive on "
            "10 October 2026 and leave on 17 October 2026.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "conference", "industry conference"),
                ("occupation_status", "employed", "I am employed"),
                ("funding_source", "employer_or_school", "company will cover flights and the hotel"),
                ("planned_arrival_date", "2026-10-10", "arrive on 10 October 2026"),
                ("planned_departure_date", "2026-10-17", "leave on 17 October 2026"),
            ],
            ("first action is one document", "ask the organiser for an invitation", "does not replace evidence"),
        ),
    ],
)
def test_known_circumstances_receive_case_specific_human_advice_in_both_languages(
    tmp_path, language, body, updates, expected,
):
    result = CapturedJourney(tmp_path).turn(body, patch(updates=updates))

    assert result.case.customer_language == language
    # The tailored guidance may acknowledge a fact without a duplicate intake
    # receipt. Verify the structured fact too, not one fixed receipt phrasing.
    for field, value, _ in updates:
        if field in {"visit_purpose", "occupation_status", "funding_source"}:
            assert getattr(result.case.profile, field) == value
    assert all(term.casefold() in result.body.casefold() for term in expected), result.body
    questions = QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN
    assert questions["planned_arrival_date"] not in result.body
    assert questions["planned_departure_date"] not in result.body
    assert not TRAVEL_FIELDS.intersection(result.case.last_requested_fields)
    assert len(reply_items(result.case)[1]) <= 1


def test_combined_process_materials_fee_and_timing_keeps_every_obligation(tmp_path):
    journey = CapturedJourney(tmp_path)
    body = "请一次性告诉我英国访问签证在哪里和怎么申请、要哪些材料、费用多少、多久出结果。"

    result = journey.turn(body, patch(questions=[
        ("application", "在哪里和怎么申请"),
        ("document_checklist", "要哪些材料"),
        ("fees", "费用多少"),
        ("timing", "多久出结果"),
    ]))

    assert all(term in result.body for term in (
        "Apply now", "在线填写申请", "预约签证中心", "有效护照或旅行证件",
        "在职、在读或自雇", "可用资金和真实来源", "完整、可核验的翻译",
        "合法居留证明", "£135", "3 周", "不保证按时出结果",
    )), result.body
    assert set(_urls(result.body)) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}
    assert all(_urls(result.body).count(url) == 1 for url in set(_urls(result.body)))
    assert result.case.last_requested_fields == []


def test_expired_guidance_withholds_prices_times_categories_and_links(tmp_path):
    journey = CapturedJourney(tmp_path, today=REVIEW_AFTER + timedelta(days=1))
    body = "英国访问签证怎么申请，要什么材料，多少钱，多久出结果？"

    result = journey.turn(body, patch(questions=[
        ("application", "怎么申请"),
        ("document_checklist", "要什么材料"),
        ("fees", "多少钱"),
        ("timing", "多久出结果"),
    ]))

    assert "复核最新 GOV.UK" in result.body
    assert "https://" not in result.body
    assert all(term not in result.body for term in (
        "£135", "3 周", "有效护照或旅行证件", "可用资金和真实来源",
    ))
    assert result.case.last_requested_fields == []


def test_repeated_undecided_dates_are_retained_and_never_asked_again(tmp_path):
    journey = CapturedJourney(tmp_path)
    turns = [journey.turn(*_known_student_context())]
    turns.append(journey.turn(
        "旅行日期还没有确定，但其他资料可以继续准备。",
        patch(deferred=True),
    ))
    turns.append(journey.turn(
        "我确实还没决定出发和回程日期，请先别再问日期。",
        patch(deferred=True),
    ))

    for result in turns:
        assert set(result.case.deferred_fields) == TRAVEL_FIELDS
        assert result.case.profile.planned_arrival_date is None
        assert result.case.profile.planned_departure_date is None
        assert not TRAVEL_FIELDS.intersection(result.case.last_requested_fields)
        assert all(QUESTION_TEXT_ZH[field] not in result.body for field in TRAVEL_FIELDS)
    assert any("日期" in result.body for result in turns)
    assert "没问题，日期先留空" in turns[-1].body
    assert "材料准备阶段先不追问日期" in turns[-1].body
    assert "？" not in turns[-1].body
    assert len(journey.gmail.calls) == 3


def test_correction_updates_the_case_and_switches_to_sponsor_specific_action(tmp_path):
    journey = CapturedJourney(tmp_path)
    initial = journey.turn(*_known_student_context())
    correction = "更正一下。我父亲会为我支付这次旅行费用。此前记录的自费不对。旅行日期还没有确定。"

    result = journey.turn(correction, patch(
        updates=[
            ("funding_source", "personal_sponsor", "我父亲会为我支付这次旅行费用"),
            ("sponsor_relationship", "father", "我父亲会为我支付这次旅行费用"),
        ],
        deferred=True,
    ))

    assert initial.case.profile.funding_source == "self"
    assert result.case.profile.funding_source == "personal_sponsor"
    assert result.case.profile.sponsor_relationship == "father"
    assert all(term in result.body for term in (
        "改为由父亲资助", "资助说明", "具体费用", "怎样支付",
    )), result.body
    assert "费用由你自己承担" not in result.body
    assert not TRAVEL_FIELDS.intersection(result.case.last_requested_fields)


def test_explicit_unknown_replacement_sponsor_retires_old_identity_and_location(tmp_path):
    journey = CapturedJourney(tmp_path)
    original = (
        "I hold a Chinese passport and will apply in Hong Kong for a holiday. "
        "I am a student. My father Jian Chen will sponsor my trip and he lives in the UK. "
        "My travel dates are not decided yet."
    )
    first = journey.turn(original, patch(
        updates=[
            ("nationality_country", "China", "Chinese passport"),
            ("application_country", "Hong Kong", "apply in Hong Kong"),
            ("visit_purpose", "tourism", "holiday"),
            ("occupation_status", "student", "I am a student"),
            ("funding_source", "personal_sponsor", "My father Jian Chen will sponsor my trip"),
            ("sponsor_relationship", "father", "My father Jian Chen will sponsor my trip"),
            ("sponsor_name", "Jian Chen", "Jian Chen"),
            ("sponsor_is_in_uk", True, "he lives in the UK"),
        ],
        deferred=True,
    ))
    assert first.case.profile.sponsor_name == "Jian Chen"
    assert first.case.profile.sponsor_relationship == "father"
    assert first.case.profile.sponsor_is_in_uk is True

    replacement = "Plans changed: someone else will sponsor my trip instead."
    result = journey.turn(replacement, patch(updates=[
        ("funding_source", "personal_sponsor", "someone else will sponsor my trip instead"),
    ]))

    assert result.case.profile.funding_source == "personal_sponsor"
    assert result.case.profile.sponsor_name is None
    assert result.case.profile.sponsor_relationship is None
    assert result.case.profile.sponsor_is_in_uk is None
    assert all(
        item.superseded
        for item in result.case.evidence
        if item.fact_key in {"sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"}
        and item.source_event_id == first.event.id
    )


def test_named_replacement_keeps_new_identity_but_never_inherits_old_location(tmp_path):
    journey = CapturedJourney(tmp_path)
    original = (
        "I hold a Chinese passport and will apply in Hong Kong for a holiday. "
        "I am a student. My father Jian Chen will sponsor my trip and he lives in the UK. "
        "My travel dates are not decided yet."
    )
    first = journey.turn(original, patch(
        updates=[
            ("nationality_country", "China", "Chinese passport"),
            ("application_country", "Hong Kong", "apply in Hong Kong"),
            ("visit_purpose", "tourism", "holiday"),
            ("occupation_status", "student", "I am a student"),
            ("funding_source", "personal_sponsor", "My father Jian Chen will sponsor my trip"),
            ("sponsor_relationship", "father", "My father Jian Chen will sponsor my trip"),
            ("sponsor_name", "Jian Chen", "Jian Chen"),
            ("sponsor_is_in_uk", True, "he lives in the UK"),
        ],
        deferred=True,
    ))
    assert first.case.profile.sponsor_is_in_uk is True

    replacement = "My father cannot pay, so my mother Mei Chen will sponsor my trip instead."
    result = journey.turn(replacement, patch(updates=[
        ("funding_source", "personal_sponsor", "my mother Mei Chen will sponsor my trip instead"),
        ("sponsor_relationship", "mother", "my mother Mei Chen will sponsor my trip instead"),
        ("sponsor_name", "Mei Chen", "Mei Chen"),
    ]))

    assert result.case.profile.funding_source == "personal_sponsor"
    assert result.case.profile.sponsor_relationship == "mother"
    assert result.case.profile.sponsor_name == "Mei Chen"
    assert result.case.profile.sponsor_is_in_uk is None
    assert all(
        item.superseded
        for item in result.case.evidence
        if item.fact_key in {"sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"}
        and item.source_event_id == first.event.id
    )


def test_personal_sponsor_question_gets_specific_materials_and_a_first_action(tmp_path):
    journey = CapturedJourney(tmp_path)
    body = "这次去英国旅游，费用由我父亲承担。旅行日期还没有确定。资助信怎么写？"

    result = journey.turn(body, patch(
        updates=[
            ("visit_purpose", "tourism", "去英国旅游"),
            ("funding_source", "personal_sponsor", "费用由我父亲承担"),
            ("sponsor_relationship", "father", "我父亲"),
        ],
        deferred=True,
        questions=[("sponsor_support", "资助信怎么写？")],
    ))

    assert all(term in result.body for term in (
        "父亲的资助承诺", "对应的是哪一次赴英访问", "具体承担哪些费用",
        "怎样支付", "资助人可用的资金和来源", "出生证明或官方户籍记录", "最先做的一步",
    )), result.body
    assert SPONSOR_SOURCE in result.body
    assert "固定余额或流水月数" in result.body
    assert "保证获批" not in result.body
    assert result.case.last_requested_fields == []


def test_parent_sponsor_guidance_sends_identity_pair_and_accepts_the_natural_short_reply(tmp_path):
    journey = CapturedJourney(tmp_path)
    first_body = (
        "我持中国护照，在香港申请，去英国旅游。"
        "我目前在读书，费用由我父母资助，日期还没定。"
    )
    first = journey.turn(first_body, patch(
        updates=[
            ("nationality_country", "China", "我持中国护照"),
            ("application_country", "Hong Kong", "在香港申请"),
            ("visit_purpose", "tourism", "去英国旅游"),
            ("occupation_status", "student", "目前在读书"),
            ("funding_source", "personal_sponsor", "费用由我父母资助"),
        ],
        deferred=True,
    ))

    assert first.case.profile.funding_source == "personal_sponsor"
    assert first.case.profile.sponsor_relationship is None
    assert first.case.last_requested_fields == ["sponsor_relationship", "sponsor_name"]
    assert first.case.question_event_ids["sponsor_relationship"] == [first.event.id]
    assert first.case.question_event_ids["sponsor_name"] == [first.event.id]
    assert all(term in first.body for term in (
        "父母资助", "资助说明", "具体费用", "父亲、母亲", "两位共同", "姓名",
    )), first.body
    assert first.body.index("资助说明") < first.body.index("父亲、母亲")

    reply = "是父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国。"
    second = journey.turn(reply, patch(updates=[
        ("sponsor_relationship", "parents", "父母两位共同资助"),
        ("sponsor_name", "陈国强、李美兰", "父亲陈国强、母亲李美兰"),
        ("sponsor_is_in_uk", False, "他们都不住在英国"),
    ]))

    assert second.case.profile.sponsor_relationship == "parents"
    assert second.case.profile.sponsor_name == "陈国强、李美兰"
    assert second.case.profile.sponsor_is_in_uk is False
    active = {
        item.fact_key: (item.value, item.source_event_id)
        for item in second.case.evidence
        if item.fact_key.startswith("sponsor_") and not item.superseded
    }
    assert active == {
        "sponsor_relationship": ("parents", second.event.id),
        "sponsor_name": ("陈国强、李美兰", second.event.id),
        "sponsor_is_in_uk": (False, second.event.id),
    }
    assert not {"sponsor_relationship", "sponsor_name", "sponsor_is_in_uk"}.intersection(
        second.case.last_requested_fields
    )
    assert all(term in second.body for term in ("父母共同资助", "陈国强、李美兰", "不住在英国"))
    assert "这次由谁资助你" not in second.body
    assert second.case.status == CaseStatus.DRAFT
    assert not second.case.profile_confirmed and not second.case.final_summary_confirmed
    assert second.case.delivery_path is None
    assert not evaluate_gate(second.case.model_copy(deep=True), POLICY, TODAY).allowed
    assert len(journey.gmail.calls) == 2


@pytest.mark.parametrize(
    ("purpose", "purpose_text", "expected"),
    [
        (
            "business",
            "去伦敦拜访客户并开会",
            ("雇主会承担机票、住宿", "正式抬头说明", "具体费用", "怎样支付", "可核实的联系人"),
        ),
        (
            "conference",
            "去伦敦参加行业会议",
            ("主办方出具邀请函", "会议或活动名称", "时间地点", "正式抬头说明", "不能代替单位资助证明"),
        ),
    ],
)
def test_business_and_conference_cases_get_distinct_coherent_evidence_advice(
    tmp_path, purpose, purpose_text, expected,
):
    journey = CapturedJourney(tmp_path)
    body = (
        f"我持中国护照，在香港递交，目前受雇工作，{purpose_text}。"
        "公司会承担机票和住宿。请告诉我现在要准备什么。"
    )

    result = journey.turn(body, patch(
        updates=[
            ("nationality_country", "China", "我持中国护照"),
            ("application_country", "Hong Kong", "在香港递交"),
            ("occupation_status", "employed", "目前受雇工作"),
            ("visit_purpose", purpose, purpose_text),
            ("funding_source", "employer_or_school", "公司会承担机票和住宿"),
        ],
        questions=[("next_step", "请告诉我现在要准备什么。")],
    ))

    assert all(term in result.body for term in expected), result.body
    assert APPLICATION_URL in result.body and SPONSOR_SOURCE in result.body
    if purpose == "conference":
        assert DOCUMENTS_URL + "#attendees-of-business-related-events-or-conferences" in result.body
    else:
        assert "主办方出具邀请函" not in result.body
    assert set(result.case.last_requested_fields) == TRAVEL_FIELDS
    assert len(reply_items(result.case)[1]) == 1


@pytest.mark.parametrize(
    ("body", "excerpt"),
    [
        (
            "我在2022年有一次加拿大签证拒签。现在申请英国旅游签证要怎么办？",
            "我在2022年有一次加拿大签证拒签",
        ),
        (
            "我之前有过逾期停留和遣返记录，现在申请英国访问签证要怎么处理？",
            "我之前有过逾期停留和遣返记录",
        ),
        (
            "我有一次刑事定罪记录，英国访问签证材料应该怎么准备？",
            "我有一次刑事定罪记录",
        ),
    ],
    ids=["prior-refusal", "immigration-history", "criminal-history"],
)
def test_serious_history_stops_automation_and_gives_a_concrete_handoff_action(
    tmp_path, body, excerpt,
):
    journey = CapturedJourney(tmp_path)

    result = journey.turn(body, patch(
        updates=[("has_serious_history", True, excerpt)],
        questions=[("unsupported", body)],
    ))

    assert result.case.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    assert result.case.profile.has_serious_history is True
    assert result.case.last_requested_fields == []
    assert all(term in result.body for term in (
        "已记下", "人工顾问", "相关记录", "不用重发",
    )), result.body
    # A human-like handoff must tell the applicant what the reviewer needs next,
    # without predicting eligibility or continuing automated assessment.
    assert all(term in result.body for term in (
        "完整文件", "国家或地区", "发生日期", "原因", "后续结果", "不要猜或省略",
    )), result.body
    assert all(term not in result.body for term in (
        "一定拒签", "一定获批", "保证获批", "可以忽略", "不需要申报",
    ))


def test_repeated_next_step_preserves_the_open_question_but_keeps_giving_new_work(tmp_path):
    journey = CapturedJourney(tmp_path)
    first = journey.turn(*_known_student_context())
    request = "下一步我该准备什么？"
    next_step_patch = patch(questions=[("next_step", request)])
    actions: list[str] = []
    asked = None
    for _ in range(7):
        current = journey.turn(request, next_step_patch)
        actions.append(current.body)
        if current.case.last_requested_fields == ["full_name"]:
            asked = current
            break

    assert asked is not None
    assert "预计行程" in "\n".join(actions)
    assert "合法居留" in "\n".join(actions)
    assert QUESTION_TEXT_ZH["full_name"] in asked.body
    ledger = {field: list(ids) for field, ids in asked.case.question_event_ids.items()}

    repeated = journey.turn(
        "姓名我稍后补，现在还能先做什么？",
        patch(questions=[("next_step", "现在还能先做什么？")]),
    )
    again_text = "那之后还有什么不依赖姓名的事可以先做？"
    again = journey.turn(
        again_text,
        patch(questions=[("next_step", again_text)]),
    )

    assert QUESTION_TEXT_ZH["full_name"] not in repeated.body + again.body
    assert all(term in repeated.body for term in ("护照资料页", "清晰、完整的 PDF", "页面四边不要裁掉"))
    assert "居留身份文件" in again.body
    assert repeated.case.pending_question_fields == ["full_name"]
    assert again.case.pending_question_fields == ["full_name"]
    assert repeated.case.question_event_ids == ledger
    assert again.case.question_event_ids == ledger
    assert reply_items(repeated.case)[1] == reply_items(again.case)[1] == []
    assert first.body.count(DOCUMENTS_URL) == 1
    assert sum(item.count(DOCUMENTS_URL) for item in actions + [repeated.body, again.body]) == 0
    assert len(set((actions[-2] if len(actions) > 1 else "", repeated.body, again.body))) == 3


def test_acceptance_review_date_is_inside_the_verified_guidance_window():
    assert CHECKED_AT <= TODAY <= REVIEW_AFTER
    assert POLICY.is_current(TODAY)
    assert ROUTE_CHECK_SOURCE == ROUTE_CHECK_URL
