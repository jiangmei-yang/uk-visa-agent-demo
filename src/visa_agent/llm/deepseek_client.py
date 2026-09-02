from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.openai_client import EXTRACTION_INSTRUCTIONS, _usage_dict
from visa_agent.llm.ports import CasePatch


class DeepSeekStructuredLLM:
    """DeepSeek Responses API adapter; all state changes remain outside this class."""

    version = "configured-deepseek-model"

    def __init__(self, model: str, *, api_key: str, base_url: str = "https://api.deepseek.com") -> None:
        if not api_key:
            raise ValueError("DeepSeek API key is required")
        module = import_module("openai")
        client_type = module.OpenAI
        self.client: Any = client_type(api_key=api_key, base_url=base_url)
        self.model = model
        self.version = model
        self.last_usage: dict[str, int] | None = None

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": EXTRACTION_INSTRUCTIONS},
                {"role": "user", "content": event.body},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "case_patch",
                    "schema": CasePatch.model_json_schema(),
                }
            },
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.last_usage = _usage_dict(response)
        output_text = cast(str, response.output_text)
        if not output_text.strip():
            raise ValueError("DeepSeek returned no CasePatch content")
        return CasePatch.model_validate_json(output_text)

    def render_message(self, case: Case, plan: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=(
                "Write a concise, courteous email from this explicit plan. Do not claim eligibility, "
                "sufficiency, readiness for approval, or guaranteed success. "
                f"Plan: {plan}. Open issues: {[item.title for item in case.open_blockers()]}"
            ),
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.last_usage = _usage_dict(response)
        return cast(str, response.output_text)
