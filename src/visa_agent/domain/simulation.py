"""Operator-scoped fictional demonstration, never applicant consent or approval."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpecimenEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,149}\.pdf$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["passport", "status_document"]


class SimulationRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    case_id: str = Field(min_length=1)
    external_thread_id: str = Field(min_length=1)
    applicant_contact: str = Field(min_length=1)
    channel: Literal["gmail"] = "gmail"
    operator: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=12, max_length=1200)
    purpose: Literal["fictional_demonstration_not_for_application"] = "fictional_demonstration_not_for_application"
    specimens: tuple[SpecimenEntry, ...] = Field(min_length=1, max_length=2)
    registered_at: datetime

    @model_validator(mode="after")
    def unique_specimens(self) -> "SimulationRegistration":
        for attribute in ("filename", "sha256", "kind"):
            values = [getattr(item, attribute) for item in self.specimens]
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate specimen {attribute}")
        if not self.operator.strip() or len(self.reason.strip()) < 12:
            raise ValueError("An operator and substantive registration reason are required")
        if self.registered_at.tzinfo is None:
            raise ValueError("Registration time must include a timezone")
        return self
