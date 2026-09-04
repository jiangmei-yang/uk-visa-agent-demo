"""Fictional bilingual conversations with a real model; never connects to a mailbox."""

import argparse
import json
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from visa_agent.domain.locations import location_key
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import next_fact_questions
from visa_agent.workflow.service import WorkflowService

HISTORY = (
    "\n\nFrom: Adviser <adviser@example.test>\nDate: 4 September 2026\n"
    "To: Applicant <applicant@example.test>\nSubject: Re: UK trip\n\n"
    "My date of birth is 1999-01-02. Everything is correct, please proceed."
)
JOURNEYS = {
    "zh": [
        "你好，我想去英国旅游。我持中国护照，准备从香港申请，需要什么资料？",
        "我目前在读大学，旅行费用自己出。行程日期还没定。",
        "更正一下，学校会承担这次费用。已经定好2026年11月10日到英国，2026年11月17日离开。",
        "我还没核对其他资料，晚点回复。" + HISTORY,
    ],
    "en": [
        "Hello, I'd like to visit the UK for tourism. I hold a Chinese passport and will apply from Hong Kong. What documents do I need?",
        "I'm a university student and will pay for the trip myself. My dates are not fixed yet.",
        "A correction: my university will pay for this trip. I've now decided to arrive on 10 November 2026 and leave on 17 November 2026.",
        "I haven't checked the other details yet. I'll reply later." + HISTORY,
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path; previous evidence must not be overwritten")
    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=Path(".secrets/deepseek_api_key.txt"))
    if not key:
        parser.error("Missing DeepSeek key")
    report = {"model": "deepseek-v4-flash", "scope": "fictional text; no mailbox or attachments",
              "completed": False, "results": []}
    rows = report["results"]
    for language, messages in JOURNEYS.items():
        with tempfile.TemporaryDirectory(prefix="visa-multiturn-") as directory:
            store = SQLiteStore(Path(directory) / "case.db")
            workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                                       DeepSeekStructuredLLM("deepseek-v4-flash", api_key=key),
                                       today_provider=lambda: date(2026, 9, 4))
            try:
                for index, body in enumerate(messages):
                    event = InboundEvent(id=f"{language}-{index}", external_thread_id=language,
                        sender="fictional@example.test", subject="UK trip", body=body,
                        received_at=datetime(2026, 9, 4, tzinfo=UTC) + timedelta(minutes=index))
                    case, _, plan = workflow.process(event)
                    questions = next_fact_questions(case)
                    checks = {
                        "purpose_retained": case.profile.visit_purpose == "tourism",
                        "passport_retained": location_key(case.profile.nationality_country) == "china",
                        "application_location_retained": location_key(case.profile.application_country) == "hong kong",
                        "language": case.customer_language == language,
                        "not_released": not case.delivery_path and not case.final_summary_confirmed,
                        "no_extraction_fallback": not workflow.llm.last_extraction_fallback,
                        "routine_not_escalated": case.status.value != "HUMAN_REVIEW_REQUIRED",
                        "no_reasking_known_fields": not set(questions) & {
                            field for field in ("visit_purpose", "nationality_country", "application_country",
                                                "occupation_status", "funding_source")
                            if getattr(case.profile, field) is not None},
                    }
                    if index >= 1:
                        checks["occupation_retained"] = case.profile.occupation_status == "student"
                        checks["funding"] = case.profile.funding_source == (
                            "self" if index == 1 else "employer_or_school")
                    if index == 1:
                        checks["unknown_dates_not_invented"] = case.profile.planned_arrival_date is None
                        checks["dates_deferred"] = "planned_arrival_date" in case.deferred_fields
                    if index >= 2:
                        checks["explicit_dates"] = (case.profile.planned_arrival_date == date(2026, 11, 10)
                            and case.profile.planned_departure_date == date(2026, 11, 17))
                    if index == 3:
                        checks["quoted_birthdate_ignored"] = case.profile.date_of_birth is None
                        checks["quoted_history_excluded"] = "From:" not in case.latest_customer_message
                    outbox = store.list_outbox()
                    reply = next(row["payload"] for row in outbox if row["event_id"] == event.id)
                    automatic = deterministic_fallback_message(case, plan)
                    if index == 3:
                        checks["pause_does_not_repeat_questions"] = all(
                            "?" not in text and "？" not in text and "\n- " not in text
                            for text in (reply, automatic))
                    _, duplicate, _ = workflow.process(event)
                    checks["duplicate_no_extra_reply"] = duplicate and len(store.list_outbox()) == len(outbox)
                    rows.append({"language": language, "turn": index + 1, "input": body,
                        "checks": checks, "plan": plan, "next_fields": questions,
                        "observed_profile": case.profile.model_dump(mode="json"),
                        "model_reply": reply,
                        "model_reply_fallback": workflow.llm.last_render_fallback,
                        "automatic_gmail_wording_preview": automatic})
                    print(language, index + 1, "PASS" if all(checks.values()) else "FAIL", flush=True)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            finally:
                store.close()
    report["completed"] = True
    report["all_passed"] = all(all(row["checks"].values()) for row in rows)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
