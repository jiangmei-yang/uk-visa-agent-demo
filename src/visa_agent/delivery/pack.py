from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.platypus.flowables import Flowable

from visa_agent.domain.models import Case, CaseStatus, IssueStatus, WorkflowStage
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import evaluate_gate, transition
from visa_agent.storage.sqlite import SQLiteStore

FIXED_ZIP_TIME = (2026, 2, 25, 0, 0, 0)


def _pdf(
    path: Path,
    title: str,
    paragraphs: list[str],
    label: str,
    *,
    compact: bool = False,
) -> None:
    styles = getSampleStyleSheet()
    if compact:
        styles["BodyText"].fontSize = 8.5
        styles["BodyText"].leading = 10.5
    document = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="UK Visa Agent Demo",
        invariant=1,
    )
    story: list[Flowable] = [
        Paragraph(label, styles["Heading3"]),
        Paragraph(title, styles["Title"]),
    ]
    paragraph_gap = 2 * mm if compact else 4 * mm
    for value in paragraphs:
        story.extend([Spacer(1, paragraph_gap), Paragraph(value, styles["BodyText"])])
    document.build(story)


def _write_zip(source_dir: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(source_dir).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def _document_index_pdf(path: Path, rows: list[str], label: str) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, _ = A4
    pdf.setTitle("Document index")
    pdf.setAuthor("UK Visa Agent Demo")
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(18 * mm, 790, label)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawCentredString(width / 2, 755, "Document index")
    y = 710
    for row in rows:
        description, digest_and_notes = row.split("; SHA-256 ", 1)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(18 * mm, y, description)
        y -= 11
        pdf.setFont("Helvetica", 8)
        pdf.drawString(18 * mm, y, f"SHA-256 {digest_and_notes}")
        y -= 19
    pdf.save()


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
    profile_rows = [
        f"{name.replace('_', ' ').title()}: {value if value is not None else 'UNKNOWN'}"
        for name, value in case.profile.model_dump(mode="json").items()
    ]
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
            f"Applicant: {case.profile.full_name}.",
            f"Proposed visit: {case.profile.planned_arrival_date} to "
            f"{case.profile.planned_departure_date} for {case.profile.visit_purpose}.",
            f"Funding arrangement recorded as: {case.profile.funding_source}.",
            "Please check every statement and supporting reference before using this draft.",
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

    zip_path = output_root / f"visa_application_pack_{case.id}.zip"
    _write_zip(pack_dir, zip_path)
    transition(case, CaseStatus.READY_FOR_HUMAN_REVIEW)
    case.stage = WorkflowStage.READY_FOR_HUMAN_REVIEW
    case.delivery_path = str(zip_path)
    store.save_case(case)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    store.save_delivery(case.id, str(zip_path), digest)
    return zip_path, []
