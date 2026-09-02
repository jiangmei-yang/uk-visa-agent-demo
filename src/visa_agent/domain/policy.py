from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class PolicyRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    blocker: bool
    applies_when: str
    description: str
    condition: str
    severity: str
    acceptable_evidence: list[str]
    source_title: str
    source_url: str
    source_updated_at: date
    checked_at: date
    version: str


class Policy(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_id: str
    version: str
    effective_date: date
    valid_until: date
    sources: list[str]
    scope: dict[str, Any]
    requirements: list[PolicyRequirement]
    disclaimer: str

    def is_current(self, today: date) -> bool:
        return self.effective_date <= today <= self.valid_until


def load_policy(path: Path) -> Policy:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Policy.model_validate(payload)
