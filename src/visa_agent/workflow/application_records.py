"""Case-binding adapter for the trusted application-record planner.

This is not a public processing API: consent, statement ownership, current intent
and safe record selection must be established by the inbound workflow first.
The natural-language planner and collection-completeness gate are not yet wired.
"""

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    RecordCommand,
    apply_record_commands,
)
from visa_agent.domain.models import Case, CaseStatus, InboundEvent


def case_with_record_commands(
    case: Case, event: InboundEvent, commands: list[RecordCommand], *,
    declarations: list[CollectionDeclaration] | None = None,
) -> Case:
    """Apply only to the bound draft case and invalidate material confirmations."""
    if case.status != CaseStatus.DRAFT:
        raise ValueError("Record changes to reviewed/delivered cases require the existing revision workflow")
    if event.external_thread_id != case.external_thread_id or event.sender != case.applicant_contact:
        raise ValueError("Record event does not belong to this applicant thread")
    prior = case.application_records or ApplicationRecordLedger(case_id=case.id)
    updated = apply_record_commands(prior, case_id=case.id, event_id=event.id, body=event.body,
                                    commands=commands, declarations=declarations)
    result = case.model_copy(deep=True)
    result.application_records = updated
    if updated.fingerprint() != prior.fingerprint():
        result.profile_confirmed = False
        result.final_summary_confirmed = False
        result.confirmation_fingerprint = None
        result.confirmation_kind = None
        result.confirmation_request_event_id = None
    return result
