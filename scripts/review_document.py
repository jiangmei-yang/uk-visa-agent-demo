"""Inspect/recover one retained Gmail attachment. Never approve documents or send mail.

The local operator identity is asserted, not authenticated. Retry may transmit the
selected PDF to the configured DeepSeek document reader; it needs the explicit
--allow-model-processing flag. That operator flag is not applicant privacy consent.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.documents.natural import DocumentReader, hold_unconfigured_live_pdf
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.document_review import recover_document
from visa_agent.workflow.review import review_fingerprint
from visa_agent.workflow.service import WorkflowService


def _cloud_reader(model_name: str) -> DocumentReader:
    # No Gmail auth/API, send adapter or fixture-document reader is imported here.
    from visa_agent.documents.natural import NaturalPDFReader
    from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
    from visa_agent.secrets import read_secret

    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=Path(".secrets/deepseek_api_key.txt"))
    if not key:
        raise ValueError("DeepSeek key is missing")
    return NaturalPDFReader(DeepSeekStructuredLLM(model_name, api_key=key))


def main(argv: Sequence[str] | None = None, *,
         reader_factory: Callable[[str], DocumentReader] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "retry", "replace"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--document", help="Original unreadable/unknown document ID")
    parser.add_argument("--replacement", help="Already received, normally classified replacement ID")
    parser.add_argument("--fingerprint")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--allow-model-processing", action="store_true",
                        help="Retry only: explicitly authorize this operator-requested cloud reread")
    args = parser.parse_args(argv)
    database = args.state_dir / "sandbox.db"
    if not database.is_file():
        parser.error("Existing Gmail state database required")
    if args.action != "inspect" and not all((args.document, args.fingerprint, args.actor, args.reason)):
        parser.error("Recovery requires --document, --fingerprint, --actor and --reason")
    if args.action == "replace" and not args.replacement:
        parser.error("Replace requires --replacement naming an already received document")
    if args.action != "replace" and args.replacement:
        parser.error("--replacement applies only to replace")
    if args.action == "retry" and not args.allow_model_processing:
        parser.error("Retry requires --allow-model-processing; applicant processing consent must already be established")
    if args.action != "retry" and args.allow_model_processing:
        parser.error("--allow-model-processing applies only to retry")
    with exclusive_state(args.state_dir):
        store = SQLiteStore(database)
        try:
            case = store.get_case(args.case)
            if case is None:
                parser.error("Case not found")
            if args.action == "inspect":
                print(json.dumps({"case_id": case.id, "status": case.status.value,
                    "fingerprint": review_fingerprint(case),
                    "documents": [{"id": doc.id, "filename": doc.filename, "kind": doc.kind,
                                   "status": doc.status.value, "source_event_id": doc.source_event_id,
                                   "retry_of_document_id": doc.retry_of_document_id,
                                   "supersedes_document_id": doc.supersedes_document_id}
                                  for doc in case.documents],
                    "open_blockers": [{"code": issue.code, "document_ids": issue.related_document_ids}
                                      for issue in case.open_blockers()]}, ensure_ascii=False, indent=2))
                return
            if reader_factory is None:
                # A real operator flag cannot substitute for an applicant's
                # consent or silently change the provider they were told about.
                from visa_agent.privacy.consent import ConsentLedger, ProcessingScope

                consent = ConsentLedger(store)
                configured = consent.scope()
                expected = ProcessingScope(provider="DeepSeek", model=args.model)
                if configured is None or (args.action == "retry" and configured.id != expected.id):
                    parser.error("Establish applicant consent for this provider/model through the registered Gmail service first")
                try:
                    consent.require(case)
                except ValueError as error:
                    parser.error(str(error))
            factory = reader_factory or _cloud_reader
            def read_selected(path: Path):
                # Delay key access/client construction until the recovery API has
                # checked fingerprint, scope, eligibility and source integrity.
                return factory(args.model)(path)
            workflow = WorkflowService(store,
                load_policy(Path(__file__).resolve().parents[1] / "knowledge/uk_standard_visitor_2026-02-25.yaml"),
                OfflineFixtureLLM(),
                document_reader=read_selected if args.action == "retry" else hold_unconfigured_live_pdf)
            try:
                action_id = recover_document(workflow, case_id=args.case, document_id=args.document,
                    replacement_document_id=args.replacement, expected_fingerprint=args.fingerprint,
                    actor=args.actor, reason=args.reason)
            except ValueError as error:
                parser.error(str(error))
            updated = store.get_case(args.case)
            old = next(doc for doc in updated.documents if doc.id == args.document)
            print(json.dumps({"action_id": action_id,
                              "outcome": "recovered" if old.status.value == "SUPERSEDED" else "still_blocked",
                              "open_blocker_count": len(updated.open_blockers()),
                              "new_fingerprint": review_fingerprint(updated)}, indent=2))
            print("No document manually approved, applicant event fabricated, message sent or pack generated. Fresh summary confirmations are required.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
