from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pypdf import PdfReader

FACT_PATTERN = re.compile(r"^FACT\s+([a-z0-9_]+)=(.+)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path) -> tuple[str, str, int, dict[str, tuple[str, int, str]]]:
    reader = PdfReader(path)
    kind = "unknown"
    language = "en"
    facts: dict[str, tuple[str, int, str]] = {}
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("DOCUMENT_KIND="):
                kind = line.split("=", 1)[1].strip()
            elif line.startswith("LANGUAGE="):
                language = line.split("=", 1)[1].strip()
            else:
                match = FACT_PATTERN.match(line)
                if match:
                    facts[match.group(1)] = (match.group(2).strip(), page_number, line)
    return kind, language, len(reader.pages), facts
