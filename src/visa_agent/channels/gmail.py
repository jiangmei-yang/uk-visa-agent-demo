from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any


class GmailAdapter:
    """Thin Gmail API boundary; OAuth service creation lives in deployment setup."""

    def __init__(self, service: Any, user_id: str = "me") -> None:
        self.service = service
        self.user_id = user_id

    def list_message_ids(self, query: str, max_results: int = 20) -> list[str]:
        response = (
            self.service.users()
            .messages()
            .list(userId=self.user_id, q=query, maxResults=max_results)
            .execute()
        )
        return [str(item["id"]) for item in response.get("messages", [])]

    def get_message(self, message_id: str) -> dict[str, Any]:
        result = (
            self.service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="full")
            .execute()
        )
        return dict(result)

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        result = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId=self.user_id, messageId=message_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(str(result["data"]).encode("ascii"))

    def send_reply(
        self,
        recipient: str,
        subject: str,
        body: str,
        thread_id: str,
        in_reply_to: str,
        references: str,
        attachment: tuple[str, bytes] | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message["In-Reply-To"] = in_reply_to
        message["References"] = references
        message.set_content(body)
        if attachment:
            filename, content = attachment
            message.add_attachment(content, maintype="application", subtype="zip", filename=filename)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = (
            self.service.users()
            .messages()
            .send(userId=self.user_id, body={"raw": raw, "threadId": thread_id})
            .execute()
        )
        return dict(result)
