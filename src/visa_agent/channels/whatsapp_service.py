"""Supervised WhatsApp intake/reply cycles; final packs remain operator-reviewed."""

from datetime import datetime
from typing import Any

from visa_agent.channels.inbound_worker import InboundEventWorker, InboundWorkflow
from visa_agent.channels.outbound import OutboxDispatcher, PermanentChannelError, ReplyRequest
from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppSender
from visa_agent.storage.sqlite import SQLiteStore

CHANNEL = "whatsapp_twilio"
REPLY_TYPES = ("blocked", "awaiting_profile_confirmation", "awaiting_confirmation")


def intake_unfinished(store: SQLiteStore) -> bool:
    return store.connection.execute(
        "SELECT 1 FROM inbound_queue WHERE channel=? AND status!='PROCESSED' LIMIT 1", (CHANNEL,),
    ).fetchone() is not None


class CurrentWhatsAppSender(TwilioWhatsAppSender):
    def __init__(self, client: Any, service_address: str, callback_url: str, store: SQLiteStore) -> None:
        super().__init__(client, service_address, callback_url)
        self.store = store

    def send(self, request: ReplyRequest) -> str:
        row = self.store.connection.execute(
            "SELECT case_id, message_type FROM outbox WHERE id=? AND channel=?",
            (request.outbox_id, CHANNEL),
        ).fetchone()
        if row is None or row["message_type"] not in REPLY_TYPES or request.attachment is not None:
            raise PermanentChannelError("Automatic WhatsApp final delivery is not enabled")
        latest = self.store.connection.execute(
            "SELECT id FROM outbox WHERE case_id=? ORDER BY rowid DESC LIMIT 1", (row["case_id"],),
        ).fetchone()
        if latest["id"] != request.outbox_id:
            raise PermanentChannelError("Obsolete WhatsApp reply withheld")
        if intake_unfinished(self.store) or self.store.has_unreviewed_held_updates(row["case_id"]):
            raise PermanentChannelError("New or held intake requires review before replying")
        return super().send(request)


def run_cycle(store: SQLiteStore, workflow: InboundWorkflow, sender: CurrentWhatsAppSender,
              now: datetime, *, inbound_limit: int = 20) -> dict[str, object]:
    if inbound_limit < 1:
        raise ValueError("Inbound limit must be positive")
    dispatcher = OutboxDispatcher(store, sender, channel=CHANNEL, allowed_message_types=REPLY_TYPES)
    reconciled = dispatcher.reconcile_sending(sender, now)
    processed = InboundEventWorker(store, workflow, channel=CHANNEL).process_due(now, limit=inbound_limit)
    if intake_unfinished(store):
        return {"phase": "intake_wait", "processed": len(processed), "dispatched": 0,
                "reconciled": len(reconciled)}
    with store.connection:
        withheld = store.connection.execute("""
            UPDATE outbox AS old SET status='FAILED', last_error='Obsolete unsent WhatsApp reply withheld'
            WHERE channel=? AND status='PENDING' AND attempt_count=0
              AND message_type IN ('blocked','awaiting_profile_confirmation','awaiting_confirmation')
              AND EXISTS (SELECT 1 FROM outbox newer WHERE newer.case_id=old.case_id AND newer.rowid>old.rowid)
        """, (CHANNEL,)).rowcount
    dispatched = dispatcher.dispatch_due(now, limit=1)
    return {"phase": "idle", "processed": len(processed), "withheld": withheld,
            "dispatch_outcomes": [item.status for item in dispatched],
            "dispatched": len(dispatched), "reconciled": len(reconciled)}
