from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from visa_agent.config import Settings
from visa_agent.demo import run_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="UK Visa Agent Demo")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="Run credential-free email replay")
    demo_parser.add_argument("--reset", action="store_true")
    web_parser = subparsers.add_parser("web", help="Open the local review console")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", default=8000, type=int)
    gmail_parser = subparsers.add_parser(
        "gmail-auth", help="Authorize and verify a synthetic Gmail sandbox account"
    )
    gmail_parser.add_argument(
        "--credentials", type=Path, default=Path(".secrets/gmail_credentials.json")
    )
    gmail_parser.add_argument("--token", type=Path, default=Path(".secrets/gmail_token.json"))
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
    else:
        from visa_agent.channels.gmail_auth import build_gmail_service

        service = build_gmail_service(args.credentials, args.token, interactive=True)
        profile = service.users().getProfile(userId="me").execute()
        print("Gmail sandbox authorization succeeded.")
        print(f"Mailbox: {profile.get('emailAddress', 'address unavailable')}")
        print(f"Token stored privately at: {args.token}")
