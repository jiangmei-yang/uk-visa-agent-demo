from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent.documents.natural import (
    DocumentFact,
    DocumentProposal,
    NaturalPDFReader,
    validate_document,
)

TEXT = "Conference invitation for Lin Chen. The event begins on 2026-11-10 and ends on 2026-11-12."


@pytest.mark.parametrize("warning", [
    "SAMPLE PASSPORT — NOT VALID FOR TRAVEL", "SPECIMEN", "For demonstration purposes only",
    "ＤＵＭＭＹ ＰＡＳＳＰＯＲＴ", "护照样张，仅供演示", "仅供测试，不可用于旅行",
])
def test_explicit_identity_specimens_cannot_satisfy_document_requirements(warning):
    text = ("Passport. Name: Lin Chen. Born: 1995-02-03. Expiry: 2030-12-31.\n" + warning)
    candidate = DocumentProposal(kind="passport", language="en", classification_page=1,
        classification_excerpt="Passport.", confidence=0.99, facts=[
            DocumentFact(field="full_name", value="Lin Chen", page=1, excerpt="Name: Lin Chen", confidence=0.99),
            DocumentFact(field="date_of_birth", value="1995-02-03", page=1, excerpt="Born: 1995-02-03", confidence=0.99),
            DocumentFact(field="passport_expiry_date", value="2030-12-31", page=1, excerpt="Expiry: 2030-12-31", confidence=0.99),
        ])
    result = validate_document(candidate, [text], method="text", version="fake-high-confidence")
    assert result.requires_review
    assert result.review_reason == "Specimen is not an identity document"


def proposal() -> DocumentProposal:
    return DocumentProposal(
        kind="conference_invitation",
        language="en",
        classification_page=1,
        classification_excerpt="Conference invitation for Lin Chen.",
        confidence=0.99,
        facts=[
            DocumentFact(
                field="full_name", value="Lin Chen", page=1, excerpt="for Lin Chen", confidence=0.99
            ),
            DocumentFact(
                field="invitation_event_end_date",
                value="2026-11-12",
                page=1,
                excerpt="ends on 2026-11-12",
                confidence=0.99,
            ),
            DocumentFact(
                field="invitation_event_start_date",
                value="2026-11-10",
                page=1,
                excerpt="begins on 2026-11-10",
                confidence=0.99,
            ),
        ],
    )


def test_natural_evidence_retains_page_excerpt_and_model_provenance() -> None:
    result = validate_document(
        proposal(), [TEXT], method="bounded_pdf_text_extraction", version="fake"
    )
    assert result.facts["full_name"] == ("Lin Chen", 1, "for Lin Chen")
    assert result.model_version == "fake"
    assert result.method != "deterministic_pdf_fixture_extractor"
    assert not result.requires_review


@pytest.mark.parametrize(
    "change", ["invented_excerpt", "wrong_page", "invented_date", "low_confidence", "invented_name"]
)
def test_document_proposals_cannot_invent_page_evidence(change: str) -> None:
    candidate = proposal()
    if change == "invented_excerpt":
        candidate.classification_excerpt = "Approved by the UK Government"
    elif change == "wrong_page":
        candidate.facts[0].page = 2
    elif change == "invented_date":
        candidate.facts[1].value = "2027-11-12"
    elif change == "low_confidence":
        candidate.facts[0].confidence = 0.5
    else:
        candidate.facts[0].value = "Someone Else"
    with pytest.raises(ValueError):
        validate_document(candidate, [TEXT], method="text", version="fake")


def test_ordinary_pdf_needs_no_machine_markers(tmp_path: Path) -> None:
    path = tmp_path / "invitation.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 750, TEXT)
    pdf.save()

    class Model:
        version = "fake"

        def extract_document(self, pages: list[str]) -> DocumentProposal:
            assert "DOCUMENT_KIND=" not in pages[0]
            return proposal()

    result = NaturalPDFReader(Model())(path)
    assert result.kind == "conference_invitation"
    assert not result.requires_review


def test_provider_failure_retains_document_for_review(tmp_path: Path) -> None:
    path = tmp_path / "letter.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 750, TEXT)
    pdf.save()

    class Model:
        version = "unavailable"

        def extract_document(self, pages: list[str]) -> DocumentProposal:
            raise TimeoutError("provider unavailable")

    result = NaturalPDFReader(Model())(path)
    assert result.requires_review and result.facts == {}


def test_missing_invitee_is_held_even_when_model_claims_confidence() -> None:
    candidate = proposal()
    candidate.facts = candidate.facts[1:]
    result = validate_document(candidate, [TEXT], method="text", version="fake")
    assert result.requires_review
    assert "full_name" in (result.review_reason or "")
