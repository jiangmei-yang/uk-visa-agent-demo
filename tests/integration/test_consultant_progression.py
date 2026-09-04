"""Conversation progression through the real reviewed Gmail sending boundary.

These are synthetic extraction proposals, not provider results or a naturalness
score. Each turn reopens an isolated store and captures actual sender SENT text.
The assertions test relevant acknowledgement and a bounded action, not merely
the presence of an official URL. No live mailbox, model or processing DB is used.
"""

import re

import pytest
from test_consultant_value import Conversation, _context, _patch

from visa_agent.workflow.adviser_guidance import DOCUMENTS_URL
from visa_agent.workflow.conversation import reply_items

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
    _, location = _up_to_location(Conversation(tmp_path), language)
    _assert_no_invented_profile(location)
    assert re.search(r"(?:居留|居住).{0,12}(?:证明|身份)|(?:residen\w*|immigration status)",
                     location.body, re.I), location.body
    assert re.search(r"核对|准备|整理|找|查看|check|gather|prepare|look for", location.body, re.I), location.body
    assert DOCUMENTS_URL in location.body, location.body
    # The message must connect the action to applying outside nationality, without
    # inventing which local status/document the applicant holds or deciding the route.
    assert re.search(r"申请地|香港|Hong Kong|applying|apply from", location.body, re.I), location.body
    assert len(reply_items(location.case)[1]) <= 1


@pytest.mark.parametrize("language", ["zh", "en"])
def test_supplied_name_is_acknowledged_without_restarting_a_welcome_questionnaire(tmp_path, language):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn(*_context("student"))
    assert first.case.last_requested_fields == ["full_name"]
    words = SCENARIOS[language]
    result = dialogue.turn(words["name"], _patch(
        updates=[("full_name", words["full_name"], words["name"])]))
    assert result.case.id == first.case.id and result.model.events[0].requested_fields == ["full_name"]
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
