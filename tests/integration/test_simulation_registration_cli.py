"""CLI failure rolls back the manifest without touching customer state."""

import pytest

from scripts.register_fictional_case import main
from visa_agent.domain.models import Case
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("valid", [False, True])
def test_cli_validates_before_committing(tmp_path, monkeypatch, valid):
    state = tmp_path / "state"
    state.mkdir()
    path = tmp_path / "passport.pdf"
    path.write_bytes(b"isolated mocked parser input")
    store = SQLiteStore(state / "sandbox.db")
    try:
        case = Case(id="demo", external_thread_id="thread-demo", primary_channel="gmail",
                    applicant_contact="demo@example.test", policy_version="v")
        store.save_case(case)
        before = store.get_case(case.id).model_dump()

        def parse(*args):
            if not valid:
                raise ValueError("Unmarked specimen")

        monkeypatch.setattr("scripts.register_fictional_case.read_simulation_document", parse)
        args = ["--state-dir", str(state), "--case", case.id, "--thread", case.external_thread_id,
                "--sender", case.applicant_contact, "--operator", "fixture",
                "--reason", "User approved an independent fictional case",
                "--specimen", f"passport={path}"]
        if valid:
            main(args)
        else:
            with pytest.raises(ValueError, match="Unmarked"):
                main(args)
        assert store.connection.execute("SELECT COUNT(*) FROM simulation_registrations").fetchone()[0] == int(valid)
        assert store.get_case(case.id).model_dump() == before
        assert store.list_outbox() == []
    finally:
        store.close()
