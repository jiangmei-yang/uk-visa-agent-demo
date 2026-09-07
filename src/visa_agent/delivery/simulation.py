"""Fictional identity scope must survive generation, download and delivery."""

import json
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from visa_agent.domain.models import Case

MANIFEST_NAME = "07_fictional_demonstration.json"


def simulation_manifest(case: Case) -> dict[str, Any] | None:
    if case.simulation is None:
        return None
    return {"purpose": case.simulation.purpose, "case_id": case.id,
            "specimens": [entry.model_dump(mode="json") for entry in case.simulation.specimens]}


def require_simulation_archive(case: Case, content: bytes) -> None:
    expected = simulation_manifest(case)
    # Ordinary delivery byte-integrity checks are unchanged. Registered specimen
    # evidence without a case registration is rejected by the store binding guard.
    if expected is None:
        return
    try:
        with ZipFile(BytesIO(content)) as archive:
            matches = [item for item in archive.infolist() if item.filename == MANIFEST_NAME]
            if len(matches) != 1 or matches[0].file_size > 12_000:
                raise ValueError("Fictional archive must retain its unique scope manifest")
            if json.loads(archive.read(matches[0])) != expected:
                raise ValueError("Fictional archive scope does not match the registered case")
    except (BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Fictional archive scope cannot be verified") from error
