"""Usefulness regressions with fictional extraction and a captured real sender.

These are deterministic workflow tests, not a live-model naturalness score.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, CustomerQuestion
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL
from visa_agent.workflow.conversation import QUESTION_TEXT_ZH
from visa_agent.workflow.customer_questions import grounded_customer_answers
from visa_agent.workflow.service import WorkflowService

TODAY = date(2026, 9, 4)
CONTACT = "fictional-information-first@example.test"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Fictional adviser regression cannot access a network")
    monkeypatch.setattr("socket.socket.connect", reject)
    monkeypatch.setattr("socket.create_connection", reject)


class Capture(GmailAdapter):
    def __init__(self):
        self.bodies = []

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == CONTACT and kwargs.get("attachment") is None
        self.bodies.append(kwargs["body"])
        return {"id": f"captured-information-{len(self.bodies)}"}


class Model:
    def __init__(self, patch):
        self.patch = patch

    def extract_case_patch(self, event):
        return self.patch.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


def turn(tmp_path, transport, body, patch):
    event = InboundEvent(id=f"information-{len(transport.bodies)}", channel="gmail",
        external_thread_id="fictional-information-thread", sender=CONTACT, subject="英国签证咨询",
        body=body, received_at=datetime(2026, 9, 4, 11, tzinfo=UTC) + timedelta(minutes=len(transport.bodies)),
        rfc_message_id=f"<information-{len(transport.bodies)}@example.test>")
    store = SQLiteStore(tmp_path / "information.db")
    try:
        workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                                   Model(patch), today_provider=lambda: TODAY)
        case, duplicate, plan = workflow.process(event)
        assert not duplicate and plan == "blocked"
        result = OutboxDispatcher(store, AutomaticGmailReplySender(transport, store, CONTACT),
            channel="gmail", allowed_message_types=("blocked",)).dispatch_due(event.received_at)
        assert len(result) == 1 and result[0].status == "SENT"
        assert store.get_case(case.id).model_dump() == case.model_dump()
        assert not case.profile_confirmed and not case.final_summary_confirmed and not case.delivery_path
        return case, transport.bodies[-1]
    finally:
        store.close()


def exploratory_context():
    values = [
        ("nationality_country", "China", "我持中国护照"),
        ("application_country", "Hong Kong", "在香港申请"),
        ("visit_purpose", "tourism", "去英国旅游"),
        ("occupation_status", "student", "我在香港读大学"),
        ("funding_source", "self", "费用自己承担"),
    ]
    body = "，".join(text for _, _, text in values) + "。旅行日期还没有确定，想先了解准备过程。"
    return body, CasePatch.model_validate({"updates": [
        {"field": field, "value": value, "source_excerpt": excerpt, "confidence": 1}
        for field, value, excerpt in values], "ambiguities": []})


def test_exploratory_advice_does_not_start_an_identity_questionnaire_and_can_later_continue(tmp_path):
    transport = Capture()
    case, reply = turn(tmp_path, transport, *exploratory_context())
    assert APPLICATION_URL in reply and "在读证明" in reply and "银行流水" in reply
    assert case.last_requested_fields == [] and case.question_event_ids == {}
    assert all(question not in reply for question in QUESTION_TEXT_ZH.values())
    assert set(case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert not case.preparation_paused and case.profile.full_name is None
    # An actual later request can start intake; consultation did not pause or
    # waive any required fact, invent a name, or create an invisible SENT question.
    request = "下一步我们开始整理个人资料吧。"
    continued, _ = turn(tmp_path, transport, request, CasePatch(updates=[], ambiguities=[],
        customer_questions=[CustomerQuestion(topic="next_step", source_excerpt=request, confidence=1)]))
    assert continued.id == case.id and continued.last_requested_fields == ["full_name"]
    assert set(continued.deferred_fields) == set(case.deferred_fields)


@pytest.mark.parametrize("body", [
    "For my UK visa, how do I explain being self-employed in Hong Kong?",
    "For my UK visa, how do I explain my current employment in Singapore?",
    "在英国签证申请中怎么说明我在香港的工作？",
])
def test_existing_foreign_employment_is_not_misrepresented_as_planned_uk_work(body):
    text = "\n".join(grounded_customer_answers(body, "en", TODAY, semantic_questions=[
        CustomerQuestion(topic="unsupported", source_excerpt=body, confidence=1)]))
    assert "On working in the UK" not in text and "关于在英国工作" not in text
    assert "Standard Visitors generally cannot" not in text


@pytest.mark.parametrize("body", ["Can I work part-time in the UK as a Standard Visitor?",
    "Can I do a paid job during a UK visit?", "我能在英国旅行时做兼职吗？"])
def test_an_actual_uk_work_activity_question_retains_its_boundary(body):
    text = "\n".join(grounded_customer_answers(body, "en", TODAY, semantic_questions=[
        CustomerQuestion(topic="unsupported", source_excerpt=body, confidence=1)]))
    assert "On working in the UK" in text and "https://www.gov.uk/standard-visitor" in text


@pytest.mark.parametrize("topic", ["document_checklist", "next_step", "unsupported", None])
@pytest.mark.parametrize(("question", "required", "irrelevant"), [
    ("学校的在读证明该找谁开，里面需要写什么？", ("学校", "抬头纸", "在读", "请假"), "接下来还需要这些材料"),
    ("在职证明怎么准备，应该找谁开，里面写什么？", ("人事", "职位", "薪资", "任职时间"), "在读证明"),
    ("我自己经营业务，没有HR，在职证明怎么办？", ("经营登记", "业务发票", "不存在的雇主"), "向公司人事"),
    ("邀请信主要写什么，姐姐只是让我住她家，不出旅行费用。", ("接待", "访问目的", "谁负担哪些费用"), "接下来还需要这些材料"),
])
def test_specific_document_help_survives_three_model_labels_without_a_questionnaire(
    tmp_path, topic, question, required, irrelevant,
):
    transport = Capture()
    initial, _ = turn(tmp_path, transport, *exploratory_context())
    patch = CasePatch(updates=[], ambiguities=[], customer_questions=[
        CustomerQuestion(topic=topic, source_excerpt=question, confidence=1)] if topic else [])
    case, text = turn(tmp_path, transport, question, patch)
    assert case.id == initial.id and case.profile == initial.profile
    assert case.deferred_fields == initial.deferred_fields and case.last_requested_fields == []
    assert all(word in text for word in required), text
    assert irrelevant not in text and "没有核验过的依据" not in text
    assert "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/" in text
    assert all(q not in text for q in QUESTION_TEXT_ZH.values())


@pytest.mark.parametrize("topic", ["document_checklist", "next_step", "unsupported"])
def test_self_employed_visitor_gets_home_business_preparation_not_uk_work_warning(tmp_path, topic):
    question = "For my UK visa, how do I explain being self-employed in Hong Kong?"
    _, text = turn(tmp_path, Capture(), question, CasePatch(updates=[], ambiguities=[],
        customer_questions=[CustomerQuestion(topic=topic, source_excerpt=question, confidence=1)]))
    assert "registration records" in text and "business invoices" in text, text
    assert "On working in the UK" not in text and "name as it appears" not in text


@pytest.mark.parametrize("question", [
    "学校的在读证明该找谁开，写什么才能保证我获批？",
    "如果学校能保证过签，在读证明该找谁开，里面需要写什么？",
    "我在申请学生签证。学校的在读证明该找谁开，里面需要写什么？",
])
def test_operational_question_does_not_strip_an_outcome_or_other_route_qualifier(question):
    answers = grounded_customer_answers(question, "zh", TODAY, semantic_questions=[
        CustomerQuestion(topic="unsupported", source_excerpt=question, confidence=1)])
    assert all("可以先问学校" not in answer for answer in answers)


def test_paused_case_can_get_specific_invitation_help_without_restarting_or_assuming_sponsorship(tmp_path):
    transport = Capture()
    initial, _ = turn(tmp_path, transport, *exploratory_context())
    pause = "请暂停我的材料准备。"
    paused, _ = turn(tmp_path, transport, pause, CasePatch.model_validate({
        "updates": [], "ambiguities": [], "preparation_intent": {
            "action": "pause", "source_excerpt": pause, "confidence": 1}}))
    question = "我只想了解邀请信主要写什么，姐姐只是让我住她家，不出旅行费用。"
    result, reply = turn(tmp_path, transport, question, CasePatch(updates=[], ambiguities=[],
        customer_questions=[CustomerQuestion(topic="unsupported", source_excerpt=question, confidence=1)]))
    assert result.id == initial.id and result.preparation_paused
    assert result.preparation_control_epoch == paused.preparation_control_epoch
    assert result.profile == paused.profile and result.profile.funding_source == "self"
    assert result.last_requested_fields == [] and "谁负担哪些费用" in reply
    assert "访问目的" in reply and "邀请" in reply and "接下来还需要这些材料" not in reply


@pytest.mark.parametrize("topic", ["unsupported", None])
def test_declining_links_does_not_decline_an_independent_practical_answer(tmp_path, topic):
    question = "在读证明该找谁开，里面需要写什么？"
    _, reply = turn(tmp_path, Capture(), question + "不用发链接。", CasePatch(updates=[], ambiguities=[],
        customer_questions=[CustomerQuestion(topic=topic, source_excerpt=question, confidence=1)] if topic else []))
    assert "抬头纸" in reply and "学校" in reply and "http" not in reply
    assert "方便告诉我护照上的姓名" not in reply
