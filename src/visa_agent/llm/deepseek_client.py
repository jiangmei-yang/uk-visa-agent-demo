from __future__ import annotations

import json
from importlib import import_module
from typing import Any, cast

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.openai_client import (
    EXTRACTION_INSTRUCTIONS,
    _usage_dict,
    extraction_input,
    message_input,
)
from visa_agent.llm.ports import CasePatch


class DeepSeekStructuredLLM:
    """DeepSeek JSON Chat adapter; all state changes remain outside this class."""

    version = "configured-deepseek-model"

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek API key is required")
        module = import_module("openai")
        client_type = module.OpenAI
        self.client: Any = client_type(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self.model = model
        self.version = model
        self.last_usage: dict[str, int] | None = None
        self.usage_history: list[dict[str, int | str]] = []

    def _record_usage(self, response: Any, operation: str) -> None:
        self.last_usage = _usage_dict(response)
        if self.last_usage is not None:
            self.usage_history.append({"operation": operation, **self.last_usage})

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        schema = json.dumps(CasePatch.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"{EXTRACTION_INSTRUCTIONS} Return one JSON object matching this JSON "
                        f"Schema exactly: {schema}"
                    ),
                },
                {"role": "user", "content": extraction_input(event.body)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1_200,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self._record_usage(response, "extract_case_patch")
        output_text = cast(str | None, response.choices[0].message.content)
        if output_text is None or not output_text.strip():
            raise ValueError("DeepSeek returned no CasePatch content")
        return CasePatch.model_validate_json(output_text)

    def render_message(self, case: Case, plan: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": message_input(case, plan),
                }
            ],
            temperature=0,
            max_tokens=500,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self._record_usage(response, "render_message")
        content = cast(str | None, response.choices[0].message.content)
        if content is None:
            raise ValueError("DeepSeek returned no message content")
        return content
