"""Bounded PDF text/OCR extraction. Model proposals never establish document authenticity."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from visa_agent.documents.processor import inspect_pdf
from visa_agent.domain.date_evidence import date_is_grounded

DocumentKind = Literal[
    "unknown",
    "passport",
    "travel_document",
    "employment_letter",
    "student_letter",
    "self_employment_evidence",
    "invitation_letter",
    "conference_invitation",
    "itinerary_description",
    "bank_statement",
    "funding_letter",
    "sponsor_letter",
    "sponsor_funds",
    "relationship_evidence",
    "sponsor_uk_status",
    "status_document",
    "certified_translation",
    "other_supporting_document",
]
FactName = Literal[
    "full_name",
    "date_of_birth",
    "passport_expiry_date",
    "invitation_event_start_date",
    "invitation_event_end_date",
    "occupation_status",
    "funding_source",
    "translation_for_filename",
]


class DocumentFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: FactName
    value: str
    page: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)


class DocumentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: DocumentKind
    language: Literal["en", "cy", "zh", "other"]
    classification_page: int = Field(ge=1)
    classification_excerpt: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)
    facts: list[DocumentFact] = Field(default_factory=list, max_length=30)
    requires_review: bool = False
    review_reason: str | None = None


@dataclass(frozen=True)
class DocumentReadResult:
    kind: str
    language: str
    page_count: int
    facts: dict[str, tuple[str, int, str]]
    method: str = "deterministic_pdf_fixture_extractor"
    model_version: str = "none"
    confidence: float = 1.0
    requires_review: bool = False
    review_reason: str | None = None


class DocumentReader(Protocol):
    def __call__(self, path: Path) -> DocumentReadResult: ...


class DocumentModel(Protocol):
    version: str

    def extract_document(self, pages: list[str]) -> DocumentProposal: ...


def read_fixture_pdf(path: Path) -> DocumentReadResult:
    kind, language, count, facts = inspect_pdf(path)
    return DocumentReadResult(kind, language, count, facts)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _grounded(excerpt: str, page: int, pages: list[str]) -> bool:
    return 1 <= page <= len(pages) and _normalise(excerpt) in _normalise(pages[page - 1])


def validate_document(
    proposal: DocumentProposal, pages: list[str], *, method: str, version: str
) -> DocumentReadResult:
    if not _grounded(proposal.classification_excerpt, proposal.classification_page, pages):
        raise ValueError("Document classification lacks a source excerpt on the stated page")
    facts: dict[str, tuple[str, int, str]] = {}
    for item in proposal.facts:
        if item.confidence < 0.95 or not _grounded(item.excerpt, item.page, pages):
            raise ValueError("Document fact lacks sufficiently grounded page evidence")
        if item.field.endswith("_date") or item.field == "date_of_birth":
            if not date_is_grounded(
                item.value, item.excerpt, allow_shared_year=item.field != "date_of_birth"
            ):
                raise ValueError("A document date cannot be traced to its excerpt")
        elif item.field in {"full_name", "translation_for_filename"} and _normalise(
            item.value
        ) not in _normalise(item.excerpt):
            raise ValueError("Document value is absent from its source excerpt")
        if item.field in facts and facts[item.field][0] != item.value:
            raise ValueError("Conflicting document facts require review")
        facts[item.field] = (item.value, item.page, item.excerpt)
    required = {
        "passport": {"full_name", "date_of_birth", "passport_expiry_date"},
        "travel_document": {"full_name", "passport_expiry_date"},
        "conference_invitation": {
            "full_name",
            "invitation_event_start_date",
            "invitation_event_end_date",
        },
        "student_letter": {"full_name"},
        "employment_letter": {"full_name"},
        "bank_statement": {"full_name"},
    }.get(proposal.kind, set())
    missing = required - facts.keys()
    specimen_identity = proposal.kind in {"passport", "travel_document"} and any(
        phrase in _normalise("\n".join(pages))
        for phrase in ("not an identity document", "fictional specimen", "非身份证件")
    )
    return DocumentReadResult(
        proposal.kind,
        proposal.language,
        len(pages),
        facts,
        method,
        version,
        min([proposal.confidence] + [item.confidence for item in proposal.facts]),
        proposal.requires_review
        or proposal.confidence < 0.95
        or proposal.kind == "unknown"
        or bool(missing)
        or specimen_identity,
        "Missing grounded document fields: " + ", ".join(sorted(missing))
        if missing
        else (
            "Specimen is not an identity document" if specimen_identity else proposal.review_reason
        ),
    )


class NaturalPDFReader:
    def __init__(self, model: DocumentModel, *, allow_ocr: bool = True) -> None:
        self.model = model
        self.allow_ocr = allow_ocr

    def __call__(self, path: Path) -> DocumentReadResult:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("PDF exceeds the 10 MB processing limit")
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF requires an unlocked copy")
        if not 1 <= len(reader.pages) <= 20:
            raise ValueError("PDF must contain between 1 and 20 pages")
        pages = [page.extract_text() or "" for page in reader.pages]
        scanned = [i for i, text in enumerate(pages) if len(text.strip()) < 20]
        if scanned:
            if not self.allow_ocr or len(scanned) > 5:
                raise ValueError("Scanned pages exceed the configured OCR boundary")
            for index in scanned:
                pages[index] = self._ocr_page(path, index + 1)
        if any(len(text.strip()) < 20 for text in pages):
            raise ValueError("Some PDF pages remain unreadable after extraction")
        if any(len(text) > 15000 for text in pages) or sum(map(len, pages)) > 60000:
            raise ValueError("PDF text exceeds the bounded model input; no silent truncation")
        # Do not treat protocol-like lines embedded in customer documents as instructions.
        method = "bounded_pdf_ocr_extraction" if scanned else "bounded_pdf_text_extraction"
        try:
            proposal = self.model.extract_document(pages)
            return validate_document(proposal, pages, method=method, version=self.model.version)
        except Exception:
            # Retain the file for review, without treating provider failure as valid evidence.
            return DocumentReadResult(
                "unknown", "other", len(pages), {}, method, self.model.version, 0, True
            )

    @staticmethod
    def _ocr_page(path: Path, page: int) -> str:
        renderer, ocr = shutil.which("pdftoppm"), shutil.which("tesseract")
        if not renderer or not ocr:
            raise ValueError("Local OCR dependencies are unavailable")
        environment = os.environ.copy()
        if not environment.get("FONTCONFIG_FILE"):
            for candidate in ("/etc/fonts/fonts.conf", "/opt/homebrew/etc/fonts/fonts.conf"):
                if Path(candidate).is_file():
                    environment["FONTCONFIG_FILE"] = candidate
                    break
        try:
            languages = subprocess.run(
                [ocr, "--list-langs"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
            language = "+".join(
                item for item in ("eng", "chi_sim", "chi_tra") if item in languages.splitlines()
            )
            if not language:
                raise ValueError("No supported local OCR language is installed")
            with tempfile.TemporaryDirectory(prefix="visa-ocr-") as directory:
                target = Path(directory) / "page"
                subprocess.run(
                    [
                        renderer,
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-r",
                        "150",
                        "-singlefile",
                        "-png",
                        str(path.resolve()),
                        str(target),
                    ],
                    env=environment,
                    capture_output=True,
                    timeout=20,
                    check=True,
                )
                result = subprocess.run(
                    [ocr, str(target) + ".png", "stdout", "-l", language],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                )
                return result.stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("Local OCR failed or timed out; manual review required") from error
