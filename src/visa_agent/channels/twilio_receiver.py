from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppWebhook
from visa_agent.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class TwilioReceiveOutcome:
    event_id: str
    queued: bool


class TwilioWebhookReceiver:
    """Authenticate/normalise the webhook, durably enqueue it, then return immediately."""

    def __init__(self, boundary: TwilioWhatsAppWebhook, store: SQLiteStore) -> None:
        self.boundary = boundary
        self.store = store

    def receive(
        self,
        form: dict[str, str],
        signature: str,
        *,
        received_at: datetime | None = None,
    ) -> TwilioReceiveOutcome:
        parsed = self.boundary.parse(form, signature, received_at=received_at)
        return TwilioReceiveOutcome(
            event_id=parsed.event.id,
            queued=self.store.enqueue_inbound(parsed.event),
        )
