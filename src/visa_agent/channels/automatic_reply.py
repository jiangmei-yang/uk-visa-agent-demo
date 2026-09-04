"""Bounded automatic service replies; final packs still require reviewed dispatch."""

from dataclasses import replace
from email.utils import getaddresses

from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.outbound import PermanentChannelError, ReplyRequest
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.storage.sqlite import SQLiteStore


class AutomaticGmailReplySender(GmailReplySender):
    def __init__(self, adapter: GmailAdapter, store: SQLiteStore, allowed_sender: str) -> None:
        super().__init__(adapter)
        self.store = store
        self.allowed_sender = allowed_sender

    def send(self, request: ReplyRequest) -> str:
        row = self.store.connection.execute(
            "SELECT case_id, message_type FROM outbox WHERE id = ?", (request.outbox_id,)
        ).fetchone()
        recipients = [address.casefold() for _, address in getaddresses([request.recipient])]
        if row is None or recipients != [self.allowed_sender.casefold()]:
            raise PermanentChannelError("Automatic reply is outside the registered sender scope")
        if request.attachment is not None or row["message_type"] == "ready":
            raise PermanentChannelError("Final delivery requires reviewed dispatch")
        latest = self.store.connection.execute(
            "SELECT id FROM outbox WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (row["case_id"],)
        ).fetchone()
        if latest["id"] != request.outbox_id:
            raise PermanentChannelError("Obsolete reply withheld in favour of current case state")
        case = self.store.get_case(row["case_id"])
        if case is None or row["message_type"] not in {
            "blocked", "awaiting_profile_confirmation", "awaiting_confirmation"
        }:
            raise PermanentChannelError("Unsupported automatic reply plan")
        body = deterministic_fallback_message(case, row["message_type"])
        # Persist the exact body before the side effect, including on a send-response crash.
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE outbox SET payload = ? WHERE id = ? AND status = 'SENDING'",
                (body, request.outbox_id),
            )
        return super().send(replace(request, body=body))
