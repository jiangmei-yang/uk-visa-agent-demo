from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

SAMPLE_DOCUMENTS: dict[str, list[str]] = {
    "passport.pdf": [
        "Synthetic passport fixture - NOT A REAL IDENTITY DOCUMENT",
        "DOCUMENT_KIND=passport",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
        "FACT date_of_birth=1997-04-18",
        "FACT passport_expiry_date=2028-07-01",
    ],
    "conference_invitation_original.pdf": [
        "Synthetic Northstar Research Conference invitation",
        "DOCUMENT_KIND=conference_invitation",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
        "FACT invitation_event_start_date=2026-09-12",
        "FACT invitation_event_end_date=2026-09-16",
        "Event location: London",
    ],
    "student_letter.pdf": [
        "Synthetic University student and leave letter",
        "DOCUMENT_KIND=student_letter",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
        "FACT occupation_status=student",
        "FACT visit_purpose=conference",
    ],
    "school_funding_letter.pdf": [
        "Synthetic University funding confirmation",
        "DOCUMENT_KIND=funding_letter",
        "LANGUAGE=en",
        "FACT funding_source=employer_or_school",
        "The university will pay return airfare and accommodation.",
    ],
    "bank_statement.pdf": [
        "Synthetic personal funds statement - DEMO ONLY",
        "DOCUMENT_KIND=bank_statement",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
        "Available funds are shown without an invented fixed-month rule.",
    ],
    "hong_kong_residence_status.pdf": [
        "Synthetic Hong Kong lawful residence evidence - DEMO ONLY",
        "DOCUMENT_KIND=status_document",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
    ],
    "family_funds_cn.pdf": [
        "Synthetic Chinese-language supporting document placeholder",
        "DOCUMENT_KIND=other_supporting_document",
        "LANGUAGE=zh",
        "This page intentionally starts without a certified translation.",
    ],
    "conference_invitation_corrected.pdf": [
        "Synthetic corrected Northstar Research Conference invitation",
        "DOCUMENT_KIND=conference_invitation",
        "LANGUAGE=en",
        "FACT full_name=Lin Chen",
        "FACT invitation_event_start_date=2026-09-11",
        "FACT invitation_event_end_date=2026-09-14",
        "Event location: London",
    ],
    "family_funds_certified_translation.pdf": [
        "Synthetic certified translation - DEMO ONLY",
        "DOCUMENT_KIND=certified_translation",
        "LANGUAGE=en",
        "FACT translation_for_filename=family_funds_cn.pdf",
        "Translation covers family_funds_cn.pdf in full.",
        "Translator name, qualifications, date, and contact details: SYNTHETIC FIXTURE.",
    ],
}


def generate_sample_documents(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, lines in SAMPLE_DOCUMENTS.items():
        path = output_dir / filename
        pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        pdf.setTitle(filename)
        text = pdf.beginText(54, 790)
        text.setFont("Helvetica", 10)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.save()
