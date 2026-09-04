from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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

from visa_agent.domain.models import (
    Case,
    CaseStatus,
    DocumentStatus,
    GateResult,
    IssueStatus,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy
from visa_agent.domain.rules import evaluate_gate, transition
from visa_agent.privacy.consent import ConsentLedger
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
    small = ParagraphStyle("IndexSmall", parent=cell, fontSize=6.2, leading=7.5, textColor=MUTED)
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
                Paragraph(
                    f"<b>{escape(filename)}</b><br/><font size='6'>{escape(digest)}</font>", cell
                ),
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
    # Serialize materialization and registration with persisted pause/resume changes.
    # Nested save_delivery/save_case calls must not commit this transaction early.
    with store.atomic_write():
        rejection = _preparation_control_rejection(case, store)
        if rejection:
            return None, [rejection]
        path, reasons = _generate_pack(case, policy, store, output_root, today)
    # Do not publish an in-memory ready path before the outer SQLite commit succeeds.
    if path is not None and case.delivery_path is None:
        case.status = CaseStatus.READY_FOR_HUMAN_REVIEW
        case.stage = WorkflowStage.READY_FOR_HUMAN_REVIEW
        case.delivery_path = str(path)
    return path, reasons


def _preparation_control_rejection(case: Case, store: SQLiteStore) -> str | None:
    current = store.get_case(case.id)
    if not ConsentLedger(store).allowed(current or case):
        return "Processing consent is required; pack generation and access are withheld."
    if case.preparation_paused or (current is not None and current.preparation_paused):
        return "Preparation is paused; pack generation and access are withheld."
    if current is not None and case.preparation_control_epoch != current.preparation_control_epoch:
        return "Preparation control changed; reload the current case before generating a pack."
    if current is not None and case.delivery_revision != current.delivery_revision:
        return "Delivery revision changed; reload the current case before generating a pack."
    return None


def _generate_pack(
    case: Case,
    policy: Policy,
    store: SQLiteStore,
    output_root: Path,
    today: date,
) -> tuple[Path | None, list[str]]:
    if store.has_unreviewed_held_updates(case.id):
        return None, ["Retained applicant updates still require review before pack generation."]
    gate = evaluate_gate(case, policy, today)
    if not gate.allowed:
        return None, gate.reasons
    registered = store.connection.execute(
        "SELECT path, sha256, case_revision FROM deliveries WHERE case_id=?", (case.id,),
    ).fetchone()
    if case.delivery_path:
        if (registered is None or registered["path"] != case.delivery_path
                or registered["case_revision"] != case.delivery_revision):
            return None, ["Existing pack does not match the current delivery revision."]
        if not _registered_archive_is_intact(registered["path"], registered["sha256"], output_root):
            return None, ["Registered pack is missing, unreadable or changed; recover its original bytes before continuing."]
        return Path(case.delivery_path), []
    if registered is not None:
        if registered["case_revision"] == case.delivery_revision:
            return None, ["Registered pack has no matching case path; recover its recorded state, do not rebuild it."]
        if (registered["case_revision"] != case.delivery_revision - 1
                or not _registered_archive_is_intact(registered["path"], registered["sha256"], output_root)):
            return None, ["A revised pack requires its intact registered predecessor."]
        # A legitimate next revision keeps the predecessor; save_delivery still checks
        # that its operator-authorized revision was actually processed.
    if store.connection.execute(
        "SELECT 1 FROM delivery_versions WHERE case_id=? AND case_revision>=?",
        (case.id, case.delivery_revision),
    ).fetchone():
        return None, ["Historical artifacts already exist for this revision; do not rebuild or replace them."]
    if any(
        row["case_id"] == case.id
        and int(row.get("case_revision", 1)) == case.delivery_revision
        and row["message_type"] == "ready"
        and (int(row["attempt_count"]) > 0 or row["status"] in {"SENDING", "SENT", "AMBIGUOUS"})
        for row in store.list_outbox()
    ):
        return None, [
            "A delivery send was already attempted; keep its original artifact immutable."
        ]

    if case.id in {"", ".", ".."} or "/" in case.id or "\\" in case.id:
        return None, ["Case artifact identifier must be a single safe path component."]
    case_dir = output_root / case.id
    if case.delivery_revision > 1:
        case_dir = case_dir / f"revision-{case.delivery_revision}"
    revision_suffix = f"_revision-{case.delivery_revision}" if case.delivery_revision > 1 else ""
    zip_path = output_root / f"visa_application_pack_{case.id}{revision_suffix}.zip"
    try:
        _check_materialization_paths(case_dir, zip_path, output_root)
        zip_location = zip_path.resolve()
        case_location = case_dir.resolve()
        for row in store.connection.execute("SELECT path FROM deliveries UNION SELECT path FROM delivery_versions"):
            registered_location = Path(row["path"]).resolve()
            if (registered_location in {zip_location, case_location}
                    or case_location in registered_location.parents):
                return None, ["Target artifacts include a registered archive; its historical path cannot be replaced."]
    except (OSError, RuntimeError, ValueError):
        return None, ["Pack artifact paths are unavailable or outside the configured output directory."]
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pack-stage-", dir=output_root) as directory:
        staging = Path(directory)
        return _materialize_fresh_pack(
            case, policy, store, gate, output_root, case_dir, zip_path, staging,
        )


def _registered_archive_is_intact(path: str, digest: str, output_root: Path) -> bool:
    try:
        archive = Path(path).resolve()
        if output_root.resolve() not in archive.parents or not archive.is_file():
            return False
        return hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    except (OSError, RuntimeError):
        return False


def _check_materialization_paths(case_dir: Path, zip_path: Path, output_root: Path) -> None:
    root = output_root.resolve()
    for path in (case_dir, case_dir / "pack", case_dir / "audit",
                 case_dir / "pack" / "supporting_documents", zip_path):
        if root not in path.resolve().parents:
            raise ValueError("Materialization paths must stay inside the configured output directory")


def _publish_fresh_materialization(
    staged_case: Path, staged_zip: Path, case_dir: Path, zip_path: Path, output_root: Path,
) -> None:
    _check_materialization_paths(case_dir, zip_path, output_root)
    _check_materialization_paths(staged_case, staged_zip, output_root)
    # Existing unregistered partials are evidence of an interrupted attempt, not
    # inputs for a new pack. Preserve them without deleting or merging their files.
    if case_dir.exists() or case_dir.is_symlink() or zip_path.exists() or zip_path.is_symlink():
        quarantine = Path(tempfile.mkdtemp(prefix=".unregistered-pack-", dir=output_root))
        if case_dir.exists() or case_dir.is_symlink():
            os.replace(case_dir, quarantine / "case-tree")
        if zip_path.exists() or zip_path.is_symlink():
            os.replace(zip_path, quarantine / "archive.zip")
    case_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_case, case_dir)
    os.replace(staged_zip, zip_path)


