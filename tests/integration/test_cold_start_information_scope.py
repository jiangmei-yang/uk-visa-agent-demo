"""Local repairs of exposed development replies plus new synthetic scope controls.

This uses captured patches, never a provider, and does not modify original reports
or turn their development failures into unseen or naturalness scores.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.domain.models import Case, CaseProfile, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import (
    document_list_requested,
    general_document_list_requested,
)
from visa_agent.workflow.service import WorkflowService

REPORT = Path("eval_output/cold_start_development_2026-09-04.json")
EXPOSED_FIRST_HOLDOUT = Path("eval_output/cold_start_holdout_first_2026-09-04.json")
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 4)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Information-scope contracts cannot contact providers or mailboxes")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


class CapturedModel:
    def __init__(self, patch):
        self.patch = patch
        self.calls = 0

    def extract_case_patch(self, event):
        self.calls += 1
        return self.patch.model_copy(deep=True)

    def render_message(self, case, plan):
        return deterministic_fallback_message(case, plan)


def _run(tmp_path, event, patch, initial=None):
    store = SQLiteStore(tmp_path / "information-scope.db")
    try:
        if initial is not None:
            store.save_case(initial)
        model = CapturedModel(patch)
        service = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
        case, duplicate, plan = service.process(event)
        assert not duplicate and model.calls == 1 and not service.llm.last_extraction_fallback
        assert plan == "blocked" and not case.profile_confirmed and not case.final_summary_confirmed
        assert case.delivery_path is None and case.confirmation_kind is None
        rows = store.list_outbox()
        assert len(rows) == 1 and rows[0]["status"] == "PENDING"
        assert store.get_case(case.id).model_dump() == case.model_dump()
        assert service.process(event)[1] and model.calls == 1
        assert store.list_outbox() == rows
        return case, rows[0]["payload"]
    finally:
        store.close()


def _captured(identifier, *, report_path=REPORT, split="development"):
    original = report_path.read_bytes()
    report = json.loads(original)
    assert report["split"] == split and report["completed"]
    assert report["source_unchanged"] and report["corpus_unchanged"]
    row = next(row for row in report["turns"] if row["turn_id"] == identifier)
    assert len(row["attempts"]) == 1 and row["attempts"][0]["extraction_available"]
    initial = Case.model_validate(row["before"]["cases"][0]) if row["before"]["cases"] else None
    return row, initial, InboundEvent.model_validate(row["input_event"]), CasePatch.model_validate(
        row["attempts"][0]["raw_patch"]), hashlib.sha256(original).hexdigest()


def _assert_reference(reply, language):
    assert ("一般可以参考以下材料类别" in reply if language == "zh"
            else "For general reference" in reply)
    assert ("有效护照" in reply if language == "zh" else "passport" in reply.lower())
    assert ("可用资金及来源" in reply if language == "zh" else "Available funds and their source" in reply)
    assert ("如由他人资助" in reply if language == "zh" else "If someone else provides funding" in reply)
    assert "接下来还需要这些材料" not in reply and "We'll also need these documents" not in reply
    assert "待补材料" not in reply and "for your circumstances" not in reply


def test_exposed_first_enquiry_gets_requested_overview_before_funding_is_known(tmp_path):
    row, initial, event, patch, original_hash = _captured("dev_zh_01_t01")
    assert initial is None and patch.customer_questions[0].topic == "document_checklist"
    case, reply = _run(tmp_path, event, patch)
    _assert_reference(reply, "zh")
    assert case.profile.model_dump(mode="json") == row["after"]["cases"][0]["profile"]
    assert case.profile.funding_source is None
    assert set(case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert not case.preparation_paused and case.last_requested_fields == case.question_plan == []
    assert "计划哪天" not in reply
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == original_hash


def test_exposed_single_employment_letter_question_is_not_a_four_document_demand(tmp_path):
    row, initial, event, patch, original_hash = _captured("dev_zh_01_t03")
    assert patch.customer_questions[0].topic == "document_checklist"
    case, reply = _run(tmp_path, event, patch, initial)
    assert case.profile.model_dump(mode="json") == row["after"]["cases"][0]["profile"]
    assert case.latest_changes["estimated_trip_cost_gbp"] == "3200" and "3200" in reply
    assert not document_list_requested(case)
    assert "接下来还需要这些材料" not in reply and "待补材料" not in reply
    assert "- 有效护照或旅行证件" not in reply
    assert all(word in reply for word in ("收入", "职位", "薪资", "任职时间", "联系方式", "如果假期已获批准"))
    assert "不是所有访问申请都必须" in reply
    assert not case.preparation_paused and case.deferred_fields == initial.deferred_fields
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == original_hash


def _synthetic(tmp_path, language, body, questions, *, complete_context=False, paused=False, empty_profile=False):
    initial = Case(id="fictional-information-case", external_thread_id="fictional-information-thread",
        applicant_contact="reader@example.test", primary_channel="gmail", policy_version=POLICY.version,
        customer_language=language, preparation_paused=paused, preparation_control_epoch=int(paused),
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
        profile=CaseProfile(full_name="Rin Example", nationality_country="China",
            application_country="China", visit_purpose="tourism", occupation_status="employed",
            funding_source="self" if complete_context else None))
    if empty_profile:
        initial.profile = CaseProfile()
    event = InboundEvent(id="fictional-information-event", external_thread_id=initial.external_thread_id,
        sender=initial.applicant_contact, channel="gmail", subject="UK visitor preparation",
        body=body, received_at=datetime(2026, 9, 4, 16, tzinfo=UTC))
    patch = CasePatch.model_validate({"updates": [], "ambiguities": [], "customer_questions": [
        {"topic": topic, "source_excerpt": excerpt, "confidence": 1.0} for topic, excerpt in questions
    ]})
    case, reply = _run(tmp_path, event, patch, initial)
    assert case.profile == initial.profile and case.preparation_paused == paused
    assert case.preparation_control_epoch == initial.preparation_control_epoch
    return case, reply


@pytest.mark.parametrize("language,question", [
    ("zh", "第一次了解英国旅游签证，通常需要准备哪些类型的材料？"),
    ("zh", "英国访问签证一般有哪些材料类别，想看个概览。"),
    ("en", "What types of evidence do people normally prepare for a UK visitor visa?"),
    ("en", "Could you explain the usual categories of documents for a UK visitor application?"),
])
@pytest.mark.parametrize("paused", [False, True])
def test_explicit_general_categories_are_information_even_with_incomplete_profile(tmp_path, language, question, paused):
    case, reply = _synthetic(tmp_path, language, question, [("document_checklist", question)], paused=paused)
    assert document_list_requested(case) and general_document_list_requested(case)
    _assert_reference(reply, language)
    assert case.last_requested_fields == case.question_plan == []


@pytest.mark.parametrize("language,question", [
    ("zh", "请介绍英国访问签证通常需要哪些类型的材料。"),
    ("en", "What types of evidence do people normally prepare for a UK visitor visa?"),
])
def test_general_overview_does_not_require_even_initial_identity_or_location(tmp_path, language, question):
    case, reply = _synthetic(tmp_path, language, question, [("document_checklist", question)], empty_profile=True)
    _assert_reference(reply, language)
    assert case.profile == CaseProfile()


@pytest.mark.parametrize("language,question", [
    ("zh", "我想了解在职证明的作用，是说明工作和收入的吗？"),
    ("en", "What is the purpose of an employer letter for my UK visit?"),
    ("zh", "请解释计划行程表有什么作用，为什么要写？"),
    ("en", "Why might a short itinerary help explain my visit?"),
])
def test_misclassified_single_document_purpose_does_not_expand_to_collection(tmp_path, language, question):
    case, reply = _synthetic(tmp_path, language, question, [("document_checklist", question)], complete_context=True)
    assert not document_list_requested(case)
    assert "接下来还需要这些材料" not in reply and "We'll also need these documents" not in reply
    assert "- 有效护照" not in reply and "- Valid passport" not in reply
    if language == "en" and "employer" in question:
        assert all(word in reply for word in ("income", "role", "salary", "length of employment", "contact details", "If leave is approved"))
        assert "not a universal requirement" in reply
    elif language == "en":
        assert all(word in reply for word in ("dates", "accommodation", "budget", "not confirmed bookings"))
        assert "not a universal requirement" in reply


@pytest.mark.parametrize("language,purpose,checklist", [
    ("zh", "在职证明有什么作用？", "另外请发一份通常的材料清单作参考。"),
    ("en", "What is the purpose of an employer letter?", "Separately, please share the usual document checklist for reference."),
])
def test_single_document_purpose_does_not_veto_an_independent_overview(tmp_path, language, purpose, checklist):
    case, reply = _synthetic(tmp_path, language, purpose + " " + checklist,
        [("document_checklist", purpose), ("document_checklist", checklist)])
    assert document_list_requested(case)
    _assert_reference(reply, language)


@pytest.mark.parametrize("language,purpose,checklist", [
    ("zh", "在职证明有什么作用？", "另外请根据我的情况发一份待补材料清单。"),
    ("en", "What is the purpose of an employer letter?", "Separately, please send the document checklist for my application."),
])
def test_single_document_purpose_preserves_an_independent_personal_checklist(tmp_path, language, purpose, checklist):
    case, reply = _synthetic(tmp_path, language, purpose + " " + checklist,
        [("document_checklist", purpose), ("document_checklist", checklist)], complete_context=True)
    assert document_list_requested(case) and not general_document_list_requested(case)
    assert ("待补材料" in reply if language == "zh" else "for your circumstances" in reply)


@pytest.mark.parametrize("language,question", [
    ("zh", "不要一般清单，请根据我的情况告诉我还缺哪些材料？"),
    ("en", "Do not send a general checklist. Which documents are still missing from my application?"),
])
@pytest.mark.parametrize("complete_context", [False, True])
def test_personal_missing_items_keep_context_gate_instead_of_becoming_generic(tmp_path, language, question, complete_context):
    excerpt = question.split("，", 1)[-1] if language == "zh" else question.split(". ", 1)[-1]
    case, reply = _synthetic(tmp_path, language, question, [("document_checklist", excerpt)],
        complete_context=complete_context)
    assert document_list_requested(case) == complete_context
    assert not general_document_list_requested(case)
    assert "For general reference" not in reply and "一般可以参考以下材料类别" not in reply
    if complete_context:
        assert ("待补材料" in reply if language == "zh" else "for your circumstances" in reply)


@pytest.mark.parametrize("language,current,old", [
    ("zh", "今天只想知道在职证明有什么作用。", "请发一份通常材料清单供参考。"),
    ("en", "Today, what is the purpose of an employer letter?", "Please share the usual document checklist."),
])
def test_quoted_historical_overview_cannot_override_current_single_document_scope(tmp_path, language, current, old):
    body = current + "\n> " + old
    case, reply = _synthetic(tmp_path, language, body, [("document_checklist", current)], complete_context=True)
    assert not document_list_requested(case)
    assert "For general reference" not in reply and "一般可以参考以下材料类别" not in reply


@pytest.mark.parametrize("language,overview,step", [
    ("zh", "一般需要哪些类型的材料？", "另外按我的情况，下一步先补哪一项？"),
    ("en", "What types of evidence are usually needed?", "Separately, what is the next step for my own UK visitor application?"),
])
def test_general_overview_preserves_an_independent_actionable_next_step(tmp_path, language, overview, step):
    case, reply = _synthetic(tmp_path, language, overview + " " + step,
        [("document_checklist", overview), ("next_step", step)])
    _assert_reference(reply, language)
    assert case.next_step_advice and case.next_step_advice.kind == "question"
    assert case.next_step_advice.question_field in case.last_requested_fields


@pytest.mark.parametrize("identifier,language", [
    ("holdout_zh_03_t01", "zh"), ("holdout_en_04_t01", "en"),
])
def test_exposed_first_holdout_overview_is_answered_without_a_private_questionnaire(tmp_path, identifier, language):
    row, initial, event, patch, original_hash = _captured(
        identifier, report_path=EXPOSED_FIRST_HOLDOUT, split="holdout",
    )
    assert initial is None and patch.customer_questions[0].topic == "document_checklist"
    case, reply = _run(tmp_path, event, patch)
    _assert_reference(reply, language)
    assert case.profile.model_dump(mode="json") == row["after"]["cases"][0]["profile"]
    assert not case.preparation_paused
    assert case.last_requested_fields == case.question_plan == [] and case.question_event_ids == {}
    assert "Who will pay" not in reply and "费用由你自己承担，还是" not in reply
    assert "Where are you planning to stay" not in reply and "在英国准备住哪里" not in reply
    assert "Roughly how much" not in reply and "大约打算花多少" not in reply
    assert hashlib.sha256(EXPOSED_FIRST_HOLDOUT.read_bytes()).hexdigest() == original_hash


def test_exposed_first_holdout_translation_question_does_not_request_four_documents(tmp_path):
    row, initial, event, patch, original_hash = _captured(
        "holdout_zh_03_t03", report_path=EXPOSED_FIRST_HOLDOUT, split="holdout",
    )
    assert [question.topic for question in patch.customer_questions] == ["translation"]
    case, reply = _run(tmp_path, event, patch, initial)
    assert case.profile.model_dump(mode="json") == row["after"]["cases"][0]["profile"]
    assert "2800" in reply and all(word in reply for word in ("准确性声明", "全名", "签名", "联系方式"))
    assert not document_list_requested(case)
    assert "接下来还需要这些材料" not in reply and "待补材料" not in reply
    assert "- 有效护照" not in reply
    assert hashlib.sha256(EXPOSED_FIRST_HOLDOUT.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("language,question", [
    ("zh", "一般会看哪些方面的证明呢？"),
    ("zh", "能概述一下通常的证明资料吗？"),
    ("en", "Could you give me an overview of the usual supporting documents?"),
    ("en", "What evidence is normally considered for a visitor application?"),
])
def test_general_semantic_scope_is_not_limited_to_material_types_word_order(tmp_path, language, question):
    case, reply = _synthetic(tmp_path, language, question, [("document_checklist", question)])
    _assert_reference(reply, language)
    assert case.last_requested_fields == case.question_plan == []


@pytest.mark.parametrize("question", [
    "中文材料的翻译需要写哪些译者信息？",
    "中文材料配的英文翻译要包括哪些声明和联系方式？",
    "提交材料时，译文需要提供哪些译者声明？",
])
def test_which_translator_details_does_not_mean_which_documents_to_collect(tmp_path, question):
    case, reply = _synthetic(tmp_path, "zh", question, [("translation", question)], complete_context=True)
    assert not document_list_requested(case)
    assert "翻译" in reply and "联系方式" in reply
    assert "接下来还需要这些材料" not in reply and "- 有效护照" not in reply


@pytest.mark.parametrize("question", ["材料需要准备哪些？", "资料要提供哪几份文件？", "材料需要交哪些材料？"])
def test_sentence_final_or_document_object_list_question_still_selects_personal_items(tmp_path, question):
    case, reply = _synthetic(tmp_path, "zh", question, [], complete_context=True)
    assert document_list_requested(case)
    assert not general_document_list_requested(case)
    assert "待补材料" in reply


@pytest.mark.parametrize("language,overview,continuation", [
    ("zh", "请发一般材料清单供参考。", "另外请继续准备我的申请材料。"),
    ("en", "Please share a general supporting document checklist.", "Separately, please continue preparing my UK visa application."),
])
def test_independent_current_continue_request_still_permits_missing_fact_questions(tmp_path, language, overview, continuation):
    case, reply = _synthetic(tmp_path, language, overview + "\n" + continuation,
        [("document_checklist", overview)])
    _assert_reference(reply, language)
    assert case.last_requested_fields and not case.preparation_paused


@pytest.mark.parametrize("language,overview,extra", [
    ("zh", "请发一般材料清单供参考。", "今天不想继续准备材料。"),
    ("zh", "请发一般材料清单供参考。", "如果以后继续准备材料，我会告诉你。"),
    ("zh", "请发一般材料清单供参考。", "旧邮件写着‘请继续准备申请材料’。"),
    ("en", "Please share a general supporting document checklist.", "I do not want to continue preparing the application today."),
    ("en", "Please share a general supporting document checklist.", "If I am ready later, we can continue preparing the application."),
    ("en", "Please share a general supporting document checklist.", 'An old message said "Please continue preparing my UK visa application."'),
])
def test_non_current_continue_wording_cannot_turn_overview_into_intake(tmp_path, language, overview, extra):
    case, reply = _synthetic(tmp_path, language, overview + "\n" + extra,
        [("document_checklist", overview)])
    _assert_reference(reply, language)
    assert case.last_requested_fields == case.question_plan == []


@pytest.mark.parametrize("language,overview,next_step", [
    ("zh", "请发一般材料清单供参考。", "另外我下一步应该先准备哪一份材料？"),
    ("en", "Please share a general supporting document checklist.", "Separately, which document should I prepare next for my own application?"),
])
def test_personal_next_document_clause_does_not_reframe_separate_general_overview(tmp_path, language, overview, next_step):
    case, reply = _synthetic(tmp_path, language, overview + "\n" + next_step,
        [("document_checklist", overview), ("next_step", next_step)])
    _assert_reference(reply, language)
    assert case.next_step_advice and case.next_step_advice.kind == "question"
    assert case.next_step_advice.question_field in case.last_requested_fields
