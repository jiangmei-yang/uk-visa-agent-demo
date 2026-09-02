from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from typing import Any

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)


def build_gmail_service(
    credentials_path: Path,
    token_path: Path,
    *,
    interactive: bool,
) -> Any:
    """Create a Gmail v1 service without putting OAuth inside the domain layer."""

    if not credentials_path.is_file():
        raise FileNotFoundError(
            f"Gmail OAuth client file was not found at {credentials_path}. "
            "Download a Desktop app credential from Google Cloud first."
        )
    credentials_type = import_module("google.oauth2.credentials").Credentials
    request_type = import_module("google.auth.transport.requests").Request
    flow_type = import_module("google_auth_oauthlib.flow").InstalledAppFlow
    build = import_module("googleapiclient.discovery").build

    credentials: Any = None
    if token_path.is_file():
        credentials = credentials_type.from_authorized_user_file(
            str(token_path), list(GMAIL_SCOPES)
        )
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(request_type())
        _write_private_token(token_path, credentials.to_json())
    elif not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError(
                "Gmail authorization is required. Run the explicit gmail-auth command once."
            )
        flow = flow_type.from_client_secrets_file(str(credentials_path), list(GMAIL_SCOPES))
        credentials = flow.run_local_server(port=0)
        _write_private_token(token_path, credentials.to_json())
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _write_private_token(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)
