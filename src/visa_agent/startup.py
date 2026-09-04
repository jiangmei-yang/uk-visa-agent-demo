"""Seed a fresh review-console installation without resetting existing state."""

from visa_agent.config import Settings
from visa_agent.demo import run_demo


def initialize_if_new(settings: Settings) -> bool:
    # Even a zero-byte existing database may belong to an interrupted migration.
    # Never infer permission to overwrite it from its size or current table count.
    if settings.database_path.exists():
        return False
    run_demo(settings, reset=False)
    return True


if __name__ == "__main__":
    initialize_if_new(Settings.from_env())
