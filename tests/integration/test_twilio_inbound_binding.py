"""Real SDK-signed, fictional inbound scope tests; no Twilio/network access."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from visa_agent import web
from visa_agent.channels.twilio_whatsapp import TwilioMediaDownloader
from visa_agent.channels.whatsapp_service import CurrentWhatsAppSender, run_cycle
from visa_agent.config import Settings
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.public_webhook import app as public_app
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

ACCOUNT = "AC" + "a" * 32
TOKEN = "fictional-local-signature-token"
SERVICE = "whatsapp:+10000000001"
APPLICANT = "whatsapp:+10000000002"
BASE = "https://example.test/webhooks/twilio/whatsapp"
MESSAGE = "SM" + "b" * 32


class CaptureModel:
    def __init__(self):
        self.events = []

    def extract_case_patch(self, event):
        self.events.append(event)
        return CasePatch(updates=[], ambiguities=[])

    render_message = staticmethod(deterministic_fallback_message)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network use is forbidden in inbound binding tests")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    settings = Settings(database_path=tmp_path / "channel.db", output_dir=tmp_path / "output")
    monkeypatch.setattr(web, "settings", settings)
    for name, value in {"TWILIO_ACCOUNT_SID": ACCOUNT, "TWILIO_AUTH_TOKEN": TOKEN,
                        "TWILIO_WHATSAPP_FROM": SERVICE, "TWILIO_WEBHOOK_PUBLIC_URL": BASE}.items():
        monkeypatch.setenv(name, value)
    downloads, sends = [], []

    def download(self, url):
        downloads.append(url)
        return b"%PDF-1.4 fictional media bytes"

    def send(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(sid="SM" + "c" * 32)

    monkeypatch.setattr(TwilioMediaDownloader, "download", download)
    store = SQLiteStore(settings.database_path)
    model = CaptureModel()
    workflow = WorkflowService(store, load_policy(settings.policy_path), model)
    sender = CurrentWhatsAppSender(SimpleNamespace(messages=SimpleNamespace(create=send)),
                                  SERVICE, BASE + "/status", store)
    yield SimpleNamespace(settings=settings, store=store, model=model, workflow=workflow,
                          sender=sender, downloads=downloads, sends=sends)
    store.close()


def _form(*, media=False):
    result = {"AccountSid": ACCOUNT, "MessageSid": MESSAGE, "From": APPLICANT,
              "To": SERVICE, "Body": "A fictional visitor preparation enquiry.", "NumMedia": "0"}
    if media:
        result.update(NumMedia="1", MediaContentType0="application/pdf",
                      MediaUrl0=f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}/Messages/{MESSAGE}/Media/MEfictional")
    return result


def _post(form, *, app=public_app, bad_signature=False):
    signature = RequestValidator(TOKEN).compute_signature(BASE, form)
    with TestClient(app) as client:
        return client.post("/webhooks/twilio/whatsapp", data=form,
                           headers={"X-Twilio-Signature": "invalid" if bad_signature else signature})


def _assert_no_intake_or_effects(env):
    assert env.store.list_inbound_queue() == []
    assert env.store.counts() == {"cases": 0, "processed_events": 0, "outbox": 0, "deliveries": 0}
    assert env.downloads == env.model.events == env.sends == []
    assert not list(env.settings.output_dir.rglob("*.pdf"))
    # Exercise the real worker/dispatcher too: a rejected HTTP request must not
    # leave work that would reach the model or provider on a later cycle.
    result = run_cycle(env.store, env.workflow, env.sender, datetime.now(UTC))
    assert result["processed"] == result["dispatched"] == 0
    assert env.model.events == env.sends == []


@pytest.mark.parametrize("app", [public_app, web.app], ids=["public-gateway", "private-route"])
def test_matching_sdk_signed_account_and_service_are_queued_once_then_processed(isolated, app):
    response = _post(_form(), app=app)
    assert response.status_code == 204
    assert _post(_form(), app=app).status_code == 204
    queued = isolated.store.list_inbound_queue()
    assert len(queued) == 1 and queued[0]["id"] == MESSAGE and queued[0]["status"] == "PENDING"
    assert isolated.model.events == isolated.sends == isolated.downloads == []

    result = run_cycle(isolated.store, isolated.workflow, isolated.sender, datetime.now(UTC))
    assert result["processed"] == 1 and result["dispatch_outcomes"] == ["SENT"]
    assert len(isolated.model.events) == len(isolated.sends) == 1
    assert isolated.model.events[0].sender == APPLICANT
    assert isolated.sends[0]["from_"] == SERVICE and isolated.sends[0]["to"] == APPLICANT
    snapshot = isolated.store.counts()
    assert _post(_form(), app=app).status_code == 204
    assert run_cycle(isolated.store, isolated.workflow, isolated.sender, datetime.now(UTC))["dispatched"] == 0
    assert isolated.store.counts() == snapshot and len(isolated.sends) == len(isolated.model.events) == 1


@pytest.mark.parametrize("field,value", [
    ("AccountSid", "AC" + "d" * 32), ("AccountSid", None), ("AccountSid", ""),
    ("To", "whatsapp:+10000000099"), ("To", None), ("To", ""),
])
@pytest.mark.parametrize("media", [False, True], ids=["text", "pdf"])
def test_signed_wrong_or_missing_binding_has_no_durable_or_downstream_effect(isolated, field, value, media):
    form = _form(media=media)
    if value is None:
        form.pop(field)
    else:
        form[field] = value
    response = _post(form)
    assert response.status_code == 403
    assert response.json() == {"detail": "Webhook authentication or routing is invalid"}
    assert all(private not in response.text for private in (TOKEN, ACCOUNT, SERVICE, APPLICANT, MESSAGE))
    _assert_no_intake_or_effects(isolated)


def test_correct_binding_does_not_bypass_signature_verification(isolated):
    assert _post(_form(media=True), bad_signature=True).status_code == 403
    _assert_no_intake_or_effects(isolated)


@pytest.mark.parametrize("name", ["TWILIO_ACCOUNT_SID", "TWILIO_WHATSAPP_FROM"])
def test_missing_configured_binding_fails_closed_before_intake(isolated, monkeypatch, name):
    monkeypatch.delenv(name)
    assert _post(_form(media=True)).status_code == 503
    _assert_no_intake_or_effects(isolated)


def test_correct_sdk_signed_media_reaches_download_and_durable_queue(isolated):
    assert _post(_form(media=True)).status_code == 204
    queued = isolated.store.list_inbound_queue()
    assert len(queued) == 1 and queued[0]["status"] == "PENDING"
    assert len(isolated.downloads) == 1
    files = list(isolated.settings.output_dir.rglob("*.pdf"))
    assert len(files) == 1 and files[0].read_bytes() == b"%PDF-1.4 fictional media bytes"
    assert isolated.model.events == isolated.sends == []
