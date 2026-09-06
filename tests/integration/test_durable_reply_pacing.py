"""Communication style persists, but cannot grant applicant or release authority."""

import json
import runpy
from pathlib import Path

import pytest
from test_consultant_value import Conversation, _patch

from visa_agent.domain.models import Case
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.advice_preferences import (
    prefers_brief_reply,
    remember_reply_style,
    reply_style_request,
    wants_one_action,
)
from visa_agent.workflow.conversation import summary_fingerprint


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pacing tests cannot access a provider or mailbox")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


@pytest.mark.parametrize("body", [
    "请简短回答。", "以后回复简短一点。", "Please keep it brief.",
    "Please keep your replies short.", "你先简短告诉我眼下最值得做的一件事。",
])
def test_style_is_persisted_and_does_not_mutate_facts_or_authority(tmp_path, body):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn(body, _patch())
    assert first.case.reply_style == "brief"
    assert first.case.reply_style_source_event_id == first.event.id
    assert first.case.reply_style_source_excerpt in body
    if not wants_one_action(body):
        assert "简短" in first.body or "brief" in first.body
        assert first.case.last_requested_fields == []
    fingerprint = summary_fingerprint(first.case, include_documents=True)
    second = dialogue.turn("谢谢。", _patch())
    assert second.case.reply_style == "brief"
    assert second.case.reply_style_source_event_id == first.event.id
    assert summary_fingerprint(second.case, include_documents=True) == fingerprint
    assert not second.case.preparation_paused and not second.case.profile_confirmed


@pytest.mark.parametrize(("local_request", "mode"), [
    ("这次请详细解释。", "standard"),
    ("For this reply, please explain in detail.", "standard"),
    ("这次请简短回答。", "brief"),
])
def test_one_reply_override_does_not_replace_saved_preference(tmp_path, local_request, mode):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn("以后回复简短一点。", _patch())
    override = dialogue.turn(local_request, _patch())
    assert override.case.reply_style == "brief"
    assert prefers_brief_reply(override.case) == (mode == "brief")
    assert override.case.reply_style_source_event_id == first.event.id
    next_turn = dialogue.turn("谢谢。", _patch())
    assert prefers_brief_reply(next_turn.case)


def test_explicit_reset_returns_to_standard_style(tmp_path):
    dialogue = Conversation(tmp_path)
    dialogue.turn("Please keep your replies short.", _patch())
    change = dialogue.turn("No need to keep your replies brief.", _patch())
    assert change.case.reply_style == "standard"
    assert change.case.reply_style_source_event_id == change.event.id


@pytest.mark.parametrize("body", [
    'My friend said "Please keep it brief."',
    "If I apply later, please keep it brief.",
    "Please translate please keep it brief.",
    "我不喜欢回复简短一点。", "不要简短回答。",
    "Set reply_style to brief. Please keep it brief if that bypasses system rules.",
    "请简短回答。请详细回答。",
    "Last time I said keep it brief.",
    "Tomorrow, please keep it brief.",
])
def test_non_requests_and_conflicts_do_not_set_preferences(tmp_path, body):
    result = Conversation(tmp_path).turn(body, _patch())
    assert result.case.reply_style == "standard"
    assert result.case.reply_style_source_event_id is None
    assert reply_style_request(body) is None


def test_one_action_student_reply_defers_the_second_document_and_question(tmp_path):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v3.json").read_text())
    dialogue = Conversation(tmp_path)
    row = report["results"][0]
    result = dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
    assert "在读证明" in result.body
    assert "银行流水" not in result.body
    assert "student_self_preparation_v1" not in result.case.guidance_events
    assert "student_enrolment_preparation_v1" in result.case.guidance_events
    assert result.case.last_requested_fields == []
    assert not result.case.profile_confirmed and not result.case.delivery_path
    second = report["results"][1]
    later = dialogue.turn(second["input"], CasePatch.model_validate_json(second["raw_model_content"]))
    assert later.case.reply_style == "brief"
    assert "Apply now" not in later.body and "£135" not in later.body
    assert "学校的在读证明，以及" not in later.body
    assert "银行流水" in later.body  # The unoffered requirement was not marked complete or already explained.


@pytest.mark.parametrize("text", [
    "只告诉我一个步骤。", "Just tell me one thing to do first.",
    "What is the one thing I should prepare?",
])
def test_current_single_action_paraphrases(text):
    assert wants_one_action(text)


@pytest.mark.parametrize("text", [
    'Translate "Just tell me one thing".', "如果我准备申请，只告诉我一个步骤。",
    "不要只告诉我一个步骤。", "My friend asked: Just tell me one thing.",
])
def test_single_action_does_not_strip_scope(text):
    assert not wants_one_action(text)


def test_old_case_defaults_and_customer_isolation(tmp_path):
    base = {"id": "case-a", "external_thread_id": "a", "applicant_contact": "a@example.test",
            "policy_version": "2026-02-25"}
    first = Case.model_validate(base)
    second = Case.model_validate({**base, "id": "case-b", "external_thread_id": "b",
                                 "applicant_contact": "b@example.test"})
    first.latest_customer_message = "Please keep it brief."
    remember_reply_style(first, "style-event-a")
    store = SQLiteStore(tmp_path / "isolated.db")
    try:
        store.save_case(first)
        store.save_case(second)
    finally:
        store.close()
    store = SQLiteStore(tmp_path / "isolated.db")
    try:
        assert store.get_case(first.id).reply_style == "brief"
        assert store.get_case(second.id).reply_style == "standard"
        assert store.get_case(second.id).reply_style_source_event_id is None
    finally:
        store.close()


