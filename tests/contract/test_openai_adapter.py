from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.openai_client import OpenAIStructuredLLM
from visa_agent.llm.ports import CasePatch, FactUpdate


class FakeResponses:
    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch
        self.parse_arguments: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.parse_arguments = kwargs
        return SimpleNamespace(
            output_parsed=self.patch,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
        )


def test_openai_extraction_contract_uses_private_schema_bound_request() -> None:
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
    adapter = OpenAIStructuredLLM.__new__(OpenAIStructuredLLM)
    adapter.client = SimpleNamespace(responses=responses)
    adapter.model = "evaluated-model"
    adapter.version = "evaluated-model"
    adapter.last_usage = None
    event = InboundEvent(
        id="provider-1",
        external_thread_id="thread-1",
        sender="applicant@example.test",
        subject="Details",
        body="My name is Ada Lovelace",
        received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
    )

    result = adapter.extract_case_patch(event)

    assert result == patch
    assert responses.parse_arguments["model"] == "evaluated-model"
    assert responses.parse_arguments["text_format"] is CasePatch
    assert responses.parse_arguments["store"] is False
    assert responses.parse_arguments["safety_identifier"] != event.sender
    assert len(responses.parse_arguments["safety_identifier"]) == 32
    assert adapter.last_usage == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
