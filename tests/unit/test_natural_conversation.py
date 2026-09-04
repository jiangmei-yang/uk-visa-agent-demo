from datetime import UTC, date, datetime

import pytest

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.guarded import deterministic_fallback_message, validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.workflow.conversation import (
    clear_natural_confirmation,
    confirmation_message,
    latest_reply_text,
    next_fact_questions,
)


@pytest.mark.parametrize(
    "text",
    [
        "资料都正确，可以继续。",
        "信息没问题，麻烦整理。",
        "确认无误。",
        "Everything is correct, please proceed.",
        "All the details are accurate.",
        "I confirm the summary is correct.",
    ],
)
def test_clear_assent_in_ordinary_words(text: str) -> None:
    assert clear_natural_confirmation(text)


@pytest.mark.parametrize(
    "text",
    [
        "收到",
        "好的",
        "Thanks",
        "我还没看，稍等",
        "资料都正确吗？",
        "资料都正确，但是日期要修改",
        "如果没问题，可以继续",
        "Not everything is correct.",
        "Everything is correct, but change the dates.",
        "收到\n\nOn Friday, Visa wrote:\n资料都正确，可以继续。",
        "> Everything is correct, please proceed.",
    ],
)
def test_receipt_questions_negation_conditions_and_quotes_are_not_consent(text: str) -> None:
    assert not clear_natural_confirmation(text)


def test_old_gmail_reply_is_not_a_new_fact() -> None:
    assert (
        latest_reply_text("New date is 11 October.\n\nOn Fri, Visa wrote:\nDate is 10 October.")
        == "New date is 11 October."
    )


@pytest.mark.parametrize("value", ["mother", "My mother", "母亲", "我的妈妈", "employer"])
def test_sponsor_relationship_cannot_satisfy_personal_name(value: str) -> None:
    event = InboundEvent(
        id="e", external_thread_id="t", sender="a@example.test", subject="Visa",
        body=value, received_at=datetime.now(UTC),
    )
    result = validate_case_patch(event, CasePatch(updates=[FactUpdate(
        field="sponsor_name", value=value, source_excerpt=value, confidence=1,
    )], ambiguities=[]))
    assert result.updates == []
    assert not result.requires_human_review


def test_actual_sponsor_name_remains_accepted() -> None:
    event = InboundEvent(
        id="e", external_thread_id="t", sender="a@example.test", subject="Visa",
        body="My mother is Mei Chen.", received_at=datetime.now(UTC),
    )
    result = validate_case_patch(event, CasePatch(updates=[FactUpdate(
        field="sponsor_name", value="Mei Chen", source_excerpt="My mother is Mei Chen.",
        confidence=1,
    )], ambiguities=[]))
    assert result.updates[0].value == "Mei Chen"


def test_chinese_first_turn_asks_only_next_three_questions_without_internal_codes() -> None:
    case = Case(
        id="c",
        external_thread_id="t",
        applicant_contact="a@example.test",
        policy_version="v",
        customer_language="zh",
    )
    case.profile.visit_purpose = "tourism"
    case.profile.planned_arrival_date = date(2026, 11, 10)
    message = deterministic_fallback_message(case, "blocked")
    assert len(next_fact_questions(case)) == 3
    assert message.count("\n- ") == 3
    assert "收到，我再了解一下你的情况" in message
    assert "planned_arrival_date" not in message
    assert "Date Of Birth" not in message
    assert "测试" not in message
    assert "PROFILE CONFIRMED" not in message


def test_general_enquiry_reads_like_a_reply_not_an_internal_process_notice() -> None:
    case = Case(id="c", external_thread_id="t", applicant_contact="a@example.test",
                policy_version="v", customer_language="zh")
    message = deterministic_fallback_message(case, "blocked")
    assert "具体要准备哪些材料" in message
    assert "出行目的和申请地点" in message
    assert "护照" in message and "打算从哪里申请" in message
    assert "有其他安排" in message  # do not assume the user wants a visitor route
    assert "\n- " not in message
    assert "材料包" not in message
    assert "可以先聊" not in message
    assert "不会交付" not in message
    assert len(message) < 180


