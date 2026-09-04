from __future__ import annotations

from fastapi import FastAPI

from visa_agent.web import health, twilio_status_webhook, twilio_whatsapp_webhook

app = FastAPI(title="UK Visa Agent Provider Webhook")
app.add_api_route("/health", health, methods=["GET"])
app.add_api_route("/webhooks/twilio/whatsapp/status", twilio_status_webhook, methods=["POST"], status_code=204)
app.add_api_route(
    "/webhooks/twilio/whatsapp",
    twilio_whatsapp_webhook,
    methods=["POST"],
    status_code=204,
)
