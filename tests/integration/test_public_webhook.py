from __future__ import annotations

from visa_agent.public_webhook import app


def test_public_gateway_exposes_only_health_and_twilio_webhook() -> None:
    routes = {route.path: route for route in app.routes}

    assert "/health" in routes
    assert "/webhooks/twilio/whatsapp" in routes
    assert "/" not in routes
    assert "/api/cases" not in routes
    assert routes["/health"].methods == {"GET"}
    assert routes["/webhooks/twilio/whatsapp"].methods == {"POST"}
