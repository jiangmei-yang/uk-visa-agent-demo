"""Immutable registration only. This module cannot approve or deliver documents."""

from datetime import UTC, datetime

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.domain.simulation import SimulationRegistration, SpecimenEntry
from visa_agent.storage.sqlite import SQLiteStore


def _matches(registration: SimulationRegistration, case: Case) -> bool:
    return (
        registration.case_id == case.id
        and registration.external_thread_id == case.external_thread_id
        and registration.applicant_contact == case.applicant_contact
        and registration.channel == case.primary_channel
    )


def get_simulation(store: SQLiteStore, case: Case) -> SimulationRegistration | None:
    row = store.connection.execute(
        "SELECT registration_json FROM simulation_registrations WHERE case_id=?", (case.id,),
    ).fetchone()
    if row is None:
        return None
    registration = SimulationRegistration.model_validate_json(row[0])
    if not _matches(registration, case):
        raise ValueError("Simulation registration does not match the current case scope")
    return registration


def register_simulation(
    store: SQLiteStore, *, case_id: str, external_thread_id: str,
    applicant_contact: str, operator: str, reason: str,
    specimens: tuple[SpecimenEntry, ...],
) -> SimulationRegistration:
    """Register a separately created Gmail case before any file is received.

    Deliberately not called from an inbound message or model patch. No existing
    customer files can be reclassified by converting an ordinary case afterward.
    """
    with store.atomic_write():
        case = store.get_case(case_id)
        if case is None:
            raise ValueError("Create the independent Gmail thread before registration")
        proposed = SimulationRegistration(
            case_id=case_id, external_thread_id=external_thread_id,
            applicant_contact=applicant_contact, operator=operator, reason=reason,
            specimens=specimens, registered_at=datetime.now(UTC),
        )
        if not _matches(proposed, case):
            raise ValueError("Case, thread, owner and Gmail channel must match exactly")
        existing = get_simulation(store, case)
        if existing is not None:
            if existing.model_dump(exclude={"registered_at"}) != proposed.model_dump(exclude={"registered_at"}):
                raise ValueError("An existing simulation registration cannot be changed")
            return existing
        if (
            case.documents or case.profile_confirmed or case.final_summary_confirmed
            or case.delivery_path or case.preparation_paused
            or case.status != CaseStatus.DRAFT or case.human_review_reason
        ):
            raise ValueError("Register a fresh, unconfirmed case before receiving documents")
        store.connection.execute(
            "INSERT INTO simulation_registrations(case_id,registration_json) VALUES (?,?)",
            (case.id, proposed.model_dump_json()),
        )
        return proposed


def require_simulation_binding(store: SQLiteStore, case: Case) -> None:
    """A copied case flag alone can never authorize specimen processing or release."""
    registration = get_simulation(store, case)
    if case.simulation != registration:
        raise ValueError("Fictional case snapshot and operator registration disagree")
    if registration is None and any(
        item.extraction_method == "registered_fictional_identity_specimen" for item in case.evidence
    ):
        raise ValueError("Registered specimen evidence cannot become an ordinary case")
