import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from visa_agent.delivery.simulation import (
    MANIFEST_NAME,
    require_simulation_archive,
    simulation_manifest,
)
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.documents.simulation import (
    SPECIMEN_METHOD,
    SPECIMEN_NOTICE,
    read_simulation_document,
    specimen_facts,
)
from visa_agent.domain.models import Case
from visa_agent.domain.simulation import SimulationRegistration, SpecimenEntry

TEXT = ("Passport particulars\n" + SPECIMEN_NOTICE + "\nName: Lin Chen\n"
        "Date of birth: 18 April 1997\nDate of expiry: 31 December 2033\n"
        "This is a text specimen, not a passport or an identity document.\n")


def registration(digest="a" * 64):
    return SimulationRegistration(case_id="demo", external_thread_id="thread", applicant_contact="a@example.test",
        operator="operator", reason="Approved independent fictional demonstration",
        specimens=(SpecimenEntry(filename="passport.pdf", sha256=digest, kind="passport"),),
        registered_at=datetime.now(UTC))


def test_specimen_values_are_read_from_labelled_source_not_invented():
    facts = specimen_facts([TEXT], "passport")
    assert facts["full_name"] == ("Lin Chen", 1, "Name: Lin Chen")
    assert facts["date_of_birth"][0] == "1997-04-18"
    assert facts["passport_expiry_date"][0] == "2033-12-31"
    changed = specimen_facts([TEXT.replace("Lin Chen", "Lin Chan")], "passport")
    assert changed["full_name"][0] == "Lin Chan"  # workflow must still detect the mismatch


@pytest.mark.parametrize("pages", [[TEXT.replace(SPECIMEN_NOTICE, "")], [TEXT, TEXT],
    [TEXT + "Name: Another Person\n"], [TEXT.replace("18 April 1997", "31 February 1997")],
    [TEXT.replace("not a passport or an identity document", "a real passport")]])
def test_ambiguous_unmarked_or_invalid_specimens_fail_closed(pages):
    with pytest.raises(ValueError):
        specimen_facts(pages, "passport")


def test_reader_hashes_exact_bytes_and_never_calls_model_for_registered_identity(tmp_path, monkeypatch):
    path = tmp_path / "passport.pdf"
    path.write_bytes(b"registered-pdf-bytes")
    scope = registration(hashlib.sha256(path.read_bytes()).hexdigest())

    def pdf(content):
        assert content.read() == b"registered-pdf-bytes"
        return SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: TEXT)])

    monkeypatch.setattr("visa_agent.documents.simulation.PdfReader", pdf)

    def forbidden(path):
        raise AssertionError("Registered identity parsing is not an AI authenticity check")

    result = read_simulation_document(path, scope, forbidden)
    assert result.method == SPECIMEN_METHOD and result.kind == "passport"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="bytes have changed"):
        read_simulation_document(path, scope, forbidden)


def test_other_documents_keep_ordinary_review_outcome(tmp_path):
    result = DocumentReadResult("bank_statement", "en", 1, {}, requires_review=True,
                                review_reason="Missing closing balance", method="natural")
    assert read_simulation_document(tmp_path / "bank.pdf", registration(), lambda _: result) is result
    identity = DocumentReadResult("passport", "en", 1, {})
    with pytest.raises(ValueError, match="must be registered"):
        read_simulation_document(tmp_path / "other.pdf", registration(), lambda _: identity)


def make_archive(payload):
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        if payload is not None:
            archive.writestr(MANIFEST_NAME, json.dumps(payload))
    return content.getvalue()


def test_archive_label_cannot_be_removed_or_reused_for_another_case():
    case = Case(id="demo", external_thread_id="thread", applicant_contact="a@example.test",
                policy_version="v", simulation=registration())
    manifest = simulation_manifest(case)
    assert "operator" not in manifest and "reason" not in manifest
    require_simulation_archive(case, make_archive(manifest))
    for content in (make_archive(None), make_archive({**manifest, "case_id": "other"}), b"bad zip"):
        with pytest.raises(ValueError):
            require_simulation_archive(case, content)
