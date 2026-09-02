from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from visa_agent.config import Settings
from visa_agent.domain.models import Case
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.review_ui import render_empty_page, render_page
from visa_agent.storage.sqlite import SQLiteStore

MAX_WEBHOOK_BODY_BYTES = 64 * 1024

settings = Settings.from_env()
policy = load_policy(settings.policy_path)


app = FastAPI(title="UK Visa Agent Review Console")


def load_cases() -> list[Case]:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.list_cases()
    finally:
        request_store.close()


def load_case(case_id: str) -> Case | None:
    request_store = SQLiteStore(settings.database_path)
    try:
        return request_store.get_case(case_id)
    finally:
        request_store.close()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    cases = load_cases()
    if not cases:
        return HTMLResponse(render_empty_page())
    case = cases[0]
    gate = evaluate_gate(case, policy, date.today())
    return HTMLResponse(render_page(case, gate))


@app.get("/api/cases")
def list_cases() -> list[dict[str, object]]:
    return [case.model_dump(mode="json") for case in load_cases()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, object]:
    case = load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.model_dump(mode="json")


@app.get("/api/cases/{case_id}/pack")
def get_pack(case_id: str) -> FileResponse:
    case = load_case(case_id)
    if case is None or case.delivery_path is None:
        raise HTTPException(status_code=404, detail="Review pack is not available")
    gate = evaluate_gate(case, policy, date.today())
    if not gate.allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Review pack is withheld because current safety checks did not pass",
                "failed_checks": gate.reasons,
            },
        )
    path = Path(case.delivery_path).resolve()
    allowed_root = settings.output_dir.resolve()
    if allowed_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Review pack is not available")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policy_version": policy.version}


@app.post("/webhooks/twilio/whatsapp", status_code=204)
async def twilio_whatsapp_webhook(request: Request) -> Response:
    from visa_agent.channels.twilio_receiver import TwilioWebhookReceiver
    from visa_agent.channels.twilio_whatsapp import (
        TwilioMediaDownloader,
        TwilioWhatsAppWebhook,
    )

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    public_url = os.getenv("TWILIO_WEBHOOK_PUBLIC_URL", "")
    if not account_sid or not auth_token or not public_url:
        raise HTTPException(status_code=503, detail="WhatsApp sandbox is not configured")
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook body is too large")
    try:
        pairs = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Webhook form is invalid") from error
    if len({key for key, _ in pairs}) != len(pairs):
        raise HTTPException(status_code=400, detail="Duplicate webhook form field")
    form = dict(pairs)
    signature = request.headers.get("X-Twilio-Signature", "")
    store = SQLiteStore(settings.database_path)
    try:
        boundary = TwilioWhatsAppWebhook(
            auth_token,
            public_url,
            settings.output_dir / "whatsapp_uploads",
            media_downloader=TwilioMediaDownloader(account_sid, auth_token),
        )
        TwilioWebhookReceiver(boundary, store).receive(form, signature)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="Webhook signature is invalid") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=502, detail="Provider media could not be downloaded"
        ) from error
    finally:
        store.close()
    return Response(status_code=204)
