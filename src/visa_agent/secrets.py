from __future__ import annotations

import os
from pathlib import Path


def read_secret(
    environment_name: str,
    *,
    file_environment_name: str,
    default_file: Path | None = None,
) -> str:
    """Read a server-side secret without exposing it to application output."""

    direct = os.getenv(environment_name, "").strip()
    if direct:
        return direct
    configured_path = os.getenv(file_environment_name, "").strip()
    path = Path(configured_path) if configured_path else default_file
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()
