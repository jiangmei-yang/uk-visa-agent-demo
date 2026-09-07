"""Materialization from an isolated ready-state fixture, NOT a real client approval."""

from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZipFile

from pypdf import PdfReader
from test_pack_materialization_recovery import _ready_unmaterialized

from visa_agent.delivery.pack import generate_pack
from visa_agent.delivery.simulation import MANIFEST_NAME, require_simulation_archive
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.domain.simulation import SimulationRegistration, SpecimenEntry
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.workflow.conversation import confirmation_message


def test_pack_summary_and_ready_reply_retain_simulation_scope(tmp_path):
    store, case, policy, output = _ready_unmaterialized(tmp_path)
    try:
        # Represent an already registered and confirmed fixture state. Registration
        # itself has separate tests prohibiting retroactive conversion in service.
        case.primary_channel = "gmail"
        scope = SimulationRegistration(case_id=case.id,
            external_thread_id=case.external_thread_id, applicant_contact=case.applicant_contact,
            operator="fixture", reason="Isolated already-confirmed simulation materialization fixture",
            registered_at=datetime.now(UTC), specimens=(SpecimenEntry(filename="passport.pdf",
                sha256="a" * 64, kind="passport"),))
        case.simulation = scope
        store.save_case(case)
        with store.connection:
            store.connection.execute("INSERT INTO simulation_registrations(case_id,registration_json) VALUES (?,?)",
                                     (case.id, scope.model_dump_json()))
        archive, reasons = generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)
        assert archive is not None, reasons
        assert archive.name.startswith("fictional_preparation_pack_")
        require_simulation_archive(case, archive.read_bytes())
        with ZipFile(archive) as zipped:
            assert MANIFEST_NAME in zipped.namelist()
            generated = [name for name in zipped.namelist() if name.endswith(".pdf") and "/" not in name]
            for name in generated:
                for page in PdfReader(BytesIO(zipped.read(name))).pages:
                    assert "FICTIONAL DEMONSTRATION" not in page.extract_text()
                    assert "NOT FOR APPLICATION" not in page.extract_text()
            readme = " ".join(page.extract_text() for page in PdfReader(BytesIO(zipped.read("00_READ_ME_FIRST.pdf"))).pages)
            assert "uses example information" in readme
            for name in generated:
                if name != "00_READ_ME_FIRST.pdf":
                    assert all("uses example information" not in page.extract_text()
                               for page in PdfReader(BytesIO(zipped.read(name))).pages)
        assert "fictional" in deterministic_fallback_message(case, "ready")
        assert "fictional" in confirmation_message(case)
        with store.connection:
            store.connection.execute("DELETE FROM simulation_registrations WHERE case_id=?", (case.id,))
        assert generate_pack(case, policy, store, output, DEMO_EVALUATION_DATE)[0] is None
    finally:
        store.close()
