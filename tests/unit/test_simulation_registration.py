from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from visa_agent.domain.models import Case, CaseStatus, Document, DocumentStatus
from visa_agent.domain.simulation import SimulationRegistration, SpecimenEntry
from visa_agent.storage.simulation import get_simulation, register_simulation
from visa_agent.storage.sqlite import SQLiteStore

SPECIMENS = (SpecimenEntry(filename="passport.pdf", sha256="a" * 64, kind="passport"),)


@pytest.fixture
def store(tmp_path):
    value = SQLiteStore(tmp_path / "isolated.db")
    value.save_case(Case(id="demo", external_thread_id="thread-demo",
                        applicant_contact="demo@example.test", primary_channel="gmail", policy_version="v"))
    yield value
    value.close()


def register(store, **changes):
    args = dict(case_id="demo", external_thread_id="thread-demo", applicant_contact="demo@example.test",
                operator="operator", reason="User approved independent fictional demonstration", specimens=SPECIMENS)
    args.update(changes)
    return register_simulation(store, **args)


def test_registration_is_durable_idempotent_and_does_not_approve_case(store):
    before = store.get_case("demo").model_dump()
    first = register(store)
    assert register(store) == first
    assert get_simulation(store, store.get_case("demo")) == first
    assert store.get_case("demo").model_dump() == before
    assert store.list_outbox() == []
    reopened = SQLiteStore(store.path)
    try:
        assert get_simulation(reopened, reopened.get_case("demo")) == first
    finally:
        reopened.close()


@pytest.mark.parametrize("changes", [
    {"case_id": "other"}, {"external_thread_id": "other-thread"},
    {"applicant_contact": "other@example.test"},
])
def test_other_customer_or_thread_cannot_register(store, changes):
    with pytest.raises(ValueError):
        register(store, **changes)
    assert store.connection.execute("SELECT COUNT(*) FROM simulation_registrations").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [
    ("profile_confirmed", True), ("final_summary_confirmed", True), ("preparation_paused", True),
    ("delivery_path", "pack.zip"), ("status", CaseStatus.HUMAN_REVIEW_REQUIRED),
    ("primary_channel", "whatsapp"),
])
def test_existing_review_and_channel_boundaries_are_not_bypassed(store, field, value):
    case = store.get_case("demo")
    setattr(case, field, value)
    if field == "preparation_paused":
        case.preparation_control_epoch += 1
    store.save_case(case)
    with pytest.raises(ValueError):
        register(store)


def test_cannot_swap_registered_specimen(store):
    first = register(store)
    changed = (SPECIMENS[0].model_copy(update={"sha256": "b" * 64}),)
    with pytest.raises(ValueError, match="cannot be changed"):
        register(store, specimens=changed)
    assert get_simulation(store, store.get_case("demo")) == first


def test_received_documents_cannot_be_retroactively_converted(store):
    case = store.get_case("demo")
    case.documents.append(Document(id="doc", filename="passport.pdf", kind="passport",
                                   sha256="a" * 64, mime_type="application/pdf",
                                   status=DocumentStatus.HUMAN_REVIEW_REQUIRED,
                                   source_event_id="received", path="retained/passport.pdf"))
    store.save_case(case)
    with pytest.raises(ValueError, match="before receiving documents"):
        register(store)
    assert store.get_case("demo").documents[0].status == DocumentStatus.HUMAN_REVIEW_REQUIRED


def test_scope_changes_fail_closed(store):
    register(store)
    case = store.get_case("demo")
    case.applicant_contact = "other@example.test"
    with pytest.raises(ValueError, match="scope"):
        get_simulation(store, case)


@pytest.mark.parametrize("field,value", [("filename", "../passport.pdf"), ("sha256", "short"), ("kind", "bank_statement")])
def test_manifest_cannot_address_paths_or_money_documents(field, value):
    data = SPECIMENS[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        SpecimenEntry.model_validate(data)


def test_manifest_rejects_ambiguous_duplicates():
    with pytest.raises(ValidationError, match="Duplicate"):
        SimulationRegistration(case_id="c", external_thread_id="t", applicant_contact="a@example.test",
                               operator="operator", reason="Independent fictional case approved",
                               specimens=SPECIMENS * 2, registered_at=datetime.now(UTC))


def test_invalid_nested_model_copy_is_revalidated(store):
    invalid = SPECIMENS[0].model_copy(update={"kind": "bank_statement"})
    with pytest.raises(ValidationError):
        register(store, specimens=(invalid,))
