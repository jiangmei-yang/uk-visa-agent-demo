"""Actual reviewed SENT application help, independent of classifier neighbours.

The extractor proposals are synthetic robustness inputs, not provider results.
Workflow, guard, case persistence, dispatcher and reviewed Gmail sender are real;
only transport is captured. Every turn reopens an isolated database. No live
mailbox, paid model, manual SENT state or processing-consent shortcut is used.
"""

import re
from datetime import date

import pytest
from test_advice_continuation import Conversation as AttachmentConversation
from test_consultant_value import Conversation, _patch

from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL

LANGUAGE = {
    "zh": {
        "first": [("visit_purpose", "tourism", "我想申请英国旅游签证。"),
                  ("nationality_country", "China", "我持中国护照。"),
                  ("application_country", "Hong Kong", "我会在香港申请。")],
        "second": [("occupation_status", "student", "我现在在香港读大学。"),
                   ("funding_source", "self", "旅行费用由我自己的存款承担。")],
        "dates": "旅行日期还没有确定。",
        "complaint": "先别一直问我个人信息，告诉我英国旅游签证在哪个网页申请、怎么开始。",
        "cold": "我想申请英国旅游签证，先不用问个人信息。请告诉我在哪个网页申请、怎么开始。",
        "name_body": "我护照上的姓名是示例安宁。",
        "name": "示例安宁",
        "no_links": "这封回复不要链接，只讲怎么开始申请的步骤。",
        "fees": "普通访问签证申请费是多少？",
        "negatives": {
            "quoted": "例句写着：‘英国旅游签证在哪个网页申请、怎么开始？’我只是引用这句话。",
            "conditional": "如果以后我决定申请英国旅游签证，我再问你在哪个网页申请、怎么开始。现在我没有决定。",
            "third_party": "我朋友昨天问我英国旅游签证在哪个网页申请、怎么开始，我当时没有回答他。",
            "declined": "不要告诉我在哪个网页申请，也不用解释申请步骤。",
            "other_route": "我这次问的是学生签证，不是访问签证。它在哪个网页申请、怎么开始？",
            "fee_qualifier": "我只问十年英国访客签证的申请费是多少，先不要讲申请网页或操作步骤。",
        },
    },
    "en": {
        "first": [("visit_purpose", "tourism", "I want to apply for a UK tourist visa."),
                  ("nationality_country", "China", "I hold a Chinese passport."),
                  ("application_country", "Hong Kong", "I will apply from Hong Kong.")],
        "second": [("occupation_status", "student", "I am currently studying at university in Hong Kong."),
                   ("funding_source", "self", "I will pay for the trip from my own savings.")],
        "dates": "My travel dates are still undecided.",
        "complaint": "Please stop asking me for personal details for now. Tell me which webpage to use to apply for a UK tourist visa and how to get started.",
        "cold": "I want to apply for a UK tourist visa. Please explain which webpage to use and how to get started before asking for personal details.",
        "name_body": "The name on my passport is Example Morgan.",
        "name": "Example Morgan",
        "no_links": "Please do not include any links in this reply; just explain the steps to get the application started.",
        "fees": "What is the ordinary visitor visa application fee?",
        "negatives": {
            "quoted": 'An example says: "Which webpage do I use to apply for a UK tourist visa and how do I get started?" I am only quoting that sentence.',
            "conditional": "If I decide to apply for a UK tourist visa later, I will ask which webpage to use and how to get started. I have not decided yet.",
            "third_party": "My friend asked me yesterday which webpage to use to apply for a UK tourist visa and how to get started. I did not answer him then.",
            "declined": "Do not tell me which webpage to use or explain the application steps.",
            "other_route": "I am asking about a student visa, not a visitor visa. Which webpage do I use to apply and how do I get started?",
            "fee_qualifier": "I am only asking how much the application fee is for a ten-year UK visitor visa. Do not explain the application webpage or steps for now.",
        },
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Application-information regressions must remain offline")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def _sent(dialogue, result):
    store = SQLiteStore(dialogue.path)
    try:
        row = next(row for row in store.list_outbox() if row["event_id"] == result.event.id)
        assert row["status"] == "SENT" and row["provider_message_id"]
        assert row["reply_render_mode"] == "reviewed"
        assert row["payload"] == result.body == dialogue.gmail.calls[-1]["body"]
        assert store.get_case(result.case.id).model_dump() == result.case.model_dump()
    finally:
        store.close()
    return result


def _known_student(tmp_path, language, *, conversation_type=Conversation):
    dialogue = conversation_type(tmp_path)
    words = LANGUAGE[language]
    first_body = " ".join(text for _, _, text in words["first"])
    first = _sent(dialogue, dialogue.turn(first_body, _patch(updates=words["first"])))
    body = " ".join([*(text for _, _, text in words["second"]), words["dates"]])
    patch = _patch(updates=words["second"]).model_dump()
    patch["question_deferrals"] = [
        {"field": field, "source_excerpt": words["dates"], "confidence": 1}
        for field in ("planned_arrival_date", "planned_departure_date")
    ]
    second = _sent(dialogue, dialogue.turn(body, CasePatch.model_validate(patch)))
    assert first.case.id == second.case.id and len(dialogue.gmail.calls) == 2
    assert second.case.profile.occupation_status == "student"
    assert second.case.profile.nationality_country == "China"
    assert second.case.profile.application_country == "Hong Kong"
    return dialogue, second


def _proposal(label, body, *, updates=(), extra=()):
    return _patch(updates=updates, questions=[*([] if label == "empty" else [(label, body)]), *extra])


def _assert_authority_unchanged(result, original=None, *, paused=False):
    assert result.case.documents == [] and result.case.delivery_path is None
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.confirmation_kind is None and result.case.confirmation_fingerprint is None
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert result.case.preparation_paused is paused
    assert result.case.preparation_control_epoch == (original.case.preparation_control_epoch if original else 0)
    assert result.case.latest_preparation_action is None
    assert result.case.profile.date_of_birth is None and result.case.profile.current_address is None
    assert result.case.profile.planned_arrival_date is None and result.case.profile.planned_departure_date is None
    if original is not None:
        assert result.case.id == original.case.id
        assert result.case.requirements == original.case.requirements
        assert result.case.deferred_fields == original.case.deferred_fields


def _assert_application_steps(text, *, links=True):
    if links:
        assert APPLICATION_URL in text, text
    else:
        assert not re.search(r"https?://|www\.", text, re.I), text
    # A URL by itself is insufficient: explain the action at the page, the form,
    # returning to it, and the appointment stage without deciding visa eligibility.
    assert "Apply now" in text, text
    assert re.search(r"在线(?:填写|填表|申请)|apply online|online (?:application|form)", text, re.I), text
    assert re.search(r"保存|save|return to|finish it later", text, re.I), text
    assert re.search(r"预约|appointment", text, re.I), text
    assert re.search(r"如果|如需|尚未确认|不代表.{0,10}(?:路线|资格)|if you need|does not.{0,15}confirm|route.{0,20}confirm",
                     text, re.I), text


def _assert_application_help(result, *, links=True):
    text = result.body
    _assert_application_steps(text, links=links)
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert not re.search(r"护照上的姓名吗|你的出生日期|哪天到英国|哪天离开英国|"
                         r"What is your (?:name|full date of birth)|When (?:will|do) you (?:arrive|leave)",
                         text, re.I), text


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("label", ["empty", "application", "unsupported", "next_step"])
def test_known_student_complaint_prioritizes_application_information_across_model_labels(tmp_path, language, label):
    dialogue, original = _known_student(tmp_path, language)
    body = LANGUAGE[language]["complaint"]
    result = _sent(dialogue, dialogue.turn(body, _proposal(label, body)))
    _assert_application_help(result)
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence
    assert len(dialogue.gmail.calls) == 3


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("label", ["empty", "unsupported"])
def test_cold_start_can_explain_the_application_without_first_collecting_identity(tmp_path, language, label):
    dialogue = Conversation(tmp_path)
    body = LANGUAGE[language]["cold"]
    result = _sent(dialogue, dialogue.turn(body, _proposal(label, body)))
    _assert_application_help(result)
    _assert_authority_unchanged(result)
    assert result.case.profile.full_name is None
    assert result.case.profile.nationality_country is None and result.case.profile.application_country is None
    assert result.case.profile.visit_purpose is None and result.case.profile.occupation_status is None
    assert result.case.profile.funding_source is None and result.case.evidence == []
    assert len(dialogue.gmail.calls) == 1


@pytest.mark.parametrize("language", ["zh", "en"])
def test_information_first_request_does_not_permanently_pause_later_fact_collection(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    words = LANGUAGE[language]
    information = _sent(dialogue, dialogue.turn(words["complaint"], _proposal("application", words["complaint"])))
    _assert_application_help(information)
    result = _sent(dialogue, dialogue.turn(words["name_body"], _patch(
        updates=[("full_name", words["name"], words["name_body"])])))
    _assert_authority_unchanged(result, original)
    assert result.case.profile.full_name == words["name"]
    assert result.case.active_evidence("full_name")[0].source_event_id == result.event.id
    assert result.case.last_requested_fields == ["date_of_birth"]
    assert len(result.model.events) == 1 and len(dialogue.gmail.calls) == 4


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_fact_with_application_question_is_kept_without_an_extra_identity_question(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    words = LANGUAGE[language]
    body = words["name_body"] + " " + words["complaint"]
    result = _sent(dialogue, dialogue.turn(body, _proposal(
        "next_step", words["complaint"], updates=[("full_name", words["name"], words["name_body"])])))
    _assert_application_help(result)
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile.model_copy(update={"full_name": words["name"]})
    assert result.case.active_evidence("full_name")[0].source_event_id == result.event.id
    assert len(result.case.evidence) == len(original.case.evidence) + 1
    assert len(result.model.events) == 1 and len(dialogue.gmail.calls) == 3


@pytest.mark.parametrize("language", ["zh", "en"])
def test_no_links_preference_keeps_practical_application_steps(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    words = LANGUAGE[language]
    body = words["complaint"] + " " + words["no_links"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("application", words["complaint"])))
    _assert_application_help(result, links=False)
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("scope", ["quoted", "conditional", "third_party", "declined", "other_route", "fee_qualifier"])
def test_application_rescue_preserves_current_request_scope_and_fee_qualifiers(tmp_path, language, scope):
    dialogue, original = _known_student(tmp_path, language)
    words = LANGUAGE[language]
    scoped = words["negatives"][scope]
    extra = [("fees", words["fees"])] if scope == "declined" else []
    body = scoped + (" " + words["fees"] if scope == "declined" else "")
    result = _sent(dialogue, dialogue.turn(body, _proposal("unsupported", scoped, extra=extra)))
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence
    if scope not in {"declined", "fee_qualifier"}:
        assert APPLICATION_URL not in result.body, result.body
    # GOV.UK publishes the fee on this same page. Citing it for the independent
    # fee answer is not an unsolicited application tutorial or a no-links breach.
    assert not re.search(r"页面点 Apply now|start with Apply now|choose Apply now", result.body, re.I), result.body
    if scope == "declined":
        # Declining the process explanation does not cancel the separate fee FAQ.
        assert re.search(r"£\s*\d", result.body), result.body
        assert re.search(r"申请费|application fee", result.body, re.I), result.body
    if scope == "fee_qualifier":
        assert not re.search(r"£\s*\d", result.body), result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize("language", ["zh", "en"])
def test_independent_six_month_fee_and_application_questions_both_survive_without_intake(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    complaint = LANGUAGE[language]["complaint"]
    fee = ("另外，6个月英国访问签证的申请费是多少？" if language == "zh" else
           "Separately, what is the application fee for a 6-month UK visitor visa?")
    result = _sent(dialogue, dialogue.turn(complaint + " " + fee, _proposal(
        "unsupported", complaint, extra=[("fees", fee)])))
    _assert_application_help(result)
    _assert_authority_unchanged(result, original)
    assert re.search(r"£\s*135\b", result.body), result.body
    assert re.search(r"6\s*个月|6-month", result.body, re.I), result.body
    assert re.search(r"申请费|application fee", result.body, re.I), result.body
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("separator", [", ", "; "])
def test_excerpt_cannot_remove_an_eligibility_condition_from_the_application_request(tmp_path, language, separator):
    dialogue, original = _known_student(tmp_path, language)
    condition = "如果我符合英国旅游签证申请条件" if language == "zh" else "If I am eligible for a UK tourist visa"
    excerpt = ("告诉我英国旅游签证在哪个网页申请、怎么开始。" if language == "zh" else
               "tell me which webpage to use to apply for a UK tourist visa and how to get started.")
    body = condition + separator + excerpt
    result = _sent(dialogue, dialogue.turn(body, _proposal("unsupported", excerpt)))
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence
    assert APPLICATION_URL not in result.body and "Apply now" not in result.body, result.body


@pytest.mark.parametrize("language", ["zh", "en"])
def test_expired_application_source_produces_a_recheck_not_stale_steps_or_personal_intake(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language, conversation_type=AttachmentConversation)
    body = LANGUAGE[language]["complaint"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("unsupported", body), today=date(2026, 10, 5)))
    _assert_authority_unchanged(result, original)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence
    assert re.search(r"复核|核实|重新核对|recheck|re-check|current official|up.to.date", result.body, re.I), result.body
    assert "Apply now" not in result.body and "预约" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize("language", ["zh", "en"])
def test_application_consultation_while_paused_does_not_resume_preparation(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    body = ("请先暂停我的英国签证材料准备。" if language == "zh" else
            "Please pause preparation of my UK visa documents.")
    paused = _sent(dialogue, dialogue.turn(body, _patch(control=("pause", body))))
    assert paused.case.preparation_paused and paused.case.profile == original.case.profile
    complaint = LANGUAGE[language]["complaint"]
    result = _sent(dialogue, dialogue.turn(complaint, _proposal("unsupported", complaint)))
    _assert_application_help(result)
    _assert_authority_unchanged(result, paused, paused=True)
    assert result.case.profile == original.case.profile and result.case.evidence == original.case.evidence
    assert re.search(r"暂停|on hold|paused", result.body, re.I), result.body


@pytest.mark.parametrize("language", ["zh", "en"])
def test_current_explicit_dob_question_can_override_general_no_intake_without_granting_authority(tmp_path, language):
    dialogue, original = _known_student(tmp_path, language)
    words = LANGUAGE[language]
    named = _sent(dialogue, dialogue.turn(words["name_body"], _patch(
        updates=[("full_name", words["name"], words["name_body"])])))
    assert named.case.last_requested_fields == ["date_of_birth"]
    specific = ("不过现在请先问我的出生日期。" if language == "zh" else
                "But please ask me for my date of birth now.")
    body = words["complaint"] + " " + specific
    result = _sent(dialogue, dialogue.turn(body, _proposal(
        "application", words["complaint"], extra=[("next_step", specific)])))
    _assert_application_steps(result.body)
    _assert_authority_unchanged(result, named)
    assert result.case.profile == named.case.profile and result.case.evidence == named.case.evidence
    assert result.case.question_plan == result.case.last_requested_fields == ["date_of_birth"]
    assert re.search(r"出生日期|date of birth", result.body, re.I), result.body
    assert result.case.profile.date_of_birth is None
