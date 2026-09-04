from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from visa_agent.domain.models import Case, InboundEvent


class FactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: str | int | bool
    source_excerpt: str
    confidence: float = Field(ge=0, le=1)


class QuestionDeferral(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["planned_arrival_date", "planned_departure_date"]
    source_excerpt: str = Field(min_length=1, max_length=320)
    confidence: float = Field(ge=0, le=1)


class CustomerQuestion(BaseModel):
    """A proposed topic, never a free-form answer, source URL or workflow instruction."""

    model_config = ConfigDict(extra="forbid")

    topic: Literal["application", "timing", "translation", "booking", "fees", "bank_period",
                   "document_checklist", "unsupported", "off_topic"]
    source_excerpt: str = Field(min_length=1, max_length=320)
    confidence: float = Field(ge=0, le=1)


class CustomerQuestionBatch(BaseModel):
    """Question-only experiment output; no facts, answers or workflow authority."""

    model_config = ConfigDict(extra="forbid")

    customer_questions: list[CustomerQuestion] = Field(max_length=4)


class PreparationIntent(BaseModel):
    """A customer preference proposal, never consent or direct workflow authority."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume"]
    source_excerpt: str = Field(min_length=1, max_length=320)
    confidence: float = Field(ge=0, le=1)


class CasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[FactUpdate]
    ambiguities: list[str]
    requires_human_review: bool = False
    question_deferrals: list[QuestionDeferral] = Field(default_factory=list, max_length=2)
    customer_questions: list[CustomerQuestion] = Field(default_factory=list, max_length=4)
    preparation_intent: PreparationIntent | None = None


class LLMClient(Protocol):
    def extract_case_patch(self, event: InboundEvent) -> CasePatch: ...

    def render_message(self, case: Case, plan: str) -> str: ...
