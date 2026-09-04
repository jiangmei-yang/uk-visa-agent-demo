from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from visa_agent import web
from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.domain.models import InboundEvent
from visa_agent.storage.sqlite import SQLiteStore


def _post(path: str, **kwargs: Any) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, **kwargs)

    return asyncio.run(request())


def _get(path: str, **kwargs: Any) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, **kwargs)

    return asyncio.run(request())


def _delete(path: str, **kwargs: Any) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=web.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.delete(path, **kwargs)

    return asyncio.run(request())


def test_review_console_and_pack_download(tmp_path: Path) -> None:
    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    result = run_demo(test_settings, reset=True)
    web.settings = test_settings

    with ThreadPoolExecutor(max_workers=1) as executor:
        page = executor.submit(web.index).result()
        download = executor.submit(web.get_pack, result.case.id).result()
    assert page.status_code == 200
    body = page.body.decode("utf-8")
    assert "The application pack is ready for adviser review" in body
    assert "Recorded sample walkthrough" in body
    assert "Service response:" in body
    assert "Event ends 16 Sep" in body
    assert "12 deterministic case checks" in body
    assert "no email sent here" in body
    assert "not the current case’s message history" in body
    assert body.index("Current outcome") < body.index("Delivery gate")
    assert "<details>" in body
    assert "Delivery gate" in body
    assert "Active evidence ledger" in body
    assert download.media_type == "application/zip"
    assert bytes(download.body) == result.package_path.read_bytes()
    assert download.headers["cache-control"] == "no-store"


def test_guided_lab_runs_real_workflow_one_step_at_a_time(tmp_path: Path) -> None:
    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    web.settings = test_settings

    page = _get("/try")
    initial = _post("/api/lab/reset").json()
    assert page.status_code == 200
    assert "Try the safety workflow yourself" in page.text
    assert initial["processed_steps"] == 0
    assert initial["pack_available"] is False

    first = _post("/api/lab/steps/1")
    assert first.status_code == 200
    first_state = first.json()
    assert first_state["processed_steps"] == 1
    assert {item["code"] for item in first_state["open_blockers"]} == {
        "DATE_CONFLICT",
        "MISSING_CERTIFIED_TRANSLATION",
    }
    assert "DEMO_FACTS" not in first_state["conversation"][0]["body"]
    assert first_state["pack_available"] is False

    out_of_order = _post("/api/lab/steps/3")
    assert out_of_order.status_code == 409

    second_state = _post("/api/lab/steps/2").json()
    assert second_state["processed_steps"] == 2
    assert second_state["open_blockers"] == []
    assert second_state["gate"]["checks"]["applicant_explicitly_confirmed_final_summary"] is False

    final_state = _post("/api/lab/steps/3").json()
    assert final_state["processed_steps"] == 3
    assert final_state["pack_available"] is True
    assert all(final_state["gate"]["checks"].values())
    assert _get("/api/lab/pack").status_code == 200

    reset = _post("/api/lab/reset").json()
    assert reset["processed_steps"] == 0
    assert _get("/api/lab/pack").status_code == 404


def test_pack_download_is_withheld_if_current_gate_fails(tmp_path: Path) -> None:
    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    result = run_demo(test_settings, reset=True)
    store = SQLiteStore(test_settings.database_path)
    try:
        case = store.get_case(result.case.id)
        assert case is not None and case.delivery_path is not None
        case.final_summary_confirmed = False
        store.save_case(case)
    finally:
        store.close()

    web.settings = test_settings
    with pytest.raises(HTTPException) as error:
        web.get_pack(result.case.id)
    assert error.value.status_code == 409


