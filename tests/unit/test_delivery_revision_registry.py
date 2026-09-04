"""Registry and deletion boundaries, with non-document transport fixtures only."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from visa_agent.domain.models import Case
from visa_agent.storage.sqlite import SQLiteStore


def test_changing_case_revision_alone_cannot_replace_an_original_delivery(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    case = Case(id="case", external_thread_id="t", applicant_contact="example@example.test", policy_version="v")
    try:
        store.save_case(case)
        store.save_delivery(case.id, "old.zip", "old-hash")
        case.delivery_revision = 2
        store.save_case(case)
        for path in ("old.zip", "new_revision-2.zip"):
            with pytest.raises(ValueError):
                store.save_delivery(case.id, path, "new-hash", case_revision=2)
        assert store.connection.execute("SELECT path FROM deliveries").fetchone()[0] == "old.zip"
        assert store.export_case_data(case.id)["delivery_versions"] == []
    finally:
        store.close()


def test_processed_revision_audit_required_and_current_attempt_stays_immutable(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    case = Case(id="case", external_thread_id="t", applicant_contact="example@example.test", policy_version="v")
    try:
        store.save_case(case)
        store.save_delivery(case.id, "old.zip", "old-hash")
        before = case.model_dump_json()
        case.delivery_revision = 2
        store.save_case(case)
        with store.connection:
            store.connection.execute("""INSERT INTO review_actions
                (id,case_id,held_event_id,actor,reason,before_json,after_json,retry_event_id,action_kind)
                VALUES ('audit',?,'held','Fixture reviewer','Registry-only test',?,?,'retry','revision')""",
                (case.id, before, case.model_dump_json()))
        with pytest.raises(ValueError, match="processed"):
            store.save_delivery(case.id, "new.zip", "new-hash", case_revision=2)
        with store.connection:
            store.connection.execute("INSERT INTO processed_events(event_id,case_id) VALUES ('retry',?)", (case.id,))
            store.connection.execute("""INSERT INTO outbox
                (id,case_id,event_id,message_type,payload,status,case_revision)
                VALUES ('old',?,'e1','ready','Original','SENT',1)""", (case.id,))
        store.save_delivery(case.id, "new.zip", "new-hash", case_revision=2)
        assert store.export_case_data(case.id)["delivery_versions"][0]["case_revision"] == 1
        with pytest.raises(ValueError, match="current case"):
            store.save_delivery(case.id, "old.zip", "old-hash", case_revision=1)
        with store.connection:
            store.connection.execute("""INSERT INTO outbox
                (id,case_id,event_id,message_type,payload,status,case_revision)
                VALUES ('new',?,'e2','ready','Current','SENDING',2)""", (case.id,))
        with pytest.raises(ValueError, match="send attempt"):
            store.save_delivery(case.id, "third.zip", "third-hash", case_revision=2)
        store.mark_outbox_sent("new", "accepted", datetime.now(UTC))
        # Registering the same immutable bytes is idempotent, never a new send.
        store.save_delivery(case.id, "new.zip", "new-hash", case_revision=2)
        assert store.connection.execute("SELECT path FROM deliveries").fetchone()[0] == "new.zip"
    finally:
        store.close()


def test_case_deletion_and_reset_remove_version_metadata(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    try:
        for identifier in ("deleted-case", "other-case"):
            store.save_case(Case(id=identifier, external_thread_id=identifier,
                                applicant_contact="example@example.test", policy_version="v"))
            store.save_delivery(identifier, "old.zip", "old")
            store.save_delivery(identifier, "new.zip", "new")
        store.delete_case("deleted-case")
        assert store.connection.execute("SELECT case_id FROM delivery_versions").fetchone()[0] == "other-case"
        store.reset()
        assert not store.connection.execute("SELECT 1 FROM delivery_versions").fetchall()
    finally:
        store.close()
