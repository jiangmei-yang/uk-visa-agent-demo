from __future__ import annotations

import shutil
from pathlib import Path

from visa_agent.domain.models import Case


def delete_case_artifacts(case: Case, output_root: Path) -> None:
    """Delete only derived artifacts owned by one case inside the configured output root."""

    allowed_root = output_root.resolve()
    case_dir = (allowed_root / case.id).resolve()
    if allowed_root in case_dir.parents and case_dir.is_dir():
        shutil.rmtree(case_dir)
    if case.delivery_path:
        pack_path = Path(case.delivery_path).resolve()
        if allowed_root in pack_path.parents and pack_path.is_file():
            pack_path.unlink()

