from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.domain.address_evidence import (
    address_detail_is_sufficient,
    address_value_is_grounded,
)
from visa_agent.domain.models import Case, CaseProfile, CaseStatus, Evidence, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate, profile_fact_complete
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message, validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import next_fact_questions
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("value", [
    None, "", "China", "India", "London", "Hong Kong", "Shenzhen, China", "Mumbai, India",
    "中国", "中国香港", "广东省深圳市南山区", "東京都新宿区", "上海市1号", "SW1A 1AA, London",
    "High Street, London", "Flat 2", "Room 4", "Fictional campus address", "address to follow",
])
def test_country_city_postcode_or_unlocated_unit_is_not_a_complete_home_address(value):
    assert not address_detail_is_sufficient(value)


@pytest.mark.parametrize("value", [
    "88 Synthetic Road, Hong Kong", "12 High Street London", "Flat 2, Rose House, London",
    "Room 4, Example Hall, Hong Kong", "Rose Cottage, Littleford, Dorset",
    "Rose Cottage Littleford Dorset", "Room 4, Building W, Mumbai",
    "中国香港薄扶林道83号", "广东省深圳市南山区示例路88号3栋405室",
    "北京市朝阳区望京西园四区5号楼202室", "浙江省杭州市余杭区瓶窑镇长命村张家宅",
    "山田邸，青山村，長野県，日本", "शांति निवास, गांधी नगर, पुणे, भारत",
])
def test_minimum_premises_detail_does_not_require_universal_number_postcode_or_latin_script(value):
    assert address_detail_is_sufficient(value)


@pytest.mark.parametrize("value,excerpt,expected", [
    ("China", "现在住在中国", True), ("China", "我目前住在中国", True),
    ("India", "living in India", True), ("Mumbai, India", "My home address is Mumbai, India", True),
    ("88 Synthetic Road, Hong Kong", "I live at 88 Synthetic Road, Hong Kong", True),
    ("88 Synthetic Road, Hong Kong", "I live in Hong Kong", False),
    ("89 Synthetic Road, Hong Kong", "I live at 88 Synthetic Road, Hong Kong", False),
    ("12 Synthetic Road, London", "I live at 312 Synthetic Road, London", False),
    ("India", "I live in Indiana", False),
    ("Flat 2, Rose House, London", "My home address: Flat 2\nRose House\nLondon", True),
])
def test_grounding_never_invents_finer_address_details(value, excerpt, expected):
    assert address_value_is_grounded(value, excerpt) is expected


def _case(address):
    return Case(id="fictional-address-case", external_thread_id="fictional-thread",
                applicant_contact="fictional@example.test", primary_channel="gmail", policy_version="test",
                profile=CaseProfile(full_name="Fictional Person", date_of_birth=date(1992, 7, 8),
                    nationality_country="China", application_country="Hong Kong", visit_purpose="tourism",
                    planned_arrival_date=date(2026, 11, 5), planned_departure_date=date(2026, 11, 12),
                    uk_accommodation="Planned hotel in London", estimated_trip_cost_gbp=2400,
                    occupation_status="student", funding_source="self", current_address=address,
                    has_serious_history=False, route_confirmed_standard_visitor=True))


@pytest.mark.parametrize("address,expected", [("China", False), ("Mumbai, India", False),
    ("88 Synthetic Road, Hong Kong", True), ("Rose Cottage, Littleford, Dorset", True)])
