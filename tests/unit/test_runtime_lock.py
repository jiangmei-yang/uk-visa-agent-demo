from pathlib import Path

import pytest

from visa_agent.channels.runtime_lock import exclusive_state


def test_same_state_excludes_second_worker_and_releases_after_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError), exclusive_state(tmp_path):
        with pytest.raises(RuntimeError, match="Another worker"), exclusive_state(tmp_path):
            pytest.fail("Concurrent worker acquired ownership")
        raise ValueError("simulated worker crash")
    with exclusive_state(tmp_path):
        assert (tmp_path / "worker.lock").exists()


def test_independent_states_can_run_concurrently(tmp_path: Path) -> None:
    with exclusive_state(tmp_path / "a"), exclusive_state(tmp_path / "b"):
        pass
