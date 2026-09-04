"""Gmail trial runner restricted to an operator-allowlisted sender, optionally one subject."""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import nullcontext
from datetime import UTC, date, datetime
from email import message_from_bytes, policy
from email.utils import getaddresses, parseaddr
from pathlib import Path

from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.gmail_auth import build_gmail_service
from visa_agent.channels.gmail_intake import discover_messages, ordered_candidates, scope_rejection
from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.channels.outbound import (
    OutboxDispatcher,
    PermanentChannelError,
    ReconciliationAccessError,
    ReplyRequest,
)
from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.delivery.pack import generate_pack
from visa_agent.documents.natural import NaturalPDFReader
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.privacy.consent import ConsentLedger, ProcessingScope
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


class PackPreparationError(RuntimeError):
    """Committed applicant work still has unresolved materialization; never imply delivery."""


PRIVACY_MESSAGE_TYPES = ("processing_notice", "processing_receipt")


def _resume_consent_deferred(ledger: ConsentLedger, store: SQLiteStore,
                             journal: GmailSyncJournal) -> None:
    for case in store.list_cases():
        if ledger.allowed(case):
            journal.resume_awaiting_consent(ledger.deferred_ids(case.id))


def _scanned_business_ids(journal: GmailSyncJournal, store: SQLiteStore) -> list[str]:
    ordered = journal.consent_scanned_ids()
    # A committed pack retry must not starve a later original correction/pause.
    # Within unprocessed business, retain provider chronology (including deferred mail).
    return ([value for value in ordered if not store.event_processed(value)]
            + [value for value in ordered if store.event_processed(value)])[:100]


def _consent_preflight(adapter: GmailAdapter, ingestion: EmailIngestionBoundary,
                       ledger: ConsentLedger, store: SQLiteStore, journal: GmailSyncJournal,
                       identifiers: list[str], args: argparse.Namespace, policy_version: str) -> None:
    """Scan controls before any model work; checkpoint metadata only across bounded cycles.

    Gmail raw bytes necessarily enter RAM. This path never saves them, decodes an
    attachment, or sends body text to a model. Business messages are refetched by
    original provider ID only after every discovered candidate has been scanned.
    """
    for identifier in identifiers:
        raw = adapter.get_raw_message(identifier)
        if raw.provider_message_id != identifier:
            raise ValueError("Gmail raw response does not match the requested candidate")
        mime = message_from_bytes(raw.raw, policy=policy.default)
        rejection = scope_rejection(mime, args.sender, args.mailbox, args.subject)
        if rejection:
            journal.acknowledge(identifier, "ignored", rejection)
            continue
        result = ingestion.preview(raw.raw, provider_message_id=raw.provider_message_id,
            provider_thread_id=raw.provider_thread_id, channel="gmail",
            received_at=datetime.fromtimestamp(journal.receipt_ms(identifier) / 1000, UTC))
        if result.event is None:
            journal.acknowledge(identifier, "rejected", result.failure_code)
            continue
        journal.record_thread(identifier, raw.provider_thread_id)
        if store.event_processed(identifier):
            # Migration/re-scan is not a new applicant request or a new grant.
            case = store.get_case_by_thread(raw.provider_thread_id)
            if case is not None and ledger.allowed(case):
                journal.acknowledge(identifier, "consent_scanned")
            else:
                journal.acknowledge(identifier, "processed")
                ledger.mark_completed(identifier)
            continue
        decision = ledger.handle(result.event, policy_version)
        if decision.action == "allow":
            journal.acknowledge(identifier, "consent_scanned")
        elif decision.action == "control":
            journal.acknowledge(identifier, "consent_control", "PROCESSING_CONTROL_RECORDED")
        else:
            journal.acknowledge(identifier, "awaiting_consent", "PROCESSING_CONSENT_REQUIRED")
    # A late grant may release earlier deferred mail. Re-scan its original ID in
    # the next cycle before newer business is processed; never forge a new date.
    _resume_consent_deferred(ledger, store, journal)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve", "send-reviewed", "reconcile", "status"))
    parser.add_argument("--after", type=int, help="Required activation Unix timestamp for automatic service")
    parser.add_argument("--sender", required=True)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument(
        "--subject",
        help="Optional exact subject; omit to accept ordinary subjects from the allowed sender",
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--reply-style", choices=("reviewed", "guarded-draft"), default="reviewed",
                        help="Optional revalidated workflow prose for blocked/intake replies only")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--watch", action="store_true", help="Repeat prepare or controlled serve cycles"
    )
    parser.add_argument("--interval", type=int, default=60, help="Polling seconds (minimum 30)")
    parser.add_argument(
        "--crash-after-send",
        action="store_true",
        help="Synthetic crash test: terminate after provider acceptance, before local SENT commit",
    )
    args = parser.parse_args()
    if args.crash_after_send and args.action != "send-reviewed":
        parser.error("Crash injection is only available for a reviewed synthetic send")
    if any(c in args.sender + args.mailbox + (args.subject or "") for c in '\r\n"'):
        parser.error("Addresses and subject must not contain quotes or line breaks")
    if args.subject is not None and not args.subject.strip():
        parser.error("An exact non-empty subject is required for this bounded conversation")
    if any(
        parseaddr(address)[1] != address
        or "@" not in address
        or any(char.isspace() for char in address)
        for address in (args.sender, args.mailbox)
    ):
        parser.error("Supply one plain mailbox address for sender and mailbox")
    if args.action == "serve" and (args.after is None or args.after <= 0):
        parser.error("Automatic service requires an explicit positive --after activation timestamp")
    if args.watch and (args.action not in {"prepare", "serve"} or args.interval < 30):
        parser.error("Watch requires prepare/serve and an interval of at least 30 seconds")
    while True:
        with exclusive_state(args.state_dir):
            heartbeat = args.state_dir / "worker_status.json"
            try:
                heartbeat.write_text(json.dumps({"pid": os.getpid(), "phase": "polling",
                    "at": datetime.now(UTC).isoformat(), "action": args.action}))
                run_once(args, parser)
                heartbeat.write_text(json.dumps({"pid": os.getpid(), "phase": "idle",
                    "at": datetime.now(UTC).isoformat(), "action": args.action}))
            except Exception as error:
                heartbeat.write_text(json.dumps({"pid": os.getpid(), "phase": "error",
                    "at": datetime.now(UTC).isoformat(), "error_type": type(error).__name__}))
                if not args.watch:
                    raise
                print("Worker iteration failed:", type(error).__name__, flush=True)
        if not args.watch:
            return
        time.sleep(args.interval)


