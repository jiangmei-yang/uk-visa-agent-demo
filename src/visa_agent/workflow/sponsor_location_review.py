"""Local operator transaction; caller owns state lock and operator authentication."""

from datetime import UTC, date, datetime

from visa_agent.domain.models import CaseStatus, WorkflowStage
from visa_agent.domain.policy import Policy
from visa_agent.domain.sponsor_location_review import (
    SponsorLocationReview,
    sponsor_location_applicability,
    sponsor_location_binding,
    sponsor_location_policy_digest,
)
from visa_agent.privacy.consent import ConsentLedger
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.sponsor_location import LOCATION_REVIEW_REASON


def review_sponsor_location(store: SQLiteStore, *, case_id: str, expected_fingerprint: str,
                            actor: str, rationale: str, policy: Policy, today: date) -> SponsorLocationReview:
    """Record conservative evidence applicability, not verification of legal status.

    No model/public endpoint invokes this operation. Actor text is not a login.
    Registered source linkage can be checked; purged email truth cannot be.
    """
    if not 2 <= len(actor.strip()) <= 120 or not 12 <= len(rationale.strip()) <= 2000:
        raise ValueError("Provide operator identity and substantive rationale")
    if not policy.is_current(today):
        raise ValueError("Review requires current policy")
    with store.atomic_write():
        case = store.get_case(case_id)
        if case is None or review_fingerprint(case) != expected_fingerprint:
            raise ValueError("Case changed or missing; inspect again")
        ConsentLedger(store).require(case)
        if (case.status != CaseStatus.HUMAN_REVIEW_REQUIRED or case.delivery_path or case.preparation_paused
                or LOCATION_REVIEW_REASON not in (case.human_review_reason or "")):
            raise ValueError("Expected an active non-finalized sponsor location review")
        if store.has_unreviewed_held_updates(case.id):
            raise ValueError("Review retained applicant updates first")
        if store.connection.execute("SELECT 1 FROM outbox WHERE case_id=? AND status='SENDING'", (case.id,)).fetchone():
            raise ValueError("Reconcile uncertain sends first")
        applicability = sponsor_location_applicability(case)
        if applicability is None:
            raise ValueError("Both current sponsor dimensions must be explicit and nonconflicting")
        current = [item for item in case.sponsor_location_statements
                   if item.identity_epoch == case.sponsor_location_epoch
                   and (item.sponsor_name, item.sponsor_relationship)
                   == (case.profile.sponsor_name, case.profile.sponsor_relationship)]
        for item in current:
            if not store.connection.execute("SELECT 1 FROM processed_events WHERE event_id=? AND case_id=?",
                                            (item.source_event_id, case.id)).fetchone():
                raise ValueError("Location source is not registered to this case")
        review = SponsorLocationReview(case_id=case.id, binding_digest=sponsor_location_binding(case),
            policy_digest=sponsor_location_policy_digest(policy), actor=actor.strip(), rationale=rationale.strip(),
            reviewed_at=datetime.now(UTC), uk_status_evidence_required=applicability)
        case.sponsor_location_review = review
        case.sponsor_location_review_history.append(review)
        # Remove only our complete marker: it itself contains a semicolon.
        remaining = (case.human_review_reason or "").replace(LOCATION_REVIEW_REASON, "").strip("; ")
        case.human_review_reason = remaining or None
        if not remaining:
            case.status = CaseStatus.DRAFT
            case.stage = WorkflowStage.INTAKE
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        case.last_requested_fields = []
        store.save_case(case)
        return review
