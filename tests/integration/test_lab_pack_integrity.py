from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from visa_agent import web
from visa_agent.config import Settings
from visa_agent.domain.models import InboundEvent
from visa_agent.lab import get_lab_state, lab_paths, process_lab_step
from visa_agent.storage.sqlite import SQLiteStore


def _get(path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


@pytest.fixture
def ready_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, str, Path]:
    settings = Settings(
        database_path=tmp_path / "isolated.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    monkeypatch.setattr(web, "settings", settings)
    for step in (1, 2, 3):
        state = process_lab_step(settings, step)
    assert state["synthetic"] is True
    assert state["mode"] == "deterministic_fixture"
    assert state["pack_available"] is True
    store = SQLiteStore(lab_paths(settings).database)
    try:
        case = store.list_cases()[0]
        assert case.delivery_path is not None
        return settings, case.id, Path(case.delivery_path)
    finally:
        store.close()


@pytest.mark.parametrize(
    "change",
    [
        "modified_bytes",
        "missing_archive",
        "missing_registry",
        "different_registry_path",
        "different_revision",
        "outside_output_root",
        "symlink_escape",
        "confirmation_revoked",
        "held_update",
    ],
)
def test_lab_state_and_download_withhold_unverified_pack(
    ready_lab: tuple[Settings, str, Path], tmp_path: Path, change: str
) -> None:
    settings, case_id, archive = ready_lab
    original_bytes = archive.read_bytes()
    store = SQLiteStore(lab_paths(settings).database)
    try:
        case = store.get_case(case_id)
        assert case is not None
        if change == "modified_bytes":
            archive.write_bytes(b"modified synthetic archive")
        elif change == "missing_archive":
            archive.unlink()
        elif change in {"outside_output_root", "symlink_escape"}:
            outside = tmp_path / "outside-guided-output.zip"
            outside.write_bytes(original_bytes)
            if change == "symlink_escape":
                archive.unlink()
                archive.symlink_to(outside)
            else:
                case.delivery_path = str(outside)
                store.save_case(case)
                with store.connection:
                    store.connection.execute(
                        "UPDATE deliveries SET path=? WHERE case_id=?", (str(outside), case_id)
                    )
        elif change == "confirmation_revoked":
            case.final_summary_confirmed = False
            store.save_case(case)
        elif change == "held_update":
            event = InboundEvent(
                id="isolated-lab-held-correction",
                external_thread_id=case.external_thread_id,
                sender=case.applicant_contact,
                subject="Synthetic correction",
                body="Please review a correction before using the old pack.",
                received_at=datetime(2026, 9, 4, tzinfo=UTC),
            )
            store.record_rejected_event(
                event_id=event.id,
                case_id=case.id,
                thread_id=case.external_thread_id,
                reason_code="FINALIZED_CASE_NEW_EVENT",
                detail="Synthetic lab revision is required",
                held_event=event,
            )
        else:
            with store.connection:
                if change == "missing_registry":
                    store.connection.execute("DELETE FROM deliveries WHERE case_id=?", (case_id,))
                elif change == "different_registry_path":
                    store.connection.execute(
                        "UPDATE deliveries SET path=? WHERE case_id=?",
                        (str(archive.with_name("different.zip")), case_id),
                    )
                else:
                    store.connection.execute(
                        "UPDATE deliveries SET case_revision=case_revision+1 WHERE case_id=?",
                        (case_id,),
                    )
    finally:
        store.close()

    # Both reads reopen the isolated SQLite state, so stale in-memory readiness cannot pass.
    state = get_lab_state(settings)
    response = _get("/api/lab/pack")
    assert state["processed_steps"] == 3
    assert state["pack_available"] is False
    assert response.status_code == 404
    assert response.headers.get("content-type") != "application/zip"
    if change not in {"modified_bytes", "missing_archive"}:
        assert archive.read_bytes() == original_bytes  # Withholding does not delete old evidence.


def test_verified_lab_archive_survives_reopen_and_downloads_exact_bytes(
    ready_lab: tuple[Settings, str, Path],
) -> None:
    settings, _, archive = ready_lab
    expected = archive.read_bytes()
    assert get_lab_state(settings)["pack_available"] is True
    response = _get("/api/lab/pack")
    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"


def test_lab_response_uses_bytes_verified_before_a_file_change(
    ready_lab: tuple[Settings, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, _, archive = ready_lab
    expected = archive.read_bytes()
    original_check = web.get_lab_pack

    def change_after_check(current_settings: Settings):
        verified = original_check(current_settings)
        assert verified is not None
        archive.write_bytes(b"changed after verification; do not serve these bytes")
        return verified

    monkeypatch.setattr(web, "get_lab_pack", change_after_check)
    response = _get("/api/lab/pack")
    assert response.status_code == 200
    assert response.content == expected
    assert get_lab_state(settings)["pack_available"] is False


def test_lab_unreadable_archive_fails_closed(
    ready_lab: tuple[Settings, str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, _, archive = ready_lab
    original_read_bytes = Path.read_bytes

    def fail_archive_read(path: Path) -> bytes:
        if path == archive.resolve():
            raise PermissionError("Synthetic archive read denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_archive_read)
    assert get_lab_state(settings)["pack_available"] is False
    assert _get("/api/lab/pack").status_code == 404