def test_chinese_summary_is_readable_and_does_not_demand_a_magic_phrase() -> None:
    case = Case(
        id="c",
        external_thread_id="t",
        applicant_contact="a@example.test",
        policy_version="v",
        customer_language="zh",
    )
    case.profile.full_name = "林晓"
    case.profile.funding_source = "employer_or_school"
    text = confirmation_message(case)
    assert "护照上的姓名：林晓" in text
    assert "雇主或学校" in text
    assert "employer_or_school" not in text
    assert "SHA-256" not in text
    assert "I CONFIRM" not in text


def test_passport_workplace_and_vague_month_do_not_become_other_precise_facts() -> None:
    event = InboundEvent(
        id="e",
        external_thread_id="t",
        sender="a@example.test",
        subject="英国旅游签证材料咨询",
        body="我持中国护照，在深圳上班，想11月去玩。",
        received_at=datetime.now(UTC),
    )
    patch = CasePatch(
        updates=[
            FactUpdate(
                field="application_country", value="China", source_excerpt="中国护照", confidence=1
            ),
            FactUpdate(
                field="current_address", value="深圳", source_excerpt="在深圳上班", confidence=1
            ),
            FactUpdate(
                field="planned_arrival_date",
                value="2026-11-01",
                source_excerpt="11月",
                confidence=1,
            ),
        ],
        ambiguities=[],
    )
    result = validate_case_patch(event, patch)
    assert result.updates == []
    assert not result.requires_human_review


def test_short_answer_uses_the_previous_unambiguous_question() -> None:
    event = InboundEvent(
        id="e",
        external_thread_id="t",
        sender="a@example.test",
        subject="Re: enquiry",
        body="China",
        requested_fields=["application_country"],
        received_at=datetime.now(UTC),
    )
    patch = CasePatch(
        updates=[
            FactUpdate(
                field="application_country", value="China", source_excerpt="China", confidence=1
            )
        ],
        ambiguities=[],
    )
    assert validate_case_patch(event, patch).updates == patch.updates


def test_shared_year_expands_to_a_real_sentence_not_current_year() -> None:
    event = InboundEvent(
        id="e",
        external_thread_id="t",
        sender="a@example.test",
        subject="dates",
        body="计划 2026 年 11 月 9 日到英国，11 月 11\n日离开。",
        received_at=datetime.now(UTC),
    )
    patch = CasePatch(
        updates=[
            FactUpdate(
                field="planned_departure_date",
                value="2026-11-11",
                source_excerpt="11 月 11\n日离开",
                confidence=1,
            )
        ],
        ambiguities=[],
    )
    checked = validate_case_patch(event, patch)
    assert not checked.requires_human_review
    assert "2026 年" in checked.updates[0].source_excerpt
    assert checked.updates[0].source_excerpt in event.body
    patch.updates[0].value = "2027-11-11"
    assert validate_case_patch(event, patch).updates == []


def test_study_location_does_not_establish_nationality() -> None:
    event = InboundEvent(
        id="e",
        external_thread_id="t",
        sender="a@example.test",
        subject="enquiry",
        body="在香港读大学",
        received_at=datetime.now(UTC),
    )
    patch = CasePatch(
        updates=[
            FactUpdate(
                field="nationality_country", value="China", source_excerpt=event.body, confidence=1
            )
        ],
        ambiguities=[],
    )
    checked = validate_case_patch(event, patch)
    assert checked.updates == []
    assert not checked.requires_human_review


def test_reply_acknowledges_correction_instead_of_restarting_onboarding() -> None:
    case = Case(
        id="c",
        external_thread_id="t",
        applicant_contact="a@example.test",
        policy_version="v",
        customer_language="zh",
    )
    case.latest_changes = {"planned_departure_date": "2026-11-13"}
    message = deterministic_fallback_message(case, "blocked")
    assert "已按你说的改为" in message
    assert "2026-11-13" in message
    assert "可以先聊" not in message
