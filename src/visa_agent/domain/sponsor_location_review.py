"""Source-bound sponsor applicability review, separate from immigration status."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from visa_agent.domain.sponsor_location import SponsorLocationStatement

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
    selected_source_event_ids: dict[str, str] = Field(default_factory=dict)


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
    values = current_sponsor_location_values(case)
    if any(len(items) != 1 for items in values.values()):
        return None
    return any(True in items for items in values.values())


def current_sponsor_location_statements(case: Case, *, selected: dict[str, str] | None = None) -> list[SponsorLocationStatement]:
    """Keep history intact; apply only a source-bound operator resolution.

    Explicit selections are validated against raw current-identity observations,
    never against an earlier review's already filtered observations.
    """
    profile = case.profile
    rows = [item for item in case.sponsor_location_statements
            if item.identity_epoch == case.sponsor_location_epoch
            and (item.sponsor_name, item.sponsor_relationship)
            == (profile.sponsor_name, profile.sponsor_relationship)]
    if selected is None:
        review = case.sponsor_location_review
        selected = (review.selected_source_event_ids if review and review.case_id == case.id
                    and review.binding_digest == sponsor_location_binding(case) else {})
    for dimension, event_id in selected.items():
        raw = [item for item in rows if item.dimension == dimension]
        chosen = [item for item in raw if item.source_event_id == event_id]
        if (dimension not in {"residence", "current_presence"}
                or len({item.value for item in raw}) != 2
                or len({item.value for item in chosen}) != 1):
            raise ValueError("Resolution must select an unambiguous current source for a conflicting dimension")
    return [item for item in rows
            if item.dimension not in selected or item.source_event_id == selected[item.dimension]]


def current_sponsor_location_values(case: Case) -> dict[str, set[bool]]:
    """Current identity only; stale or malformed review cannot hide a conflict."""
    values: dict[str, set[bool]] = {"residence": set(), "current_presence": set()}
    try:
        rows = current_sponsor_location_statements(case)
    except ValueError:
        rows = current_sponsor_location_statements(case, selected={})
    for item in rows:
        values[item.dimension].add(item.value)
    return values


def sponsor_location_policy_digest(policy: Policy) -> str:
    return hashlib.sha256(json.dumps(policy.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def sponsor_location_review_is_current(case: Case, policy: Policy | None = None) -> bool:
    review = case.sponsor_location_review
    try:
        current_sponsor_location_statements(case)
    except ValueError:
        return False
    applicability = sponsor_location_applicability(case)
    return bool(review and applicability is not None and review.case_id == case.id
                and review.binding_digest == sponsor_location_binding(case)
                and (policy is None or review.policy_digest == sponsor_location_policy_digest(policy))
                and review.uk_status_evidence_required == applicability)
