from __future__ import annotations

from pathlib import Path

from visa_agent.secrets import read_secret


def test_direct_secret_takes_precedence_over_file(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    secret_file = tmp_path / "provider-key.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("TEST_API_KEY", "environment-secret")
    monkeypatch.setenv("TEST_API_KEY_FILE", str(secret_file))

    assert read_secret("TEST_API_KEY", file_environment_name="TEST_API_KEY_FILE") == (
        "environment-secret"
    )


def test_secret_can_be_loaded_from_private_file(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    secret_file = tmp_path / "provider-key.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    monkeypatch.setenv("TEST_API_KEY_FILE", str(secret_file))

    assert read_secret("TEST_API_KEY", file_environment_name="TEST_API_KEY_FILE") == "file-secret"


def test_missing_secret_returns_empty(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    monkeypatch.delenv("TEST_API_KEY_FILE", raising=False)

    assert (
        read_secret(
            "TEST_API_KEY",
            file_environment_name="TEST_API_KEY_FILE",
            default_file=tmp_path / "missing.txt",
        )
        == ""
    )
