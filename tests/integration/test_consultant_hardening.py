"""Hardening contracts for deterministic consultant replies.

These scenarios exercise the real workflow, SQLite reopen and automatic Gmail
sender through the existing integration harnesses.  They intentionally describe
the customer-visible contract even when an implementation is not there yet.
"""

import re
from datetime import timedelta

import pytest
import test_consultant_value as consultant
from test_advice_continuation import Conversation as AdviceConversation
from test_advice_continuation import proposal

from visa_agent.llm.ports import QuestionDeferral
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL, ROUTE_CHECK_URL
from visa_agent.workflow.conversation import QUESTION_TEXT_EN, QUESTION_TEXT_ZH, reply_items
from visa_agent.workflow.customer_questions import (
    ROUTE_CHECK_SOURCE,
    STANDARD_VISITOR_SOURCE,
    STUDENT_SOURCE,
)
from visa_agent.workflow.guidance_freshness import REVIEW_AFTER
from visa_agent.workflow.sponsor_guidance import (
    SPONSOR_SOURCE,
    sponsor_support_answer,
    sponsor_support_question,
    sponsor_verification_question,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Consultant hardening tests cannot access a live provider")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def _urls(body: str) -> list[str]:
    return [match.rstrip(".,;:，。；：") for match in re.findall(r"https://[^\s)]+", body)]


def test_english_father_sponsor_preserves_the_named_relationship_and_costs(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    question = (
        "What should the sponsor statement include? My father will cover flights and accommodation. "
        "What evidence can show our relationship?"
    )

    result = dialogue.turn(
        question,
        consultant._patch(questions=[("sponsor_support", question)]),
    )

    answer = result.body.casefold()
    assert result.case.customer_language == "en"
    assert "your father plans to cover flights and accommodation" in answer
    assert "relationship to your father" in answer
    assert "your sponsor plans to cover" not in answer
    assert SPONSOR_SOURCE in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_mixed_payers_are_not_collapsed_into_one_person_or_one_cost_bundle(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    question = (
        "What should the sponsor statement include? My father will pay for flights, "
        "while my mother will pay for accommodation."
    )

    result = dialogue.turn(
        question,
        consultant._patch(questions=[("sponsor_support", question)]),
    )

    answer = result.body.casefold()
    assert "father" in answer and "flights" in answer
    assert "mother" in answer and "accommodation" in answer
    assert "father's promise of support" not in answer
    assert "mother's promise of support" not in answer
    assert not re.search(r"(?:father|mother).{0,35}cover(?:s|ing)? flights and accommodation", answer)
    assert SPONSOR_SOURCE in result.body


@pytest.mark.parametrize(
    "model_topic",
    [
        "route_orientation",
        "application",
        "unsupported",
        "next_step",
        "document_checklist",
        "off_topic",
        None,
    ],
)
def test_visitor_student_route_question_is_rescued_across_topic_labels(tmp_path, model_topic):
    question = (
        "I am not sure whether I need a Standard Visitor visa or a Student visa. "
        "Which route should I check?"
    )
    questions = [] if model_topic is None else [(model_topic, question)]

    result = consultant.Conversation(tmp_path).turn(
        question,
        consultant._patch(questions=questions),
    )

    assert "Standard Visitor" in result.body and "Student" in result.body
    assert all(
        source in result.body
        for source in (ROUTE_CHECK_SOURCE, STANDARD_VISITOR_SOURCE, STUDENT_SOURCE)
    )
    assert result.case.profile.route_confirmed_standard_visitor is False
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert result.case.customer_question_topics == (
        [] if model_topic is None else ["route_orientation"]
    )
    assert all(_urls(result.body).count(source) == 1 for source in (
        ROUTE_CHECK_SOURCE,
        STANDARD_VISITOR_SOURCE,
        STUDENT_SOURCE,
    ))


def test_traditional_chinese_complete_list_works_without_a_model_topic(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    initial = dialogue.turn(*consultant._context("student"))
    profile_before = initial.case.profile.model_dump(mode="json")
    question = "請把我總共需要準備的資料一次說清楚。"

    result = dialogue.turn(question, consultant._patch())

    assert result.case.profile.model_dump(mode="json") == profile_before
    assert result.case.customer_question_topics == []
    assert all(term in result.body for term in (
        "在线申请表",
        "证明材料",
        "香港合法居留证明",
        "在读证明",
        "自费资金",
        "最先做的一步",
    ))
    assert all(url in result.body for url in (ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL))
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_traditional_chinese_application_entry_works_without_a_model_topic(tmp_path):
    question = "標準訪客簽證應該從哪個網頁開始申請？請給我官方入口。"

    result = consultant.Conversation(tmp_path).turn(question, consultant._patch())

    assert APPLICATION_URL in result.body
    assert any(term in result.body for term in ("Apply now", "在线申请", "官方申请"))
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_traditional_chinese_sponsor_question_works_without_a_model_topic(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    question = "我父親會負責機票和住宿，資助說明應該寫甚麼，關係要用甚麼材料證明？"

    result = dialogue.turn(question, consultant._patch())

    assert "父亲" in result.body
    assert "机票" in result.body and "住宿" in result.body
    assert "资助说明" in result.body and "关系" in result.body
    assert "资金和来源" in result.body
    assert SPONSOR_SOURCE in result.body
    assert result.case.customer_question_topics == []
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_expired_overview_withholds_stale_details_and_links(tmp_path, monkeypatch):
    dialogue = consultant.Conversation(tmp_path)
    initial = dialogue.turn(*consultant._context("student"))
    profile_before = initial.case.profile.model_dump(mode="json")
    monkeypatch.setattr(consultant, "TODAY", REVIEW_AFTER + timedelta(days=1))
    question = "总共需要哪些资料，请一次性说清楚。"

    result = dialogue.turn(
        question,
        consultant._patch(questions=[("document_checklist", question)]),
    )

    assert result.case.profile.model_dump(mode="json") == profile_before
    assert any(term in result.body for term in ("到了复核日期", "需要重新核实", "复核最新官网"))
    assert "https://" not in result.body
    assert "近 10 年旅行记录" not in result.body
    assert "在线申请表要提前准备的信息" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_paused_customer_can_request_a_no_links_overview_without_resuming(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("student"))
    pause = "请先暂停我的英国签证材料准备。"
    paused = dialogue.turn(pause, consultant._patch(control=("pause", pause)))
    question = "总共需要哪些资料，请一次性说清楚，但不要发链接。"

    result = dialogue.turn(
        question,
        consultant._patch(questions=[("document_checklist", question)]),
    )

    assert result.case.preparation_paused
    assert result.case.preparation_control_epoch == paused.case.preparation_control_epoch
    assert "https://" not in result.body
    assert all(term in result.body for term in (
        "在线申请表",
        "香港合法居留证明",
        "在读证明",
        "自费资金",
    ))
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize(
    "prefix",
    [
        'My friend wrote "No links, please."',
        "If I ask for this later, please do not include any links in that reply.",
        'The quoted example says "No links, please."',
    ],
    ids=["third-party", "conditional", "quoted"],
)
def test_noncurrent_no_links_language_does_not_strip_current_overview_sources(tmp_path, prefix):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("student"))
    request = "Could you give me the complete list of documents and explain everything in one message?"

    result = dialogue.turn(
        f"{prefix} {request}",
        consultant._patch(questions=[("document_checklist", request)]),
    )

    assert "Valid passport" in result.body
    assert APPLICATION_URL in result.body and DOCUMENTS_URL in result.body
    assert "https://" in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_failed_mother_sponsor_answer_is_not_replayed_after_father_correction(tmp_path):
    dialogue = AdviceConversation(tmp_path)
    mother_question = "What should my mother include in her sponsor statement?"
    mother_funding = "My mother will pay for my trip."
    mother_location = "My mother does not live in the UK."
    failed = dialogue.turn(
        " ".join((mother_funding, mother_location, mother_question)),
        proposal(
            facts=[
                ("funding_source", "personal_sponsor", mother_funding),
                ("sponsor_relationship", "mother", mother_funding),
                ("sponsor_is_in_uk", False, mother_location),
            ],
            questions=[("sponsor_support", mother_question)],
        ),
        delivery="FAILED",
    )
    old_answers = [
        item.source_answer
        for item in failed.case.unsent_advice
        if item.topic == "sponsor_support" and item.source_answer
    ]
    assert len(old_answers) == 1
    correction = "Correction: my sponsor is now my father, not my mother. My father will pay for my trip."

    result = dialogue.turn(
        correction,
        proposal(facts=[
            ("sponsor_relationship", "father", "my sponsor is now my father"),
            ("funding_source", "personal_sponsor", "My father will pay for my trip."),
        ]),
    )

    answer = result.body.casefold()
    assert result.case.profile.sponsor_relationship == "father"
    assert old_answers[0] not in result.body
    assert "mother's promise of support" not in answer
    assert "relationship to the mother" not in answer
    if "sponsor statement" in answer:
        assert "father" in answer


def test_rejected_guarantee_question_never_revives_an_old_failed_sponsor_template(tmp_path):
    dialogue = AdviceConversation(tmp_path)
    first_question = "What should my mother include in her sponsor statement?"
    failed = dialogue.turn(
        "My mother will pay for my trip. " + first_question,
        proposal(
            facts=[
                ("funding_source", "personal_sponsor", "My mother will pay for my trip."),
                ("sponsor_relationship", "mother", "my mother"),
            ],
            questions=[("sponsor_support", first_question)],
        ),
        delivery="FAILED",
    )
    old = next(
        item.source_answer for item in failed.case.unsent_advice
        if item.topic == "sponsor_support" and item.source_answer
    )
    guarantee = "Will this sponsor letter guarantee that my visa is approved?"

    result = dialogue.turn(
        guarantee,
        proposal(questions=[("sponsor_support", guarantee)]),
    )

    assert result.case.customer_question_topics == ["unsupported"]
    assert old not in result.body
    assert "mother's promise of support" not in result.body.casefold()
    assert any(term in result.body.casefold() for term in ("cannot", "can't", "guarantee"))


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Does my sponsor need a notarised letter?",
            ("does not set a universal rule", "would not add that cost by default"),
        ),
        (
            "Must the sponsor letter be witnessed by a solicitor?",
            ("does not set a universal rule", "matching financial and relationship evidence"),
        ),
        (
            "Can my sponsor submit only a letter with no bank evidence?",
            ("on its own", "accessible funds", "first action"),
        ),
        (
            "Does relationship evidence have to be a birth certificate?",
            ("not the one universal document", "genuine existing records", "first action"),
        ),
        (
            "Can my sponsor use cryptocurrency as evidence?",
            ("do not rely on it alone", "conventional records", "manual review"),
        ),
    ],
)
@pytest.mark.parametrize("model_topic", ["sponsor_support", "unsupported"])
def test_specific_sponsor_requirement_is_not_rescued_to_a_generic_yes_template(
    tmp_path, question, expected, model_topic,
):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))

    result = dialogue.turn(
        question,
        consultant._patch(questions=[(model_topic, question)]),
    )

    answer = result.body.casefold()
    assert result.case.customer_question_topics == ["unsupported"]
    assert sponsor_support_question(question) is False
    assert sponsor_verification_question(question) is True
    assert all(term in answer for term in expected)
    assert "the sponsor statement does not need legalistic wording" not in answer
    assert not re.search(r"(?:^|\n\n)yes[.,]", answer)
    assert result.body.count(SPONSOR_SOURCE) == 1
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_specific_sponsor_requirement_has_a_safe_deterministic_fallback_without_model_topic(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    question = "Can my sponsor submit only a letter with no bank evidence?"

    result = dialogue.turn(question, consultant._patch())

    answer = result.body.casefold()
    assert "on its own" in answer and "accessible funds" in answer and "first action" in answer
    assert "the sponsor statement does not need legalistic wording" not in answer
    assert result.body.count(SPONSOR_SOURCE) == 1
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_chinese_notarisation_question_gets_scoped_advice_not_a_generic_template(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    question = "资助信必须公证，或者找律师见证吗？"

    result = dialogue.turn(
        question,
        consultant._patch(questions=[("sponsor_support", question)]),
    )

    assert result.case.customer_question_topics == ["unsupported"]
    assert "没有规定所有资助说明都必须公证" in result.body
    assert "不会建议你默认花这笔费用" in result.body
    assert "资助说明不用写成法律文书" not in result.body
    assert result.body.count(SPONSOR_SOURCE) == 1


def test_general_sponsor_questions_keep_the_high_value_template_without_yes_preface():
    for question in (
        "What should my sponsor statement include?",
        "How can I show my relationship to my sponsor?",
    ):
        assert sponsor_support_question(question) is True
        assert sponsor_verification_question(question) is False
        answer = sponsor_support_answer(question, "en")
        assert answer.startswith("The aim is to connect")
        assert "exact costs covered" in answer
        assert "accessible funds" in answer
        assert "genuine existing records" in answer
        assert answer.endswith(SPONSOR_SOURCE)

    chinese = "资助信需要写什么？"
    assert sponsor_support_question(chinese) is True
    assert sponsor_verification_question(chinese) is False
    assert sponsor_support_answer(chinese, "zh").startswith("这部分要把")


@pytest.mark.parametrize(
    ("party", "funding_text"),
    [
        ("employer", "My employer will pay for the trip."),
        ("university", "My university will pay for the trip."),
    ],
)
@pytest.mark.parametrize("model_topic", ["sponsor_support", "unsupported", None])
def test_organisation_funding_uses_an_institutional_not_family_template(
    tmp_path, party, funding_text, model_topic,
):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(
        funding_text,
        consultant._patch(updates=[("funding_source", "employer_or_school", funding_text)]),
    )
    question = f"What should my {party}'s sponsorship letter include?"
    questions = [] if model_topic is None else [(model_topic, question)]

    result = dialogue.turn(question, consultant._patch(questions=questions))

    answer = result.body.casefold()
    assert party in answer
    assert all(term in answer for term in (
        "purpose of this uk trip", "exact costs", "pay directly", "contact person's role",
        "invitation", "first practical step",
    ))
    assert "birth record" not in answer and "household record" not in answer
    assert "dependants' costs" not in answer and "lawful uk status" not in answer
    assert SPONSOR_SOURCE in result.body


@pytest.mark.parametrize(
    ("language", "body", "updates", "expected", "old_deferral"),
    [
        (
            "zh",
            "我持中国护照，我会在香港递交申请。我在香港的大学读书，这次去伦敦参加学术会议。"
            "学校会直接支付机票和住宿。请帮我准备材料。",
            [
                ("nationality_country", "China", "我持中国护照"),
                ("application_country", "Hong Kong", "我会在香港递交申请"),
                ("occupation_status", "student", "我在香港的大学读书"),
                ("visit_purpose", "conference", "这次去伦敦参加学术会议"),
                ("funding_source", "employer_or_school", "学校会直接支付机票和住宿"),
            ],
            (
                "学校会承担机票、住宿，并直接支付",
                "正式抬头说明",
                "可核实的联系人",
                "主办方出具邀请函",
                "会议或活动名称",
                "邀请函解释为什么去英国",
            ),
            "谁承担费用的证明还需要另外结合",
        ),
        (
            "en",
            "I hold a Chinese passport and will apply in Hong Kong. I study at a university in Hong Kong "
            "and will attend an academic conference in London. My university will pay directly for flights "
            "and accommodation. Please help me prepare the documents.",
            [
                ("nationality_country", "China", "I hold a Chinese passport"),
                ("application_country", "Hong Kong", "will apply in Hong Kong"),
                ("occupation_status", "student", "I study at a university in Hong Kong"),
                ("visit_purpose", "conference", "will attend an academic conference in London"),
                (
                    "funding_source",
                    "employer_or_school",
                    "My university will pay directly for flights and accommodation",
                ),
            ],
            (
                "school or university will cover flights and accommodation and pay directly",
                "headed letter",
                "verifiable contact",
                "ask the organiser for an invitation",
                "event name, dates and location",
                "the invitation explains why you are visiting the UK",
            ),
            "funding evidence still needs to match whoever",
        ),
    ],
)
def test_first_conference_turn_carries_invitation_and_known_organisation_payment_into_captured_reply(
    tmp_path, language, body, updates, expected, old_deferral,
):
    dialogue = consultant.Conversation(tmp_path)

    result = dialogue.turn(body, consultant._patch(updates=updates))

    assert result.case.customer_language == language
    assert result.case.profile.visit_purpose == "conference"
    assert result.case.profile.funding_source == "employer_or_school"
    assert "conference_organisation_funding_preparation_v1" in result.case.guidance_events
    assert all(term in result.body for term in expected)
    assert old_deferral not in result.body
    assert SPONSOR_SOURCE in result.body
    assert DOCUMENTS_URL + "#attendees-of-business-related-events-or-conferences" in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    if language == "zh":
        assert "最先只做一件事" in result.body
        assert "同时请主办方" not in result.body
    else:
        assert "Your first action is one document" in result.body
        assert "request both at the same time" not in result.body
    assert len(dialogue.gmail.calls) == 1
    assert dialogue.gmail.calls[0]["body"] == result.body


def test_combined_overview_and_application_deduplicates_each_official_url(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("student"))
    overview = "Could you give me the complete list of documents and explain everything in one message?"
    application = "Where do I apply for the Standard Visitor visa?"

    result = dialogue.turn(
        f"{overview} {application}",
        consultant._patch(questions=[
            ("document_checklist", overview),
            ("application", application),
        ]),
    )

    urls = _urls(result.body)
    assert set(urls) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}
    assert all(urls.count(url) == 1 for url in set(urls))
    assert "Information to prepare for the online form" in result.body
    assert "Apply online" in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


@pytest.mark.parametrize(
    ("body", "purpose_question"),
    [
        ("Please tell me all the documents in one message.", "main reason for your visit"),
        ("请一次告诉我所有需要的材料。", "这次去英国主要是"),
    ],
)
def test_full_list_request_without_profile_gets_route_safe_value_before_one_question(
    tmp_path, body, purpose_question,
):
    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(questions=[("document_checklist", body)]),
    )

    assert ROUTE_CHECK_URL in result.body and APPLICATION_URL in result.body
    assert purpose_question.casefold() in result.body.casefold()
    assert result.case.question_plan == result.case.last_requested_fields == ["visit_purpose"]
    assert not result.case.profile.route_confirmed_standard_visitor
    # Before the route and circumstances are known, the useful common
    # categories must be labelled as conditional rather than presented as a
    # universal mandatory list.
    assert (
        "不是所有人一模一样的必交清单" in result.body
        or "not a universal mandatory checklist" in result.body
    )


