"""Conversation progression through the real reviewed Gmail sending boundary.

These are synthetic extraction proposals, not provider results or a naturalness
score. Each turn reopens an isolated store and captures actual sender SENT text.
The assertions test relevant acknowledgement and a bounded action, not merely
the presence of an official URL. No live mailbox, model or processing DB is used.
"""

import re

import pytest
from test_consultant_value import Conversation, _context, _patch

from visa_agent.llm.ports import QuestionDeferral
from visa_agent.workflow.adviser_guidance import DOCUMENTS_URL
from visa_agent.workflow.conversation import QUESTION_TEXT_EN, QUESTION_TEXT_ZH, reply_items

SCENARIOS = {
    "zh": {
        "opening": "我想办英国签证，需要什么？",
        "purpose": "主要是去旅游。",
        "passport": "我持中国护照。",
        "location": "我住香港，会在香港申请。",
        "name": "我的护照姓名是示例安宁。",
        "full_name": "示例安宁",
        "faq": "中文的证明材料应该怎样翻译？",
    },
    "en": {
        "opening": "I want to apply for a UK visa. What do I need?",
        "purpose": "I am going for tourism.",
        "passport": "I hold a Chinese passport.",
        "location": "I live in Hong Kong and will apply from Hong Kong.",
        "name": "The name on my passport is Example Morgan.",
        "full_name": "Example Morgan",
        "faq": "How should I translate my Chinese supporting documents?",
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Progression regressions cannot contact a provider or Gmail")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def _up_to_location(dialogue, language):
    words = SCENARIOS[language]
    opening = dialogue.turn(words["opening"], _patch(
        questions=[("document_checklist", words["opening"])]))
    purpose = dialogue.turn(words["purpose"], _patch(
        updates=[("visit_purpose", "tourism", words["purpose"])]))
    passport = dialogue.turn(words["passport"], _patch(
        updates=[("nationality_country", "China", words["passport"])]))
    location = dialogue.turn(words["location"], _patch(
        updates=[("application_country", "Hong Kong", words["location"])]))
    assert len({item.case.id for item in (opening, purpose, passport, location)}) == 1
    assert len(dialogue.gmail.calls) == 4
    assert passport.model.events[0].requested_fields == ["nationality_country"]
    assert location.model.events[0].requested_fields == ["application_country"]
    return passport, location


def _assert_no_invented_profile(result):
    profile = result.case.profile
    assert profile.visit_purpose == "tourism"
    assert profile.nationality_country == "China"
    assert profile.application_country == "Hong Kong"
    assert profile.occupation_status is None and profile.funding_source is None
    assert profile.planned_arrival_date is None and profile.planned_departure_date is None
    assert profile.current_address is None and profile.route_confirmed_standard_visitor is not True
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert not result.case.preparation_paused and result.case.delivery_path is None
    assert not re.search(
        r"你(?:已|已经)(?:有|具备|获得)合法居留|你(?:已|已经)符合(?:申请|签证)资格|"
        r"you (?:are|have been) (?:eligible|approved)|you have lawful residence", result.body, re.I,
    ), result.body


@pytest.mark.parametrize("language", ["zh", "en"])
def test_answering_passport_and_location_receives_contextual_acknowledgement_after_reopen(tmp_path, language):
    dialogue = Conversation(tmp_path)
    passport, location = _up_to_location(dialogue, language)
    _assert_no_invented_profile(location)
    assert re.search(r"中国|Chinese|China", passport.body, re.I), passport.body
    assert re.search(r"香港|Hong Kong", location.body, re.I), location.body
    assert "具体要准备哪些材料，要先看你的出行目的和申请地点" not in location.body
    assert not re.match(r"(?:你好[。！]|Hello[,]?)", location.body), location.body
    assert len(reply_items(location.case)[1]) <= 1
    assert location.case.active_evidence("nationality_country")[0].source_event_id == passport.event.id
    assert location.case.active_evidence("application_country")[0].source_event_id == location.event.id


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_application_location_offers_a_bounded_residence_evidence_action_not_eligibility(tmp_path, language):
    dialogue = Conversation(tmp_path)
    _, location = _up_to_location(dialogue, language)
    _assert_no_invented_profile(location)
    assert re.search(r"(?:居留|居住).{0,12}(?:证明|身份)|(?:residen\w*|immigration status)",
                     location.body, re.I), location.body
    assert re.search(r"核对|准备|整理|找|查看|check|gather|prepare|look for", location.body, re.I), location.body
    # The supporting-evidence source was already supplied with the itinerary
    # action. Keep the residence advice source-bound without pasting the same
    # long GOV.UK page into every follow-up email.
    assert DOCUMENTS_URL not in location.body, location.body
    assert sum(DOCUMENTS_URL in call["body"] for call in dialogue.gmail.calls) == 1
    # The message must connect the action to applying outside nationality, without
    # inventing which local status/document the applicant holds or deciding the route.
    assert re.search(r"申请地|香港|Hong Kong|applying|apply from", location.body, re.I), location.body
    assert len(reply_items(location.case)[1]) <= 1


@pytest.mark.parametrize("language", ["zh", "en"])
def test_supplied_name_is_acknowledged_without_restarting_a_welcome_questionnaire(tmp_path, language):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn(*_context("student"))
    # A complete first description should receive case-specific preparation
    # value before administrative form intake. The applicant may still provide
    # their name naturally, and that unsolicited fact must remain usable.
    assert first.case.last_requested_fields == []
    assert re.search(r"在读|资金|enrolment|fund", first.body, re.I), first.body
    words = SCENARIOS[language]
    result = dialogue.turn(words["name"], _patch(
        updates=[("full_name", words["full_name"], words["name"])]))
    assert result.case.id == first.case.id and result.model.events[0].requested_fields == []
    assert result.case.profile.full_name == words["full_name"]
    assert result.case.active_evidence("full_name")[0].source_event_id == result.event.id
    assert re.search(r"(?:收到|记下|记录|姓名|名字)|(?:noted|recorded|received|name)", result.body, re.I), result.body
    assert "具体要准备哪些材料，要先看你的出行目的和申请地点" not in result.body
    assert not re.search(r"(?:，你好[。！]|^Hello(?: [^,]+)?,|^你好[。！])", result.body), result.body
    assert len(reply_items(result.case)[1]) <= 1
    assert "full_name" not in result.case.last_requested_fields
    assert set(result.case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert len(dialogue.gmail.calls) == 2


@pytest.mark.parametrize("language", ["zh", "en"])
def test_residence_guidance_actually_sent_is_not_repeated_for_an_identity_answer(tmp_path, language):
    dialogue = Conversation(tmp_path)
    _, location = _up_to_location(dialogue, language)
    residence_answers = [answer for answer in location.case.customer_answers
                         if re.search(r"居留|居住|residen\w*|immigration status", answer, re.I)]
    assert residence_answers, location.body
    assert all(answer in dialogue.gmail.calls[-1]["body"] for answer in residence_answers)
    words = SCENARIOS[language]
    result = dialogue.turn(words["name"], _patch(
        updates=[("full_name", words["full_name"], words["name"])]))
    assert result.case.id == location.case.id and len(dialogue.gmail.calls) == 5
    assert all(answer not in result.body for answer in residence_answers), result.body
    assert not re.search(r"居留|居住|residen\w*|immigration status", result.body, re.I), result.body


@pytest.mark.parametrize("language", ["zh", "en"])
def test_independent_faq_is_answered_without_adding_a_location_guide_or_intake(tmp_path, language):
    dialogue = Conversation(tmp_path)
    _, location = _up_to_location(dialogue, language)
    words = SCENARIOS[language]
    result = dialogue.turn(words["faq"], _patch(questions=[("translation", words["faq"])]))
    assert result.case.id == location.case.id and len(dialogue.gmail.calls) == 5
    assert re.search(r"完整翻译|full translation|complete translation", result.body, re.I), result.body
    assert re.search(r"译者|translator", result.body, re.I), result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert not re.search(r"居留|居住|residen\w*|immigration status|Apply now", result.body, re.I), result.body
    assert result.case.profile.occupation_status is None and result.case.profile.funding_source is None


@pytest.mark.parametrize("language", ["zh", "en"])
def test_unprovided_passport_and_application_location_are_not_assumed_for_new_guidance(tmp_path, language):
    dialogue = Conversation(tmp_path)
    words = SCENARIOS[language]
    dialogue.turn(words["opening"], _patch(questions=[("document_checklist", words["opening"])]))
    dialogue.turn(words["purpose"], _patch(updates=[("visit_purpose", "tourism", words["purpose"])]))
    body = ("申请地点还没定，护照国籍我稍后再告诉你。" if language == "zh" else
            "I have not decided where I will apply, and I will tell you my passport nationality later.")
    result = dialogue.turn(body, _patch())
    assert result.case.profile.nationality_country is None and result.case.profile.application_country is None
    assert result.case.active_evidence("nationality_country") == []
    assert result.case.active_evidence("application_country") == []
    assert not re.search(r"中国|香港|China|Chinese|Hong Kong", result.body, re.I), result.body
    assert not re.search(r"居留|居住|residen\w*|immigration status", result.body, re.I), result.body
    assert result.case.profile.route_confirmed_standard_visitor is not True


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_location_alongside_faq_does_not_trigger_an_unrequested_residence_guide(tmp_path, language):
    dialogue = Conversation(tmp_path)
    words = SCENARIOS[language]
    dialogue.turn(words["opening"], _patch(questions=[("document_checklist", words["opening"])]))
    dialogue.turn(words["purpose"], _patch(updates=[("visit_purpose", "tourism", words["purpose"])]))
    dialogue.turn(words["passport"], _patch(updates=[("nationality_country", "China", words["passport"])]))
    result = dialogue.turn(words["location"] + " " + words["faq"], _patch(
        updates=[("application_country", "Hong Kong", words["location"])],
        questions=[("translation", words["faq"])]))
    _assert_no_invented_profile(result)
    assert re.search(r"完整翻译|full translation|complete translation", result.body, re.I), result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert not re.search(r"居留|居住|residen\w*|immigration status|Apply now", result.body, re.I), result.body
    # The new topic remains unshared. A later pure FAQ must not flush it merely
    # because the current case now has an application location.
    again = dialogue.turn(words["faq"], _patch(questions=[("translation", words["faq"])]))
    assert not re.search(r"居留|居住|residen\w*|immigration status|Apply now", again.body, re.I), again.body
    assert again.case.question_plan == again.case.last_requested_fields == []


@pytest.mark.parametrize(
    ("language", "context", "updates", "deferral", "next_step", "name_body", "name", "dob_body"),
    [
        (
            "zh",
            "我持中国护照，会在香港申请，去英国旅游。我目前在读书，费用由自己承担，旅行日期还没有确定。",
            [
                ("nationality_country", "China", "中国护照"),
                ("application_country", "Hong Kong", "香港申请"),
                ("visit_purpose", "tourism", "去英国旅游"),
                ("occupation_status", "student", "目前在读书"),
                ("funding_source", "self", "费用由自己承担"),
            ],
            "旅行日期还没有确定",
            "下一步我该准备什么？",
            "护照姓名是陈示例。",
            "陈示例",
            "生日是2000年1月2日。",
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong for a UK holiday. I am a student and "
            "will pay for the trip myself. My travel dates are not decided yet.",
            [
                ("nationality_country", "China", "Chinese passport"),
                ("application_country", "Hong Kong", "apply in Hong Kong"),
                ("visit_purpose", "tourism", "UK holiday"),
                ("occupation_status", "student", "I am a student"),
                ("funding_source", "self", "pay for the trip myself"),
            ],
            "travel dates are not decided yet",
            "What should I prepare next?",
            "My passport name is Example Chen.",
            "Example Chen",
            "My date of birth is 2 January 2000.",
        ),
    ],
)
def test_identity_fact_follow_ups_sound_consultative_and_ask_only_one_new_detail(
    tmp_path, language, context, updates, deferral, next_step, name_body, name, dob_body,
):
    dialogue = Conversation(tmp_path)
    deferred_patch = _patch(updates=updates).model_copy(update={
        "question_deferrals": [
            QuestionDeferral(field=field, source_excerpt=deferral, confidence=1)
            for field in ("planned_arrival_date", "planned_departure_date")
        ],
    })
    current = dialogue.turn(context, deferred_patch)
    for _ in range(4):
        if current.case.last_requested_fields == ["full_name"]:
            break
        current = dialogue.turn(next_step, _patch(questions=[("next_step", next_step)]))
    assert current.case.last_requested_fields == ["full_name"], current.body

    questions = QUESTION_TEXT_ZH if language == "zh" else QUESTION_TEXT_EN
    named = dialogue.turn(name_body, _patch(updates=[("full_name", name, name_body)]))
    assert named.model.events[0].requested_fields == ["full_name"]
    assert named.case.profile.full_name == name
    assert named.case.last_requested_fields == ["date_of_birth"]
    assert reply_items(named.case)[1] == [questions["date_of_birth"]]
    assert questions["full_name"] not in named.body
    if language == "zh":
        assert "好的，护照姓名已经记下了。" in named.body
        assert "出生日期" in named.body and "护照资料页" in named.body and "申请表" in named.body
    else:
        assert "Thanks — I've noted your passport name." in named.body
        assert "date of birth" in named.body and "passport details" in named.body
        assert "application information" in named.body

    birth = dialogue.turn(dob_body, _patch(updates=[("date_of_birth", "2000-01-02", dob_body)]))
    assert birth.model.events[0].requested_fields == ["date_of_birth"]
    assert str(birth.case.profile.date_of_birth) == "2000-01-02"
    assert birth.case.last_requested_fields == ["uk_accommodation"]
    assert reply_items(birth.case)[1] == [questions["uk_accommodation"]]
    assert questions["full_name"] not in birth.body and questions["date_of_birth"] not in birth.body
    assert not {"planned_arrival_date", "planned_departure_date"}.intersection(
        birth.case.last_requested_fields
    )
    assert questions["planned_arrival_date"] not in birth.body
    assert questions["planned_departure_date"] not in birth.body
    if language == "zh":
        assert "好的，出生日期也记下了。" in birth.body
        assert "住宿安排" in birth.body and "预计行程" in birth.body and "申请表" in birth.body
    else:
        assert "Thanks — I've noted your date of birth." in birth.body
        assert "UK accommodation" in birth.body and "visit plan" in birth.body
        assert "application form" in birth.body
