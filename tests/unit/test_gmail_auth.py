from __future__ import annotations

import stat
from pathlib import Path

import pytest

from visa_agent.channels.gmail_auth import _write_private_token, build_gmail_service


def test_gmail_auth_explains_missing_oauth_client_before_import_or_network(tmp_path: Path) -> None:
    missing = tmp_path / "missing-credentials.json"

    with pytest.raises(FileNotFoundError, match="Desktop app credential"):
        build_gmail_service(missing, tmp_path / "token.json", interactive=False)


def test_gmail_token_is_written_with_owner_only_permissions(tmp_path: Path) -> None:
    token = tmp_path / "nested" / "token.json"

    _write_private_token(token, '{"synthetic": true}')

    assert token.read_text(encoding="utf-8") == '{"synthetic": true}'
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