@pytest.mark.parametrize(
    ("body", "questions", "expected"),
    [
        (
            "我想申请英国签证，需要什么材料，在哪里申请？",
            [("document_checklist", "需要什么材料"), ("application", "在哪里申请")],
            (
                "有效护照或旅行证件",
                "赴英目的和预计安排",
                "在职、在读或自雇",
                "可用资金和真实来源",
                "非英文或威尔士文",
                "护照国以外",
                "合法居留证明",
                "等关键情况确认后",
                "Apply now",
            ),
        ),
        (
            "I want to apply for a UK visa. What documents do I need and where do I apply?",
            [("document_checklist", "What documents do I need"), ("application", "where do I apply?")],
            (
                "valid passport or travel document",
                "purpose and intended arrangements",
                "employment, study or self-employment",
                "accessible funds and their genuine source",
                "not in English or Welsh",
                "outside your country of nationality",
                "evidence of lawful residence",
                "not a universal mandatory checklist",
                "Apply now",
            ),
        ),
    ],
    ids=["zh", "en"],
)
def test_combined_cold_start_materials_and_application_keeps_both_obligations_in_captured_gmail(
    tmp_path, body, questions, expected,
):
    dialogue = consultant.Conversation(tmp_path)

    result = dialogue.turn(body, consultant._patch(questions=questions))

    assert all(term.casefold() in result.body.casefold() for term in expected), result.body
    urls = _urls(result.body)
    assert set(urls) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}
    assert all(urls.count(url) == 1 for url in set(urls))
    assert len(reply_items(result.case)[1]) <= 1
    assert set(result.case.last_requested_fields) <= {
        "visit_purpose", "nationality_country", "application_country",
    }
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert all(claim.casefold() not in result.body.casefold() for claim in (
        "所有申请人都必须", "all applicants must", "guaranteed complete checklist",
    ))
    assert len(dialogue.gmail.calls) == 1
    assert dialogue.gmail.calls[0]["body"] == result.body


