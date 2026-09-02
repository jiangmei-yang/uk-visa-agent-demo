from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.ports import CasePatch, FactUpdate


class FakeResponses:
    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch
        self.create_arguments: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.create_arguments = kwargs
        return SimpleNamespace(
            output_text=json.dumps(self.patch.model_dump()),
            usage=SimpleNamespace(input_tokens=80, output_tokens=16, total_tokens=96),
        )


def test_deepseek_extraction_uses_responses_json_schema_without_openai_only_fields() -> None:
    patch = CasePatch(
        updates=[
            FactUpdate(
                field="full_name",
                value="Ada Lovelace",
                source_excerpt="My name is Ada Lovelace",
                confidence=1,
            )
        ],
        ambiguities=[],
    )
    responses = FakeResponses(patch)
    adapter = DeepSeekStructuredLLM.__new__(DeepSeekStructuredLLM)
    adapter.client = SimpleNamespace(responses=responses)
    adapter.model = "deepseek-v4-flash"
    adapter.version = "deepseek-v4-flash"
    adapter.last_usage = None
    event = InboundEvent(
        id="provider-1",
        external_thread_id="thread-1",
        sender="applicant@example.test",
        subject="Details",
        body="My name is Ada Lovelace",
        received_at=datetime(2026, 9, 3, 9, tzinfo=UTC),
    )

    result = adapter.extract_case_patch(event)

    assert result == patch
    arguments = responses.create_arguments
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["text"]["format"]["type"] == "json_schema"
    assert arguments["text"]["format"]["schema"] == CasePatch.model_json_schema()
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "store" not in arguments
    assert "safety_identifier" not in arguments
    assert adapter.last_usage == {"input_tokens": 80, "output_tokens": 16, "total_tokens": 96}
