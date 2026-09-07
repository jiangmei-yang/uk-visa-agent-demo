"""Real specimen PDFs through normal workflow; no provider or mailbox calls."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from scripts.gmail_tourism_documents import generate
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import Case, DocumentStatus, InboundEvent, ProvenanceState
from visa_agent.domain.policy import load_policy
from visa_agent.domain.simulation import SpecimenEntry
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.simulation import register_simulation
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


class Model:
    def extract_case_patch(self, event):
        return CasePatch(updates=[], ambiguities=[])

    render_message = staticmethod(deterministic_fallback_message)


def test_registered_identity_is_simulated_but_other_case_keeps_review(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("No model or mailbox calls in simulation integration test")

    monkeypatch.setattr("socket.socket.connect", no_network)
    docs = tmp_path / "specimens"
    generate(docs)
    path = docs / "passport.pdf"
    store = SQLiteStore(tmp_path / "cases.db")
    try:
        policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
        case = Case(id="demo", external_thread_id="demo-thread", applicant_contact="a@example.test",
                    primary_channel="gmail", policy_version=policy.version)
        store.save_case(case)
        register_simulation(store, case_id=case.id, external_thread_id=case.external_thread_id,
                            applicant_contact=case.applicant_contact, operator="operator",
                            reason="User approved independent fictional demonstration",
                            specimens=(SpecimenEntry(filename=path.name,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), kind="passport"),))
        ordinary_calls = []

        def ordinary(path):
            ordinary_calls.append(path)
            return DocumentReadResult("passport", "en", 1, {}, method="natural",
                                      requires_review=True, review_reason="Specimen is not an identity document")

        workflow = WorkflowService(store, policy, Model(), document_reader=ordinary)
        event = InboundEvent(id="first", external_thread_id="demo-thread", sender="a@example.test",
                             channel="gmail", subject="英国旅行材料", body="附上资料。",
                             received_at=datetime.now(UTC), attachment_paths=[str(path)])
        result, _, _ = workflow.process(event)
        assert result.simulation is not None
        assert ordinary_calls == []
        assert result.documents[0].status == DocumentStatus.ACCEPTED_FOR_REVIEW
        assert all(item.provenance_state == ProvenanceState.DEMO_SYNTHETIC
                   for item in result.evidence if item.source_document_id)
        assert not result.final_summary_confirmed and result.delivery_path is None
        other = event.model_copy(update={"id": "other", "external_thread_id": "ordinary-thread"})
        ordinary_case, _, _ = workflow.process(other)
        assert ordinary_case.simulation is None
        assert ordinary_calls == [path]
        assert ordinary_case.documents[0].status == DocumentStatus.HUMAN_REVIEW_REQUIRED
        assert ordinary_case.delivery_path is None
    finally:
        store.close()
