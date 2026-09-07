"""Regressions captured from the independent fictional Gmail deployment journey."""

from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.workflow.adviser_guidance import _conditional_common_evidence_orientation


@pytest.mark.parametrize("body,accepted", [
    ("您好，我想准备英国旅游签证。", True),
    ("我准备英国旅游签证，去伦敦玩一周。", True),
    ("如果我准备英国旅游签证，需要什么？", False),
    ("我的朋友准备英国旅游签证。", False),
    ("我不准备英国旅游签证，我去英国工作。", False),
])
def test_preparing_tourist_visa_is_an_owned_assertion_not_a_hypothetical(body, accepted):
    event = InboundEvent(id="finding", external_thread_id="finding", sender="fictional@example.test",
                         subject="材料准备", body=body, received_at=datetime.now(UTC))
    patch = CasePatch(updates=[FactUpdate(field="visit_purpose", value="tourism",
                                        source_excerpt=body, confidence=1)], ambiguities=[])
    checked = validate_case_patch(event, patch)
    assert any(item.field == "visit_purpose" for item in checked.updates) == accepted


def test_chinese_orientation_localises_known_countries_without_changing_facts():
    case = Case(id="finding", external_thread_id="finding", applicant_contact="fictional@example.test",
                policy_version="v", customer_language="zh")
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    before = case.model_dump_json()
    message = _conditional_common_evidence_orientation(case, no_links=True)
    assert "中国护照" in message and "在香港递交" in message
    assert "China" not in message and "Hong Kong" not in message
    assert case.model_dump_json() == before
