from __future__ import annotations

from datetime import date
from pathlib import Path

from visa_agent.domain.policy import load_policy


def test_policy_has_explicit_freshness_boundary() -> None:
    policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    assert policy.is_current(date(2026, 9, 2))
    assert not policy.is_current(date(2026, 10, 3))
