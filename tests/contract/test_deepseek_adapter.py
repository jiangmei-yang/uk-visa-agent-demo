from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.ports import CasePatch, FactUpdate


class FakeCompletions:
    def __init__(self, patch: CasePatch) -> None:
        self.patch = patch
        self.create_arguments: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.create_arguments = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.patch.model_dump()))
                )
            ],
            usage=SimpleNamespace(prompt_tokens=80, completion_tokens=16, total_tokens=96),
        )


def test_deepseek_extraction_uses_json_chat_without_openai_only_fields() -> None:
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
    completions = FakeCompletions(patch)
    adapter = DeepSeekStructuredLLM.__new__(DeepSeekStructuredLLM)
    adapter.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
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
    arguments = completions.create_arguments
    assert arguments["model"] == "deepseek-v4-flash"
    assert arguments["response_format"] == {"type": "json_object"}
    assert arguments["temperature"] == 0
    assert arguments["max_tokens"] == 1_200
    assert "JSON Schema" in arguments["messages"][0]["content"]
    assert '"email_body": "My name is Ada Lovelace"' in arguments["messages"][1]["content"]
    assert arguments["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "store" not in arguments
    assert "safety_identifier" not in arguments
    assert adapter.last_usage == {"input_tokens": 80, "output_tokens": 16, "total_tokens": 96}
