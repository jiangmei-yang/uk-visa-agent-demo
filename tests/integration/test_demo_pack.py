from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from visa_agent.config import Settings
from visa_agent.demo import run_demo

EXPECTED_PACK_FILES = {
    "00_READ_ME_FIRST.pdf",
    "01_case_summary.pdf",
    "02_personalised_document_checklist.pdf",
    "03_document_index.pdf",
    "04_cover_letter_draft.pdf",
    "05_application_answers.json",
    "06_open_issues.pdf",
}


def test_demo_generates_source_linked_pack_and_is_idempotent(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    first = run_demo(settings, reset=True)
    second = run_demo(settings, reset=False)
    assert first.counts == second.counts == {
        "cases": 1,
        "processed_events": 3,
        "outbox": 3,
        "deliveries": 1,
    }
    assert first.package_path.read_bytes() == second.package_path.read_bytes()
    with zipfile.ZipFile(first.package_path) as archive:
        names = set(archive.namelist())
        assert names >= EXPECTED_PACK_FILES
        assert any(name.startswith("supporting_documents/") for name in names)
        answers = json.loads(archive.read("05_application_answers.json"))
        assert answers["status"] == "READY_FOR_HUMAN_REVIEW"
        assert all(item["source_event_id"] for item in answers["facts"])
        summary_text = PdfReader(BytesIO(archive.read("01_case_summary.pdf"))).pages[0].extract_text()
        cover_text = PdfReader(BytesIO(archive.read("04_cover_letter_draft.pdf"))).pages[0].extract_text()
        assert "Funding source: Employer or school" in summary_text
        assert "Sponsor name: Not applicable" in summary_text
        assert "The estimated trip cost is GBP 2,200" in cover_text
        assert "Adviser note: verify every statement" in cover_text
    snapshot = json.loads(
        next((first.package_path.parent / first.case.id / "audit").glob("case_snapshot.json"))
        .read_text(encoding="utf-8")
    )
    assert snapshot["status"] == "READY_FOR_HUMAN_REVIEW"
    assert snapshot["stage"] == "READY_FOR_HUMAN_REVIEW"
    assert snapshot["delivery_path"] == str(first.package_path)
    translated_original = next(
        item for item in snapshot["documents"] if item["filename"] == "family_funds_cn.pdf"
    )
    assert translated_original["status"] == "ACCEPTED_FOR_REVIEW"


def test_clean_runs_generate_identical_pack_bytes(tmp_path: Path) -> None:
    policy_path = Path("knowledge/uk_standard_visitor_2026-02-25.yaml")
    first = run_demo(
        Settings(
            database_path=tmp_path / "first.db",
            output_dir=tmp_path / "first-output",
            policy_path=policy_path,
        ),
        reset=True,
    )
    second = run_demo(
        Settings(
            database_path=tmp_path / "second.db",
            output_dir=tmp_path / "second-output",
            policy_path=policy_path,
        ),
        reset=True,
    )
    assert first.package_path.read_bytes() == second.package_path.read_bytes()


def test_offline_demo_remains_replayable_after_policy_review_date(tmp_path: Path) -> None:
    result = run_demo(
        Settings(
            database_path=tmp_path / "future.db",
            output_dir=tmp_path / "future-output",
            policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
        ),
        reset=True,
    )
    assert result.package_path.is_file()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["evaluation_date"] == "2026-09-02"