def test_gate_and_next_question_share_address_completeness(address, expected):
    case = _case(address)
    gate = evaluate_gate(case, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")), date(2026, 9, 4))
    assert profile_fact_complete(case, "current_address") is expected
    assert gate.checks["required_profile_facts_complete"] is expected
    assert ("current_address" in next_fact_questions(case)) is not expected
    assert case.status == CaseStatus.DRAFT


@pytest.mark.parametrize("value,body", [("China", "现在住在中国"), ("India", "I currently live in India.")])
def test_coarse_residential_context_is_retained_without_escalation(value, body):
    event = InboundEvent(id="new-location", external_thread_id="fictional", sender="fictional@example.test",
                         subject="UK visit", body=body, received_at=datetime(2026, 9, 4, tzinfo=UTC))
    patch = CasePatch(updates=[FactUpdate(field="current_address", value=value, source_excerpt=body,
                                         confidence=1)], ambiguities=[])
    guarded = validate_case_patch(event, patch)
    assert not guarded.requires_human_review and guarded.updates[0].value == value


@pytest.mark.parametrize("body", [
    "My office address is 88 Synthetic Road, Hong Kong.",
    "My mother's address is 88 Synthetic Road, Hong Kong.",
    "My friend lives at 88 Synthetic Road, Hong Kong.",
    "公司地址是88 Synthetic Road, Hong Kong。",
])
def test_other_address_is_not_an_update_even_when_home_address_was_requested(body):
    event = InboundEvent(id="other-address", external_thread_id="fictional", sender="fictional@example.test",
                         subject="Address", body=body, requested_fields=["current_address"],
                         received_at=datetime(2026, 9, 4, tzinfo=UTC))
    patch = CasePatch(updates=[FactUpdate(field="current_address", value="88 Synthetic Road, Hong Kong",
                                        source_excerpt=body, confidence=1)], ambiguities=[])
    result = validate_case_patch(event, patch)
    assert result.updates == [] and not result.requires_human_review


@pytest.mark.parametrize("body", [
    "My home address is 88 Synthetic Road, Hong Kong, not my office address.",
    "I live with my mother at 88 Synthetic Road, Hong Kong.",
    "My office address is 12 Other Street, London. My home address is 88 Synthetic Road, Hong Kong.",
    "My mother's address is 12 Other Street, London. I live at 88 Synthetic Road, Hong Kong.",
    "我的家庭住址是88 Synthetic Road, Hong Kong，不是公司地址。",
])
def test_own_address_is_not_discarded_by_an_independent_other_location(body):
    event = InboundEvent(id="own-address", external_thread_id="fictional", sender="fictional@example.test",
                         subject="Address", body=body, requested_fields=["current_address"],
                         received_at=datetime(2026, 9, 4, tzinfo=UTC))
    result = validate_case_patch(event, CasePatch(updates=[FactUpdate(field="current_address",
        value="88 Synthetic Road, Hong Kong", source_excerpt=body, confidence=1)], ambiguities=[]))
    assert not result.requires_human_review and result.updates[0].value == "88 Synthetic Road, Hong Kong"


@pytest.mark.parametrize("paused", [False, True])
def test_incomplete_correction_replaces_old_complete_address_without_human_review(tmp_path, paused):
    database = tmp_path / "address.db"
    initial = _case("88 Synthetic Road, Hong Kong")
    initial.profile_confirmed = initial.final_summary_confirmed = True
    initial.preparation_paused = paused
    initial.evidence.append(Evidence(id="old-address-evidence", fact_key="current_address",
        value=initial.profile.current_address, source_event_id="old-address-event",
        source_excerpt="My home address is 88 Synthetic Road, Hong Kong", extraction_method="synthetic-test",
        model_version="none", confidence=1, confirmed=True))
    body = "My home address has changed. I now live in Mumbai, India."
    event = InboundEvent(id="address-correction", external_thread_id=initial.external_thread_id,
                         sender=initial.applicant_contact, subject="A correction", body=body,
                         received_at=datetime(2026, 9, 4, tzinfo=UTC), channel="gmail")

    class Model:
        version = "synthetic-address-proposal"

        def extract_case_patch(self, prepared):
            return CasePatch(updates=[FactUpdate(field="current_address", value="Mumbai, India",
                source_excerpt="I now live in Mumbai, India", confidence=1)], ambiguities=[])

        render_message = staticmethod(deterministic_fallback_message)

    store = SQLiteStore(database)
    try:
        store.save_case(initial)
        guard = GuardedLLM(Model())
        workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                                   guard, today_provider=lambda: date(2026, 9, 4))
        case, duplicate, _ = workflow.process(event)
        assert not duplicate and not guard.last_extraction_fallback and case.status == CaseStatus.DRAFT
        assert case.profile.current_address == "Mumbai, India" and not profile_fact_complete(case, "current_address")
        assert not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None
        assert case.latest_changes["current_address"] == "Mumbai, India"
        assert len(case.active_evidence("current_address")) == 1
        assert case.active_evidence("current_address")[0].source_excerpt == "I now live in Mumbai, India"
        assert next(item for item in case.evidence if item.id == "old-address-evidence").superseded
        assert case.preparation_paused is paused
        assert ("current_address" in next_fact_questions(case)) is not paused
        snapshot = case.model_dump(mode="json")
    finally:
        store.close()
    reopened = SQLiteStore(database)
    try:
        assert reopened.get_case(initial.id).model_dump(mode="json") == snapshot
    finally:
        reopened.close()
