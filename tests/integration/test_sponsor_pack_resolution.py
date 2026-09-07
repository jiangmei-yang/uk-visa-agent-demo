import json
import runpy
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

QA = runpy.run_path(str(Path(__file__).parents[2] / "scripts/sponsor_pack_resolution_qa.py"))


def test_resolved_sponsor_materializes_consistent_synthetic_pack(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("No network allowed")
    monkeypatch.setattr("socket.socket.connect", deny)
    report = QA["run"](tmp_path / "isolated")
    assert all(report["all_gate_checks"].values())
    assert report["model_calls"] == report["mailbox_calls"] == 0
    with zipfile.ZipFile(report["zip"]) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        assert "supporting_documents/funding_letter.pdf" not in names
        assert "supporting_documents/conference_invitation_original.pdf" not in names
        assert not any("audit" in name or "case_snapshot" in name for name in names)
        assert all(f"supporting_documents/{kind}.pdf" in names
                   for kind in ("sponsor_letter", "sponsor_funds", "relationship_evidence"))
        answers = json.loads(archive.read("05_application_answers.json"))
        assert "sponsor_is_in_uk" not in answers["profile"]
        residence = answers["sponsor_location"]["dimensions"]["residence"]
        assert residence["value"] is False
        assert [item["source_event_id"] for item in residence["sources"]] == ["location-correction"]
        summary = "\n".join(page.extract_text() for page in PdfReader(BytesIO(archive.read("01_case_summary.pdf"))).pages)
        assert "Sponsor lives in the UK (as reported by you): No" in summary
        assert "completeness have not yet been confirmed" not in summary
        assert "applicant declarations are not independent verification" in summary
        assert "You explicitly stated there are no relevant records." in summary
        assert "Fictional QA operator" not in summary and "location-original" not in summary
    with pytest.raises(FileExistsError):
        QA["run"](tmp_path / "isolated")
