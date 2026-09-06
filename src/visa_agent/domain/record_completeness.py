"""Universal collection intake checks, not legal applicability or truth verification.

Empty legacy state fails declaration coverage. A complete-list declaration is
independent of sufficient descriptive fields and unresolved field uncertainty.
These checks do not grant consent, mark extracted evidence verified or replace
conditional policy review for nonempty application records.
"""

from visa_agent.domain.application_records import (
    RECORD_KINDS,
    ApplicationRecordLedger,
    RecordRevision,
)

FAMILY_RELATIONSHIPS = frozenset({
    "sister", "brother", "mother", "father", "wife", "husband", "daughter", "son", "aunt", "uncle", "cousin",
    "姐姐", "妹妹", "哥哥", "弟弟", "父亲", "父親", "母亲", "母親", "妻子", "丈夫", "女儿", "女兒", "儿子", "兒子",
})


def record_intake_fields(record: RecordRevision) -> tuple[str, ...]:
    """Baseline plus a known-family conditional field, not general role inference.

    Unrecognized relationship wording remains for operator classification. This
    bounded vocabulary is shared with review to avoid collecting every friend's
    passport or asking for a different set of fields than the reviewer checks.
    """
    if record.kind == "travel":
        return ("country", "period", "purpose")
    fields: tuple[str, ...] = ("name", "relationship", "address")
    relationship = record.fields.get("relationship")
    if relationship is not None and relationship.value.strip().casefold() in FAMILY_RELATIONSHIPS:
        fields += ("passport_number",)
    return fields


def application_record_checks(ledger: ApplicationRecordLedger | None, *, case_id: str) -> dict[str, bool]:
    if ledger is not None and ledger.case_id != case_id:
        raise ValueError("Collection completeness requires a case-local ledger")
    current = ledger.current() if ledger else {}
    declarations_complete = ledger is not None and all(
        ledger.collection_state(kind) in {"none_declared", "complete_declared"} for kind in RECORD_KINDS
    )
    descriptive_fields = all(
        set(record_intake_fields(record)) <= set(record.fields) for record in current.values()
    )
    return {
        "application_collections_explicitly_declared": declarations_complete,
        "application_record_descriptive_fields_complete": descriptive_fields,
        "application_record_details_not_deferred": not ledger or not ledger.active_field_deferrals(),
    }
