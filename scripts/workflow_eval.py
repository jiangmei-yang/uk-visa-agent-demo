from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from visa_agent.config import Settings
from visa_agent.delivery.pack import generate_pack
from visa_agent.demo import DEMO_EVALUATION_DATE
from visa_agent.documents.samples import generate_sample_documents
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import FORBIDDEN_REPLY_CLAIMS
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def _event(
    step: int,
    body: str,
    attachments: list[Path],
) -> InboundEvent:
    return InboundEvent(
        id=f"live-workflow-{step}",
        channel="evaluation",
        external_thread_id="live-workflow-synthetic-lin-chen",
        sender="Lin Chen <lin.chen@example.test>",
        subject=(
            "Standard Visitor documents for London conference"
            if step == 1
            else "Re: Standard Visitor documents for London conference"
        ),
        body=body,
        attachment_paths=[str(path) for path in attachments],
        received_at=datetime(2026, 9, 2, 8 + step, tzinfo=UTC),
    )


def _events(document_dir: Path) -> list[InboundEvent]:
    first = """Hello,

My full name is Lin Chen and I was born on 1997-04-18. I am Chinese and my nationality country is China. I am applying from Hong Kong under the Standard Visitor route to attend a conference. I plan to arrive on 2026-09-10 and leave on 2026-09-15. I will stay at Northstar Hotel, London. My estimated trip cost is GBP 2200 and my current home address is 88 Synthetic Road, Hong Kong.

I am a student with annual income of GBP 18000. My main funding source is my university: it will pay the return flight and hotel, while I will pay only personal incidentals. I have no criminal convictions, civil judgments, visa refusals, removals, or immigration breaches.

I have reviewed the profile above.

PROFILE CONFIRMED

Regards,
Lin"""
    second = """Hello,

The organiser corrected the invitation. The conference now runs from 2026-09-11 to 2026-09-14, within my existing trip dates. I have also attached the complete certified translation of the Chinese supporting page.

Regards,
Lin"""
    third = """Hello,

I reviewed the final facts summary and the listed source documents.

I CONFIRM THE FINAL SUMMARY

Regards,
Lin"""
    first_files = [
        "passport.pdf",
        "conference_invitation_original.pdf",
        "student_letter.pdf",
        "school_funding_letter.pdf",
        "bank_statement.pdf",
        "hong_kong_residence_status.pdf",
        "family_funds_cn.pdf",
    ]
    second_files = [
        "conference_invitation_corrected.pdf",
        "family_funds_certified_translation.pdf",
    ]
    return [
        _event(1, first, [document_dir / name for name in first_files]),
        _event(2, second, [document_dir / name for name in second_files]),
        _event(3, third, []),
    ]


def _reply_checks(message: str, plan: str, blockers: list[str]) -> dict[str, bool]:
    normalised = message.casefold()
    checks = {
        "non_empty": bool(message.strip()),
        "within_length_limit": len(message) <= 4_000,
        "no_prohibited_outcome_claim": not any(
            claim in normalised for claim in FORBIDDEN_REPLY_CLAIMS
        ),
        "every_open_blocker_named": all(item.casefold() in normalised for item in blockers),
        "confirmation_boundary_present": (
            plan != "awaiting_confirmation"
            or "i confirm the final summary" in normalised
        ),
        "no_premature_pack_release_claim": (
            plan != "awaiting_confirmation"
            or not any(
                claim in normalised
                for claim in ("pack is ready", "pack has been prepared", "pack is released")
            )
        ),
        "human_review_boundary_present": (
            plan != "ready" or ("human" in normalised and "review" in normalised)
        ),
    }
    return checks


