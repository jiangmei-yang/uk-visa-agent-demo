"""Trusted operator record-review metadata; never part of the model patch schema."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from visa_agent.domain.models import Case
    from visa_agent.domain.policy import Policy


class RecordAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: str = Field(min_length=1)
    relationship_category: Literal["family", "other_contact", "not_a_contact"]
    applicable_details_checked: bool
    rationale: str = Field(min_length=12, max_length=2000)


class ApplicationRecordReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=12, max_length=2000)
    reviewed_at: datetime
    assessments: list[RecordAssessment]
    travel_history_scope_checked: bool


def record_review_binding(case: Case, policy: Policy) -> str:
    """Material data/policy changes stale the review; customer consent is separate."""
    payload = {
        "case_id": case.id, "profile": case.profile.model_dump(mode="json"),
        "records": case.application_records.fingerprint() if case.application_records else None,
        "documents": [item.model_dump(mode="json") for item in case.documents],
        # Customer confirmation is a separate gate, not a change to evidence
        # content. Otherwise confirmation itself would stale every review.
        "evidence": [item.model_dump(mode="json", exclude={"confirmed"}) for item in case.evidence],
        "collection_question_event_ids": case.collection_question_event_ids,
        "preparation_control_epoch": case.preparation_control_epoch,
        "delivery_revision": case.delivery_revision,
        "policy": policy.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def record_review_is_current(case: Case, policy: Policy) -> bool:
    review = case.application_record_review
    return bool(review and review.case_id == case.id and review.binding_digest == record_review_binding(case, policy))
