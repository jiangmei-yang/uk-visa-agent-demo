"""Incremental discovery and strict metadata scoping before body access."""

from __future__ import annotations

from email.message import Message
from email.utils import getaddresses

from visa_agent.channels.gmail import (
    GmailAdapter,
    GmailHistoryExpiredError,
    GmailHistoryPage,
    GmailMessagePage,
    GmailMessageUnavailableError,
)
from visa_agent.channels.gmail_sync import GmailSyncJournal


def discover_messages(adapter: GmailAdapter, journal: GmailSyncJournal, query: str,
                      max_pages: int = 10) -> bool:
    """Bound work per cycle, not the lifetime mailbox size. False prohibits dispatch."""
    if max_pages < 1:
        raise ValueError("Discovery requires a positive page budget")
    state = journal.checkpoint()
    if state is None:
        state = journal.start_full(adapter.current_history_id(), None)
    elif state.phase == "rescan":
        # Fetch a fresh anchor before paging. A provider failure leaves the request pending.
        state = journal.start_full(adapter.current_history_id(), state)
    for _ in range(max_pages):
        page: GmailMessagePage | GmailHistoryPage
        if state.phase == "full":
            page = adapter.list_message_page(query, state.page_token)
        else:
            try:
                page = adapter.list_added_history_page(state.history_id, state.page_token)
            except GmailHistoryExpiredError:
                state = journal.start_full(adapter.current_history_id(), state)
                continue
        state = journal.commit_page(state, page)
        if state.phase == "ready":
            return True
    return False


def scope_rejection(message: Message, sender: str, mailbox: str, subject: str | None) -> str | None:
    senders = [address.casefold() for _, address in getaddresses(message.get_all("From", []))]
    recipients = [address.casefold() for _, address in getaddresses(message.get_all("To", []))]
    if senders != [sender.casefold()] or mailbox.casefold() not in recipients:
        return "OUTSIDE_REGISTERED_CORRESPONDENCE"
    if len(message.get_all("Subject", [])) > 1:
        return "AMBIGUOUS_SUBJECT"
    if subject is not None and str(message.get("Subject", "")).removeprefix("Re: ") != subject:
        return "OUTSIDE_REGISTERED_SUBJECT"
    if (any(str(value).casefold() != "no" for value in message.get_all("Auto-Submitted", []))
            or message.get_all("List-Id")
            or any(str(value).casefold() in {"bulk", "list", "junk"}
                   for value in message.get_all("Precedence", []))):
        return "AUTOMATIC_OR_LIST_MESSAGE"
    return None


def ordered_candidates(adapter: GmailAdapter, journal: GmailSyncJournal, *, sender: str,
                       mailbox: str, after: int, subject: str | None) -> list[str]:
    state = journal.checkpoint()
    if state is None or state.phase != "ready":
        raise ValueError("Cannot process candidates before discovery finishes")
    candidates: list[tuple[int, str]] = []
    for identifier in journal.pending_ids():
        try:
            metadata = adapter.get_intake_metadata(identifier)
        except GmailMessageUnavailableError:
            # Retain the unknown message and global dispatch hold. Other scoped
            # messages (including pauses/corrections) must not be starved by it.
            journal.record_metadata_unavailable(identifier)
            continue
        if metadata.get("id") != identifier:
            raise ValueError("Gmail metadata does not match requested message")
        journal.metadata_available(identifier)
        headers = Message()
        for header in metadata.get("payload", {}).get("headers", []):
            headers[header["name"]] = header["value"]
        reason = scope_rejection(headers, sender, mailbox, subject)
        if set(metadata.get("labelIds", [])) & {"SPAM", "TRASH", "DRAFT"}:
            reason = "EXCLUDED_MAILBOX_LABEL"
        if reason:
            journal.acknowledge(identifier, "ignored", reason)
            continue
        timestamp = metadata.get("internalDate")
        if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdecimal():
            raise ValueError("Gmail candidate lacks a trustworthy receipt timestamp")
        received_ms = int(timestamp)
        if received_ms <= after * 1000:
            journal.acknowledge(identifier, "ignored", "BEFORE_ACTIVATION")
            continue
        candidates.append((received_ms, identifier))
    return [identifier for _, identifier in sorted(candidates)]
