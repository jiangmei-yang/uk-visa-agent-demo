"""Exposed development repairs and synthetic scope controls; not a new holdout score."""

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
from visa_agent.workflow.service import WorkflowService

REPORT = Path("eval_output/next_step_development_2026-09-04.json")
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 4)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def rejected(*args, **kwargs):
        raise AssertionError("Reply-scope regressions must not call any external service")

    monkeypatch.setattr("socket.socket.connect", rejected)
    monkeypatch.setattr("socket.create_connection", rejected)


class CapturedModel:
    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch
        self.extractions = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.extractions += 1
        return self.patch.model_copy(deep=True)

    def render_message(self, case: Case, plan: str) -> str:
        return deterministic_fallback_message(case, plan)


def _exposed_case(identifier: str) -> tuple[Case, InboundEvent, CasePatch, str]:
    before = REPORT.read_bytes()
    row = next(item for item in json.loads(before)["results"] if item["id"] == identifier)
    assert not row["holdout"]
    event = InboundEvent.model_validate(row["input_event"])
    initial = Case(id=f"preparation-probe-{identifier}", external_thread_id=event.external_thread_id,
        applicant_contact=event.sender, primary_channel="gmail", customer_language=row["language"],
        policy_version=POLICY.version, profile=CaseProfile.model_validate(row["workflow"]["profile_before"]),
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
        **row["workflow"]["control_before"])
    return initial, event, CasePatch.model_validate(row["raw_patch"]), hashlib.sha256(before).hexdigest()


def _run(tmp_path, initial: Case, event: InboundEvent, patch: CasePatch) -> tuple[Case, str]:
    store = SQLiteStore(tmp_path / "scope.db")
    model = CapturedModel(patch)
    try:
        store.save_case(initial)
        service = WorkflowService(store, POLICY, model, today_provider=lambda: TODAY)
        case, duplicate, plan = service.process(event)
        assert not duplicate and model.extractions == 1 and not service.llm.last_extraction_fallback
        assert plan == "blocked" and case.profile == initial.profile
        assert not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None
        rows = store.list_outbox()
        assert len(rows) == 1 and rows[0]["status"] == "PENDING"
        assert all(answer in rows[0]["payload"] for answer in case.customer_answers)
        assert service.process(event)[1] and model.extractions == 1 and store.list_outbox() == rows
        assert store.get_case(case.id).model_dump() == case.model_dump()
        return case, rows[0]["payload"]
    finally:
        store.close()


def test_exposed_development_general_checklist_is_information_not_a_personal_document_request(tmp_path):
    initial, event, patch, report_hash = _exposed_case("ns_dev_18")
    case, reply = _run(tmp_path, initial, event, patch)
    assert "We'll also need these documents" not in reply
    assert "Let me know if any are difficult to obtain" not in reply
    assert "reference" in reply.casefold() and "not a request" in reply.casefold()
    assert "passport" in reply.casefold() and "funds" in reply.casefold()
    assert "full translation" in reply and "translator's accuracy statement" in reply
    assert case.next_step_advice is None and not case.proactive_guidance_offered
    assert case.preparation_control_epoch == initial.preparation_control_epoch
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == report_hash


def test_exposed_development_resume_and_thats_all_does_not_push_an_application_tutorial(tmp_path):
    initial, event, patch, report_hash = _exposed_case("ns_dev_14")
    case, reply = _run(tmp_path, initial, event, patch)
    assert not case.preparation_paused and case.preparation_control_epoch == initial.preparation_control_epoch + 1
    assert case.latest_preparation_action == "resume"
    assert "pick this up again" in reply and "fresh summary" in reply
    assert "Apply now" not in reply and "GOV.UK" not in reply
    assert "bank statements" not in reply and "We'll also need" not in reply
    assert not case.proactive_guidance_offered and case.guidance_events == {}
    assert case.last_requested_fields == [] and case.next_step_advice is None
    assert hashlib.sha256(REPORT.read_bytes()).hexdigest() == report_hash


def _synthetic_turn(tmp_path, language, body, questions=(), *, resume=None, paused=False):
    initial = Case(id="synthetic-scope-case", external_thread_id="synthetic-scope-thread",
        applicant_contact="fictional@example.test", primary_channel="gmail", customer_language=language,
        policy_version=POLICY.version, preparation_paused=paused,
        preparation_control_epoch=1 if paused else 0,
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
        profile=CaseProfile(full_name="Example Reader", date_of_birth=date(1998, 5, 12),
            nationality_country="China", application_country="Hong Kong", visit_purpose="tourism",
            occupation_status="student", funding_source="self", current_address="Fictional campus",
            uk_accommodation="London, not booked", estimated_trip_cost_gbp=1500,
            has_serious_history=False, route_confirmed_standard_visitor=True))
    event = InboundEvent(id="synthetic-scope-event", external_thread_id=initial.external_thread_id,
        sender=initial.applicant_contact, channel="gmail", subject="UK visitor preparation",
        body=body, received_at=datetime(2026, 9, 4, 15, tzinfo=UTC))
    patch = CasePatch.model_validate({"updates": [], "ambiguities": [], "customer_questions": [
        {"topic": topic, "source_excerpt": excerpt, "confidence": 1.0} for topic, excerpt in questions
    ], "preparation_intent": {"action": "resume", "source_excerpt": resume, "confidence": 1.0}
        if resume else None})
    return _run(tmp_path, initial, event, patch)