def _materialize_fresh_pack(
    case: Case, policy: Policy, store: SQLiteStore, gate: GateResult, output_root: Path,
    final_case_dir: Path, zip_path: Path, staging: Path,
) -> tuple[Path | None, list[str]]:
    for document in case.documents:
        if document.status != DocumentStatus.ACCEPTED_FOR_REVIEW:
            continue
        if (not document.filename.strip() or document.filename in {".", ".."}
                or "/" in document.filename or "\\" in document.filename):
            raise ValueError("Accepted supporting document filename must be one non-empty safe path component.")
    case_dir = staging / "case-tree"
    staged_zip = staging / "archive.zip"
    _check_materialization_paths(case_dir, staged_zip, output_root)
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
            "This preparation pack organises materials for human review. It is not legal advice, "
            "does not determine eligibility, does not submit an application, and does not predict an outcome.",
            f"Delivery revision: {case.delivery_revision}. This version is a preparation record, "
            "not proof of an application submitted to UKVI.",
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
        if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
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
        "delivery_revision": case.delivery_revision,
        "purpose": "preparation_for_human_review",
        "submits_application": False,
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
    ] or ["No open issues in the recorded preparation checks. Human review is still required."]
    _pdf(pack_dir / "06_open_issues.pdf", "Open issues", open_issues, label)
    for document in case.documents:
        if document.status != DocumentStatus.ACCEPTED_FOR_REVIEW:
            continue
        destination = support_dir / document.filename
        shutil.copy2(document.path, destination)
    # Verify the final staged bytes, not a source that could change during copying.
    for document in case.documents:
        if document.status == DocumentStatus.ACCEPTED_FOR_REVIEW:
            staged_digest = hashlib.sha256((support_dir / document.filename).read_bytes()).hexdigest()
            if staged_digest != document.sha256:
                raise ValueError("Accepted supporting document bytes no longer match their recorded SHA-256.")
    prepared_case = case.model_copy(deep=True)
    if prepared_case.status != CaseStatus.READY_FOR_HUMAN_REVIEW:
        transition(prepared_case, CaseStatus.READY_FOR_HUMAN_REVIEW)
    prepared_case.stage = WorkflowStage.READY_FOR_HUMAN_REVIEW
    prepared_case.delivery_path = str(zip_path)

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
    (audit_dir / "gate_result.json").write_text(gate.model_dump_json(indent=2), encoding="utf-8")
    (audit_dir / "case_snapshot.json").write_text(prepared_case.model_dump_json(indent=2), encoding="utf-8")
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

    _write_zip(pack_dir, staged_zip)
    digest = hashlib.sha256(staged_zip.read_bytes()).hexdigest()
    rejection = _preparation_control_rejection(case, store)
    if rejection:
        return None, [rejection]
    store.save_delivery(case.id, str(zip_path), digest, case_revision=case.delivery_revision)
    store.save_case(prepared_case)
    _publish_fresh_materialization(case_dir, staged_zip, final_case_dir, zip_path, output_root)
    return zip_path, []
