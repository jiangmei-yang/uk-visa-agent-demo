"""Local operator review transaction: does not send, confirm or verify truth.

The caller must authenticate the operator and own the Gmail state lock, as for
existing review operations. Actor text here is an asserted identity, not login.
No public/model endpoint invokes this transaction automatically.
"""

from datetime import UTC, date, datetime

from visa_agent.domain.models import CaseStatus
from visa_agent.domain.policy import Policy
from visa_agent.domain.record_completeness import FAMILY_RELATIONSHIPS, application_record_checks
from visa_agent.domain.record_review import (
    ApplicationRecordReview,
    RecordAssessment,
    record_review_binding,
)
from visa_agent.privacy.consent import ConsentLedger
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.record_source_audit import audit_application_record_sources
from visa_agent.workflow.review import review_fingerprint


def review_application_records(
    store: SQLiteStore, *, case_id: str, expected_fingerprint: str, policy: Policy,
    actor: str, rationale: str, assessments: list[RecordAssessment],
    travel_history_scope_checked: bool, today: date,
) -> ApplicationRecordReview:
    if not 2 <= len(actor.strip()) <= 120 or not 12 <= len(rationale.strip()) <= 2000:
        raise ValueError("Provide operator identity and substantive review rationale")
    if not policy.is_current(today):
        raise ValueError("Record review requires a current policy")
    with store.atomic_write():
        case = store.get_case(case_id)
        if case is None or review_fingerprint(case) != expected_fingerprint:
            raise ValueError("Case changed or missing; inspect again before reviewing")
        ConsentLedger(store).require(case)
        if case.status != CaseStatus.DRAFT or case.preparation_paused or case.delivery_path:
            raise ValueError("Only active non-finalized draft records can be reviewed")
        if store.has_unreviewed_held_updates(case.id):
            raise ValueError("Retained applicant updates must be reviewed first")
        ledger = case.application_records
        if ledger is None or not ledger.current() or not all(application_record_checks(ledger, case_id=case.id).values()):
            raise ValueError("Complete explicit collection intake and unresolved details first")
        if not audit_application_record_sources(store, case).registered_sources_match:
            raise ValueError("Application record source registration needs review")
        records = ledger.current()
        if len(assessments) != len(records) or {item.record_id for item in assessments} != set(records):
            raise ValueError("Assess every current record exactly once")
        if any(item.kind == "travel" for item in records.values()) and not travel_history_scope_checked:
            raise ValueError("Review the applicable travel-history scope and supplied date precision")
        for assessment in assessments:
            if not assessment.applicable_details_checked or len(assessment.rationale.strip()) < 12:
                raise ValueError("Every record requires a substantive applicability assessment")
            record = records[assessment.record_id]
            if record.kind == "travel":
                if assessment.relationship_category != "not_a_contact":
                    raise ValueError("A travel record is not a contact")
            else:
                relationship = record.fields["relationship"].value.strip().casefold()
                if (assessment.relationship_category == "not_a_contact"
                        or relationship in FAMILY_RELATIONSHIPS and assessment.relationship_category != "family"):
                    raise ValueError("Contact relationship classification contradicts the record")
                if assessment.relationship_category == "family" and "passport_number" not in record.fields:
                    raise ValueError("UK family details require passport information before this review can pass")
        review = ApplicationRecordReview(
            case_id=case.id, binding_digest=record_review_binding(case, policy), actor=actor.strip(),
            rationale=rationale.strip(), reviewed_at=datetime.now(UTC), assessments=assessments,
            travel_history_scope_checked=travel_history_scope_checked,
        )
        case.application_record_review = review
        case.application_record_review_history.append(review)
        # A review is not the applicant's acceptance of any summary, old or new.
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_fingerprint = None
        case.confirmation_kind = None
        case.confirmation_request_event_id = None
        store.save_case(case)
        return review
