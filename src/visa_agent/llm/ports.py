from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from visa_agent.domain.models import Case, InboundEvent


class FactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: str | int | bool
    source_excerpt: str
    confidence: float = Field(ge=0, le=1)


class CasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[FactUpdate]
    ambiguities: list[str]
    requires_human_review: bool = False


class LLMClient(Protocol):
    def extract_case_patch(self, event: InboundEvent) -> CasePatch: ...

    def render_message(self, case: Case, plan: str) -> str: ...
