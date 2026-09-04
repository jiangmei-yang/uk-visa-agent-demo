"""Bounded real-model consultant-content probe: fictional text, captured Gmail only.

This measures specified behaviours, not a naturalness score or live mail delivery.
No applicant files, existing case data, Gmail credentials or Gmail API are used.
The production reviewed sender intentionally uses reviewed wording; only extraction
calls DeepSeek. All cases run once and all outcomes, including failures, are kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, ROUTE_CHECK_URL
from visa_agent.workflow.conversation import reply_items
from visa_agent.workflow.service import WorkflowService

CASES = [
    ("first-enquiry", "我想办英国签证，需要什么？", {}),
    ("student", "我拿中国护照，在香港读大学，也在香港申请。想去英国旅游，费用自己出，"
     "要等学校放假安排才能定哪天出发回来。请帮我准备申请。",
     {"occupation_status": "student", "visit_purpose": "tourism", "funding_source": "self"}),
    ("employed", "我持中国护照，会在香港申请，去英国旅游。我在公司上班，自己承担费用，"
     "旅行日期还没定，先帮我准备起来吧。",
     {"occupation_status": "employed", "visit_purpose": "tourism", "funding_source": "self"}),
    ("parents", "中国护照，在香港申请，去英国旅游。我现在工作了，但这次费用由父母资助。"
     "旅行日期没定，想先准备材料。",
     {"occupation_status": "employed", "visit_purpose": "tourism", "funding_source": "personal_sponsor"}),
    ("family", "我持中国护照，在香港上班，准备在香港申请签证去英国探望姐姐，住她家。"
     "旅行费用自己付，旅行日期还没有确定。先帮我准备吧。",
     {"occupation_status": "employed", "visit_purpose": "family_or_friends", "funding_source": "self"}),
    ("mixed-request", "不用讲费用但请把访问签证申请官网发我。另外，我存两万元是不是一定能获批？", {}),
    ("separate-routes", "Where do I apply for my UK visitor visa? Separately, what is the fee for a student visa?", {}),
]
CONTACT = "fictional-consultant-probe@example.test"


class ExtractionOnly(DeepSeekStructuredLLM):
    extraction_attempts: int = 0
    proposed_patch: dict[str, Any] | None = None
    extraction_error_type: str | None = None
    render_message = staticmethod(deterministic_fallback_message)

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.extraction_attempts += 1
        try:
            patch = super().extract_case_patch(event)
            self.proposed_patch = patch.model_dump(mode="json")
            return patch
        except Exception as error:
            self.extraction_error_type = type(error).__name__
            raise


class CapturedGmail(GmailAdapter):
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["recipient"] == CONTACT and kwargs.get("attachment") is None
        self.bodies.append(kwargs["body"])
        return {"id": f"captured-consultant-{len(self.bodies)}"}


def checks_for(kind: str, case: Any, reply: str, expected: dict[str, str]) -> dict[str, bool]:
    import re

    checks = {
        "expected_profile": all(getattr(case.profile, key) == value for key, value in expected.items()),
        "at_most_one_main_question": len(reply_items(case)[1]) <= 1,
        "no_confirmation_or_release": not (case.profile_confirmed or case.final_summary_confirmed or case.delivery_path),
    }
    if kind == "first-enquiry":
        checks["official_orientation"] = ROUTE_CHECK_URL in reply or APPLICATION_URL in reply
        checks["route_not_assumed"] = case.profile.visit_purpose is None and not case.profile.route_confirmed_standard_visitor
    elif kind in {"student", "employed", "parents", "family"}:
        fields = {"planned_arrival_date", "planned_departure_date"}
        checks["undecided_dates_retained"] = fields <= set(case.deferred_fields)
        checks["no_date_fabrication_or_request"] = all(getattr(case.profile, field) is None for field in fields) and not fields.intersection(case.last_requested_fields)
        checks["official_source"] = "https://www.gov.uk/" in reply
        if kind == "student":
            checks["contextual_action"] = "在读证明" in reply and "资金来源" in reply
        elif kind == "employed":
            checks["contextual_action"] = "在职证明" in reply and "职位" in reply
        elif kind == "parents":
            checks["contextual_action"] = "资助" in reply and "关系" in reply and "资金" in reply
        else:
            checks["contextual_action"] = "探亲" in reply and "住宿" in reply
            # Visiting/staying with a relative is not, by itself, a statement
            # that this person is the applicant's financial sponsor.
            checks["host_not_silently_recorded_as_sponsor"] = all(
                getattr(case.profile, field) is None for field in
                ("sponsor_name", "sponsor_relationship", "sponsor_is_in_uk")
            )
        checks["no_wrong_student_template"] = kind == "student" or "在读证明" not in reply
    else:
        checks["independent_visitor_link_answered"] = APPLICATION_URL in reply
        checks["no_visitor_fee_for_other_question"] = "135" not in reply
        checks["no_unrequested_intake"] = case.last_requested_fields == []
        # Bounded lexical proxies for an actual second answer, not a naturalness
        # score. A visitor URL or a repetition of the customer's question is not
        # evidence that their independent question was answered.
        statements = [
            sentence for sentence in re.split(r"(?<=[。!?！？])|(?<=\.)(?=\s|$)|\n", reply.casefold())
            if sentence.strip() and not sentence.rstrip().endswith(("?", "？"))
        ]
        if kind == "mixed-request":
            checks["savings_outcome_question_answered"] = any(
                any(term in sentence for term in ("存款", "余额", "储蓄"))
                and any(term in sentence for term in ("申请结果", "获批", "通过", "签证结果"))
                and any(term in sentence for term in ("不能", "无法", "不足以", "不代表", "并不"))
                for sentence in statements
            )
        elif kind == "separate-routes":
            checks["student_fee_question_answered"] = any(
                "student visa" in sentence
                and "fee" in sentence
                and any(term in sentence for term in ("check", "verify", "refer to", "consult"))
                for sentence in statements
            )
            checks["student_fee_official_source"] = "https://www.gov.uk/student-visa" in reply
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-calls", action="store_true", help="Authorize at most seven fictional DeepSeek extraction calls")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.allow_model_calls:
        parser.error("Explicit --allow-model-calls required; this probe may incur API charges")
    if args.output.exists():
        parser.error("Choose a new path; previous evidence must not be overwritten")
    key = read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=Path(".secrets/deepseek_api_key.txt"))
    if not key:
        parser.error("Missing DeepSeek key")
    report: dict[str, Any] = {"scope": "fictional text; real extraction; actual automatic sender with captured transport",
        "new_provider_result": True, "model": args.model, "mailbox_calls": 0,
        "not_a_naturalness_score": True, "completed": False, "results": [],
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [
            Path(__file__).relative_to(Path.cwd()) if Path(__file__).is_absolute() else Path(__file__),
            *sorted(Path("src/visa_agent").rglob("*.py")),
        ]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(report, output)
    with tempfile.TemporaryDirectory(prefix="visa-consultant-probe-") as directory:
        for index, (kind, text, expected) in enumerate(CASES):
            # Explicit diagnostic capture of these fictional texts only. The
            # production adapter's default remains raw-response capture off.
            model = ExtractionOnly(args.model, api_key=key, capture_raw_responses=True)
            store = SQLiteStore(Path(directory) / f"{kind}.db")
            row: dict[str, Any] = {"id": kind, "input": text, "checks": {}, "completed": False}
            try:
                workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                    GuardedLLM(model, max_attempts=1), today_provider=lambda: date(2026, 9, 4))
                event = InboundEvent(id=kind, channel="gmail", external_thread_id=kind, sender=CONTACT,
                    subject="Visitor application preparation", body=text,
                    received_at=datetime.now(UTC) + timedelta(seconds=index))
                case, _, plan = workflow.process(event)
                capture = CapturedGmail()
                outcomes = OutboxDispatcher(store, AutomaticGmailReplySender(capture, store, CONTACT),
                    channel="gmail", allowed_message_types=("blocked",)).dispatch_due(event.received_at)
                actual = store.list_outbox()[-1]
                reply = actual["payload"]
                row.update({"plan": plan, "reply": reply, "render_mode": actual["reply_render_mode"],
                    "profile": case.profile.model_dump(mode="json"), "topics": case.customer_question_topics,
                    "deferred_fields": case.deferred_fields,
                    "requested_fields": case.last_requested_fields, "checks": checks_for(kind, case, reply, expected)})
                row["checks"].update({"no_extraction_fallback": not workflow.llm.last_extraction_fallback,
                    "actual_sender_captured_once": len(capture.bodies) == 1 and capture.bodies[0] == reply and len(outcomes) == 1 and outcomes[0].status == "SENT"})
                row["completed"] = True
            except Exception as error:
                row["error_type"] = type(error).__name__  # Provider text may contain sensitive context.
            finally:
                row["usage"] = model.usage_history
                row["extraction_attempts"] = model.extraction_attempts
                row["raw_model_content"] = model.last_extraction_content
                row["proposed_patch"] = model.proposed_patch
                row["extraction_error_type"] = model.extraction_error_type
                store.close()
            report["results"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(kind, "PASS" if row["completed"] and all(row["checks"].values()) else "FAIL", flush=True)
    report["completed"] = True
    report["all_passed"] = all(row["completed"] and all(row["checks"].values()) for row in report["results"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
