"""Signed, bounded delivery receipts, separate from send acceptance."""

import re
from importlib import import_module
from urllib.parse import urlencode, urlsplit

from visa_agent.storage.sqlite import SQLiteStore

STATUSES = {"queued", "sending", "sent", "delivered", "read", "failed", "undelivered", "canceled"}


def status_callback_url(base: str, outbox_id: str) -> str:
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment
            or parsed.username or parsed.password or "_" in parsed.hostname):
        raise ValueError("Status callback requires a configured HTTPS URL without query or credentials")
    if not re.fullmatch(r"out-[A-Za-z0-9_-]{1,100}", outbox_id):
        raise ValueError("Invalid outbound correlation identifier")
    return base + "?" + urlencode({"outbox_id": outbox_id})


def receive_status(
    store: SQLiteStore, *, base_url: str, outbox_id: str, account_sid: str,
    service_address: str, auth_token: str, form: dict[str, str], signature: str,
) -> None:
    url = status_callback_url(base_url, outbox_id)
    validator = import_module("twilio.request_validator").RequestValidator(auth_token)
    if not signature or not validator.validate(url, form, signature):
        raise PermissionError("Invalid status callback signature")
    if form.get("AccountSid") != account_sid or form.get("From") != service_address:
        raise PermissionError("Callback account or sender mismatch")
    sid = form.get("MessageSid", "")
    status = form.get("MessageStatus", "")
    if form.get("EventType") == "READ" and status == "delivered":
        status = "read"
    error_code = form.get("ErrorCode", "")
    if (not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) or status not in STATUSES
            or (error_code and not re.fullmatch(r"[0-9]{1,10}", error_code))):
        raise ValueError("Unsupported or malformed status receipt")
    store.record_delivery_receipt(outbox_id, sid, status, error_code, form.get("To", ""))
