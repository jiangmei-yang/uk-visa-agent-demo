"""Synthetic pack-materialization QA, not live intake or applicant authorization.

Seeds reviewed fixture documents and explicit synthetic confirmations to exercise
the production sponsor review and pack gates. Refuses an existing output root.
"""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.delivery.pack import _pdf, generate_pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import CaseStatus, Document, DocumentStatus, Evidence
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.service import WorkflowService
from visa_agent.workflow.sponsor_location import LOCATION_REVIEW_REASON
from visa_agent.workflow.sponsor_location_review import review_sponsor_location


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    documents = root / "sources"
    generate_sample_documents(documents)
    policy = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
    store = SQLiteStore(root / "synthetic.db")
    try:
        service = WorkflowService(store, policy, OfflineFixtureLLM(), today_provider=lambda: DEMO_EVALUATION_DATE)
        for path in sorted(Path("samples/emails").glob("*.eml")):
            case, _, _ = service.process(parse_eml(path, documents))
        # Fixture preconditions only: these are not real reviewed applicant data.
        for key, value in {"funding_source": "personal_sponsor", "sponsor_name": "Mina Example",
                           "sponsor_relationship": "mother", "sponsor_address": "12 Fictional Road, Hong Kong"}.items():
            setattr(case.profile, key, value)
            for item in case.active_evidence(key):
                item.superseded = True
            case.evidence.append(Evidence(id="fixture-" + key, fact_key=key, value=value,
                source_event_id="sponsor-fixture", source_excerpt=f"SYNTHETIC FIXTURE: {key}={value}",
                extraction_method="synthetic_qa_setup", model_version="no_model", confidence=1))
        case.profile.sponsor_is_in_uk = None
        for document in case.documents:
            if document.kind == "funding_letter":
                document.status = DocumentStatus.SUPERSEDED
        for event_id, body in (
            ("location-original", "My sponsor lives in the UK but is not in the UK now."),
            ("location-correction", "My sponsor does not live in the UK."),
        ):
            case.sponsor_location_statements.extend(item.model_copy(update={"identity_epoch": case.sponsor_location_epoch})
                for item in parse_sponsor_location_statements(body,
                    source_event_id=event_id, sponsor_name="Mina Example", sponsor_relationship="mother"))
            with store.connection:
                store.connection.execute("INSERT INTO processed_events(event_id,case_id) VALUES (?,?)", (event_id, case.id))
        for kind in ("sponsor_letter", "sponsor_funds", "relationship_evidence"):
            path = documents / (kind + ".pdf")
            _pdf(path, "Fictional sponsor material", ["SYNTHETIC QA - NOT REAL EVIDENCE",
                "Applicant: Lin Chen", "Sponsor: Mina Example (mother)", f"Document kind: {kind}",
                "Fixture used to test pack inclusion, not adequacy of financial support.",
                f"FACT _fixture_document_contract={kind}"], "FICTIONAL QA", compact=True)
            case.documents.append(Document(id=kind, filename=path.name, kind=kind,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mime_type="application/pdf",
                status=DocumentStatus.ACCEPTED_FOR_REVIEW, source_event_id="sponsor-fixture", path=str(path), page_count=1))
            if kind == "sponsor_funds":
                case.evidence.append(Evidence(id="fixture-sponsor-funds", fact_key="_fixture_document_contract",
                    value=kind, source_document_id=kind, source_event_id="sponsor-fixture",
                    source_excerpt=f"FACT _fixture_document_contract={kind}",
                    extraction_method="deterministic_pdf_fixture_extractor", model_version="synthetic", confidence=1))
        case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
        case.human_review_reason = LOCATION_REVIEW_REASON
        store.save_case(case)
        blocked, _ = generate_pack(case, policy, store, root / "pack", DEMO_EVALUATION_DATE)
        assert blocked is None
        review_sponsor_location(store, case_id=case.id, expected_fingerprint=review_fingerprint(case),
            actor="Fictional QA operator", rationale="Synthetic resolution fixture, not a real applicant review.",
            policy=policy, today=DEMO_EVALUATION_DATE, selected_source_event_ids={"residence": "location-correction"})
        case = store.get_case(case.id)
        assert case is not None
        blocked, _ = generate_pack(case, policy, store, root / "pack", DEMO_EVALUATION_DATE)
        assert blocked is None and not case.profile_confirmed and not case.final_summary_confirmed
        # Explicit fixture precondition, not proof of email confirmation handling.
        case.profile_confirmed = case.final_summary_confirmed = True
        store.save_case(case)
        gate = evaluate_gate(case, policy, DEMO_EVALUATION_DATE)
        assert gate.allowed, gate.reasons
        archive, reasons = generate_pack(case, policy, store, root / "pack", DEMO_EVALUATION_DATE)
        assert archive is not None, reasons
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None
            answers = json.loads(package.read("05_application_answers.json"))
            report = {"scope": "synthetic pack materialization; confirmations and document review seeded",
                "model_calls": 0, "mailbox_calls": 0, "all_gate_checks": gate.checks,
                "zip": str(archive), "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "members": package.namelist(), "answer_keys": list(answers), "answers": answers}
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return report
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New, isolated QA directory")
    print(json.dumps(run(parser.parse_args().output), indent=2, ensure_ascii=False))
