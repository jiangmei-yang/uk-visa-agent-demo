"""Universal collection intake checks, not legal applicability or truth verification.

Empty legacy state fails declaration coverage. A complete-list declaration is
independent of sufficient descriptive fields and unresolved field uncertainty.
These checks do not grant consent, mark extracted evidence verified or replace
conditional policy review for nonempty application records.
"""

from visa_agent.domain.application_records import RECORD_KINDS, ApplicationRecordLedger


def application_record_checks(ledger: ApplicationRecordLedger | None, *, case_id: str) -> dict[str, bool]:
    if ledger is not None and ledger.case_id != case_id:
        raise ValueError("Collection completeness requires a case-local ledger")
    current = ledger.current() if ledger else {}
    declarations_complete = ledger is not None and all(
        ledger.collection_state(kind) in {"none_declared", "complete_declared"} for kind in RECORD_KINDS
    )
    descriptive_fields = all(
        set(("country", "period", "purpose") if record.kind == "travel" else ("name", "relationship", "address"))
        <= set(record.fields) for record in current.values()
    )
    return {
        "application_collections_explicitly_declared": declarations_complete,
        "application_record_descriptive_fields_complete": descriptive_fields,
        "application_record_details_not_deferred": not ledger or not ledger.active_field_deferrals(),
    }
