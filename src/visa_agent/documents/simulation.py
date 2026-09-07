"""Explicit specimen parsing for a registered fictional case, not authentication."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from visa_agent.documents.natural import DocumentReader, DocumentReadResult
from visa_agent.domain.simulation import SimulationRegistration

SPECIMEN_METHOD = "registered_fictional_identity_specimen"
SPECIMEN_NOTICE = "FICTIONAL SPECIMEN - NOT VALID FOR ANY APPLICATION"


def specimen_facts(pages: list[str], kind: str) -> dict[str, tuple[str, int, str]]:
    if len(pages) != 1 or SPECIMEN_NOTICE not in pages[0]:
        raise ValueError("A single explicitly marked specimen page is required")
    title = "Passport particulars" if kind == "passport" else "Hong Kong permission to stay"
    warning = ("not a passport or an identity document" if kind == "passport"
               else "not issued by an immigration authority")
    if title not in pages[0] or warning not in pages[0]:
        raise ValueError("Specimen title and non-authenticity notice are required")
    facts = {}
    labels = [("full_name", "Name", False), ("date_of_birth", "Date of birth", True)]
    if kind == "passport":
        labels.append(("passport_expiry_date", "Date of expiry", True))
    for field, label, is_date in labels:
        matches = re.findall(r"^" + re.escape(label) + r": ([^\r\n]+)$", pages[0], re.M)
        if len(matches) != 1 or not matches[0].strip():
            raise ValueError(f"Missing or ambiguous specimen field: {label}")
        raw = matches[0].strip()
        value = datetime.strptime(raw, "%d %B %Y").date().isoformat() if is_date else raw
        facts[field] = (value, 1, f"{label}: {matches[0]}")
    return facts


def read_simulation_document(
    path: Path, registration: SimulationRegistration, ordinary_reader: DocumentReader,
) -> DocumentReadResult:
    entry = next((item for item in registration.specimens if item.filename == path.name), None)
    if entry is None:
        result = ordinary_reader(path)
        if result.kind in {"passport", "travel_document", "status_document"}:
            raise ValueError("Identity documents in a fictional case must be registered specimens")
        return result
    if path.stat().st_size > 2_000_000:
        raise ValueError("Registered specimen exceeds size limit")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != entry.sha256:
        raise ValueError("Registered specimen bytes have changed")
    # Parse the same bytes we hashed, not a reopened mutable attachment path.
    from io import BytesIO

    pdf = PdfReader(BytesIO(content))
    if pdf.is_encrypted or len(pdf.pages) != 1:
        raise ValueError("Registered specimen must be an unencrypted single page")
    pages = [page.extract_text() or "" for page in pdf.pages]
    facts = specimen_facts(pages, entry.kind)
    return DocumentReadResult(entry.kind, "en", 1, facts, method=SPECIMEN_METHOD,
                              model_version="explicit-specimen-parser-v1")
