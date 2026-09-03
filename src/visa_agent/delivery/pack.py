from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import date
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from visa_agent.domain.models import Case, CaseStatus, IssueStatus, WorkflowStage
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import evaluate_gate, transition
from visa_agent.storage.sqlite import SQLiteStore

FIXED_ZIP_TIME = (2026, 2, 25, 0, 0, 0)
NAVY = HexColor("#183153")
BLUE = HexColor("#2457A6")
GREEN = HexColor("#18794E")
MUTED = HexColor("#5E6C7B")
LINE = HexColor("#DDE3EA")
PALE_BLUE = HexColor("#EEF4FC")
PALE_GREEN = HexColor("#EAF7F0")


def _page_frame(pdf: canvas.Canvas, document: SimpleDocTemplate) -> None:
    width, height = A4
    pdf.saveState()
    pdf.setFillColor(BLUE)
    pdf.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
    pdf.setStrokeColor(LINE)
    pdf.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(18 * mm, 10 * mm, "UK Visa Preparation - Human review pack")
    pdf.drawRightString(width - 18 * mm, 10 * mm, f"Page {document.page}")
    pdf.restoreState()


def _pdf(
    path: Path,
    title: str,
    paragraphs: list[str],
    label: str,
    *,
    compact: bool = False,
) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "PackTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=NAVY,
            alignment=0,
            spaceAfter=8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            "Status",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=GREEN,
            backColor=PALE_GREEN,
            borderColor=HexColor("#B9DEC9"),
            borderWidth=0.5,
            borderPadding=5,
            spaceAfter=7 * mm,
        )
    )
    body_style = ParagraphStyle(
        "PackBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5 if compact else 9.5,
        leading=11 if compact else 14,
        textColor=NAVY,
    )
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=21 * mm,
        title=title,
        author="UK Visa Agent Demo",
        invariant=1,
    )
    story: list[Flowable] = [
        Paragraph(escape(label.replace("_", " ")), styles["Status"]),
        Paragraph(escape(title), styles["PackTitle"]),
    ]
    rows = [[Paragraph(escape(value), body_style)] for value in paragraphs]
    if rows:
        table = Table(rows, colWidths=[document.width])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, PALE_BLUE]),
                    ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.extend([table, Spacer(1, 3 * mm)])
    document.build(story, onFirstPage=_page_frame, onLaterPages=_page_frame)