def run_once(model: str, api_key: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="visa-agent-workflow-eval-") as raw_dir:
        root = Path(raw_dir)
        settings = Settings(
            database_path=root / "data" / "visa.db",
            output_dir=root / "output",
            policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
        )
        document_dir = root / "documents"
        generate_sample_documents(document_dir)
        policy = load_policy(settings.policy_path)
        delegate = DeepSeekStructuredLLM(model=model, api_key=api_key)
        store = SQLiteStore(settings.database_path)
        service = WorkflowService(
            store,
            policy,
            delegate,
            today_provider=lambda: DEMO_EVALUATION_DATE,
        )
        step_reports: list[dict[str, Any]] = []
        expected_plans = ["blocked", "awaiting_confirmation", "ready"]
        expected_blockers = [
            {"Travel and invitation dates differ", "Certified translation required"},
            set(),
            set(),
        ]
        latencies: list[float] = []
        try:
            for step, event in enumerate(_events(document_dir), start=1):
                started = time.perf_counter()
                case, duplicate, plan = service.process(event)
                package, gate_reasons = generate_pack(
                    case,
                    policy,
                    store,
                    settings.output_dir,
                    DEMO_EVALUATION_DATE,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                latencies.append(latency_ms)
                gate = evaluate_gate(case, policy, DEMO_EVALUATION_DATE)
                message = next(
                    str(row["payload"])
                    for row in store.list_outbox()
                    if row["event_id"] == event.id
                )
                blockers = {item.title for item in case.open_blockers()}
                reply_checks = _reply_checks(message, plan, sorted(blockers))
                step_checks = {
                    "not_duplicate": not duplicate,
                    "expected_plan": plan == expected_plans[step - 1],
                    "expected_blockers": blockers == expected_blockers[step - 1],
                    "pack_release_boundary": (package is not None) == (step == 3),
                    "gate_release_boundary": gate.allowed == (step == 3),
                    "extraction_did_not_fallback": not service.llm.last_extraction_fallback,
                    **reply_checks,
                }
                step_reports.append(
                    {
                        "step": step,
                        "plan": plan,
                        "stage": case.stage.value,
                        "open_blockers": sorted(blockers),
                        "gate_reasons": gate_reasons,
                        "reply": message,
                        "model_reply_accepted": not service.llm.last_render_fallback,
                        "render_fallback_reason": service.llm.last_render_error,
                        "latency_ms": round(latency_ms, 2),
                        "checks": step_checks,
                        "passed": all(step_checks.values()),
                    }
                )
            assert package is not None
            pack_hash = hashlib.sha256(package.read_bytes()).hexdigest()
            all_checks = [
                passed
                for step_report in step_reports
                for passed in step_report["checks"].values()
            ]
            input_tokens = sum(
                int(item.get("input_tokens", 0)) for item in delegate.usage_history
            )
            output_tokens = sum(
                int(item.get("output_tokens", 0)) for item in delegate.usage_history
            )
            return {
                "synthetic": True,
                "provider": "deepseek",
                "model": model,
                "evaluation_date": DEMO_EVALUATION_DATE.isoformat(),
                "scope": "natural-language intake through deterministic pack release",
                "steps": step_reports,
                "metrics": {
                    "workflow_steps_passed": sum(item["passed"] for item in step_reports),
                    "workflow_step_count": len(step_reports),
                    "individual_checks_passed": sum(all_checks),
                    "individual_check_count": len(all_checks),
                    "model_replies_accepted": sum(
                        item["model_reply_accepted"] for item in step_reports
                    ),
                    "reply_count": len(step_reports),
                    "provider_call_count": len(delegate.usage_history),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms_median": round(statistics.median(latencies), 2),
                    "latency_ms_max": round(max(latencies), 2),
                    "pack_sha256": pack_hash,
                },
                "passed": all(all_checks)
                and all(item["model_reply_accepted"] for item in step_reports),
                "limitations": [
                    "All applicant data and documents are synthetic.",
                    "The document reader is the deterministic PDF fixture extractor, not production OCR.",
                    "This run does not exercise Gmail, WhatsApp, or a real applicant.",
                ],
            }
        finally:
            store.close()


def run_suite(model: str, api_key: str, repeats: int) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least one")
    runs = [run_once(model, api_key) for _ in range(repeats)]
    signatures = {
        json.dumps(
            [
                {
                    "plan": step["plan"],
                    "stage": step["stage"],
                    "open_blockers": step["open_blockers"],
                    "passed": step["passed"],
                }
                for step in run["steps"]
            ],
            sort_keys=True,
        )
        for run in runs
    }
    latencies = [
        float(step["latency_ms"])
        for run in runs
        for step in run["steps"]
    ]
    metrics = {
        "workflow_runs_passed": sum(run["passed"] for run in runs),
        "workflow_run_count": repeats,
        "workflow_steps_passed": sum(
            run["metrics"]["workflow_steps_passed"] for run in runs
        ),
        "workflow_step_count": sum(run["metrics"]["workflow_step_count"] for run in runs),
        "individual_checks_passed": sum(
            run["metrics"]["individual_checks_passed"] for run in runs
        ),
        "individual_check_count": sum(
            run["metrics"]["individual_check_count"] for run in runs
        ),
        "model_replies_accepted": sum(
            run["metrics"]["model_replies_accepted"] for run in runs
        ),
        "reply_count": sum(run["metrics"]["reply_count"] for run in runs),
        "provider_call_count": sum(
            run["metrics"]["provider_call_count"] for run in runs
        ),
        "input_tokens": sum(run["metrics"]["input_tokens"] for run in runs),
        "output_tokens": sum(run["metrics"]["output_tokens"] for run in runs),
        "semantic_repeat_consistency_rate": 1.0 if len(signatures) == 1 else 0.0,
        "latency_ms_median": round(statistics.median(latencies), 2),
        "latency_ms_max": round(max(latencies), 2),
        "unique_pack_hash_count": len(
            {run["metrics"]["pack_sha256"] for run in runs}
        ),
    }
    return {
        "synthetic": True,
        "provider": "deepseek",
        "model": model,
        "evaluation_date": DEMO_EVALUATION_DATE.isoformat(),
        "scope": "natural-language intake through deterministic pack release",
        "repeat_count": repeats,
        "runs": [
            {"repeat": index, **run} for index, run in enumerate(runs, start=1)
        ],
        "metrics": metrics,
        "passed": all(run["passed"] for run in runs) and len(signatures) == 1,
        "limitations": runs[0]["limitations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate one complete synthetic DeepSeek workflow and applicant reply path."
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval_output/deepseek_workflow_eval.json"),
    )
    args = parser.parse_args()
    api_key = read_secret(
        "DEEPSEEK_API_KEY",
        file_environment_name="DEEPSEEK_API_KEY_FILE",
        default_file=Path(".secrets/deepseek_api_key.txt"),
    )
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is not configured; no provider call was made.")
    report = run_suite(args.model, api_key, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["metrics"], indent=2))
    print(f"Full report: {args.output}")
    if not report["passed"]:
        raise SystemExit("Full workflow evaluation did not pass every release check.")


if __name__ == "__main__":
    main()
