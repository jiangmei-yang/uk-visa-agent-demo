from __future__ import annotations

import os
import tempfile
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
    reauthorize: bool = False,
    expected_mailbox: str | None = None,
) -> Any:
    """Create a Gmail v1 service without putting OAuth inside the domain layer."""

    if reauthorize and not interactive:
        raise ValueError("Gmail reauthorization requires an explicit interactive command.")
    if reauthorize and not expected_mailbox:
        raise ValueError("Gmail reauthorization requires the expected mailbox.")
    if credentials_path.resolve() == token_path.resolve():
        raise ValueError("Gmail client and token must use different paths.")
    if token_path.is_symlink():
        raise ValueError("Gmail token path must not be a symlink.")
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
    save_credentials = False
    new_consent = False
    # Recovery must be explicit: transient refresh errors must not open a consent browser.
    # Skip even loading a malformed/revoked token only for the operator's --reauthorize.
    if token_path.is_file() and not reauthorize:
        credentials = credentials_type.from_authorized_user_file(
            str(token_path), list(GMAIL_SCOPES)
        )
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(request_type())
        save_credentials = True
    elif not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError(
                "Gmail authorization is required. Run the explicit gmail-auth command once."
            )
        flow = flow_type.from_client_secrets_file(str(credentials_path), list(GMAIL_SCOPES))
        credentials = flow.run_local_server(port=0, prompt="consent")
        if not credentials.valid or not credentials.refresh_token:
            raise RuntimeError(
                "Gmail consent did not return reusable authorization; "
                "the previous token has not been replaced."
            )
        save_credentials = True
        new_consent = True
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    if new_consent or expected_mailbox is not None:
        profile = service.users().getProfile(userId="me").execute()
        actual_mailbox = profile.get("emailAddress")
        if not isinstance(actual_mailbox, str) or "@" not in actual_mailbox:
            raise RuntimeError("Gmail authorization did not return a mailbox; token not replaced.")
        if expected_mailbox is not None and actual_mailbox.casefold() != expected_mailbox.casefold():
            raise ValueError("Authorized Gmail mailbox does not match; token not replaced.")
    if save_credentials:
        _write_private_token(token_path, credentials.to_json())
    return service


def _write_private_token(path: Path, content: str) -> None:
    """Commit a complete private token without truncating the active credentials.

    File fsync + same-directory rename protects against a failed write or process exit
    before replacement. This is not a power-loss durability or multi-writer guarantee.
    An abruptly terminated process can leave an owner-only temporary file behind.
    """
    if path.is_symlink():
        raise ValueError("Gmail token path must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        # NamedTemporaryFile creates mode 0600; replacement keeps that mode even if
        # an old token was accidentally world-readable. Never chmod after commit.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".gmail-token-",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
