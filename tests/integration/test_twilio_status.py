from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from visa_agent import web
from visa_agent.channels.twilio_status import status_callback_url
from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.public_webhook import app
from visa_agent.storage.sqlite import SQLiteStore

TOKEN = "synthetic-test-token"
BASE = "https://example.test/webhooks/twilio/whatsapp/status"
SID = "SM" + "a" * 32


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(database_path=tmp_path / "db.sqlite", output_dir=tmp_path / "output")
    run_demo(settings, reset=True)
    monkeypatch.setattr(web, "settings", settings)
    for key, value in {"TWILIO_ACCOUNT_SID": "AC-test", "TWILIO_AUTH_TOKEN": TOKEN,
                       "TWILIO_WHATSAPP_FROM": "whatsapp:+10000000001",
                       "TWILIO_STATUS_CALLBACK_PUBLIC_URL": BASE}.items():
        monkeypatch.setenv(key, value)
    store = SQLiteStore(settings.database_path)
    row = store.claim_pending_outbox(datetime(2026, 9, 4, tzinfo=UTC), limit=1)[0]
    with store.connection:
        store.connection.execute("UPDATE outbox SET id = 'out-status-test', channel = 'whatsapp_twilio', recipient = ? WHERE id = ?",
                                 ("whatsapp:+10000000002", row["id"]))
    yield store, "out-status-test", TestClient(app)
    store.close()


def post(client, outbox_id, status="delivered", *, changes=None, bad_signature=False):
    form = {"AccountSid": "AC-test", "From": "whatsapp:+10000000001",
            "To": "whatsapp:+10000000002", "MessageSid": SID, "MessageStatus": status}
    form.update(changes or {})
    signature = RequestValidator(TOKEN).compute_signature(status_callback_url(BASE, outbox_id), form)
    return client.post("/webhooks/twilio/whatsapp/status?" + urlencode({"outbox_id": outbox_id}),
                       data=form, headers={"X-Twilio-Signature": "invalid" if bad_signature else signature})


def test_real_sdk_signature_and_out_of_order_dedup(prepared):
    store, identifier, client = prepared
    assert store.delivery_receipt_status(identifier) == "unconfirmed"
    for status in ["delivered", "queued", "sent", "delivered", "read", "delivered"]:
        assert post(client, identifier, status).status_code == 204
    assert store.delivery_receipt_status(identifier) == "read"
    assert store.connection.execute("SELECT COUNT(*) FROM channel_delivery_receipts").fetchone()[0] == 4
    assert store.list_sending_outbox()[0]["id"] == identifier  # acceptance is a separate fact
    assert client.get("/api/outbox/delivery-receipts").status_code == 404
    private = TestClient(web.app).get("/api/outbox/delivery-receipts").json()
    assert private[0]["delivery_status"] == "read"


@pytest.mark.parametrize("changes,code", [
    ({"AccountSid": "wrong"}, 403), ({"From": "whatsapp:+19999999999"}, 403),
    ({"To": "whatsapp:+19999999999"}, 400), ({"MessageSid": "bad"}, 400),
    ({"MessageStatus": "invented"}, 400), ({"ErrorCode": "private text"}, 400),
])
def test_wrong_scope_or_malformed_receipt_cannot_mutate_state(prepared, changes, code):
    store, identifier, client = prepared
    assert post(client, identifier, changes=changes).status_code == code
    assert store.delivery_receipt_status(identifier) == "unconfirmed"


def test_bad_signature_and_duplicate_query_are_rejected(prepared):
    store, identifier, client = prepared
    assert post(client, identifier, bad_signature=True).status_code == 403
    assert client.post(f"/webhooks/twilio/whatsapp/status?outbox_id={identifier}&outbox_id={identifier}").status_code == 400
    assert store.delivery_receipt_status(identifier) == "unconfirmed"


def test_conflicting_terminal_receipts_are_not_reported_as_delivered(prepared):
    store, identifier, client = prepared
    assert post(client, identifier, "failed", changes={"ErrorCode": "63016"}).status_code == 204
    assert post(client, identifier, "sent").status_code == 204
    assert store.delivery_receipt_status(identifier) == "failed"
    assert post(client, identifier, "delivered").status_code == 204
    assert store.delivery_receipt_status(identifier) == "conflict"


def test_callback_before_response_cannot_hide_sid_conflict(prepared):
    store, identifier, client = prepared
    assert post(client, identifier).status_code == 204
    store.mark_outbox_sent(identifier, "SM" + "b" * 32, datetime.now(UTC))
    assert store.delivery_receipt_status(identifier) == "conflict"
    assert post(client, identifier).status_code == 400


def test_deleting_case_removes_receipts(prepared):
    store, identifier, client = prepared
    assert post(client, identifier).status_code == 204
    row = next(r for r in store.list_outbox() if r["id"] == identifier)
    store.delete_case(row["case_id"])
    assert store.delivery_receipt_status(identifier) == "unconfirmed"
