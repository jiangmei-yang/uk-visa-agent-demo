"""Read-only source-registration audit; not truth or full-message verification.

Processed inbound queue bodies are deliberately purged. We can verify registered
case/event ownership and sent-question scope, not reconstruct the original body
from its hash. Literal excerpts were checked at intake. No function here approves
records, grants consent, fills absent data, sends mail or changes a release gate.
"""

from dataclasses import dataclass

from visa_agent.domain.models import Case
from visa_agent.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class RecordSourceAudit:
    issues: tuple[str, ...]
    checked_event_ids: tuple[str, ...]

    @property
    def registered_sources_match(self) -> bool:
        return not self.issues


def audit_application_record_sources(store: SQLiteStore, case: Case) -> RecordSourceAudit:
    ledger = case.application_records
    if ledger is None:
        return RecordSourceAudit(("application_record_ledger_missing",), ())
    if ledger.case_id != case.id:
        return RecordSourceAudit(("application_record_case_mismatch",), ())
    events: set[str] = set()
    question_links: set[tuple[str, str, str]] = set()
    for record in ledger.current().values():
        events.add(record.changed_by_event_id)
        events.update(value.source_event_id for value in record.fields.values())
    # Even an invalidated historical full-list assertion must remain traceable;
    # this audit does not promote its state back to complete.
    for declaration in ledger.latest_declarations().values():
        events.add(declaration.source_event_id)
        if declaration.question_event_id and declaration.question_key:
            question_links.add((declaration.source_event_id, declaration.question_event_id, declaration.question_key))
    for deferred in ledger.active_field_deferrals():
        events.add(deferred.source_event_id)
        question_links.add((deferred.source_event_id, deferred.question_event_id, deferred.question_key))
    events.update(question for _, question, _ in question_links)
    issues: list[str] = []
    for source in sorted(events):
        row = store.connection.execute("SELECT case_id FROM processed_events WHERE event_id=?", (source,)).fetchone()
        if row is None:
            issues.append(f"source_event_not_registered:{source}")
        elif row["case_id"] != case.id:
            issues.append(f"source_event_case_mismatch:{source}")
    for answer, question, key in sorted(question_links):
        if question == answer or question not in case.collection_question_event_ids.get(key, []):
            issues.append(f"question_link_not_recorded:{question}")
            continue
        rows = store.connection.execute(
            "SELECT status,sent_at,recipient,external_thread_id FROM outbox WHERE case_id=? AND event_id=?",
            (case.id, question),
        ).fetchall()
        if not any(row["status"] == "SENT" and row["sent_at"]
                   and row["recipient"] == case.applicant_contact
                   and row["external_thread_id"] == case.external_thread_id for row in rows):
            issues.append(f"question_not_sent_to_applicant:{question}")
    return RecordSourceAudit(tuple(issues), tuple(sorted(events)))
