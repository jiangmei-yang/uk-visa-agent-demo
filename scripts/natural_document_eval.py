"""Generate plainly worded fictional PDFs and verify real text/OCR model extraction."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from visa_agent.documents.natural import NaturalPDFReader
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.secrets import read_secret

DOCUMENTS = {
    "passport_summary.pdf": (
        "passport",
        [
            "Passport information summary",
            "Holder: Lin Chen",
            "Date of birth: 1997-04-18",
            "Date of expiry: 2028-07-01",
            "Fictional specimen. Not an identity document.",
        ],
    ),
    "invitation.pdf": (
        "conference_invitation",
        [
            "Northstar Research Conference - invitation",
            "Dear Lin Chen,",
            "We invite you to attend our conference in London.",
            "The conference begins on 2026-11-10 and ends on 2026-11-12.",
            "Fictional organisation and applicant; no real booking is made.",
        ],
    ),
    "student_letter.pdf": (
        "student_letter",
        [
            "Northstar University - Student Status Letter",
            "To whom it may concern:",
            "Lin Chen is enrolled as a full-time student at our university.",
            "The university confirms approved study leave for the conference.",
            "Fictional specimen for software evaluation.",
        ],
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data/natural-document-eval"))
    parser.add_argument(
        "--report", type=Path, default=Path("eval_output/natural_documents_2026-09-04.json")
    )
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for filename, (_, lines) in DOCUMENTS.items():
        pdf = canvas.Canvas(str(args.directory / filename), pagesize=A4, invariant=1)
        text = pdf.beginText(50, 770)
        text.setFont("Helvetica", 12)
        text.setLeading(22)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.save()
    environment = os.environ.copy()
    if Path("/opt/homebrew/etc/fonts/fonts.conf").exists():
        environment.setdefault("FONTCONFIG_FILE", "/opt/homebrew/etc/fonts/fonts.conf")
    png = args.directory / "student-render"
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            "150",
            "-singlefile",
            "-png",
            str(args.directory / "student_letter.pdf"),
            str(png),
        ],
        env=environment,
        check=True,
        timeout=30,
    )
    scanned = args.directory / "student_letter_scan.pdf"
    pdf = canvas.Canvas(str(scanned), pagesize=A4, invariant=1)
    pdf.drawImage(str(png) + ".png", 0, 0, width=A4[0], height=A4[1])
    pdf.save()
    key = read_secret(
        "DEEPSEEK_API_KEY",
        file_environment_name="DEEPSEEK_API_KEY_FILE",
        default_file=Path(".secrets/deepseek_api_key.txt"),
    )
    if not key:
        parser.error("Missing DeepSeek key")
    reader = NaturalPDFReader(DeepSeekStructuredLLM("deepseek-v4-flash", api_key=key))
    rows = []
    for filename, expected in [(name, kind) for name, (kind, _) in DOCUMENTS.items()] + [
        (scanned.name, "student_letter")
    ]:
        result = reader(args.directory / filename)
        checks = {
            "expected_kind": result.kind == expected,
            "grounded_name": result.facts.get("full_name", (None,))[0] == "Lin Chen",
            "review_boundary_correct": result.requires_review
            if filename == "passport_summary.pdf"
            else not result.requires_review,
            "non_fixture_method": result.method != "deterministic_pdf_fixture_extractor",
        }
        if filename == scanned.name:
            checks["actual_ocr_used"] = result.method == "bounded_pdf_ocr_extraction"
        if expected == "passport":
            checks["expiry_correct"] = (
                result.facts.get("passport_expiry_date", (None,))[0] == "2028-07-01"
            )
        if expected == "conference_invitation":
            checks["event_end_correct"] = (
                result.facts.get("invitation_event_end_date", (None,))[0] == "2026-11-12"
            )
        rows.append(
            {
                "file": filename,
                "checks": checks,
                "method": result.method,
                "kind": result.kind,
                "facts": result.facts,
            }
        )
        print(filename, "PASS" if all(checks.values()) else "FAIL", flush=True)
    report = {
        "synthetic": True,
        "scope": "ordinary-text PDFs plus one image-only scan; not authenticity verification",
        "all_passed": all(all(row["checks"].values()) for row in rows),
        "results": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(args.report)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
