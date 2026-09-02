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
    assert "See how the adviser handled the case" in body
    assert "Service response:" in body
    assert body.index("Current outcome") < body.index("Delivery gate")
    assert "<details>" in body
    assert "Delivery gate" in body
    assert "Active evidence ledger" in body
    assert download.media_type == "application/zip"


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
