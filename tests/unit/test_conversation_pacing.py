from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import (
    clear_natural_confirmation,
    next_fact_questions,
    update_deferred_questions,
    waiting_acknowledgement,
)
from visa_agent.workflow.service import WorkflowService


def example() -> Case:
    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test",
                policy_version="v", customer_language="zh")
    case.profile.visit_purpose = "tourism"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    return case


def test_material_drivers_are_asked_before_identity_details() -> None:
    assert next_fact_questions(example())[:2] == ["occupation_status", "funding_source"]


def test_unknown_dates_are_deferred_but_cannot_pass_delivery_gate() -> None:
    case = example()
    update_deferred_questions(case, "行程日期还没定，其他资料我可以先准备。")
    assert set(case.deferred_fields) == {"planned_arrival_date", "planned_departure_date"}
    assert not set(case.deferred_fields) & set(next_fact_questions(case))
    assert "日期先留空" in deterministic_fallback_message(case, "blocked")
    restored = Case.model_validate_json(case.model_dump_json())
    assert restored.deferred_fields == case.deferred_fields
    policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    gate = evaluate_gate(case, policy, date(2026, 9, 4))
    assert not gate.checks["required_profile_facts_complete"]
    assert not case.final_summary_confirmed


def test_new_dates_clear_deferral_without_deleting_existing_dates() -> None:
    case = example()
    update_deferred_questions(case, "My dates are not fixed yet.")
    case.profile.planned_arrival_date = date(2026, 11, 10)
    update_deferred_questions(case, "我定了11月10日到。")
    assert case.deferred_fields == ["planned_departure_date"]
    update_deferred_questions(case, "日期还没定")
    assert case.profile.planned_arrival_date == date(2026, 11, 10)


def test_acknowledgement_uses_only_newly_received_facts() -> None:
    case = example()
    case.latest_received_facts = {"occupation_status": "student", "funding_source": "self"}
    text = deterministic_fallback_message(case, "blocked")
    assert "你目前在读书" in text and "费用由你自己承担" in text
    assert "材料包" not in text


def test_suggested_confirmation_is_recognized_but_negation_is_not() -> None:
    assert clear_natural_confirmation("已核对无误。")
    assert clear_natural_confirmation("我已核对无误。")
    assert not clear_natural_confirmation("我还没核对无误。")


def test_escalation_and_delivery_keep_their_real_boundaries() -> None:
    case = example()
    case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    assert "需要人工核实" in deterministic_fallback_message(case, "blocked")
    case.status = CaseStatus.DRAFT
    reply = deterministic_fallback_message(case, "ready")
    assert "顾问复核" in reply and "还没有递交" in reply


@pytest.mark.parametrize("language", ["zh", "en"])
def test_attachment_reply_names_received_files_without_restarting(language: str) -> None:
    case = example()
    case.customer_language = language
    case.latest_document_names = ["Invitation.pdf", "Funding.pdf"]
    text = deterministic_fallback_message(case, "blocked")
    assert "Invitation.pdf" in text and "Funding.pdf" in text
    assert not text.startswith(("你好", "Hello"))
    assert "已通过" not in text and "accepted" not in text


def test_correction_translates_values_without_reintroducing_adviser() -> None:
    case = example()
    case.latest_changes = {"visit_purpose": "conference", "funding_source": "employer_or_school"}
    text = deterministic_fallback_message(case, "blocked")
    assert text.startswith("好的，已按你说的改为")
    assert "参加会议" in text
    assert "conference" not in text and "employer_or_school" not in text


def test_one_remaining_question_is_prose_not_a_questionnaire() -> None:
    case = example()
    # Leave only the application location actionable; deferred dates remain unknown.
    from visa_agent.domain.rules import required_profile_facts

    remaining = required_profile_facts(case)
    case.profile.application_country = None
    case.deferred_fields = sorted(remaining - {"application_country"})
    text = deterministic_fallback_message(case, "blocked")
    assert next_fact_questions(case) == ["application_country"]
    assert "你准备在哪个国家或地区递交申请？" in text
    assert "\n- " not in text
    assert "还想跟你确认一下" not in text


@pytest.mark.parametrize("body", ["我还没核对其他资料，晚点回复。",
                                  "I haven't checked the other details yet. I'll reply later."])
def test_pure_later_reply_is_acknowledged_without_reasking_or_changing_state(body: str) -> None:
    case = example()
    case.latest_customer_message = body
    before = case.model_dump_json()
    expected = waiting_acknowledgement(case)
    assert expected
    assert deterministic_fallback_message(case, "blocked") == expected
    # The fixture renderer is deliberately different: the guard must choose this reply itself.
    assert GuardedLLM(OfflineFixtureLLM()).render_message(case, "blocked") == expected
    assert "?" not in expected and "？" not in expected
    assert case.model_dump_json() == before


@pytest.mark.parametrize("body", ["晚点回复。需要哪些资料？", "If everything is correct, I'll reply later.",
                                  "我已经确认，晚点回复。"])
def test_later_phrase_is_not_a_general_skip_instruction(body: str) -> None:
    case = example()
    case.latest_customer_message = body
    assert waiting_acknowledgement(case) is None


def test_new_information_cannot_be_hidden_by_later_acknowledgement() -> None:
    case = example()
    case.latest_customer_message = "晚点回复。"
    case.latest_document_names = ["Invitation.pdf"]
    assert waiting_acknowledgement(case) is None
    case.latest_document_names = []
    case.latest_changes = {"planned_departure_date": "2026-11-17"}
    assert waiting_acknowledgement(case) is None


def test_workflow_remembers_unknown_dates_and_duplicate_is_not_another_turn(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "case.db")
    case = example()
    store.save_case(case)
    service = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                              OfflineFixtureLLM(), today_provider=lambda: date(2026, 9, 4))
    event = InboundEvent(id="unknown-dates", external_thread_id="t", sender=case.applicant_contact,
                         subject="继续准备", body="日期还没定，我在整理其他资料。",
                         channel="email_fixture", received_at=datetime.now(UTC))
    try:
        result, duplicate, _ = service.process(event)
        assert not duplicate and len(result.deferred_fields) == 2
        assert not set(result.last_requested_fields) & set(result.deferred_fields)
        assert "日期先留空" in store.list_outbox()[0]["payload"]
        again, duplicate, _ = service.process(event)
        assert duplicate and again.deferred_fields == result.deferred_fields
        assert len(store.list_outbox()) == 1
    finally:
        store.close()
