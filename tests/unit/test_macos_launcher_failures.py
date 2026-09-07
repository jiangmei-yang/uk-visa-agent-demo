"""Execute the real macOS launcher with isolated fake commands, never Docker/mail."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(ZSH is None, reason="Native launcher requires zsh")
LAUNCHER = Path(__file__).parents[2] / "START_DEMO.command"


def run_launcher(tmp_path, *, missing=False, info=0, compose=0, health=0, browser=0):
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "commands.log"
    # Absolute interpreter: the fake PATH cannot fall through to real commands.
    statuses = {"docker": 0, "curl": health, "open": browser, "sleep": 0}
    for name, status in statuses.items():
        if name == "docker" and missing:
            continue
        body = '#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$LAUNCHER_TEST_LOG"\n'
        if name == "docker":
            body += f'case "$*" in info) exit {info};; "compose up --build --detach") exit {compose};; esac\n'
        body += f"exit {status}\n"
        executable = commands / name
        executable.write_text(body)
        executable.chmod(0o700)
    launcher = tmp_path / "START_DEMO.command"
    shutil.copyfile(LAUNCHER, launcher)
    result = subprocess.run(
        [ZSH, "-f", str(launcher)], input="\n", text=True, capture_output=True,
        timeout=15, env={**os.environ, "PATH": str(commands), "LAUNCHER_TEST_LOG": str(log)},
    )
    calls = log.read_text().splitlines() if log.exists() else []
    assert not any("down" in call or "prune" in call or "volume rm" in call for call in calls)
    return result, calls


def test_missing_docker_has_install_help_without_launch(tmp_path):
    result, calls = run_launcher(tmp_path, missing=True)
    assert result.returncode == 1
    assert "not installed" in result.stdout
    assert "docker.com/products/docker-desktop" in result.stdout
    assert calls == []


def test_stopped_docker_has_actionable_help_without_build(tmp_path):
    result, calls = run_launcher(tmp_path, info=1)
    assert result.returncode == 1
    assert "not running. Open it" in result.stdout
    assert calls == ["docker info"]


def test_build_failure_never_opens_browser(tmp_path):
    result, calls = run_launcher(tmp_path, compose=1)
    assert result.returncode == 1
    assert "could not be started" in result.stdout
    assert calls == ["docker info", "docker compose up --build --detach"]


def test_health_timeout_is_bounded_and_reports_diagnostics(tmp_path):
    result, calls = run_launcher(tmp_path, health=1)
    assert result.returncode == 1
    assert "did not become ready" in result.stdout
    assert sum(call.startswith("curl ") for call in calls) == 90
    assert calls[-1] == "docker compose logs --tail 30"
    assert not any(call.startswith("open ") for call in calls)


def test_healthy_start_opens_browser_and_preserves_data(tmp_path):
    result, calls = run_launcher(tmp_path)
    assert result.returncode == 0
    assert "STOP_DEMO.command" in result.stdout
    assert calls[-1] == "open http://127.0.0.1:8000"
    assert sum(call.startswith("curl ") for call in calls) == 1


def test_browser_failure_retains_running_service_and_shows_manual_url(tmp_path):
    result, calls = run_launcher(tmp_path, browser=1)
    assert result.returncode == 0
    assert "browser could not be opened automatically" in result.stdout
    assert "Open http://127.0.0.1:8000 manually" in result.stdout
    assert calls[-1] == "open http://127.0.0.1:8000"