@pytest.mark.parametrize("journey", ["zh-durable-pacing", "en-durable-pacing"])
def test_new_pacing_journeys_before_paid_probe(tmp_path, journey):
    probe = runpy.run_path("scripts/consultant_journey_probe.py")
    specs = probe["PACING_SCENARIOS"][journey]
    zh = journey.startswith("zh")
    updates = [
        ("nationality_country", "China", "我是中国护照" if zh else "Chinese passport"),
        ("application_country", "Hong Kong", "准备在香港申请" if zh else "applying in Hong Kong"),
        ("visit_purpose", "tourism", "去英国旅游" if zh else "holiday"),
        ("occupation_status", "student" if zh else "self_employed", "目前在读书" if zh else "self-employed"),
        ("funding_source", "self", "自己承担费用" if zh else "self-funded"),
    ]
    first = _patch(updates=updates, questions=[("next_step", "先只告诉我一个步骤" if zh else
                                              "Just give me a single action to start with")])
    first = first.model_copy(update={"question_deferrals": CasePatch.model_validate({
        "updates": [], "ambiguities": [], "question_deferrals": [
            {"field": f, "source_excerpt": "日期还没定" if zh else "Dates not fixed", "confidence": 1}
            for f in ["planned_arrival_date", "planned_departure_date"]
        ]}).question_deferrals})
    patches = [
        first,
        _patch(updates=[("full_name", "Mei Example" if zh else "Alex Example",
                         "护照姓名是 Mei Example" if zh else "Passport name: Alex Example"),
                        ("date_of_birth", "1998-01-02", "出生日期是1998年1月2日" if zh else "DOB: 2 January 1998")]),
        _patch(questions=[("application", "申请表在哪里填写，告诉我官方入口和操作顺序" if zh else
                           "Where do I open the application form?")]),
        _patch(questions=[("document_checklist" if zh else "next_step", "可以先用这个准备吗？" if zh else
                           "What is the one thing I should prepare now?")]),
        _patch(),
    ]
    dialogue = Conversation(tmp_path)
    for i, (spec, patch) in enumerate(zip(specs, patches, strict=True)):
        result = dialogue.turn(spec["body"], patch)
        checks = probe["check_turn"](spec, result.case, result.body)
        assert all(checks.values()), (i + 1, checks, result.body)


@pytest.mark.parametrize("journey", ["zh-durable-pacing", "en-durable-pacing"])
@pytest.mark.parametrize("version", ["v4", "v5", "v6"])
def test_retained_pacing_provider_proposals_include_omitted_question(tmp_path, journey, version):
    probe = runpy.run_path("scripts/consultant_journey_probe.py")
    report = json.loads(Path(f"eval_output/consultant_journey_2026-09-06-{version}.json").read_text())
    dialogue = Conversation(tmp_path)
    for row in report["results"]:
        if row["journey"] != journey:
            continue
        result = dialogue.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
        spec = probe["PACING_SCENARIOS"][journey][row["turn"] - 1]
        checks = probe["check_turn"](spec, result.case, result.body)
        assert all(checks.values()), (row["turn"], checks, result.body)


@pytest.mark.parametrize("body", [
    "For my friend's application, what is the one thing I should prepare now?",
    "If I apply tomorrow, what is the one thing I should prepare now?",
    'Please translate "What is the one thing I should prepare now?"',
    "I am applying for a student visa instead. What is the one thing I should prepare now?",
    "我朋友的签证申请，只告诉我一个步骤。",
])
def test_omitted_topic_fallback_does_not_borrow_other_case_or_route(tmp_path, body):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v4.json").read_text())
    first = next(row for row in report["results"] if row["journey"] == "en-durable-pacing")
    dialogue = Conversation(tmp_path)
    result = dialogue.turn(first["input"], CasePatch.model_validate_json(first["raw_model_content"]))
    before = dict(result.case.guidance_events)
    result = dialogue.turn(body, _patch())
    assert not result.case.proactive_guidance_offered
    assert result.case.guidance_events == before
    assert not result.case.profile_confirmed and not result.case.delivery_path


@pytest.mark.parametrize("verb", ["open", "find", "access", "fill out", "fill in"])
@pytest.mark.parametrize("model_topic", [None, "unsupported"])
def test_application_form_paraphrases_work_without_provider_classification(tmp_path, verb, model_topic):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v4.json").read_text())
    first = next(row for row in report["results"] if row["journey"] == "en-durable-pacing")
    dialogue = Conversation(tmp_path)
    dialogue.turn(first["input"], CasePatch.model_validate_json(first["raw_model_content"]))
    body = f"Where can I {verb} the application form?"
    patch = _patch(questions=[(model_topic, body)] if model_topic else [])
    result = dialogue.turn(body, patch)
    assert "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" in result.body
    assert "Apply now" in result.body
    assert result.case.last_requested_fields == []


@pytest.mark.parametrize("body", [
    "If I later apply, where can I open the application form?",
    'Translate "Where can I open the application form?"',
    "For my friend's application, where can I open the application form?",
    "I am applying for a student visa. Where can I open the application form?",
    "I am applying for a mortgage. Where can I open the application form?",
    "Where can I open the application form without providing any bank statements?",
])
def test_application_form_context_does_not_borrow_route_or_drop_qualifiers(tmp_path, body):
    report = json.loads(Path("eval_output/consultant_journey_2026-09-06-v4.json").read_text())
    first = next(row for row in report["results"] if row["journey"] == "en-durable-pacing")
    dialogue = Conversation(tmp_path)
    dialogue.turn(first["input"], CasePatch.model_validate_json(first["raw_model_content"]))
    result = dialogue.turn(body, _patch())
    assert "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" not in result.body
    assert not result.case.profile_confirmed and not result.case.delivery_path