@pytest.mark.parametrize("language,question", [
    ("zh", "我只想了解英国访问签证通常有哪些材料清单，作一般参考。"),
    ("en", "What is the usual document checklist for a UK visitor visa, for general reference?"),
])
@pytest.mark.parametrize("paused", [False, True])
def test_general_reference_list_is_conditional_and_does_not_request_submission(
    tmp_path, language, question, paused,
):
    case, reply = _synthetic_turn(tmp_path, language, question,
        [("document_checklist", question)], paused=paused)
    assert ("参考" in reply if language == "zh" else "reference" in reply)
    assert "接下来还需要" not in reply and "We'll also need" not in reply
    assert "待补材料" not in reply and "for your circumstances" not in reply
    assert ("如由他人资助" in reply if language == "zh" else "If someone else provides funding" in reply)
    assert ("如材料不是英文" in reply if language == "zh" else "For documents not in English or Welsh" in reply)
    assert ("如在非国籍国申请" in reply if language == "zh" else "When applying outside the country of nationality" in reply)
    assert case.preparation_paused == paused and case.next_step_advice is None


@pytest.mark.parametrize("language,body,excerpt", [
    ("zh", "请根据我目前的情况发一份待补材料清单。", "请根据我目前的情况发一份待补材料清单。"),
    ("en", "Which documents do I still need for my application?", "Which documents do I still need for my application?"),
    ("zh", "不要一般清单，我还缺什么材料？", "我还缺什么材料？"),
    ("zh", "不要一般清单，我还缺什么？", "我还缺什么？"),
    ("en", "Don't give me a general checklist. Which documents are still missing from my file?", "Which documents are still missing from my file?"),
    ("en", "The bank reference number is X-123. Please send the document checklist.", "Please send the document checklist."),
    ("zh", "银行给的参考编号是X-123。请发一份材料清单。", "请发一份材料清单。"),
    ("en", 'The earlier email said "use a general document checklist". Please send the document checklist for my file.', "Please send the document checklist for my file."),
    ("zh", "旧邮件说‘一般材料清单仅供参考’。现在请给我这次申请的待补材料清单。", "现在请给我这次申请的待补材料清单。"),
])
def test_personal_missing_documents_are_not_replaced_by_generic_information(tmp_path, language, body, excerpt):
    _, reply = _synthetic_turn(tmp_path, language, body, [("document_checklist", excerpt)])
    assert ("待补材料" in reply if language == "zh" else "for your circumstances" in reply)
    assert ("在读证明" in reply if language == "zh" else "Evidence of your student status" in reply)
    assert "reference overview" not in reply and "参考概览" not in reply


@pytest.mark.parametrize("language,resume,closing", [
    ("zh", "现在请恢复英国签证准备。", "这封邮件就说这些，谢谢。"),
    ("en", "Please resume my UK visa preparation now.", "That's all for this message, thanks."),
])
@pytest.mark.parametrize("extra", ["none", "application", "fees", "next_step"])
def test_quiet_resume_keeps_independent_faq_or_requested_next_step(tmp_path, language, resume, closing, extra):
    question = {
        "zh": {"application": "英国访问签证的官方申请网页在哪里？",
               "fees": "六个月英国访问签证的申请费是多少？",
               "next_step": "我下一步应该先准备哪一项材料？"},
        "en": {"application": "Where is the official UK visitor application page?",
               "fees": "What is the fee for a six-month UK visitor visa?",
               "next_step": "Which document should I prepare next for my UK visitor application?"},
    }[language].get(extra)
    body = resume + " " + closing + (" " + question if question else "")
    case, reply = _synthetic_turn(tmp_path, language, body,
        [(extra, question)] if question else [], resume=resume, paused=True)
    assert not case.preparation_paused and case.preparation_control_epoch == 2
    assert not case.proactive_guidance_offered
    if extra == "application":
        assert "Apply now" in reply and "gov.uk/standard-visitor/apply-standard-visitor-visa" in reply
    elif extra == "fees":
        assert "£135" in reply
        assert "Apply now" not in reply
    elif extra == "next_step":
        assert "PDF" in reply and case.next_step_advice.requirement_id == "passport"
    else:
        assert "GOV.UK" not in reply and "PDF" not in reply
        assert "日期" not in reply and "dates" not in reply and case.customer_answers == []
    assert "As a self-funded student" not in reply and "材料方面，可以先准备学校" not in reply


@pytest.mark.parametrize("language,checklist,step", [
    ("zh", "请发一份一般材料清单供参考。", "另外按我的情况，我下一步应该先准备哪一项材料？"),
    ("en", "Please share a general document checklist for reference.", "Separately, which document should I prepare next for my own application?"),
])
def test_general_reference_and_independent_personal_next_step_are_both_kept(tmp_path, language, checklist, step):
    case, reply = _synthetic_turn(tmp_path, language, checklist + " " + step,
        [("document_checklist", checklist), ("next_step", step)])
    assert case.next_step_advice and case.next_step_advice.requirement_id == "passport"
    assert "PDF" in reply and case.next_step_advice.message in reply
    assert ("参考概览" in reply if language == "zh" else "reference overview" in reply)


def test_quoted_quiet_closing_does_not_silence_a_current_resume_request(tmp_path):
    resume = "Please resume my UK visa preparation now."
    body = 'An earlier email said "That is all for this email."\n' + resume
    case, reply = _synthetic_turn(tmp_path, "en", body, resume=resume, paused=True)
    assert case.latest_preparation_action == "resume" and not case.preparation_paused
    assert case.proactive_guidance_offered and "Apply now" in reply


@pytest.mark.parametrize("language,body", [
    ("zh", "这封不用再发一般材料清单，谢谢。"),
    ("en", "Don't give me a general checklist, thanks."),
])
def test_declining_a_general_checklist_without_another_request_does_not_emit_one(tmp_path, language, body):
    _, reply = _synthetic_turn(tmp_path, language, body)
    assert "reference overview" not in reply and "参考概览" not in reply
    assert "We'll also need" not in reply and "待补材料" not in reply
    assert "passport" not in reply.casefold() and "护照" not in reply
