"""Local configuration inventory. No network calls, secret values or delivery claims."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


def configuration_checks(root: Path, environment: Mapping[str, str]) -> dict[str, object]:
    def nonempty_file(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def https_callback(name: str, endpoint: str) -> bool:
        try:
            url = urlsplit(environment.get(name, ""))
            return (url.scheme == "https" and bool(url.hostname) and not url.username
                    and not url.password and url.path == endpoint and not url.query and not url.fragment)
        except ValueError:
            return False

    key_file = Path(environment.get("DEEPSEEK_API_KEY_FILE", ".secrets/deepseek_api_key.txt"))
    if not key_file.is_absolute():
        key_file = root / key_file
    checks = {
        "deepseek_key_present": bool(environment.get("DEEPSEEK_API_KEY", "").strip()) or nonempty_file(key_file),
        "gmail_oauth_client_file_present": nonempty_file(root / ".secrets/gmail_credentials.json"),
        "gmail_oauth_token_file_present": nonempty_file(root / ".secrets/gmail_token.json"),
        "twilio_account_sid_present": bool(environment.get("TWILIO_ACCOUNT_SID", "").strip()),
        "twilio_auth_token_present": bool(environment.get("TWILIO_AUTH_TOKEN", "").strip()),
        "twilio_whatsapp_sender_present": environment.get("TWILIO_WHATSAPP_FROM", "").startswith("whatsapp:+"),
        "twilio_intake_https_url_shaped_correctly": https_callback("TWILIO_WEBHOOK_PUBLIC_URL", "/webhooks/twilio/whatsapp"),
        "twilio_status_https_url_shaped_correctly": https_callback("TWILIO_STATUS_CALLBACK_PUBLIC_URL", "/webhooks/twilio/whatsapp/status"),
    }
    return {
        "scope": "Local presence/URL-shape checks only; no provider requests",
        "checks": checks,
        "notes": [
            "Secret values, file contents, mailbox addresses and phone numbers are not printed.",
            "Presence does not establish valid credentials, current consent, device enrollment or delivery.",
            "This process reads exported environment variables; .env files are not automatically loaded.",
            "Gmail trial scripts currently use the fixed .secrets OAuth paths checked here.",
            "Live account/device authorization and recipient-side evidence remain separate requirements.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = configuration_checks(args.root, os.environ)
    dependencies = {}
    for module in ("openai", "googleapiclient", "google_auth_oauthlib", "twilio"):
        try:
            dependencies[module] = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            dependencies[module] = False
    report["optional_python_modules_present"] = dependencies
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
