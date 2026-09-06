"""Source-bound sponsor applicability review, separate from immigration status."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from visa_agent.domain.models import Case
    from visa_agent.domain.policy import Policy


class SponsorLocationReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=12, max_length=2000)
    reviewed_at: datetime
    uk_status_evidence_required: bool


def sponsor_location_binding(case: Case) -> str:
    payload = {
        "case_id": case.id,
        "identity": [case.profile.funding_source, case.profile.sponsor_name,
                     case.profile.sponsor_relationship, case.profile.sponsor_address],
        "epoch": case.sponsor_location_epoch,
        "control_epoch": case.preparation_control_epoch,
        "statements": [item.model_dump(mode="json") for item in case.sponsor_location_statements],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sponsor_location_applicability(case: Case) -> bool | None:
    """Conservative review input, not a legal-status decision or automatic waiver.

    Require both explicit dimensions for the current identified sponsor. Mixed
    residence/presence is not a contradiction; opposite values of one dimension
    are unresolved. Earlier contradictory statements need explicit resolution.
    """
    profile = case.profile
    if (profile.funding_source != "personal_sponsor" or not profile.sponsor_name
            or not profile.sponsor_relationship):
        return None
    values: dict[str, set[bool]] = {"residence": set(), "current_presence": set()}
    for item in case.sponsor_location_statements:
        if (item.identity_epoch == case.sponsor_location_epoch
                and (item.sponsor_name, item.sponsor_relationship) == (profile.sponsor_name, profile.sponsor_relationship)):
            values[item.dimension].add(item.value)
    if any(len(items) != 1 for items in values.values()):
        return None
    return any(True in items for items in values.values())


def sponsor_location_policy_digest(policy: Policy) -> str:
    return hashlib.sha256(json.dumps(policy.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def sponsor_location_review_is_current(case: Case, policy: Policy | None = None) -> bool:
    review = case.sponsor_location_review
    applicability = sponsor_location_applicability(case)
    return bool(review and applicability is not None and review.case_id == case.id
                and review.binding_digest == sponsor_location_binding(case)
                and (policy is None or review.policy_digest == sponsor_location_policy_digest(policy))
                and review.uk_status_evidence_required == applicability)
