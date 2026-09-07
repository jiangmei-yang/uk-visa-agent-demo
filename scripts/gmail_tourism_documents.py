"""Create visibly fictional ordinary PDFs for the Gmail journey; no mailbox or case writes."""

import argparse
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

DOCUMENTS = {
    "passport.pdf": ("Passport particulars", [
        "Name: Lin Chen", "Date of birth: 18 April 1997", "Nationality: Chinese",
        "Passport number: DEMO000001", "Date of issue: 1 January 2024",
        "Date of expiry: 31 December 2033", "Issuing country: China",
        "This is a text specimen, not a passport or an identity document.",
    ]),
    "student_letter.pdf": ("Confirmation of current enrolment", [
        "Example Harbour University, Hong Kong", "Date: 7 September 2026",
        "To whom it may concern:",
        "Lin Chen is enrolled full time in a master's programme from 1 September 2025 to 31 August 2027.",
        "The student receives a personal living stipend of GBP 1,500 per month (GBP 18,000 annually).",
        "The stipend is paid into the student's own bank account. The university is not sponsoring this holiday.",
        "The student intends to return to Hong Kong after the proposed visit and continue the programme.",
        "Academic Registry, Example Harbour University (fictional institution)",
    ]),
    "residence_status.pdf": ("Hong Kong permission to stay", [
        "Name: Lin Chen", "Nationality: Chinese", "Date of birth: 18 April 1997",
        "Permission category: student", "Valid from: 1 September 2025", "Valid until: 31 August 2027",
        "Place of residence: Hong Kong", "This specimen is not issued by an immigration authority.",
    ]),
    "travel_plan.pdf": ("Proposed London holiday", [
        "Traveller: Lin Chen", "Purpose: tourism", "Arrival in the UK: 10 November 2026",
        "Departure from the UK: 17 November 2026", "Destination: London",
        "10 November: arrive in London and check in.",
        "11-16 November: visit museums, parks and historic sites in London.",
        "17 November: depart London and return to Hong Kong to resume studies.",
        "Proposed accommodation: Example Garden Hotel, 20 Fictional Square, London.",
        "The hotel and flights are not booked. These are proposed arrangements, not paid reservations.",
        "Estimated total trip cost: GBP 2,200, paid from the traveller's own savings.",
    ]),
    "bank_statement_original.pdf": ("Personal account statement", [
        "Example Bank (fictional institution)", "Account holder: Lin Chan", "Currency: GBP",
        "Account ending: 1234", "Statement period: 1 August 2026 to 31 August 2026",
        "Opening balance: GBP 11,500.00", "5 August: student living stipend received, GBP 1,500.00",
        "20 August: living expenses paid, GBP 500.00", "Closing balance: GBP 12,500.00",
    ]),
    "bank_statement_corrected.pdf": ("Corrected personal account statement", [
        "Example Bank (fictional institution)", "Account holder: Lin Chen", "Currency: GBP",
        "Account ending: 1234", "Statement period: 1 August 2026 to 31 August 2026",
        "Opening balance: GBP 11,500.00", "5 August: student living stipend received, GBP 1,500.00",
        "20 August: living expenses paid, GBP 500.00", "Closing balance: GBP 12,500.00",
        "Correction issued 7 September 2026: account holder spelling corrected from Lin Chan to Lin Chen.",
        "This replaces the original statement for the same account and period; no balances have changed.",
    ]),
}


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    styles = getSampleStyleSheet()
    styles["BodyText"].leading = 16
    styles["BodyText"].spaceAfter = 12
    styles["Heading2"].textColor = colors.HexColor("#9b2c2c")
    for name, (title, lines) in DOCUMENTS.items():
        story = [Paragraph(title, styles["Title"]), Spacer(1, 12),
                 Paragraph("FICTIONAL SPECIMEN - NOT VALID FOR ANY APPLICATION", styles["Heading2"]),
                 Spacer(1, 12)]
        story.extend(Paragraph(line, styles["BodyText"]) for line in lines)
        SimpleDocTemplate(str(root / name), pagesize=A4, leftMargin=48, rightMargin=48,
                          topMargin=48, bottomMargin=48).build(story)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    generate(parser.parse_args().output)
