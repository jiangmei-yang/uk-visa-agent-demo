import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_agent.channels.gmail import GmailHistoryPage


@pytest.mark.parametrize("expired", [False, True])
def test_probe_requires_observed_history_error_not_just_a_new_cursor(tmp_path, monkeypatch, expired):
    spec = importlib.util.spec_from_file_location("history_probe", Path("scripts/gmail_history_recovery_probe.py"))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"emailAddress": "service@example.test"})

    def discover(adapter, journal, query, max_pages):
        adapter.expired_responses = int(expired)
        adapter.history_calls = 1
        adapter.full_pages = int(expired)
        journal.commit_page(journal.checkpoint(), GmailHistoryPage((), None, "200"))
        return True

    monkeypatch.setattr(probe, "build_gmail_service", lambda *a, **kw: Service())
    monkeypatch.setattr(probe, "discover_messages", discover)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--sender", "applicant@example.test", "--mailbox",
        "service@example.test", "--after", "1", "--report", str(report_path)])
    if expired:
        probe.main()
    else:
        with pytest.raises(SystemExit, match="did not prove"):
            probe.main()
    report = json.loads(report_path.read_text())
    assert report["passed"] == expired and report["recovered_to_ready"]
    original = report_path.read_bytes()
    with pytest.raises(SystemExit):
        probe.main()
    assert report_path.read_bytes() == original