@pytest.mark.parametrize(
    ("body", "questions", "expected"),
    [
        (
            "请一次性告诉我英国签证的流程、材料、费用和审理时间。",
            [("application", "流程"), ("document_checklist", "材料"),
             ("fees", "费用"), ("timing", "审理时间")],
            ("Apply now", "有效护照或旅行证件", "在职、在读或自雇", "资金", "翻译", "合法居留", "£135", "3 周"),
        ),
        (
            "Please explain the UK visa process, documents, fee and timing all at once.",
            [("application", "process"), ("document_checklist", "documents"),
             ("fees", "fee"), ("timing", "timing")],
            ("Apply now", "valid passport or travel document", "employment, study or self-employment",
             "accessible funds", "translation", "lawful residence", "£135", "3 weeks"),
        ),
    ],
    ids=["zh", "en"],
)
def test_cold_start_process_materials_fee_and_timing_are_all_kept_in_one_captured_reply(
    tmp_path, body, questions, expected,
):
    dialogue = consultant.Conversation(tmp_path)

    result = dialogue.turn(body, consultant._patch(questions=questions))

    assert all(term.casefold() in result.body.casefold() for term in expected), result.body
    urls = _urls(result.body)
    assert set(urls) == {ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL}
    assert all(urls.count(url) == 1 for url in set(urls))
    assert len(reply_items(result.case)[1]) <= 1
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert len(dialogue.gmail.calls) == 1
    assert dialogue.gmail.calls[0]["body"] == result.body


