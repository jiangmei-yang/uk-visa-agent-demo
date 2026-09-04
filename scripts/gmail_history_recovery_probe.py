"""Real read-only Gmail history recovery using an isolated, operator-selected cursor.

Does not edit the live journal, process applicant bodies, create outbox rows or send mail.
The saved OAuth client may refresh normally; no credentials are copied into the report.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

from visa_agent.channels.gmail import (
    GmailAdapter,
    GmailHistoryExpiredError,
    GmailHistoryPage,
    GmailMessagePage,
)
from visa_agent.channels.gmail_auth import build_gmail_service
from visa_agent.channels.gmail_intake import discover_messages
from visa_agent.channels.gmail_sync import GmailSyncJournal


class ObservedAdapter(GmailAdapter):
    history_calls = 0
    full_pages = 0
    expired_responses = 0

    def list_message_page(self, query: str, page_token: str | None = None) -> GmailMessagePage:
        self.full_pages += 1
        return super().list_message_page(query, page_token)

    def list_added_history_page(self, start_history_id: str,
                                page_token: str | None = None) -> GmailHistoryPage:
        self.history_calls += 1
        try:
            return super().list_added_history_page(start_history_id, page_token)
        except GmailHistoryExpiredError:
            self.expired_responses += 1
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--after", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start-history-id", default="1",
                        help="Explicit probe cursor; provider rejection is observed, not assumed")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Preserve prior evidence; use a new report path")
    for address in (args.sender, args.mailbox):
        if (parseaddr(address)[1] != address or "@" not in address
                or any(char.isspace() or char in '\"()' for char in address)):
            parser.error("Supply a single plain mailbox address")
    if args.after <= 0:
        parser.error("Activation timestamp must be positive")
    if (not args.start_history_id.isascii() or not args.start_history_id.isdecimal()
            or int(args.start_history_id) <= 0):
        parser.error("Probe history ID must be a positive decimal string")
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "evidence_class": "real provider read-only requests; isolated operator-selected checkpoint",
        "requested_checkpoint": args.start_history_id,
        "authorized_mailbox_verified": False,
        "history_404_observed": False,
        "recovered_to_ready": False,
        "live_worker_state_changed": False,
        "raw_bodies_read": 0,
        "sends": 0,
        "limitations": [
            "An operator-selected probe cursor, not a naturally expired live-service cursor.",
            "No real multi-page backlog, message processing, dispatch or recipient-delivery claim.",
            "No quota exhaustion, provider 5xx, credential revocation or live journal modification.",
        ],
    }
    try:
        service = build_gmail_service(Path(".secrets/gmail_credentials.json"),
                                     Path(".secrets/gmail_token.json"), interactive=False)
        if service.users().getProfile(userId="me").execute()["emailAddress"] != args.mailbox:
            raise ValueError("Authorized mailbox mismatch")
        report["authorized_mailbox_verified"] = True
        adapter = ObservedAdapter(service)
        query = f"from:{args.sender} to:{args.mailbox} after:{args.after}"
        with tempfile.TemporaryDirectory(prefix="visa-gmail-history-probe-") as directory:
            journal = GmailSyncJournal(Path(directory) / "sync.db", query)
            try:
                state = journal.start_full(args.start_history_id, None)
                journal.commit_page(state, GmailMessagePage((), None))
                complete = discover_messages(adapter, journal, query, max_pages=10)
                checkpoint = journal.checkpoint()
                report.update({
                    "history_404_observed": adapter.expired_responses > 0,
                    "history_calls": adapter.history_calls,
                    "full_pages": adapter.full_pages,
                    "pending_candidates": len(journal.pending_ids()),
                    "recovered_to_ready": complete and checkpoint is not None
                        and checkpoint.phase == "ready"
                        and checkpoint.history_id != args.start_history_id,
                })
            finally:
                journal.close()
    except Exception as error:
        report["error_type"] = type(error).__name__
        status = getattr(getattr(error.__cause__, "resp", None), "status", None)
        if isinstance(status, int):
            report["http_status"] = status
    report["passed"] = bool(report["history_404_observed"] and report["recovered_to_ready"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit("Probe did not prove the narrow history-recovery criterion; evidence preserved")


if __name__ == "__main__":
    main()