def _write_zip(source_dir: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(source_dir).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def _profile_rows(case: Case) -> list[str]:
    labels = {
        "date_of_birth": "Date of birth",
        "nationality_country": "Country of nationality",
        "application_country": "Country of application",
        "planned_arrival_date": "Planned arrival",
        "planned_departure_date": "Planned departure",
        "visit_purpose": "Visit purpose",
        "uk_accommodation": "UK accommodation",
        "estimated_trip_cost_gbp": "Estimated trip cost",
        "current_address": "Current home address",
        "occupation_status": "Occupation status",
        "annual_income_gbp": "Annual income",
        "funding_source": "Funding source",
        "sponsor_name": "Sponsor name",
        "sponsor_relationship": "Sponsor relationship",
        "sponsor_is_in_uk": "Sponsor is in the UK",
        "has_serious_history": "Serious history declared",
        "route_confirmed_standard_visitor": "Standard Visitor route confirmed",
    }
    profile = case.profile.model_dump(mode="json")
    if profile["funding_source"] != "personal_sponsor":
        for field in ("sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"):
            profile[field] = "Not applicable"
    for field in ("estimated_trip_cost_gbp", "annual_income_gbp"):
        if isinstance(profile[field], int):
            profile[field] = f"GBP {profile[field]:,}"
    for field, value in profile.items():
        if isinstance(value, bool):
            profile[field] = "Yes" if value else "No"
        elif isinstance(value, str) and "_" in value:
            profile[field] = value.replace("_", " ").capitalize()
    return [
        f"{labels.get(field, field.replace('_', ' ').capitalize())}: "
        f"{value if value is not None else 'Not provided'}"
        for field, value in profile.items()
    ]


def _document_index_pdf(path: Path, rows: list[str], label: str) -> None:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "IndexTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=NAVY,
        alignment=0,
        spaceAfter=7 * mm,
    )
    status_style = ParagraphStyle(
        "IndexStatus",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=GREEN,
        backColor=PALE_GREEN,
        borderColor=HexColor("#B9DEC9"),
        borderWidth=0.5,
        borderPadding=5,
        spaceAfter=7 * mm,
    )
    cell = ParagraphStyle(
        "IndexCell", parent=styles["BodyText"], fontSize=7.2, leading=9, textColor=NAVY
    )
    small = ParagraphStyle(
        "IndexSmall", parent=cell, fontSize=6.2, leading=7.5, textColor=MUTED
    )
    header_cell = ParagraphStyle(
        "IndexHeader",
        parent=cell,
        fontName="Helvetica-Bold",
        fontSize=7,
        textColor=colors.white,
    )
    table_rows: list[list[Paragraph]] = [
        [
            Paragraph("Document and SHA-256", header_cell),
            Paragraph("Type", header_cell),
            Paragraph("Review status", header_cell),
            Paragraph("File", header_cell),
        ]
    ]
    for row in rows:
        description, digest_and_notes = row.split("; SHA-256 ", 1)
        filename, kind_and_status = description.split(" - ", 1)
        kind, status = kind_and_status.split("; ", 1)
        digest, language, pages = digest_and_notes.split("; ", 2)
        table_rows.append(
            [
                Paragraph(f"<b>{escape(filename)}</b><br/><font size='6'>{escape(digest)}</font>", cell),
                Paragraph(escape(kind.replace("_", " ")), cell),
                Paragraph(escape(status.replace("_", " ")), cell),
                Paragraph(f"{escape(language)}<br/>{escape(pages)}", small),
            ]
        )
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=21 * mm,
        title="Document index",
        author="UK Visa Agent Demo",
        invariant=1,
    )
    table = Table(table_rows, colWidths=[71 * mm, 37 * mm, 45 * mm, 21 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story: list[Flowable] = [
        Paragraph(escape(label.replace("_", " ")), status_style),
        Paragraph("Document index", title_style),
        table,
    ]
    document.build(story, onFirstPage=_page_frame, onLaterPages=_page_frame)


def generate_pack(
    case: Case,
    policy: Policy,
    store: SQLiteStore,
    output_root: Path,
    today: date,
) -> tuple[Path | None, list[str]]:
    gate = evaluate_gate(case, policy, today)
    if not gate.allowed:
        return None, gate.reasons
    if case.delivery_path:
        return Path(case.delivery_path), []

    case_dir = output_root / case.id
    pack_dir = case_dir / "pack"
    audit_dir = case_dir / "audit"
    support_dir = pack_dir / "supporting_documents"
    support_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    label = CaseStatus.READY_FOR_HUMAN_REVIEW.value

    _pdf(
        pack_dir / "00_READ_ME_FIRST.pdf",
        "Read me first",
        [
            "This synthetic demo pack organises materials for human review. It is not legal advice, "
            "does not determine eligibility, does not submit an application, and does not predict an outcome.",
            policy.disclaimer,
            f"Policy snapshot: {policy.version}. Generated status: {label}.",
        ],
        label,
    )
    profile_rows = _profile_rows(case)
    _pdf(pack_dir / "01_case_summary.pdf", "Case summary", profile_rows, label)
    checklist = [
        f"{'Satisfied' if item.satisfied else 'Outstanding'} - {item.title} "
        f"(rule {item.id}, policy {item.rule_version})"
        for item in case.requirements
        if item.applicable
    ]
    _pdf(
        pack_dir / "02_personalised_document_checklist.pdf",
        "Personalised document checklist",
        checklist,
        label,
    )
    index_rows = [
        f"{doc.filename} - {doc.kind}; {doc.status}; SHA-256 {doc.sha256}; "
        f"language {doc.language}; pages {doc.page_count}"
        for doc in case.documents
    ]
    _document_index_pdf(pack_dir / "03_document_index.pdf", index_rows, label)
    _pdf(
        pack_dir / "04_cover_letter_draft.pdf",
        "Cover letter draft",
        [
            "For review by the Entry Clearance Officer.",
            f"I am {case.profile.full_name}. I plan to visit the United Kingdom from "
            f"{case.profile.planned_arrival_date} to {case.profile.planned_departure_date} "
            f"for {str(case.profile.visit_purpose).replace('_', ' ')}.",
            f"During the visit I plan to stay at {case.profile.uk_accommodation}. The estimated "
            f"trip cost is GBP {case.profile.estimated_trip_cost_gbp:,}.",
            f"My recorded occupation status is {str(case.profile.occupation_status).replace('_', ' ')}. "
            f"The recorded funding arrangement is "
            f"{str(case.profile.funding_source).replace('_', ' ')}.",
            "The accompanying index identifies the supporting documents and their review status. "
            "Please consider this draft together with the source documents.",
            f"Yours faithfully, {case.profile.full_name}.",
            "Adviser note: verify every statement and source reference before this draft is used.",
        ],
        f"DRAFT - {label}",
    )
    answers = {
        "case_id": case.id,
        "status": label,
        "policy_version": policy.version,
        "profile": case.profile.model_dump(mode="json"),
        "facts": [
            {
                "key": evidence.fact_key,
                "value": evidence.value,
                "source_event_id": evidence.source_event_id,
                "source_document_id": evidence.source_document_id,
                "source_excerpt": evidence.source_excerpt,
                "page": evidence.page,
                "confidence": evidence.confidence,
                "confirmed": evidence.confirmed,
                "provenance_state": evidence.provenance_state,
            }
            for evidence in case.evidence
            if not evidence.superseded
        ],
    }
    (pack_dir / "05_application_answers.json").write_text(
        json.dumps(answers, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    open_issues = [
        f"{item.severity}: {item.title} - {item.detail}"
        for item in case.issues
        if item.status == IssueStatus.OPEN
    ] or ["No open issues in the deterministic demo checks. Human review is still required."]
    _pdf(pack_dir / "06_open_issues.pdf", "Open issues", open_issues, label)
    for document in case.documents:
        destination = support_dir / document.filename
        if not destination.exists():
            shutil.copy2(document.path, destination)

    zip_path = output_root / f"visa_application_pack_{case.id}.zip"
    transition(case, CaseStatus.READY_FOR_HUMAN_REVIEW)
    case.stage = WorkflowStage.READY_FOR_HUMAN_REVIEW
    case.delivery_path = str(zip_path)

    (audit_dir / "evidence_ledger.json").write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in case.evidence],
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (audit_dir / "gate_result.json").write_text(
        gate.model_dump_json(indent=2), encoding="utf-8"
    )
    (audit_dir / "case_snapshot.json").write_text(
        case.model_dump_json(indent=2), encoding="utf-8"
    )
    (audit_dir / "rule_evaluations.json").write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in case.requirements],
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    _write_zip(pack_dir, zip_path)
    store.save_case(case)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    store.save_delivery(case.id, str(zip_path), digest)
    return zip_path, []
