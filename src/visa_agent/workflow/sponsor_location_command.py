"""Local operator entry point: inspect sources, explicitly acknowledge, never send."""

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.domain.policy import load_policy
from visa_agent.domain.sponsor_location_review import (
    current_sponsor_location_values,
    sponsor_location_applicability,
    sponsor_location_policy_digest,
)
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.sponsor_location_review import review_sponsor_location


class LocationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    expected_fingerprint: str
    policy_digest: str
    actor: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=12, max_length=2000)
    source_and_applicability_checked: Literal[True]
    selected_source_event_ids: dict[str, str] = Field(default_factory=dict)


def sponsor_location_command(*, state_dir: Path, policy_path: Path,
                             case_id: str | None = None, decision_path: Path | None = None) -> dict[str, Any]:
    if (case_id is None) == (decision_path is None):
        raise ValueError("Choose one case to inspect or one decision file to apply")
    directory = state_dir.resolve(strict=True)
    database = directory / "sandbox.db"
    if not database.is_file() or database.is_symlink():
        raise ValueError("Expected an existing non-symlink Gmail sandbox.db")
    with database.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            raise ValueError("Expected an existing SQLite database; refusing to initialize it")
    policy = load_policy(policy_path)
    decision = None
    if decision_path is not None:
        if decision_path.stat().st_size > 256_000:
            raise ValueError("Decision file exceeds size limit")
        document = json.loads(decision_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) - {"context", "decision"}:
            raise ValueError("Expected a plan with decision and optional informational context")
        decision = LocationDecision.model_validate(document.get("decision"))
        if decision.policy_digest != sponsor_location_policy_digest(policy):
            raise ValueError("Policy changed; inspect again")
        case_id = decision.case_id
    with exclusive_state(directory):
        store = SQLiteStore(database)
        try:
            case = store.get_case(str(case_id))
            if case is None:
                raise ValueError("Case not found")
            if decision is not None:
                review = review_sponsor_location(store, case_id=case.id,
                    expected_fingerprint=decision.expected_fingerprint, actor=decision.actor,
                    rationale=decision.rationale, policy=policy, today=date.today(),
                    selected_source_event_ids=decision.selected_source_event_ids)
                updated = store.get_case(case.id)
                assert updated is not None
                return {"status": "review_saved", "case_id": case.id,
                    "case_status": updated.status.value, "remaining_review_reason": updated.human_review_reason,
                    "uk_status_evidence_required": review.uk_status_evidence_required,
                    "mail_sent": False, "customer_confirmed": False,
                    "next": "Retain other review holds; resume normal intake only if draft. Fresh applicant confirmations are required."}
            return {
                "context": {"notice": "Private local operator review. Context is not authority. No email will be sent.",
                    "authentication": "Local filesystem access only; actor text is not a login.",
                    "source_limit": "Check original sources; registered event linkage alone does not prove their truth.",
                    "case_status": case.status.value, "review_reason": case.human_review_reason,
                    "sponsor_name": case.profile.sponsor_name, "sponsor_relationship": case.profile.sponsor_relationship,
                    "identity_epoch": case.sponsor_location_epoch,
                    "current_values": {key: sorted(values) for key, values in current_sponsor_location_values(case).items()},
                    "proposed_uk_status_evidence_required": sponsor_location_applicability(case),
                    "statements": [item.model_dump(mode="json") for item in case.sponsor_location_statements]},
                "decision": {"case_id": case.id, "expected_fingerprint": review_fingerprint(case),
                    "policy_digest": sponsor_location_policy_digest(policy), "actor": "", "rationale": "",
                    "source_and_applicability_checked": False, "selected_source_event_ids": {}},
            }
        finally:
            store.close()
