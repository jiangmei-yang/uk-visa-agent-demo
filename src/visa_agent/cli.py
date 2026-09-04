from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import uvicorn

from visa_agent.config import Settings
from visa_agent.demo import run_demo
from visa_agent.llm.ports import LLMClient
from visa_agent.secrets import read_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="UK Visa Agent Demo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="Run credential-free email replay")
    demo_parser.add_argument("--reset", action="store_true")
    web_parser = subparsers.add_parser("web", help="Open the local review console")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", default=8000, type=int)
    webhook_server_parser = subparsers.add_parser(
        "webhook-server", help="Run the provider-only webhook gateway"
    )
    webhook_server_parser.add_argument("--host", default="127.0.0.1")
    webhook_server_parser.add_argument("--port", default=8001, type=int)
    gmail_parser = subparsers.add_parser(
        "gmail-auth", help="Authorize and verify a synthetic Gmail sandbox account"
    )
    gmail_parser.add_argument(
        "--credentials", type=Path, default=Path(".secrets/gmail_credentials.json")
    )
    gmail_parser.add_argument("--token", type=Path, default=Path(".secrets/gmail_token.json"))
    gmail_parser.add_argument(
        "--reauthorize", action="store_true",
        help="Explicitly obtain new consent without first loading or refreshing the saved token",
    )
    gmail_parser.add_argument(
        "--mailbox", help="Verify this mailbox before saving authorization; required with --reauthorize",
    )
    inbound_parser = subparsers.add_parser(
        "inbound-worker", help="Process one batch from the durable inbound channel queue"
    )
    inbound_parser.add_argument("--channel", required=True)
    inbound_parser.add_argument(
        "--provider",
        choices=("openai", "deepseek"),
        default=os.getenv("LLM_PROVIDER", "openai"),
    )
    inbound_parser.add_argument(
        "--model", default=os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL")
    )
    inbound_parser.add_argument("--limit", type=int, default=20)
    whatsapp_parser = subparsers.add_parser(
        "whatsapp-dispatch", help="Send one due WhatsApp outbox batch"
    )
    whatsapp_parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "demo":
        result = run_demo(settings, reset=bool(args.reset))
        print("Credential-free synthetic email demo completed.")
        print(f"Case: {result.case.id} — {result.case.status}")
        print(f"Application pack: {result.package_path}")
        print(f"Audit report: {result.report_path}")
        print(f"Idempotent counts: {result.counts}")
    elif args.command == "web":
        uvicorn.run("visa_agent.web:app", host=args.host, port=args.port, reload=False)
    elif args.command == "webhook-server":
        uvicorn.run("visa_agent.public_webhook:app", host=args.host, port=args.port, reload=False)
    elif args.command == "gmail-auth":
        from visa_agent.channels.gmail_auth import build_gmail_service

        if args.reauthorize and not args.mailbox:
            parser.error("gmail-auth --reauthorize requires --mailbox to prevent an account switch")
        service = build_gmail_service(
            args.credentials, args.token, interactive=True, reauthorize=args.reauthorize,
            expected_mailbox=args.mailbox,
        )
        # An expected mailbox was already verified before the credential commit.
        # Do not add a second network failure after a successful reconnection.
        mailbox = args.mailbox
        if mailbox is None:
            mailbox = service.users().getProfile(userId="me").execute().get(
                "emailAddress", "address unavailable"
            )
        print("Gmail sandbox authorization succeeded.")
        print(f"Mailbox: {mailbox}")
        print(f"Token stored privately at: {args.token}")
    elif args.command == "inbound-worker":
        if not args.model:
            raise SystemExit("Set --model or LLM_MODEL to an evaluated model ID.")
        from visa_agent.channels.inbound_worker import InboundEventWorker
        from visa_agent.documents.natural import (
            DocumentReader,
            NaturalPDFReader,
            hold_unconfigured_live_pdf,
        )
        from visa_agent.domain.policy import load_policy
        from visa_agent.storage.sqlite import SQLiteStore
        from visa_agent.workflow.service import WorkflowService

        live_llm: LLMClient
        document_reader: DocumentReader = hold_unconfigured_live_pdf
        if args.provider == "deepseek":
            from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM

            deepseek_key = read_secret(
                "DEEPSEEK_API_KEY",
                file_environment_name="DEEPSEEK_API_KEY_FILE",
                default_file=Path(".secrets/deepseek_api_key.txt"),
            )
            if not deepseek_key:
                raise SystemExit("Set DEEPSEEK_API_KEY for the DeepSeek provider.")
            deepseek_model = DeepSeekStructuredLLM(args.model, api_key=deepseek_key)
            live_llm = deepseek_model
            document_reader = NaturalPDFReader(deepseek_model)
        else:
            if not os.getenv("OPENAI_API_KEY"):
                raise SystemExit("Set OPENAI_API_KEY for the OpenAI provider.")
            from visa_agent.llm.openai_client import OpenAIStructuredLLM

            live_llm = OpenAIStructuredLLM(args.model)

        store = SQLiteStore(settings.database_path)
        try:
            from visa_agent.privacy.consent import ConsentLedger, ProcessingScope

            # Live worker configuration is an execution requirement, never an
            # applicant grant. Existing cases remain unknown until they opt in.
            ConsentLedger(store).configure(ProcessingScope(provider=args.provider, model=args.model))
            workflow = WorkflowService(
                store,
                load_policy(settings.policy_path),
                live_llm,
                document_reader=document_reader,
            )
            inbound_outcomes = InboundEventWorker(
                store,
                workflow,
                channel=args.channel,
            ).process_due(datetime.now(UTC), limit=args.limit)
        finally:
            store.close()
        print(f"Processed {len(inbound_outcomes)} inbound queue item(s).")
        for inbound_outcome in inbound_outcomes:
            print(f"{inbound_outcome.event_id}: {inbound_outcome.status}")
    else:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        service_address = os.getenv("TWILIO_WHATSAPP_FROM", "")
        callback_url = os.getenv("TWILIO_STATUS_CALLBACK_PUBLIC_URL", "")
        if not account_sid or not auth_token or not service_address:
            raise SystemExit("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_FROM.")
        if not callback_url:
            raise SystemExit("Set TWILIO_STATUS_CALLBACK_PUBLIC_URL to track delivery receipts.")
        from visa_agent.channels.outbound import OutboxDispatcher
        from visa_agent.channels.twilio_whatsapp import TwilioWhatsAppSender
        from visa_agent.storage.sqlite import SQLiteStore

        client_type = import_module("twilio.rest").Client
        store = SQLiteStore(settings.database_path)
        try:
            sender = TwilioWhatsAppSender(client_type(account_sid, auth_token), service_address, callback_url)
            dispatch_outcomes = OutboxDispatcher(
                store, sender, channel="whatsapp_twilio"
            ).dispatch_due(datetime.now(UTC), limit=args.limit)
        finally:
            store.close()
        print(f"Dispatched {len(dispatch_outcomes)} WhatsApp outbox item(s).")
        for dispatch_outcome in dispatch_outcomes:
            print(f"{dispatch_outcome.outbox_id}: {dispatch_outcome.status}")
