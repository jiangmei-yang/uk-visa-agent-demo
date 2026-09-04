from pathlib import Path
from unittest.mock import patch

from visa_agent.config import Settings
from visa_agent.startup import initialize_if_new


def test_existing_database_is_never_reseeded(tmp_path: Path) -> None:
    database = tmp_path / "existing.db"
    database.touch()
    with patch("visa_agent.startup.run_demo") as seed:
        assert not initialize_if_new(Settings(database_path=database))
    seed.assert_not_called()
    assert database.read_bytes() == b""


def test_new_installation_seeds_without_reset(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "new.db")
    with patch("visa_agent.startup.run_demo") as seed:
        assert initialize_if_new(settings)
    seed.assert_called_once_with(settings, reset=False)
