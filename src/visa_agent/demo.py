from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from visa_agent.channels.email_fixture import parse_eml
from visa_agent.channels.outbound_fixture import write_outbound_eml
from visa_agent.config import Settings
from visa_agent.delivery.pack import generate_pack
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import Case
from visa_agent.domain.policy import load_policy
from visa_agent.llm.offline import OfflineFixtureLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

DEMO_EVALUATION_DATE = date(2026, 9, 2)


@dataclass
class DemoResult:
    case: Case
    package_path: Path
    report_path: Path
    counts: dict[str, int]


def run_demo(
    settings: Settings,
    reset: bool = False,
    evaluation_date: date = DEMO_EVALUATION_DATE,
) -> DemoResult:
    if reset:
        if settings.output_dir.exists():
            shutil.rmtree(settings.output_dir)
        if settings.database_path.exists():
            settings.database_path.unlink()
    document_dir = settings.output_dir / "synthetic_documents"
    outbound_dir = settings.output_dir / "outbound_inbox"
    generate_sample_documents(document_dir)
    policy = load_policy(settings.policy_path)
    store = SQLiteStore(settings.database_path)
    service = WorkflowService(
        store,
        policy,
        OfflineFixtureLLM(),
        today_provider=lambda: evaluation_date,
    )
    steps: list[dict[str, object]] = []
    case: Case | None = None
    try:
        for eml_path in sorted(Path("samples/emails").glob("*.eml")):
            event = parse_eml(eml_path, document_dir)
            case, duplicate, plan = service.process(event)
            package, reasons = generate_pack(
                case,
                policy,
                store,
                settings.output_dir,
                evaluation_date,
            )
            steps.append(
                {
                    "fixture": eml_path.name,
                    "duplicate": duplicate,
                    "plan": plan,
                    "stage": case.stage,
                    "status": case.status,
                    "package_generated": package is not None,
                    "gate_reasons": reasons,
                    "open_blockers": [item.code for item in case.open_blockers()],
                }
            )

        if case is None or case.delivery_path is None:
            raise RuntimeError("Demo did not produce the required package")

        for index, item in enumerate(store.list_outbox(), start=1):
            write_outbound_eml(
                outbound_dir,
                index,
                case.applicant_contact,
                case.external_thread_id,
                item["event_id"],
                item["payload"],
            )

        counts_before_replay = store.counts()
        for eml_path in sorted(Path("samples/emails").glob("*.eml")):
            service.process(parse_eml(eml_path, document_dir))
        counts_after_replay = store.counts()
        replay_idempotent = counts_before_replay == counts_after_replay
        report = {
            "synthetic_demo": True,
            "evaluation_date": evaluation_date.isoformat(),
            "case_id": case.id,
            "steps": steps,
            "counts_before_replay": counts_before_replay,
            "counts_after_replay": counts_after_replay,
            "replay_idempotent": replay_idempotent,
            "package_path": case.delivery_path,
        }
        report_path = settings.output_dir / "demo_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        if not replay_idempotent:
            raise RuntimeError("Replay changed persistent counts")
        return DemoResult(
            case=case,
            package_path=Path(case.delivery_path),
            report_path=report_path,
            counts=counts_after_replay,
        )
    finally:
        store.close()
