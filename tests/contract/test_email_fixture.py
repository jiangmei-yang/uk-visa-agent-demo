from __future__ import annotations

from pathlib import Path

from visa_agent.channels.email_fixture import parse_eml


def test_fixture_preserves_thread_and_provider_id(tmp_path: Path) -> None:
    event = parse_eml(Path("samples/emails/01_initial_submission.eml"), tmp_path)
    assert event.id == "demo-message-001@example.test"
    assert event.external_thread_id == "demo-thread-lin-chen-001"
    assert event.sender == "Lin Chen <lin.chen@example.test>"
    assert len(event.attachment_paths) == 7
