from __future__ import annotations

import json
from pathlib import Path

from visa_agent.config import Settings
from visa_agent.demo import run_demo


def test_featured_flow_has_expected_block_then_release(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    result = run_demo(settings, reset=True)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    steps = report["steps"]
    assert steps[0]["package_generated"] is False
    assert set(steps[0]["open_blockers"]) == {
        "DATE_CONFLICT",
        "MISSING_CERTIFIED_TRANSLATION",
    }
    assert steps[1]["gate_reasons"] == ["applicant explicitly confirmed final summary"]
    assert steps[2]["package_generated"] is True
    assert report["replay_idempotent"] is True
