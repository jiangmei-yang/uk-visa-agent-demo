from __future__ import annotations

import pytest
from pydantic import ValidationError

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
    assert set(properties) == {"updates", "ambiguities", "requires_human_review"}
