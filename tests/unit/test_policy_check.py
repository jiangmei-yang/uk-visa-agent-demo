from __future__ import annotations

import subprocess


def test_policy_check_passes_inside_window_and_fails_after_review_boundary() -> None:
    current = subprocess.run(
        ["uv", "run", "python", "scripts/policy_check.py", "--date", "2026-09-04"],
        capture_output=True,
        check=False,
        text=True,
    )
    stale = subprocess.run(
        ["uv", "run", "python", "scripts/policy_check.py", "--date", "2026-10-03"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert current.returncode == 0
    assert "within its reviewed window" in current.stdout
    assert stale.returncode != 0
    assert "outside its reviewed window" in stale.stderr
