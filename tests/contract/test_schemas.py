from __future__ import annotations

import pytest
from pydantic import ValidationError

from visa_agent.domain.models import Case
from visa_agent.llm.ports import CasePatch


def test_case_patch_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CasePatch.model_validate(
            {
                "updates": [],
                "ambiguities": [],
                "requires_human_review": False,
                "workflow_state": "READY_FOR_HUMAN_REVIEW",
            }
        )


def test_case_patch_schema_contains_no_state_field() -> None:
    properties = CasePatch.model_json_schema()["properties"]
    assert set(properties) == {
        "updates", "ambiguities", "requires_human_review", "question_deferrals", "customer_questions",
        "preparation_intent", "application_records", "collection_declarations",
    }
    intent = CasePatch.model_json_schema()['$defs']['QuestionDeferral']['properties']
    assert set(intent) == {'field', 'source_excerpt', 'confidence'}
    assert set(intent['field']['enum']) == {'planned_arrival_date', 'planned_departure_date'}
    question = CasePatch.model_json_schema()['$defs']['CustomerQuestion']
    assert question['additionalProperties'] is False
    assert set(question['properties']) == {'topic', 'source_excerpt', 'confidence'}
    assert set(question['properties']['topic']['enum']) == {
        'application', 'route_orientation', 'eligibility_overview', 'biometrics', 'after_apply',
        'timing', 'translation', 'booking', 'fees', 'bank_period',
        'sponsor_support', 'document_checklist', 'next_step', 'unsupported', 'off_topic',
    }
    assert properties['customer_questions']['maxItems'] == 4
    assert properties['application_records']['maxItems'] == 20
    assert properties['collection_declarations']['maxItems'] == 2
    record = CasePatch.model_json_schema()['$defs']['ApplicationRecordProposal']
    assert record['additionalProperties'] is False
    assert set(record['properties']) == {'action', 'record', 'source_excerpt', 'target_reference', 'confidence'}
    declaration = CasePatch.model_json_schema()['$defs']['CollectionDeclarationProposal']
    assert declaration['additionalProperties'] is False
    assert set(declaration['properties']) == {'kind', 'state', 'source_excerpt', 'confidence'}


def test_legacy_email_named_case_snapshot_migrates_to_channel_neutral_fields() -> None:
    case = Case.model_validate(
        {
            "id": "case-legacy",
            "email_thread_id": "thread-legacy",
            "applicant_email": "applicant@example.test",
            "policy_version": "test-policy",
        }
    )

    assert case.external_thread_id == "thread-legacy"
    assert case.applicant_contact == "applicant@example.test"
    assert case.primary_channel == "email"
    assert "email_thread_id" not in case.model_dump()
    assert "applicant_email" not in case.model_dump()
