"""Model-proposed advice topics are bounded, current-message intents, not facts."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, CustomerQuestion, FactUpdate
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    PROCESSING_SOURCE,
    SOURCE,
    grounded_customer_answers,
)


def inbound(body: str) -> InboundEvent:
    return InboundEvent(
        id="semantic-question", channel="gmail", external_thread_id="fictional-thread",
        sender="fictional@example.test", subject="英国旅行咨询", body=body,
        received_at=datetime(2026, 9, 4, tzinfo=UTC),
    )


def question(topic: str, excerpt: str, confidence: float = 0.99) -> CustomerQuestion:
    return CustomerQuestion.model_validate({
        "topic": topic, "source_excerpt": excerpt, "confidence": confidence,
    })


def proposed(*questions: CustomerQuestion) -> CasePatch:
    return CasePatch(updates=[], ambiguities=[], customer_questions=list(questions))


def test_old_extraction_payload_defaults_to_no_customer_questions() -> None:
    assert CasePatch(updates=[], ambiguities=[]).customer_questions == []


@pytest.mark.parametrize("topic", [
    "application", "timing", "translation", "booking", "fees", "bank_period",
    "document_checklist", "unsupported",
])
def test_only_bounded_advice_topic_names_are_part_of_schema(topic: str) -> None:
    item = question(topic, "请解释一下这个问题。")
    assert item.topic == topic
    assert set(item.model_dump()) == {"topic", "source_excerpt", "confidence"}


@pytest.mark.parametrize("topic", [
    "approval", "confirm_profile", "ready", "send_pack", "visa_approved",
    "https://unreviewed.example.test/", "legal_advice",
])
def test_unreviewed_topic_names_are_not_schema_extensions(topic: str) -> None:
    with pytest.raises(ValidationError):
        question(topic, "请解释一下这个问题。")


@pytest.mark.parametrize(("extra", "value"), [
    ("answer", "You are guaranteed a visa."),
    ("url", "https://unreviewed.example.test/pay"),
    ("fee", 1),
    ("status", "READY_FOR_HUMAN_REVIEW"),
    ("route_confirmed_standard_visitor", True),
    ("profile_confirmed", True),
    ("final_summary_confirmed", True),
])
def test_topic_cannot_smuggle_prose_links_prices_or_state(extra: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        CustomerQuestion.model_validate({
            "topic": "fees", "source_excerpt": "大概要花多少？", "confidence": 0.99,
            extra: value,
        })


@pytest.mark.parametrize(("excerpt", "confidence"), [
    ("", 0.99), ("x" * 321, 0.99), ("问一下", -0.01), ("问一下", 1.01),
])
def test_question_evidence_and_confidence_have_schema_bounds(
    excerpt: str, confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        question("unsupported", excerpt, confidence)


@pytest.mark.parametrize(("text", "topic"), [
    ("我还没弄明白应该从哪个页面开始这件事。", "application"),
    ("等到结果出来一般得留出多大空档？", "timing"),
    ("这些纸都是母语写的，对面看得懂吗？", "translation"),
    ("出结果前就把交通和住处的钱付掉有必要吗？", "booking"),
    ("政府那边这一笔大概要收多少？", "fees"),
    ("账户的进出记录得往前追溯到什么程度？", "bank_period"),
    ("我周末想先整理一批东西，手头该找些什么？", "document_checklist"),
    ("以前的一次拒签到底会怎么影响我？", "unsupported"),
])
def test_grounded_semantic_question_does_not_require_a_keyword_match(text: str, topic: str) -> None:
    checked = validate_case_patch(inbound(text), proposed(question(topic, text)))
    assert [item.topic for item in checked.customer_questions] == [topic]
    assert checked.customer_questions[0].source_excerpt == text
    assert checked.updates == [] and checked.ambiguities == []
    assert not checked.requires_human_review


@pytest.mark.parametrize(("body", "excerpt", "confidence"), [
    ("我在读书。", "政府那边这一笔大概要收多少？", 0.99),
    ("政府那边这一笔大概要收多少？", "政府那边这一笔大概要收多少？", 0.79),
    ("收到\n> 政府那边这一笔大概要收多少？", "政府那边这一笔大概要收多少？", 0.99),
    ("收到\nOn Friday the adviser wrote:\n政府那边这一笔大概要收多少？",
     "政府那边这一笔大概要收多少？", 0.99),
    ("朋友问“政府那边这一笔大概要收多少？”，不是我问的。",
     "政府那边这一笔大概要收多少？", 0.99),
    ("不用告诉我政府那边这一笔大概要收多少。", "政府那边这一笔大概要收多少", 0.99),
    ("Don't tell me what the government charges for this.",
     "what the government charges for this", 0.99),
    ("Don't explain the visa fee.", "visa fee", 0.99),
    ("我在准备资料。", " ", 0.99),
])
def test_bad_questions_are_dropped_without_discarding_a_valid_fact_or_escalating(
    body: str, excerpt: str, confidence: float,
) -> None:
    # The latest explicit birthday is valid even when a model also hallucinates intent.
    text = "我的出生日期是1994.6.12。\n" + body
    patch = proposed(question("fees", excerpt, confidence))
    patch.updates = [FactUpdate(
        field="date_of_birth", value="1994.6.12", source_excerpt="1994.6.12", confidence=0.99,
    )]
    checked = validate_case_patch(inbound(text), patch)
    assert checked.customer_questions == []
    assert len(checked.updates) == 1 and checked.updates[0].value == "1994-06-12"
    assert checked.ambiguities == [] and not checked.requires_human_review


def test_minimum_question_confidence_is_inclusive_and_duplicate_topics_collapse() -> None:
    text = "政府那边这一笔大概要收多少？这笔官方收费能讲讲吗？"
    checked = validate_case_patch(inbound(text), proposed(
        question("fees", "政府那边这一笔大概要收多少？", 0.8),
        question("fees", "这笔官方收费能讲讲吗？", 0.99),
    ))
    assert len(checked.customer_questions) == 1
    assert checked.customer_questions[0].topic == "fees"
    assert not checked.requires_human_review


def test_declining_one_topic_does_not_discard_a_different_current_request() -> None:
    text = "不用告诉我政府那边这一笔大概要收多少。我周末想整理一批东西，手头该找些什么？"
    checked = validate_case_patch(inbound(text), proposed(
        question("fees", "政府那边这一笔大概要收多少"),
        question("document_checklist", "手头该找些什么？"),
    ))
    assert [item.topic for item in checked.customer_questions] == ["document_checklist"]
    assert not checked.requires_human_review


def test_latest_request_remains_valid_when_old_history_contains_the_same_words() -> None:
    text = "政府那边这一笔大概要收多少？"
    checked = validate_case_patch(
        inbound(text + "\n> " + text), proposed(question("fees", text)),
    )
    assert [item.topic for item in checked.customer_questions] == ["fees"]


def test_malformed_top_level_answer_is_rejected_instead_of_becoming_a_patch() -> None:
    with pytest.raises(ValidationError):
        CasePatch.model_validate({
            "updates": [], "ambiguities": [], "customer_questions": [],
            "customer_answers": ["Your visa will be approved."],
        })


def test_combined_excerpt_cannot_bridge_across_a_declined_clause() -> None:
    text = "不用告诉我签证费多少，申请入口是哪个？"
    checked = validate_case_patch(inbound(text), proposed(
        question("fees", text),
        question("application", "申请入口是哪个？"),
    ))
    assert [item.topic for item in checked.customer_questions] == ["application"]
    assert not checked.requires_human_review


@pytest.mark.parametrize("separator", ["  ", "\t", "\n"])
def test_unsupported_suppression_uses_the_same_whitespace_rules_as_grounding(separator: str) -> None:
    body = f"What does{separator}a ten-year visitor visa cost?"
    answers = grounded_customer_answers(body, "en", date(2026, 9, 4), semantic_questions=[
        question("unsupported", "What does a ten-year visitor visa cost?"),
    ])
    assert len(answers) == 1
    assert "verified guidance" in answers[0]
    assert "£135" not in answers[0] and "6-month" not in answers[0]


@pytest.mark.parametrize("fee_excerpt", ["申请费是多少？", "十年访问签证的申请费是多少？"])
def test_unknown_scope_wins_over_a_conflicting_narrower_model_answer(fee_excerpt: str) -> None:
    body = "十年访问签证的申请费是多少？"
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4), semantic_questions=[
        question("unsupported", "十年访问签证"), question("fees", fee_excerpt),
    ])
    assert len(answers) == 1 and "另行核实" in answers[0]
    assert "£135" not in answers[0]


def test_unknown_question_does_not_suppress_a_separate_supported_question() -> None:
    body = "十年访问签证的申请费是多少？普通六个月申请费又是多少？"
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4), semantic_questions=[
        question("unsupported", "十年访问签证的申请费是多少？"),
        question("fees", "普通六个月申请费又是多少？"),
    ])
    assert len(answers) == 2
    assert "£135" in "\n".join(answers) and "另行核实" in "\n".join(answers)


@pytest.mark.parametrize("include_unsupported", [False, True])
def test_suppressing_duplicate_unknown_notice_does_not_reenable_canned_answer(
    include_unsupported: bool,
) -> None:
    text = "What does  a ten-year visitor visa cost?"
    answers = grounded_customer_answers(
        text, "en", date(2026, 9, 4), include_unsupported=include_unsupported,
        semantic_questions=[question("unsupported", "What does a ten-year visitor visa cost?")],
    )
    assert len(answers) == int(include_unsupported)
    assert "£135" not in "\n".join(answers)


@pytest.mark.parametrize("declined", [
    "不用回答申请费多少。", "我没问签证费多少。", "不是问申请费多少钱。",
    "别回答签证费多少。", "I did not ask what the visa fee is.",
    "Don't answer how much the visa application costs.",
])
def test_keyword_fallback_cannot_resurrect_a_declined_topic(declined: str) -> None:
    active = "中文材料需要翻译吗？"
    body = declined + "\n" + active
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4), semantic_questions=[
        question("translation", active),
    ])
    assert len(answers) == 1 and SOURCE in answers[0]
    assert "完整翻译" in answers[0] and "£135" not in answers[0]


@pytest.mark.parametrize("route", [
    "不是学生签证", "并非学生签证", "我不办理学生签证",
    "It is not a student visa", "I am not applying for a student visa",
])
def test_explicitly_negated_route_does_not_block_an_ordinary_visitor_answer(route: str) -> None:
    text = "政府那边这一笔要多少钱？"
    body = route + "。我只是去旅游。" + text
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4), semantic_questions=[
        question("fees", text),
    ])
    assert len(answers) == 1 and "£135" in answers[0]
    assert "需要先按对应路线核实" not in answers[0]


@pytest.mark.parametrize("route", [
    "不知道是不是学生签证", "我不确定学生签证是否合适", "如果不是学生签证",
    "不是学生签证吗？", "不是学生签证，而是工作签证",
    "I am not sure whether it is a student visa", "If it is not a student visa",
])
def test_uncertain_hypothetical_or_other_affirmative_route_is_not_erased(route: str) -> None:
    text = "政府那边这一笔要多少钱？"
    answers = grounded_customer_answers(route + "。" + text, "zh", date(2026, 9, 4), semantic_questions=[
        question("fees", text),
    ])
    assert len(answers) == 1 and "路线" in answers[0]
    assert "£135" not in answers[0]


@pytest.mark.parametrize("language", ["zh", "en"])
def test_four_topics_keep_unknown_notice_and_disclose_which_answer_is_deferred(language: str) -> None:
    questions = [
        question("application", "官网怎么申请？"), question("timing", "多久有结果？"),
        question("translation", "需要翻译吗？"), question("unsupported", "过去有拒签怎么办？"),
    ]
    body = "".join(item.source_excerpt for item in questions)
    answers = grounded_customer_answers(body, language, date(2026, 9, 4), semantic_questions=questions)
    assert len(answers) == 3
    text = "\n".join(answers)
    assert ("另行核实" in text and "还没有展开" in text) if language == "zh" else (
        "separate check" in text and "I have not covered" in text
    )
    assert "£135" not in text


def test_four_supported_answers_disclose_overflow_without_a_fake_unknown_claim() -> None:
    body = "必须先买机票吗？怎么申请签证？签证多久出结果？中文材料需要翻译吗？"
    answers = grounded_customer_answers(body, "zh", date(2026, 9, 4))
    assert len(answers) == 3 and "还没有展开" in answers[-1]
    assert "没有核验过的依据" not in "\n".join(answers)


@pytest.mark.parametrize(("text", "language"), [
    ("普通访问签证一般多久有结果？", "zh"),
    ("How long does a visitor visa decision take?", "en"),
])
def test_decision_estimate_is_not_presented_as_passport_return_time(text: str, language: str) -> None:
    answers = grounded_customer_answers(text, language, date(2026, 9, 4))
    assert len(answers) == 1
    assert APPLICATION_SOURCE in answers[0] and PROCESSING_SOURCE not in answers[0]
    assert ("不是护照返还日期" in answers[0]) if language == "zh" else (
        "not a passport-return date" in answers[0]
    )


@pytest.mark.parametrize(("text", "language"), [
    ("我的护照大概什么时候拿回来？", "zh"),
    ("When will I get my passport back?", "en"),
])
def test_passport_timing_answer_covers_same_day_and_actually_retained_documents(
    text: str, language: str,
) -> None:
    answers = grounded_customer_answers(text, language, date(2026, 9, 4), semantic_questions=[
        question("timing", text),
    ])
    assert len(answers) == 1 and PROCESSING_SOURCE in answers[0] and APPLICATION_SOURCE in answers[0]
    if language == "zh":
        assert "预约当天退回" in answers[0] and "如果你实际把护照留在" in answers[0]
        assert "等收到联系后" in answers[0] and "不能用通常 3 周" in answers[0]
    else:
        assert "appointment day" in answers[0] and "If you actually left it" in answers[0]
        assert "wait until you are contacted" in answers[0] and "cannot establish" in answers[0]


@pytest.mark.parametrize(("text", "language"), [
    ("朋友帮我翻译，可以用吗？", "zh"), ("我自己翻译可以吗？", "zh"),
    ("Can a friend translate my documents?", "en"),
    ("Can I translate these documents myself?", "en"),
])
def test_translation_answer_does_not_invent_translator_eligibility(text: str, language: str) -> None:
    answers = grounded_customer_answers(text, language, date(2026, 9, 4), semantic_questions=[
        question("translation", text),
    ])
    assert len(answers) == 1 and SOURCE in answers[0]
    if language == "zh":
        assert "实际完整译件" in answers[0] and "可核验性" in answers[0]
        assert "不能保证" in answers[0]
        assert "朋友不能翻译" not in answers[0] and "自己翻译就可以" not in answers[0]
    else:
        assert "actual full translation" in answers[0] and "independently verified" in answers[0]
        assert "cannot guarantee acceptance" in answers[0]


@pytest.mark.parametrize(("topic", "text"), [
    ("timing", "When will I get my passport back?"),
    ("translation", "Can I translate it myself?"),
])
def test_contextual_details_share_source_expiry_gate(topic: str, text: str) -> None:
    answers = grounded_customer_answers(text, "en", date(2026, 10, 5), semantic_questions=[
        question(topic, text),
    ])
    assert len(answers) == 1 and "recheck" in answers[0]
    assert "appointment day" not in answers[0] and "actual full translation" not in answers[0]
    assert PROCESSING_SOURCE not in answers[0]


def test_student_route_does_not_get_standard_visitor_passport_return_details() -> None:
    text = "For a student visa, when will I get my passport back?"
    answers = grounded_customer_answers(text, "en", date(2026, 9, 4), semantic_questions=[
        question("timing", text),
    ])
    assert len(answers) == 1 and "route" in answers[0]
    assert PROCESSING_SOURCE not in answers[0] and "appointment day" not in answers[0]
