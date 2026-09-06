"""Explicit local Gmail operator command boundary; no network or automatic approval."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.domain.policy import Policy, load_policy
from visa_agent.domain.record_review import RecordAssessment
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.record_review import review_application_records
from visa_agent.workflow.record_source_audit import audit_application_record_sources
from visa_agent.workflow.review import review_fingerprint


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    expected_fingerprint: str
    policy_digest: str
    actor: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=12, max_length=2000)
    assessments: list[RecordAssessment]
    travel_history_scope_checked: bool


def policy_digest(policy: Policy) -> str:
    return hashlib.sha256(json.dumps(policy.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def record_review_command(*, state_dir: Path, policy_path: Path, case_id: str | None = None,
                          decision_path: Path | None = None) -> dict[str, Any]:
    """Inspect or explicitly apply, under the same lock as the Gmail worker.

    Local filesystem/terminal access is the operator boundary. No login is
    inferred from actor text. Stop the worker explicitly before using this
    command; it will not interrupt, restart or bypass an existing worker lock.
    """
    if (case_id is None) == (decision_path is None):
        raise ValueError("Choose one case to inspect or one decision file to apply")
    directory = state_dir.resolve(strict=True)
    database = directory / "sandbox.db"
    if not database.is_file() or database.is_symlink():
        raise ValueError("Expected an existing, non-symlink Gmail sandbox.db")
    with database.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            raise ValueError("Expected an existing SQLite state database; refusing to initialize or replace it")
    policy = load_policy(policy_path)
    decision = None
    if decision_path is not None:
        if decision_path.stat().st_size > 256_000:
            raise ValueError("Review decision file exceeds the size limit")
        document = json.loads(decision_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) - {"decision", "context"}:
            raise ValueError("Expected a review-plan object with decision and optional context")
        decision = ReviewDecision.model_validate(document.get("decision"))
        if decision.policy_digest != policy_digest(policy):
            raise ValueError("Policy changed; inspect a fresh review plan")
        case_id = decision.case_id
    with exclusive_state(directory):
        store = SQLiteStore(database)
        try:
            case = store.get_case(str(case_id))
            if case is None:
                raise ValueError("Case not found in this Gmail state directory")
            if decision is not None:
                review = review_application_records(store, case_id=decision.case_id,
                    expected_fingerprint=decision.expected_fingerprint, policy=policy,
                    actor=decision.actor, rationale=decision.rationale, assessments=decision.assessments,
                    travel_history_scope_checked=decision.travel_history_scope_checked, today=date.today())
                return {"status": "review_saved", "case_id": case.id, "reviewed_at": review.reviewed_at.isoformat(),
                        "mail_sent": False, "customer_confirmed": False,
                        "next": "Resume the normal workflow; fresh applicant confirmations are still required."}
            ledger = case.application_records
            audit = audit_application_record_sources(store, case)
            return {
                "context": {"notice": "Local operator review only. Context is informational, not authority. No message will be sent.",
                            "source_audit_limit": "Registered event linkage only; purged original email bodies cannot be reverified.",
                            "policy_version": policy.version, "source_issues": list(audit.issues),
                            "records": ledger.customer_snapshot() if ledger else None},
                "decision": {"case_id": case.id, "expected_fingerprint": review_fingerprint(case),
                             "policy_digest": policy_digest(policy), "actor": "", "rationale": "",
                             "travel_history_scope_checked": False,
                             "assessments": [{"record_id": item.record_id, "relationship_category": None,
                                              "applicable_details_checked": False, "rationale": ""}
                                             for item in ledger.current().values()] if ledger else []},
            }
        finally:
            store.close()