@pytest.mark.parametrize(
    ("body", "questions"),
    [
        (
            "My friend wants a UK visitor visa. What documents does she need and where should she apply?",
            [("document_checklist", "What documents does she need"),
             ("application", "where should she apply?")],
        ),
        (
            "If I later need a UK visitor visa, what documents would I need and where would I apply?",
            [("document_checklist", "what documents would I need"),
             ("application", "where would I apply?")],
        ),
    ],
    ids=["third-party", "hypothetical"],
)
def test_combined_material_and_application_rescue_preserves_own_current_case_boundary(
    tmp_path, body, questions,
):
    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(questions=questions),
    )

    assert all(url not in result.body for url in (APPLICATION_URL, DOCUMENTS_URL))
    assert "conditional common categories" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert result.case.profile.route_confirmed_standard_visitor is not True


def test_named_nonvisitor_combined_material_and_application_request_gets_only_route_boundary(tmp_path):
    body = "I need a Student visa. What documents do I need and where should I apply?"
    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(questions=[
            ("document_checklist", "What documents do I need"),
            ("application", "where should I apply?"),
        ]),
    )

    assert ROUTE_CHECK_URL in result.body
    assert APPLICATION_URL not in result.body and DOCUMENTS_URL not in result.body
    assert all(term not in result.body for term in ("£135", "3 weeks", "conditional common categories"))
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_expired_cold_start_combined_request_withholds_stale_categories_and_links(tmp_path, monkeypatch):
    monkeypatch.setattr(consultant, "TODAY", REVIEW_AFTER + timedelta(days=1))
    body = "What UK visa documents do I need and where do I apply?"

    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(questions=[
            ("document_checklist", "What UK visa documents do I need"),
            ("application", "where do I apply?"),
        ]),
    )

    assert "recheck" in result.body.casefold()
    assert "https://" not in result.body
    assert all(term not in result.body for term in (
        "valid passport or travel document", "accessible funds", "lawful residence",
    ))
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_same_turn_tourism_fact_does_not_drop_materials_from_combined_cold_email(tmp_path):
    body = "你好，我想办英国旅游签证。都要准备什么材料，在哪里申请，费用多少，大概要多久？"
    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(
            updates=[("visit_purpose", "tourism", "英国旅游")],
            questions=[
                ("document_checklist", "都要准备什么材料"),
                ("application", "在哪里申请"),
                ("fees", "费用多少"),
                ("timing", "大概要多久？"),
            ],
        ),
    )

    assert all(term in result.body for term in (
        "有效护照或旅行证件", "预计旅游行程", "在职、在读或自雇", "可用资金", "翻译", "合法居留",
        "Apply now", "£135", "3 周",
    )), result.body
    assert all(url in result.body for url in (ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL))
    assert all(_urls(result.body).count(url) == 1 for url in (ROUTE_CHECK_URL, APPLICATION_URL, DOCUMENTS_URL))
    assert len(reply_items(result.case)[1]) <= 1


