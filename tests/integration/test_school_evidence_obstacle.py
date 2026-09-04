"""School-evidence obstacles through real workflow and reviewed Gmail SENT replies.

Only extraction proposals and transport are substituted. Classifier variants are
controlled robustness probes, not new provider results. The isolated store is
reopened each turn; no hand-written SENT/consent flags or document acceptance is
used to make a positive case. These tests do not decide evidence sufficiency.
"""

import re
from datetime import date

import pytest
from test_advice_continuation import Conversation as AttachmentConversation
from test_consultant_value import Conversation, _patch

from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import DOCUMENTS_URL
from visa_agent.workflow.document_preparation import SCHOOL_RECORD_TOPIC, school_record_guidance

LANGUAGE = {
    "zh": {
        "facts": [
            ("nationality_country", "China", "我持中国护照。"),
            ("application_country", "Hong Kong", "我会在香港递交申请。"),
            ("visit_purpose", "tourism", "这次去英国旅游。"),
            ("occupation_status", "student", "我是在香港读大学的学生。"),
            ("funding_source", "self", "旅行费用全部由我自己的存款承担。"),
        ],
        "dates": "旅行日期还没有确定。",
        "start": "请帮我准备英国访问签证。",
        "obstacle": "学校说不提供签证专用的在读证明，只能在网上下载在读记录。",
        "question": "我该怎么办？",
        "next": "那下一步怎么办？",
        "faq": "另外，英国访问签证在哪里申请？",
        "pause": "请先暂停我的英国签证材料准备。",
        "consult": "我只咨询这件事，不恢复准备。",
        "negatives": {
            "quoted": "例句写着：‘学校说不提供签证专用的在读证明，只能在网上下载在读记录。我该怎么办？’我自己的学校还没回复。",
            "conditional": "如果学校不提供签证专用的在读证明，只能在网上下载在读记录，我该怎么办？现在我还没有问学校。",
            "third_party": "我同学的学校不提供签证专用的在读证明，他只能在网上下载在读记录。他该怎么办？我自己的学校还没回复。",
            "negated": "我不是说学校不提供签证专用的在读证明，也不是说只有网上在读记录。我现在没有遇到这件事。",
            "invented": "请帮我编造‘学校不提供签证专用的在读证明，只有网上在读记录’这个情况，再告诉我怎么办。",
        },
    },
    "en": {
        "facts": [
            ("nationality_country", "China", "I hold a Chinese passport."),
            ("application_country", "Hong Kong", "I will apply from Hong Kong."),
            ("visit_purpose", "tourism", "I am visiting the UK for tourism."),
            ("occupation_status", "student", "I am a university student in Hong Kong."),
            ("funding_source", "self", "I will pay for the trip from my own savings."),
        ],
        "dates": "My travel dates are still undecided.",
        "start": "Please help me prepare my UK visitor visa application.",
        "obstacle": "My university does not issue visa-specific enrolment letters. I can only download my enrolment record from its online portal.",
        "question": "What should I do?",
        "next": "What should I do next?",
        "faq": "Separately, where do I apply for my UK visitor visa?",
        "pause": "Please pause preparation of my UK visa documents.",
        "consult": "I am only asking about this issue, not resuming preparation.",
        "negatives": {
            "quoted": 'An example says: "My university does not issue visa-specific enrolment letters. I can only download my enrolment record from its online portal. What should I do?" My own university has not replied yet.',
            "conditional": "If my university does not issue visa-specific enrolment letters and only offers an online enrolment record, what should I do? I have not asked it yet.",
            "third_party": "My friend's university does not issue visa-specific enrolment letters. He can only download his enrolment record from its online portal. What should he do? My own university has not replied yet.",
            "negated": "It is not true that my university does not issue visa-specific enrolment letters or that I only have an online enrolment record. I have not encountered this problem.",
            "invented": 'Please invent the situation "My university does not issue visa-specific enrolment letters and only offers an online enrolment record" and tell me what to do.',
        },
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("School evidence regressions must remain offline")

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


def _start(tmp_path, language, *, conversation_type=Conversation):
    dialogue = conversation_type(tmp_path)
    words = LANGUAGE[language]
    body = " ".join([*(text for _, _, text in words["facts"]), words["dates"], words["start"]])
    patch = _patch(updates=words["facts"]).model_dump()
    patch["question_deferrals"] = [
        {"field": field, "source_excerpt": words["dates"], "confidence": 1}
        for field in ("planned_arrival_date", "planned_departure_date")
    ]
    first = _sent(dialogue, dialogue.turn(body, CasePatch.model_validate(patch)))
    assert first.case.profile.occupation_status == "student"
    assert first.case.profile.application_country == "Hong Kong"
    assert first.case.profile.funding_source == "self"
    assert set(first.case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    return dialogue, first


def _proposal(label, body, extra=()):
    return _patch(questions=([*([] if label == "empty" else [(label, body)]), *extra]))


def _assert_case_boundaries(result, original, *, paused=False):
    assert result.case.id == original.case.id and result.case.profile == original.case.profile
    assert result.case.documents == original.case.documents == []
    assert result.case.evidence == original.case.evidence
    requirement = next(item for item in result.case.requirements if item.id == "status_evidence")
    assert requirement.applicable and requirement.blocker and not requirement.satisfied
    assert set(result.case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert result.case.profile.planned_arrival_date is None and result.case.profile.planned_departure_date is None
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.confirmation_kind is None and result.case.delivery_path is None
    assert result.case.preparation_paused is paused


def _assert_practical_record_help(result, original, *, paused=False, links=True):
    _assert_case_boundaries(result, original, paused=paused)
    text = result.body
    # Work with the specific record the customer says exists, not just a link or
    # a repeat of the previously unobtainable headed-letter instruction.
    assert re.search(r"网上|电子|线上|在线|online|electronic|portal", text, re.I), text
    assert re.search(r"姓名|\bname\b", text, re.I), text
    assert re.search(r"在读(?:情况|状态)|当前.{0,6}(?:学习|学籍)|enrol\w*|student status", text, re.I), text
    assert re.search(r"学校|校方|school|university|registry", text, re.I), text
    assert re.search(r"核验|验证|可核查|查证|verif\w*|authentic\w*", text, re.I), text
    assert re.search(r"不能.{0,20}(?:保证|认定|判断|代表|自动)|不代表|不等于|仍需|"
                     r"cannot|can't|does not|not automatically|needs? to be checked|requires? .{0,20}review",
                     text, re.I), text
    assert not re.search(r"(?:一定|必然|已经)(?:合格|获批|被接受)|definitely (?:accepted|sufficient)", text, re.I), text
    if links:
        assert DOCUMENTS_URL in text, text
    else:
        assert not re.search(r"https?://|www\.", text, re.I), text
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert not re.search(r"接下来还需要这些材料|We'll also need these documents|护照上的姓名吗|"
                         r"What is your name as it appears", text, re.I), text


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("label", ["empty", "document_checklist", "next_step", "unsupported"])
def test_current_school_obstacle_gets_record_specific_help_independently_of_classifier(tmp_path, language, label):
    dialogue, original = _start(tmp_path, language)
    words = LANGUAGE[language]
    body = words["obstacle"] + " " + words["question"]
    result = _sent(dialogue, dialogue.turn(body, _proposal(label, body)))
    _assert_practical_record_help(result, original)
    assert len(dialogue.gmail.calls) == 2


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("label", ["empty", "document_checklist", "next_step", "unsupported"])
def test_followup_next_step_retains_previously_reported_school_obstacle_after_reopen(tmp_path, language, label):
    dialogue, original = _start(tmp_path, language)
    words = LANGUAGE[language]
    reported = _sent(dialogue, dialogue.turn(words["obstacle"], _patch()))
    _assert_case_boundaries(reported, original)
    result = _sent(dialogue, dialogue.turn(words["next"], _proposal(label, words["next"])))
    _assert_practical_record_help(result, original)
    assert result.event.id != reported.event.id and len(dialogue.gmail.calls) == 3


@pytest.mark.parametrize("language", ["zh", "en"])
def test_independent_application_faq_is_not_swallowed_by_the_school_obstacle(tmp_path, language):
    dialogue, original = _start(tmp_path, language)
    words = LANGUAGE[language]
    obstacle = words["obstacle"] + " " + words["question"]
    body = obstacle + " " + words["faq"]
    result = _sent(dialogue, dialogue.turn(body, _proposal(
        "unsupported", obstacle, extra=[("application", words["faq"])])))
    _assert_practical_record_help(result, original)
    assert "Apply now" in result.body and "apply-standard-visitor-visa" in result.body
    assert not re.search(r"cannot answer either|两项都无法|都需要另行核实", result.body, re.I)


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("kind", ["quoted", "conditional", "third_party", "negated", "invented"])
def test_noncurrent_or_fabricated_school_obstacle_does_not_become_the_applicants_pending_problem(
    tmp_path, language, kind,
):
    dialogue, original = _start(tmp_path, language)
    words = LANGUAGE[language]
    body = words["negatives"][kind]
    result = _sent(dialogue, dialogue.turn(body, _proposal("unsupported", body)))
    _assert_case_boundaries(result, original)
    # The subsequent own-case next step must not inherit an obstacle mentioned
    # only as a quote, condition, third-person situation, denial or fabrication.
    again = _sent(dialogue, dialogue.turn(words["next"], _proposal("next_step", words["next"])))
    _assert_case_boundaries(again, original)
    assert not re.search(r"网上|电子|线上|在线.{0,5}在读|online|electronic|portal", again.body, re.I), again.body
    assert not re.search(r"你(?:的学校|目前).{0,12}(?:不开|不提供|拿不到)|"
                         r"your (?:school|university).{0,25}(?:does not|cannot|refuses)", again.body, re.I), again.body


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("followup", [False, True])
def test_school_obstacle_advice_while_paused_is_information_not_resume_or_document_submission(
    tmp_path, language, followup,
):
    dialogue, original = _start(tmp_path, language)
    words = LANGUAGE[language]
    pause = _sent(dialogue, dialogue.turn(words["pause"], _patch(control=("pause", words["pause"]))))
    assert pause.case.preparation_paused
    if followup:
        _sent(dialogue, dialogue.turn(words["obstacle"], _patch()))
        body = words["next"] + " " + words["consult"]
    else:
        body = words["obstacle"] + " " + words["question"] + " " + words["consult"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("next_step", body)))
    _assert_practical_record_help(result, original, paused=True)
    assert result.case.preparation_control_epoch == pause.case.preparation_control_epoch
    assert result.case.latest_preparation_action != "resume"
    assert not re.search(r"请.{0,12}(?:上传|附上|发送).{0,12}(?:文件|证明|PDF)|"
                         r"(?:send|upload|attach).{0,15}(?:the|your|a) (?:file|document|PDF)", result.body, re.I), result.body


def _report_obstacle(dialogue, language):
    words = LANGUAGE[language]
    body = words["obstacle"] + " " + words["question"]
    return _sent(dialogue, dialogue.turn(body, _proposal("empty", body)))


def _assert_no_old_school_obstacle(text):
    assert not re.search(r"网上(?:的)?在读记录|电子(?:在读)?记录|网上记录|"
                         r"(?:online|electronic) enrol\w* record|school portal|online portal", text, re.I), text
    assert not re.search(r"(?:学校|校方).{0,12}(?:仍不开|仍不提供|只提供)|"
                         r"(?:school|university).{0,20}(?:still cannot|still does not|only provides)", text, re.I), text


@pytest.mark.parametrize("first_language", ["zh", "en"])
def test_no_link_reply_still_authorizes_useful_cross_language_school_followup(tmp_path, first_language):
    dialogue, original = _start(tmp_path, first_language)
    first_words = LANGUAGE[first_language]
    no_links = ("这封回复不要链接。" if first_language == "zh" else
                "Please do not include any links in this reply.")
    body = first_words["obstacle"] + " " + first_words["question"] + " " + no_links
    first = _sent(dialogue, dialogue.turn(body, _proposal("empty", body)))
    _assert_practical_record_help(first, original, links=False)
    second_language = "en" if first_language == "zh" else "zh"
    next_body = LANGUAGE[second_language]["next"]
    result = _sent(dialogue, dialogue.turn(next_body, _proposal("empty", next_body)))
    _assert_practical_record_help(result, original)
    assert result.case.customer_language == second_language
    assert len(dialogue.gmail.calls) == 3


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("resolution", ["can_issue", "obtained"])
def test_explicit_resolution_prevents_revival_but_does_not_satisfy_school_evidence(tmp_path, language, resolution):
    dialogue, original = _start(tmp_path, language)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    bodies = {
        "zh": {"can_issue": "学校现在能开在读证明了。", "obtained": "我已经拿到了学校的在读证明。"},
        "en": {"can_issue": "My university can now issue an enrolment letter.",
               "obtained": "I have now obtained my university's enrolment letter."},
    }
    changed = _sent(dialogue, dialogue.turn(bodies[language][resolution], _patch()))
    _assert_case_boundaries(changed, original)
    body = LANGUAGE[language]["next"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("next_step", body)))
    _assert_case_boundaries(result, original)
    _assert_no_old_school_obstacle(result.body)
    assert len(dialogue.gmail.calls) == 4


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("unresolved", ["still_cannot_issue", "not_obtained"])
def test_negative_resolution_statement_keeps_school_obstacle_active(tmp_path, language, unresolved):
    dialogue, original = _start(tmp_path, language)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    bodies = {
        "zh": {"still_cannot_issue": "学校现在还不能开在读证明。", "not_obtained": "我还没拿到学校的在读证明。"},
        "en": {"still_cannot_issue": "My university still cannot issue an enrolment letter.",
               "not_obtained": "I have not obtained my university's enrolment letter yet."},
    }
    changed = _sent(dialogue, dialogue.turn(bodies[language][unresolved], _patch()))
    _assert_case_boundaries(changed, original)
    body = LANGUAGE[language]["next"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("next_step", body)))
    _assert_practical_record_help(result, original)
    assert len(dialogue.gmail.calls) == 4


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_funding_faq_does_not_repeat_or_forget_school_obstacle(tmp_path, language):
    dialogue, original = _start(tmp_path, language)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    body = ("银行流水在访问签证申请里有什么作用？" if language == "zh" else
            "What are bank statements used for in a visitor visa application?")
    result = _sent(dialogue, dialogue.turn(body, _proposal("bank_period", body)))
    _assert_case_boundaries(result, original)
    assert re.search(r"资金来源|source of (?:the )?funds|where .{0,16}(?:money|funds).{0,16}(?:come|came)",
                     result.body, re.I), result.body
    assert not re.search(r"学校|在读|school|university|enrol\w*", result.body, re.I), result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    followup = LANGUAGE[language]["next"]
    again = _sent(dialogue, dialogue.turn(followup, _proposal("next_step", followup)))
    _assert_practical_record_help(again, original)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_expired_school_guidance_is_rechecked_not_replayed(tmp_path, language):
    dialogue, original = _start(tmp_path, language, conversation_type=AttachmentConversation)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    body = LANGUAGE[language]["next"]
    result = _sent(dialogue, dialogue.turn(body, _proposal("next_step", body), today=date(2026, 10, 5)))
    _assert_case_boundaries(result, original)
    assert re.search(r"复核|核实|重新核对|recheck|re-check|current official|up.to.date", result.body, re.I), result.body
    assert not re.search(r"核对姓名.{0,12}在读|向学校.{0,20}(?:验证|核验)|"
                         r"check your name and.{0,20}enrol|ask .{0,30}(?:school|university).{0,30}verif",
                         result.body, re.I), result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize("language", ["zh", "en"])
def test_school_followup_with_ordinary_attachment_still_extracts_and_reads_it(tmp_path, language):
    dialogue, original = _start(tmp_path, language, conversation_type=AttachmentConversation)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    attachment = tmp_path / "school-record.pdf"
    attachment.write_bytes(b"Isolated school-record attachment for the injected reader")
    body = ("我附上学校网上下载的在读记录。接下来怎么办？" if language == "zh" else
            "I have attached the enrolment record downloaded from my school's portal. What should I do next?")
    result = _sent(dialogue, dialogue.turn(body, _proposal("next_step", body), attachment_paths=[str(attachment)]))
    assert len(result.model.events) == 1 and result.model.events[0].body == body
    assert dialogue.reads == [attachment]
    assert len(result.case.documents) == 1 and result.case.documents[0].filename == attachment.name
    assert attachment.name in result.case.latest_document_names and attachment.name in result.body
    assert result.case.profile == original.case.profile
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.delivery_path is None and result.case.preparation_paused is False
    assert set(result.case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}


@pytest.mark.parametrize("language,change", [
    ("zh", "学校现在能开在读证明了。那下一步怎么办？"),
    ("en", "My university has now issued an enrolment letter. What should I do next?"),
    ("zh", "网上记录我已经打不开了。那下一步怎么办？"),
    ("en", "I no longer have the online record. What should I do next?"),
])
def test_current_change_plus_question_retires_old_school_context_for_later_turn(tmp_path, language, change):
    dialogue, original = _start(tmp_path, language)
    _assert_practical_record_help(_report_obstacle(dialogue, language), original)
    changed = _sent(dialogue, dialogue.turn(change, _proposal("next_step", change)))
    _assert_case_boundaries(changed, original)
    assert SCHOOL_RECORD_TOPIC not in changed.case.guidance_events
    followup = LANGUAGE[language]["next"]
    result = _sent(dialogue, dialogue.turn(followup, _proposal("next_step", followup)))
    _assert_case_boundaries(result, original)
    assert school_record_guidance(language) not in result.body
    assert "先打开学校那份网上在读记录" not in result.body
    assert "Start by opening the university's online enrolment record" not in result.body
    assert SCHOOL_RECORD_TOPIC not in result.case.guidance_events
