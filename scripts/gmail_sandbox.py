"""Gmail trial runner restricted to an operator-allowlisted sender, optionally one subject."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, date, datetime
from email import message_from_bytes, policy
from email.utils import getaddresses, parseaddr
from pathlib import Path

from visa_agent.channels.email_ingestion import EmailIngestionBoundary
from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.gmail_auth import build_gmail_service
from visa_agent.channels.outbound import OutboxDispatcher, PermanentChannelError, ReplyRequest
from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.delivery.pack import generate_pack
from visa_agent.documents.natural import NaturalPDFReader
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


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
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--watch", action="store_true", help="Continuously prepare; never auto-send"
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


def run_once(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
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
    try:
        if args.action in {"prepare", "serve"}:
            key = read_secret(
                "DEEPSEEK_API_KEY",
                file_environment_name="DEEPSEEK_API_KEY_FILE",
                default_file=Path(".secrets/deepseek_api_key.txt"),
            )
            if not key:
                parser.error("DeepSeek key is missing")
            model = DeepSeekStructuredLLM(args.model, api_key=key)
            workflow = WorkflowService(
                store,
                load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                model,
                document_reader=NaturalPDFReader(model),
            )
            ingestion = EmailIngestionBoundary(store, args.state_dir / "attachments")
            query = f"from:{args.sender} to:{args.mailbox}"
            if args.after is not None:
                query += f" after:{args.after}"
            if args.subject:
                query += f' subject:"{args.subject}"'
            ids = adapter.list_complete_message_ids(query, limit=100)
            messages = []
            for identifier in ids:
                raw = adapter.get_raw_message(identifier)
                mime = message_from_bytes(raw.raw, policy=policy.default)
                if (str(mime.get("Auto-Submitted", "no")).casefold() != "no"
                        or mime.get("List-Id") or str(mime.get("Precedence", "")).casefold() in {"bulk", "list", "junk"}):
                    continue
                subject = str(mime.get("Subject", ""))
                if args.subject is not None and subject.removeprefix("Re: ") != args.subject:
                    continue
                if parseaddr(str(mime.get("From", "")))[1] != args.sender:
                    continue
                messages.append(raw)
            # Gmail lists newest first; process oldest first for a conversation.
            for raw in reversed(messages):
                result = ingestion.ingest(
                    raw.raw,
                    provider_message_id=raw.provider_message_id,
                    provider_thread_id=raw.provider_thread_id,
                    channel="gmail",
                )
                if result.event is None:
                    print("Ingestion rejected:", result.failure_code)
                    continue
                case, duplicate, plan = workflow.process(result.event)
                # Recover a crash after the workflow commit but before pack materialisation.
                if plan == "ready" or (duplicate and case.stage.value == "READY_FOR_HUMAN_REVIEW"):
                    generate_pack(
                        case, workflow.policy, store, args.state_dir / "packs", date.today()
                    )
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
        if args.action == "serve":
            from visa_agent.channels.automatic_reply import AutomaticGmailReplySender

            automatic_sender = AutomaticGmailReplySender(adapter, store, args.sender)
            dispatcher = OutboxDispatcher(store, automatic_sender, channel="gmail",
                allowed_message_types=("blocked", "awaiting_profile_confirmation", "awaiting_confirmation"))
            dispatcher.reconcile_sending(automatic_sender, datetime.now(UTC))
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
        store.close()


if __name__ == "__main__":
    main()