def test_pack_download_is_withheld_after_a_new_held_update(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from visa_agent.domain.models import InboundEvent

    test_settings = Settings(database_path=tmp_path / "visa.db", output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    result = run_demo(test_settings, reset=True)
    store = SQLiteStore(test_settings.database_path)
    case = store.get_case(result.case.id)
    event = InboundEvent(id="new-correction", external_thread_id=case.external_thread_id,
        sender=case.applicant_contact, body="Please do not use the old dates.", subject="Correction",
        received_at=datetime.now(UTC))
    store.record_rejected_event(event_id=event.id, case_id=case.id, thread_id=case.external_thread_id,
        reason_code="FINALIZED_CASE_NEW_EVENT", detail="Revision required", held_event=event)
    store.close()
    web.settings = test_settings
    with pytest.raises(HTTPException) as error:
        web.get_pack(case.id)
    assert error.value.status_code == 409
    assert Path(case.delivery_path).exists()  # Retain the historical artifact; do not destroy it.
    assert 'data-download href=' not in web.index().body.decode()


@pytest.mark.parametrize("change", ["modified_bytes", "missing_registry", "different_path", "different_revision"])
def test_pack_download_rejects_unverified_archive(tmp_path: Path, change: str) -> None:
    test_settings = Settings(database_path=tmp_path / "visa.db", output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    result = run_demo(test_settings, reset=True)
    store = SQLiteStore(test_settings.database_path)
    if change == "modified_bytes":
        result.package_path.write_bytes(b"changed archive")
    else:
        with store.connection:
            if change == "missing_registry":
                store.connection.execute("DELETE FROM deliveries")
            elif change == "different_revision":
                store.connection.execute("UPDATE deliveries SET case_revision=2")
            else:
                store.connection.execute("UPDATE deliveries SET path='unrelated.zip'")
    store.close()
    web.settings = test_settings
    with pytest.raises(HTTPException) as error:
        web.get_pack(result.case.id)
    assert error.value.status_code == 409
    assert 'data-download href=' not in web.index().body.decode()


def test_case_can_be_exported_and_exactly_confirmed_for_local_deletion(tmp_path: Path) -> None:
    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    result = run_demo(test_settings, reset=True)
    web.settings = test_settings

    # Retained revision paths belong to this exact case; unrelated artifacts stay.
    previous = test_settings.output_dir / "previous-revision.zip"
    previous.write_bytes(b"Previously registered fixture archive")
    unrelated = test_settings.output_dir / "unrelated-case.zip"
    unrelated.write_bytes(b"Unrelated fixture archive")
    store = SQLiteStore(test_settings.database_path)
    with store.connection:
        store.connection.execute(
            "INSERT INTO delivery_versions(case_id,path,sha256,case_revision) VALUES (?,?,?,?)",
            (result.case.id, str(previous), "test-only-previous-digest", 1),
        )
    store.close()

    exported = _get(f"/api/cases/{result.case.id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith('"synthetic-case-export.json"')
    assert exported.json()["case"]["id"] == result.case.id
    assert len(exported.json()["outbound_messages"]) == 3
    assert "Raw processed inbound messages are not retained" in exported.json()["data_note"]

    unconfirmed = _delete(f"/api/cases/{result.case.id}")
    assert unconfirmed.status_code == 400
    assert result.package_path.is_file()

    deleted = _delete(
        f"/api/cases/{result.case.id}",
        headers={"X-Confirm-Case-Deletion": result.case.id},
    )
    assert deleted.status_code == 204
    assert not result.package_path.exists()
    assert not previous.exists() and unrelated.exists()
    assert not (test_settings.output_dir / result.case.id).exists()
    assert _get(f"/api/cases/{result.case.id}").status_code == 404
    assert _get(f"/api/cases/{result.case.id}/export").status_code == 404


def test_whatsapp_webhook_is_disabled_without_explicit_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WEBHOOK_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)

    response = _post("/webhooks/twilio/whatsapp", data={"MessageSid": "SM1"})

    assert response.status_code == 503


def test_configured_whatsapp_webhook_queues_event_and_returns_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from visa_agent.channels.twilio_whatsapp import TwilioWebhookResult, TwilioWhatsAppWebhook

    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    web.settings = test_settings
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-synthetic")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "synthetic-token")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv(
        "TWILIO_WEBHOOK_PUBLIC_URL",
        "https://example.test/webhooks/twilio/whatsapp",
    )

    def fake_parse(
        self: TwilioWhatsAppWebhook,
        form: dict[str, str],
        signature: str,
        *,
        received_at: datetime | None = None,
    ) -> TwilioWebhookResult:
        del self, received_at
        assert signature == "synthetic-signature"
        return TwilioWebhookResult(
            event=InboundEvent(
                id=form["MessageSid"],
                channel="whatsapp_twilio",
                external_thread_id=form["From"],
                sender=form["From"],
                subject="WhatsApp conversation",
                body=form["Body"],
                received_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
            ),
            service_address=form["To"],
        )

    monkeypatch.setattr(TwilioWhatsAppWebhook, "parse", fake_parse)
    response = _post(
        "/webhooks/twilio/whatsapp",
        data={
            "MessageSid": "SM-webhook-1",
            "From": "whatsapp:+85255550123",
            "To": "whatsapp:+14155238886",
            "Body": "synthetic body",
            "NumMedia": "0",
        },
        headers={"X-Twilio-Signature": "synthetic-signature"},
    )

    assert response.status_code == 204
    store = SQLiteStore(test_settings.database_path)
    try:
        queued = store.list_inbound_queue()
        assert len(queued) == 1
        assert queued[0]["id"] == "SM-webhook-1"
        assert queued[0]["status"] == "PENDING"
    finally:
        store.close()


def test_whatsapp_webhook_rejects_duplicate_form_keys_before_signature_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-synthetic")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "synthetic-token")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv(
        "TWILIO_WEBHOOK_PUBLIC_URL",
        "https://example.test/webhooks/twilio/whatsapp",
    )

    response = _post(
        "/webhooks/twilio/whatsapp",
        content="MessageSid=SM1&MessageSid=SM2",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert "Duplicate" in response.json()["detail"]