def test_known_chinese_passport_tourism_visa_or_eta_question_includes_route_and_application_start(tmp_path):
    body = "我持中国护照，去英国旅游。我需要签证还是 ETA，从哪里开始申请？"
    dialogue = consultant.Conversation(tmp_path)
    result = dialogue.turn(
        body,
        consultant._patch(
            updates=[
                ("nationality_country", "China", "中国护照"),
                ("visit_purpose", "tourism", "去英国旅游"),
            ],
            questions=[("application", "从哪里开始申请？")],
        ),
    )

    assert "是否需要 ETA 还是签证" in result.body
    assert "Apply now" in result.body
    assert _urls(result.body).count(ROUTE_CHECK_URL) == 1
    assert _urls(result.body).count(APPLICATION_URL) == 1
    assert "可能不是普通 Standard Visitor" not in result.body
    assert result.case.profile.nationality_country == "China"
    assert result.case.profile.visit_purpose == "tourism"
    assert result.case.profile.route_confirmed_standard_visitor is not True
    assert len(dialogue.gmail.calls) == 1 and dialogue.gmail.calls[0]["body"] == result.body


def test_third_party_full_list_request_is_not_rescued_as_this_applicants_intake(tmp_path):
    body = "Please tell my friend all the documents for her visa application in one message."
    result = consultant.Conversation(tmp_path).turn(
        body,
        consultant._patch(questions=[("document_checklist", body)]),
    )

    assert APPLICATION_URL not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_next_step_continues_case_specific_evidence_before_switching_to_form_intake(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    first = dialogue.turn(*consultant._context("student"))
    assert "在读证明" in first.body and "资金" in first.body

    question = "下一步我该准备什么？"
    result = dialogue.turn(
        question,
        consultant._patch(questions=[("next_step", question)]),
    )

    assert "预计行程" in result.body and "护照上的姓名" not in result.body
    assert DOCUMENTS_URL not in result.body
    assert "\n".join(call["body"] for call in dialogue.gmail.calls).count(DOCUMENTS_URL) == 1
    assert result.case.question_plan == result.case.last_requested_fields == []

    residence = dialogue.turn(
        question,
        consultant._patch(questions=[("next_step", question)]),
    )
    assert "合法居留" in residence.body and "护照上的姓名" not in residence.body
    assert residence.case.question_plan == residence.case.last_requested_fields == []


def test_repeated_next_step_does_not_repeat_an_unanswered_identity_question(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("student"))
    question = "下一步我该准备什么？"
    patch = consultant._patch(questions=[("next_step", question)])
    dialogue.turn(question, patch)  # useful itinerary action
    dialogue.turn(question, patch)  # useful lawful-residence action
    asked = dialogue.turn(question, patch)
    assert "护照上的姓名" in asked.body
    question_ledger = {
        field: list(event_ids) for field, event_ids in asked.case.question_event_ids.items()
    }

    repeated = dialogue.turn("那再下一步呢？", consultant._patch(
        questions=[("next_step", "那再下一步呢？")],
    ))

    assert "护照上的姓名" not in repeated.body
    assert all(term in repeated.body for term in ("护照资料页", "清晰、完整的 PDF", "页面四边不要裁掉"))
    assert APPLICATION_URL not in repeated.body
    assert "\n".join(call["body"] for call in dialogue.gmail.calls).count(APPLICATION_URL) == 1
    assert "上一封邮件里问的护照姓名还没有收到" in repeated.body
    assert "不重复提问" in repeated.body
    assert reply_items(repeated.case)[1] == []
    assert repeated.case.question_plan == repeated.case.last_requested_fields == []
    assert repeated.case.pending_question_fields == ["full_name"]
    assert repeated.case.question_event_ids == question_ledger

    again_text = "那之后我还可以先准备什么？"
    again = dialogue.turn(
        again_text,
        consultant._patch(questions=[("next_step", again_text)]),
    )
    assert "护照上的姓名" not in again.body
    assert "居留身份文件" in again.body and "不重复提问" in again.body
    assert reply_items(again.case)[1] == []
    assert again.case.question_plan == again.case.last_requested_fields == []
    assert again.case.pending_question_fields == ["full_name"]
    assert again.case.question_event_ids == question_ledger


def test_repeated_english_next_step_keeps_question_ledger_and_sends_useful_captured_reply(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    updates = [
        ("nationality_country", "China", "I hold a Chinese passport."),
        ("application_country", "Hong Kong", "I will apply in Hong Kong."),
        ("visit_purpose", "tourism", "I am planning a holiday in the UK."),
        ("occupation_status", "student", "I am currently a university student."),
        ("funding_source", "self", "I will pay for the trip from my own savings."),
    ]
    date_deferral = "My travel dates are not decided yet."
    initial_patch = consultant._patch(updates=updates)
    initial_patch.question_deferrals = [
        QuestionDeferral(field=field, source_excerpt=date_deferral, confidence=1)
        for field in ("planned_arrival_date", "planned_departure_date")
    ]
    dialogue.turn(
        " ".join([*(text for _, _, text in updates), date_deferral]),
        initial_patch,
    )
    question = "What should I prepare next for my UK visitor application?"
    patch = consultant._patch(questions=[("next_step", question)])
    asked = None
    for _ in range(6):
        current = dialogue.turn(question, patch)
        if current.case.last_requested_fields == ["full_name"]:
            asked = current
            break
    assert asked is not None
    assert QUESTION_TEXT_EN["full_name"] in asked.body
    question_ledger = {
        field: list(event_ids) for field, event_ids in asked.case.question_event_ids.items()
    }

    followup = "I still need to know what I can work on next in the meantime."
    repeated = dialogue.turn(
        followup,
        consultant._patch(questions=[("next_step", followup)]),
    )

    assert QUESTION_TEXT_EN["full_name"] not in repeated.body
    assert all(term in repeated.body for term in (
        "scan the passport details page", "clear, complete PDF", "none of the page edges are cropped",
    ))
    assert APPLICATION_URL not in repeated.body
    assert "\n".join(call["body"] for call in dialogue.gmail.calls).count(APPLICATION_URL) == 1
    assert "I still need the passport name asked for in my previous email" in repeated.body
    assert "will not repeat the question" in repeated.body
    assert reply_items(repeated.case)[1] == []
    assert repeated.case.question_plan == repeated.case.last_requested_fields == []
    assert repeated.case.pending_question_fields == ["full_name"]
    assert repeated.case.question_event_ids == question_ledger
    assert dialogue.gmail.calls[-1]["body"] == repeated.body
    assert QUESTION_TEXT_ZH["full_name"] not in repeated.body

    again_text = "What else should I work on next while that remains open?"
    again = dialogue.turn(
        again_text,
        consultant._patch(questions=[("next_step", again_text)]),
    )
    assert QUESTION_TEXT_EN["full_name"] not in again.body
    assert "document showing your residence status" in again.body
    assert "will not repeat the question" in again.body
    assert reply_items(again.case)[1] == []
    assert again.case.question_plan == again.case.last_requested_fields == []
    assert again.case.pending_question_fields == ["full_name"]
    assert again.case.question_event_ids == question_ledger
    assert dialogue.gmail.calls[-1]["body"] == again.body


def test_complete_overview_with_next_step_has_one_practical_action_not_an_intake_tail(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn(*consultant._context("parents"))
    request = "请按我的情况把完整材料清单和下一步一次说清楚。"

    result = dialogue.turn(
        request,
        consultant._patch(questions=[
            ("document_checklist", request),
            ("next_step", "下一步先做什么？"),
        ]),
    )

    assert result.body.count("最先做的一步：") == 1
    assert "护照上的姓名" not in result.body
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_cold_start_intake_adds_consultant_value_without_turning_into_a_questionnaire(tmp_path):
    dialogue = consultant.Conversation(tmp_path)
    dialogue.turn("我想办英国签证，需要什么？", consultant._patch())

    purpose = "这次是旅游。"
    purpose_result = dialogue.turn(
        purpose,
        consultant._patch(updates=[("visit_purpose", "tourism", purpose)]),
    )
    assert all(term in purpose_result.body for term in ("预计行程", "不需要", "机票", "酒店"))
    assert DOCUMENTS_URL not in purpose_result.body
    assert "\n".join(call["body"] for call in dialogue.gmail.calls).count(DOCUMENTS_URL) == 1
    assert purpose_result.case.question_plan == purpose_result.case.last_requested_fields == ["nationality_country"]

    passport = "我持中国护照。"
    passport_result = dialogue.turn(
        passport,
        consultant._patch(updates=[("nationality_country", "China", passport)]),
    )
    assert "递交地点会影响" in passport_result.body
    assert "合法居留证明" in passport_result.body
    assert passport_result.case.question_plan == passport_result.case.last_requested_fields == ["application_country"]


@pytest.mark.parametrize(
    ("question", "model_topic"),
    [
        ("国际运动员签证多久？", "timing"),
        ("海外家政工人签证多久？", "timing"),
        ("英国祖籍签证申请费是多少？", "fees"),
        ("高级或专业工人签证费是多少？", "fees"),
        ("英国扩展工人签证费是多少？", "fees"),
        ("青年流动计划签证多久？", "timing"),
        ("全球商务流动签证费是多少？", "fees"),
        ("宗教部长签证在哪里申请？", "application"),
        ("海外企业代表签证多久？", "timing"),
        ("國際運動員簽證多久？", "timing"),
        ("全球商務流動簽證費是多少？", "fees"),
        ("宗教部長簽證在哪裡申請？", "application"),
    ],
)
def test_named_chinese_nonvisitor_routes_never_receive_standard_visitor_facts(
    tmp_path, question, model_topic,
):
    result = consultant.Conversation(tmp_path).turn(
        question,
        consultant._patch(questions=[(model_topic, question)]),
    )

    assert ROUTE_CHECK_SOURCE in result.body
    assert all(term not in result.body for term in (
        "£135", "3 个月", "3 周", APPLICATION_URL,
    ))
    assert result.case.question_plan == result.case.last_requested_fields == []


def test_negated_chinese_nonvisitor_route_keeps_the_explicit_visitor_fee_question(tmp_path):
    question = "我不是申请国际运动员签证，只申请普通访客签证，费用多少？"
    result = consultant.Conversation(tmp_path).turn(
        question,
        consultant._patch(questions=[("fees", question)]),
    )

    assert "£135" in result.body and APPLICATION_URL in result.body
    assert ROUTE_CHECK_SOURCE not in result.body
