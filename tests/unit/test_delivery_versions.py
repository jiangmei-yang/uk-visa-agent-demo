from pathlib import Path

import pytest

from visa_agent.storage.sqlite import SQLiteStore


def test_unsent_revised_pack_preserves_previous_version(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "case.db")
    try:
        store.save_delivery("c", "old.zip", "old-hash")
        store.save_delivery("c", "reviewed.zip", "new-hash")
        current = store.connection.execute("SELECT path, sha256 FROM deliveries").fetchone()
        previous = store.connection.execute("SELECT path, sha256 FROM delivery_versions").fetchone()
        assert tuple(current) == ("reviewed.zip", "new-hash")
        assert tuple(previous) == ("old.zip", "old-hash")
        store.connection.execute(
            "INSERT INTO outbox(id, case_id, event_id, message_type, payload, status) "
            "VALUES ('out', 'c', 'e', 'ready', 'ready', 'SENDING')"
        )
        store.connection.commit()
        with pytest.raises(ValueError, match="send attempt"):
            store.save_delivery("c", "unsafe.zip", "another-hash")
        assert (
            store.connection.execute("SELECT path FROM deliveries").fetchone()[0] == "reviewed.zip"
        )
    finally:
        store.close()