def run_once(args: argparse.Namespace, parser: argparse.ArgumentParser, *,
             fixture_without_processing_consent: bool = False) -> None:
    """One cycle. The explicit fixture-only override is intentionally absent from CLI."""
    binding = {"sender": args.sender, "mailbox": args.mailbox, "subject": args.subject}
    if args.after is not None:
        binding["after"] = args.after
    binding_path = args.state_dir / "binding.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            parser.error("This state directory belongs to another sandbox conversation")
    else:
        binding_path.write_text(json.dumps(binding))
    service = build_gmail_service(
        Path(".secrets/gmail_credentials.json"),
        Path(".secrets/gmail_token.json"),
        interactive=False,
    )
    if service.users().getProfile(userId="me").execute()["emailAddress"] != args.mailbox:
        parser.error("Authorized mailbox does not match the requested test mailbox")
    adapter = GmailAdapter(service)
    store = SQLiteStore(args.state_dir / "sandbox.db")
    journal = None
    try:
        ledger = ConsentLedger(store)
        if not fixture_without_processing_consent and args.action in {"prepare", "serve", "send-reviewed"}:
            ledger.configure(ProcessingScope(provider="DeepSeek", model=args.model, version="2026-09-04"))
        # Resolve previous uncertain sends even if intake/model processing fails this cycle.
        # This only observes provider state; dispatch still waits for successful intake.
        if args.action == "serve":
            from visa_agent.channels.automatic_reply import AutomaticGmailReplySender

            automatic_sender = AutomaticGmailReplySender(adapter, store, args.sender,
                allow_guarded_drafts=getattr(args, 'reply_style', 'reviewed') == 'guarded-draft')
            dispatcher = OutboxDispatcher(store, automatic_sender, channel="gmail",
                allowed_message_types=("blocked", "awaiting_profile_confirmation", "awaiting_confirmation",
                                       "held_update_received", *PRIVACY_MESSAGE_TYPES))
            reconciled = dispatcher.reconcile_sending(automatic_sender, datetime.now(UTC))
            if any(item.reason_code == "ACCESS_REQUIRED" for item in reconciled):
                raise ReconciliationAccessError(
                    "Gmail sent-message evidence is unavailable; restore authorization or access "
                    "before intake and dispatch can continue."
                ) from None
        if args.action in {"prepare", "serve"}:
            active_policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
            ingestion = EmailIngestionBoundary(store, args.state_dir / "attachments")
            query = f"from:{args.sender} to:{args.mailbox}"
            if args.after is not None:
                query += f" after:{args.after}"
            if args.subject:
                query += f' subject:"{args.subject}"'
            if args.action == "serve" or not fixture_without_processing_consent:
                journal = GmailSyncJournal(args.state_dir / "sync.db", json.dumps(binding, sort_keys=True))
                if not discover_messages(adapter, journal, query):
                    print("Intake discovery continues next cycle; no dispatch")
                    return
                if not fixture_without_processing_consent:
                    _resume_consent_deferred(ledger, store, journal)
                ordered = ordered_candidates(adapter, journal, sender=args.sender, mailbox=args.mailbox,
                                             after=args.after or 0, subject=args.subject)
                # A committed event is recovery work, not an unread customer message.
                # Keep new events chronological and ahead of old pack retries so a
                # full batch of failed artifacts cannot starve a later pause/correction.
                # Both queues share the existing 100-body cap; dispatch still requires
                # the entire discovery backlog to be acknowledged.
                unread: list[str] = []
                committed: list[str] = []
                for identifier in ordered:
                    (committed if store.event_processed(identifier) else unread).append(identifier)
                ids = (unread + committed)[:100]
                if not fixture_without_processing_consent:
                    _consent_preflight(adapter, ingestion, ledger, store, journal, ids, args,
                                       active_policy.version)
                    if not journal.consent_scan_drained():
                        print("Consent/control scan continues next cycle; no model work or dispatch")
                        return
                    ids = _scanned_business_ids(journal, store)
                    # A withdrawal may have superseded an earlier allow decision,
                    # including business scanned in a previous bounded cycle.
                    withdrawn = [identifier for identifier in ids
                        if (case := store.get_case_by_thread(journal.thread_id(identifier))) is None
                        or not ledger.allowed(case)]
                    _consent_preflight(adapter, ingestion, ledger, store, journal, withdrawn, args,
                                       active_policy.version)
                    ids = _scanned_business_ids(journal, store)
            else:
                ids = list(reversed(adapter.list_complete_message_ids(query, limit=100)))

            # A failed reviewed queue must never prevent a newly arrived withdrawal
            # from being observed. Controls above always run before this queue.
            review_rows = store.connection.execute("SELECT json_extract(payload_json,'$.external_thread_id') "
                "FROM inbound_queue WHERE channel='gmail_review' AND status!='PROCESSED'").fetchall()
            review_authorized = fixture_without_processing_consent or all(
                (case := store.get_case_by_thread(row[0])) is not None and ledger.allowed(case)
                for row in review_rows)
            workflow = None
            if ids or (review_rows and review_authorized):
                key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                                  default_file=Path(".secrets/deepseek_api_key.txt"))
                if not key:
                    parser.error("DeepSeek key is missing")
                model = DeepSeekStructuredLLM(args.model, api_key=key)
                workflow = WorkflowService(store, active_policy, model, document_reader=NaturalPDFReader(model))
            review_pending = bool(review_rows)
            if args.action == "serve" and review_rows and review_authorized:
                from visa_agent.channels.inbound_worker import InboundEventWorker

                assert workflow is not None
                review_outcomes = InboundEventWorker(store, workflow, channel="gmail_review").process_due(
                    datetime.now(UTC), limit=10)
                if review_outcomes:
                    print("Reviewed retries:", [item.status for item in review_outcomes])
                review_pending = any(row["channel"] == "gmail_review" and row["status"] != "PROCESSED"
                                     for row in store.list_inbound_queue())
            if review_pending:
                print("Reviewed retry held; only processing-control notices may be dispatched")
                ids = []
                if args.action == "serve":
                    dispatcher = OutboxDispatcher(store, automatic_sender, channel="gmail",
                                                   allowed_message_types=PRIVACY_MESSAGE_TYPES)
            # New messages remain chronological. Fetch/process one body at a time.
            pack_pending = False
            for identifier in ids:
                raw = adapter.get_raw_message(identifier)
                if raw.provider_message_id != identifier:
                    raise ValueError("Gmail raw response does not match the requested candidate")
                mime = message_from_bytes(raw.raw, policy=policy.default)
                rejection = scope_rejection(mime, args.sender, args.mailbox, args.subject)
                if rejection:
                    if journal is not None:
                        journal.acknowledge(identifier, "ignored", rejection)
                    continue
                received_at = None
                if not fixture_without_processing_consent:
                    assert journal is not None
                    if raw.provider_thread_id != journal.thread_id(identifier):
                        raise ValueError("Gmail materialization thread differs from its consent preview")
                    received_at = datetime.fromtimestamp(journal.receipt_ms(identifier) / 1000, UTC)
                    preview = ingestion.preview(raw.raw, provider_message_id=raw.provider_message_id,
                        provider_thread_id=raw.provider_thread_id, channel="gmail", received_at=received_at)
                    if preview.event is None:
                        journal.acknowledge(identifier, "rejected", preview.failure_code)
                        continue
                    case = store.get_case_by_thread(raw.provider_thread_id)
                    if store.event_processed(identifier):
                        if case is None or not ledger.allowed(case):
                            journal.acknowledge(identifier, "processed")
                            ledger.mark_completed(identifier)
                            continue
                    else:
                        decision = ledger.handle(preview.event, active_policy.version)
                        if decision.action != "allow":
                            if decision.action == "control":
                                journal.acknowledge(identifier, "consent_control", "PROCESSING_CONTROL_RECORDED")
                            else:
                                journal.acknowledge(identifier, "awaiting_consent", "PROCESSING_CONSENT_REQUIRED")
                            continue
                # Linearize local file persistence with a concurrent withdrawal.
                # Do not hold this DB lock during the subsequent external model call.
                with store.atomic_write() if not fixture_without_processing_consent else nullcontext():
                    if not fixture_without_processing_consent:
                        current = store.get_case_by_thread(raw.provider_thread_id)
                        if current is None:
                            raise ValueError("Consent-scanned Gmail case disappeared before materialization")
                        ledger.require(current)
                    result = ingestion.ingest(
                        raw.raw,
                        provider_message_id=raw.provider_message_id,
                        provider_thread_id=raw.provider_thread_id,
                        channel="gmail",
                        received_at=received_at,
                    )
                if result.event is None:
                    print("Ingestion rejected:", result.failure_code)
                    if journal is not None:
                        journal.acknowledge(identifier, "rejected", result.failure_code)
                    continue
                assert workflow is not None
                case, duplicate, plan = workflow.process(result.event)
                # Recover a crash after the workflow commit but before pack materialisation.
                if plan == "ready" or (duplicate and case.stage.value == "READY_FOR_HUMAN_REVIEW"):
                    try:
                        archive, _ = generate_pack(
                            case, workflow.policy, store, args.state_dir / "packs", date.today()
                        )
                    except Exception:
                        # Only materialization failures are recoverable here: workflow/DB
                        # processing exceptions above still stop immediately. Later customer
                        # corrections or a pause must not be trapped behind an already
                        # committed ready event. No dispatch is allowed in this failed cycle.
                        pack_pending = True
                        continue
                    if archive is None:
                        pack_pending = True
                        continue
                if journal is not None:
                    journal.acknowledge(identifier, "processed")
                if not fixture_without_processing_consent:
                    ledger.mark_completed(identifier)
                print(
                    json.dumps(
                        {
                            "plan": plan,
                            "duplicate": duplicate,
                            "stage": case.stage.value,
                            "extraction_fallback": None
                            if duplicate
                            else workflow.llm.last_extraction_fallback,
                            "reply_fallback": None
                            if duplicate
                            else workflow.llm.last_render_fallback,
                        }
                    )
                )
            if pack_pending:
                raise PackPreparationError(
                    "A required pack was not materialized. Committed applicant information and "
                    "pending candidates are retained; no automatic dispatch in this cycle."
                ) from None
        if args.action == "serve":
            if journal is None or not journal.discovery_drained():
                print("Intake backlog remains; no dispatch")
                return
            print("Held-update receipts queued:", automatic_sender.queue_finalized_update_receipts())
            print("Obsolete unsent replies withheld:", automatic_sender.withhold_obsolete_unsent())
            print("Automatic dispatch:", [item.status for item in dispatcher.dispatch_due(datetime.now(UTC), limit=1)])
        elif args.action in {"send-reviewed", "reconcile"}:

            class ScopedSender(GmailReplySender):
                def send(self, request: ReplyRequest) -> str:
                    if [address for _, address in getaddresses([request.recipient])] != [
                        args.sender
                    ] or (args.subject is not None and request.subject != "Re: " + args.subject):
                        raise PermanentChannelError("Sandbox recipient/subject boundary failed")
                    result = super().send(request)
                    if args.crash_after_send:
                        os._exit(75)  # Explicit test-only crash window; never automatic resend.
                    return result

            sender = ScopedSender(adapter)
            dispatcher = OutboxDispatcher(store, sender, channel="gmail")
            results = (
                dispatcher.reconcile_sending(sender, datetime.now(UTC))
                if args.action == "reconcile"
                else dispatcher.dispatch_due(datetime.now(UTC), limit=1)
            )
            print("Dispatch:", [item.status for item in results])
        for row in [] if args.watch else store.list_outbox():
            print(
                json.dumps(
                    {k: row.get(k) for k in ("id", "status", "message_type", "payload")},
                    ensure_ascii=False,
                )
            )
        print("Counts:", store.counts())
    finally:
        if journal is not None:
            journal.close()
        store.close()


if __name__ == "__main__":
    main()
