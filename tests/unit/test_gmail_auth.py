from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels import gmail_auth
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


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_failed_token_commit_keeps_previous_credentials(tmp_path, monkeypatch, failure_point):
    token = tmp_path / "token.json"
    token.write_text("original synthetic credentials", encoding="utf-8")
    token.chmod(0o600)

    def fail(*args, **kwargs):
        raise OSError("simulated token commit failure")

    monkeypatch.setattr(gmail_auth.os, failure_point, fail)
    with pytest.raises(OSError, match="commit failure"):
        _write_private_token(token, "replacement synthetic credentials")
    assert token.read_text() == "original synthetic credentials"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert set(tmp_path.iterdir()) == {token}


def test_replacement_is_complete_private_and_does_not_truncate_old_file(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("original synthetic credentials", encoding="utf-8")
    token.chmod(0o644)
    replace = gmail_auth.os.replace
    commits = []

    def observe(source, destination):
        source = Path(source)
        assert source.parent == token.parent
        assert source.read_text() == "replacement synthetic credentials"
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert token.read_text() == "original synthetic credentials"
        commits.append(destination)
        replace(source, destination)

    monkeypatch.setattr(gmail_auth.os, "replace", observe)
    _write_private_token(token, "replacement synthetic credentials")
    assert commits == [token]
    assert token.read_text() == "replacement synthetic credentials"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert set(tmp_path.iterdir()) == {token}


def test_process_exit_before_token_replace_preserves_old_token(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("original synthetic credentials", encoding="utf-8")
    token.chmod(0o600)
    script = """
import os
import sys
from pathlib import Path
from visa_agent.channels import gmail_auth
gmail_auth.os.replace = lambda *args: os._exit(75)
gmail_auth._write_private_token(Path(sys.argv[1]), "replacement synthetic credentials")
"""
    result = subprocess.run([sys.executable, "-c", script, str(token)], check=False, timeout=10)
    assert result.returncode == 75
    assert token.read_text() == "original synthetic credentials"
    # Abrupt process death may leave its private temporary file, never a partial active token.
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in tmp_path.iterdir())


def test_token_writer_rejects_symlink_without_touching_target(tmp_path):
    target = tmp_path / "unrelated.json"
    target.write_text("unrelated synthetic data", encoding="utf-8")
    token = tmp_path / "token.json"
    token.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        _write_private_token(token, "replacement")
    assert target.read_text() == "unrelated synthetic data"
    assert token.is_symlink()


def test_token_encoding_failure_keeps_previous_credentials(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("original synthetic credentials", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        _write_private_token(token, "partial content \ud800")
    assert token.read_text() == "original synthetic credentials"
    assert set(tmp_path.iterdir()) == {token}


@pytest.fixture
def fake_google(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text("synthetic client", encoding="utf-8")
    token = tmp_path / "token.json"
    token.write_text("original synthetic credentials", encoding="utf-8")
    calls = []
    state = SimpleNamespace(load_error=None, refresh_error=None, consent_error=None,
        build_error=None, profile_error=None, mailbox="service@example.test")
    saved = SimpleNamespace(expired=True, valid=False, refresh_token="synthetic-refresh")
    renewed = SimpleNamespace(valid=True, refresh_token="synthetic-new-refresh")
    renewed.to_json = lambda: "replacement synthetic credentials"

    def refresh(request):
        calls.append("refresh")
        if state.refresh_error:
            raise state.refresh_error
        saved.expired = False
        saved.valid = True

    saved.refresh = refresh
    saved.to_json = renewed.to_json

    def load(*args):
        calls.append("load")
        if state.load_error:
            raise state.load_error
        return saved

    def consent(**kwargs):
        calls.append(("consent", kwargs))
        if state.consent_error:
            raise state.consent_error
        return renewed

    def build(*args, **kwargs):
        calls.append("build")
        if state.build_error:
            raise state.build_error

        def profile():
            calls.append("profile")
            if state.profile_error:
                raise state.profile_error
            return {"emailAddress": state.mailbox}

        return SimpleNamespace(credentials=kwargs["credentials"], users=lambda: SimpleNamespace(
            getProfile=lambda **kw: SimpleNamespace(execute=profile)))

    modules = {
        "google.oauth2.credentials": SimpleNamespace(
            Credentials=SimpleNamespace(from_authorized_user_file=load)),
        "google.auth.transport.requests": SimpleNamespace(Request=lambda: object()),
        "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=SimpleNamespace(
            from_client_secrets_file=lambda *args: SimpleNamespace(run_local_server=consent))),
        "googleapiclient.discovery": SimpleNamespace(build=build),
    }
    monkeypatch.setattr(gmail_auth, "import_module", modules.__getitem__)
    return SimpleNamespace(client=client, token=token, calls=calls, state=state,
                           saved=saved, renewed=renewed)


def test_valid_token_is_used_without_consent_or_overwrite(fake_google):
    fake_google.saved.valid = True
    fake_google.saved.expired = False
    result = build_gmail_service(fake_google.client, fake_google.token, interactive=False)
    assert result.credentials is fake_google.saved
    assert fake_google.calls == ["load", "build"]
    assert fake_google.token.read_text() == "original synthetic credentials"


def test_expired_token_refreshes_without_interaction_and_is_saved(fake_google):
    result = build_gmail_service(fake_google.client, fake_google.token, interactive=False)
    assert result.credentials is fake_google.saved
    assert fake_google.calls == ["load", "refresh", "build"]
    assert fake_google.token.read_text() == "replacement synthetic credentials"
    assert stat.S_IMODE(fake_google.token.stat().st_mode) == 0o600


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("failure", ["load", "refresh"])
def test_auth_failure_never_discards_old_token_or_opens_unrequested_consent(
    fake_google, interactive, failure
):
    error = ValueError("synthetic malformed credentials") if failure == "load" else RuntimeError(
        "synthetic provider refresh rejection")
    setattr(fake_google.state, f"{failure}_error", error)
    with pytest.raises(type(error)):
        build_gmail_service(fake_google.client, fake_google.token, interactive=interactive)
    assert not any(isinstance(call, tuple) for call in fake_google.calls)
    assert "build" not in fake_google.calls
    assert fake_google.token.read_text() == "original synthetic credentials"


@pytest.mark.parametrize("failure", ["load", "refresh"])
def test_explicit_reauthorization_bypasses_bad_saved_token(fake_google, failure):
    setattr(fake_google.state, f"{failure}_error", RuntimeError("must never reach old credentials"))
    result = build_gmail_service(fake_google.client, fake_google.token,
        interactive=True, reauthorize=True, expected_mailbox="service@example.test")
    assert result.credentials is fake_google.renewed
    assert fake_google.calls == [("consent", {"port": 0, "prompt": "consent"}), "build", "profile"]
    assert fake_google.token.read_text() == "replacement synthetic credentials"


def test_unattended_reauthorization_is_rejected_before_any_google_call(fake_google):
    with pytest.raises(ValueError, match="interactive"):
        build_gmail_service(fake_google.client, fake_google.token,
                            interactive=False, reauthorize=True)
    assert fake_google.calls == []
    assert fake_google.token.read_text() == "original synthetic credentials"


@pytest.mark.parametrize("failure", ["cancelled", "invalid", "no_refresh"])
def test_incomplete_new_consent_preserves_previous_token(fake_google, failure):
    if failure == "cancelled":
        fake_google.state.consent_error = RuntimeError("synthetic user cancellation")
    elif failure == "invalid":
        fake_google.renewed.valid = False
    else:
        fake_google.renewed.refresh_token = None
    with pytest.raises(RuntimeError):
        build_gmail_service(fake_google.client, fake_google.token,
            interactive=True, reauthorize=True, expected_mailbox="service@example.test")
    assert "build" not in fake_google.calls
    assert fake_google.token.read_text() == "original synthetic credentials"


@pytest.mark.parametrize("failure", ["build", "profile", "wrong_mailbox", "missing_mailbox"])
def test_new_authorization_is_not_committed_until_service_and_mailbox_verified(fake_google, failure):
    if failure in {"build", "profile"}:
        setattr(fake_google.state, f"{failure}_error", RuntimeError("synthetic service failure"))
    else:
        fake_google.state.mailbox = "wrong-account@example.test" if failure == "wrong_mailbox" else None
    with pytest.raises((RuntimeError, ValueError)):
        build_gmail_service(fake_google.client, fake_google.token,
            interactive=True, reauthorize=True, expected_mailbox="service@example.test")
    assert fake_google.token.read_text() == "original synthetic credentials"


def test_reauthorize_without_expected_mailbox_does_not_open_consent(fake_google):
    with pytest.raises(ValueError, match="expected mailbox"):
        build_gmail_service(fake_google.client, fake_google.token, interactive=True, reauthorize=True)
    assert fake_google.calls == []
    assert fake_google.token.read_text() == "original synthetic credentials"


@pytest.mark.parametrize("client_symlink", [False, True])
def test_client_file_cannot_be_replaced_by_token_authorization(fake_google, client_symlink):
    client = fake_google.token
    if client_symlink:
        client = fake_google.client.parent / "linked-client.json"
        client.symlink_to(fake_google.token)
    with pytest.raises(ValueError, match="different paths"):
        build_gmail_service(client, fake_google.token, interactive=True,
            reauthorize=True, expected_mailbox="service@example.test")
    assert fake_google.calls == []
    assert fake_google.token.read_text() == "original synthetic credentials"


def test_refresh_followed_by_discovery_failure_does_not_overwrite_token(fake_google):
    fake_google.state.build_error = RuntimeError("synthetic discovery failure")
    with pytest.raises(RuntimeError):
        build_gmail_service(fake_google.client, fake_google.token, interactive=False)
    assert fake_google.calls == ["load", "refresh", "build"]
    assert fake_google.token.read_text() == "original synthetic credentials"


def test_cli_passes_explicit_reauthorize_to_interactive_flow(tmp_path, monkeypatch, capsys):
    from visa_agent.cli import main

    calls = []

    def build(client, token, **kwargs):
        calls.append((client, token, kwargs))
        return SimpleNamespace(users=lambda: pytest.fail("Mailbox was already verified before commit"))

    monkeypatch.setattr(gmail_auth, "build_gmail_service", build)
    monkeypatch.setattr(sys, "argv", ["visa-agent", "gmail-auth", "--reauthorize",
        "--mailbox", "service@example.test",
        "--credentials", str(tmp_path / "client.json"), "--token", str(tmp_path / "token.json")])
    main()
    assert calls == [(tmp_path / "client.json", tmp_path / "token.json",
                      {"interactive": True, "reauthorize": True,
                       "expected_mailbox": "service@example.test"})]
    assert "authorization succeeded" in capsys.readouterr().out
