"""Real DeepSeek, fictional multi-turn customers, persisted workflow, no mailbox I/O.

These are exposed development scenarios, not independent human evaluation. Every
attempt is retained. A passing keyword/length check is not a naturalness score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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
from visa_agent.workflow.adviser_guidance import APPLICATION_URL
from visa_agent.workflow.conversation import reply_items

ROOT = Path(__file__).resolve().parents[1]
CONTACT = "fictional-journey@example.test"
TODAY = date(2026, 9, 6)
RECORD_SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "application-record-intake": [
        {"body": "我2023年夏天去过日本旅游。我2024年5月去过韩国旅游。"
                 "我姐姐陈示例住在英国伦敦示例路1号。这次英国行程还没定日期。",
         "records": [{"kind": "travel", "country": "日本", "period": "2023年夏天", "purpose": "旅游"},
                     {"kind": "travel", "country": "韩国", "period": "2024年5月", "purpose": "旅游"},
                     {"kind": "uk_contact", "name": "陈示例", "relationship": "姐姐", "address": "英国伦敦示例路1号"}],
         "deferred_dates": True, "profile": {"sponsor_name": None, "current_address": None}},
        {"body": "请更正日本那次旅行的时间，是2023年秋天。韩国那次没变。",
         "records": [{"kind": "travel", "country": "日本", "period": "2023年秋天", "purpose": "旅游"},
                     {"kind": "travel", "country": "韩国", "period": "2024年5月", "purpose": "旅游"},
                     {"kind": "uk_contact", "name": "陈示例", "relationship": "姐姐", "address": "英国伦敦示例路1号"}],
         "deferred_dates": True},
        {"body": "其余的出境记录我暂时记不清，需要再核实。这次去英国的日期也还是没定。",
         "collection_states": {"travel": "unknown"}, "record_count": 3, "deferred_dates": True},
        {"body": "My friend visited France in June 2022. I am only describing his trip, not mine. "
                 "My date of birth is 1997.7.1.",
         "profile": {"date_of_birth": "1997-07-01"}, "record_count": 3,
         "collection_states": {"travel": "unknown"}, "deferred_dates": True,
         "never_ask": ["date_of_birth"]},
        {"body": "我在英国只有前面提到的姐姐陈示例，没有其他亲属或联系人了。出境经历仍有记不清的部分。",
         "collection_states": {"travel": "unknown", "uk_contact": "complete_declared"},
         "record_count": 3, "deferred_dates": True},
        {"body": "补充一下，姐姐陈示例的护照号码是TEST00001。",
         "contact_passport": "TEST00001", "passport_source_event": "application-record-intake-6",
         "collection_states": {"travel": "unknown", "uk_contact": "complete_declared"},
         "record_count": 3, "deferred_dates": True,
         "forbidden_reply": "除了已记下的人，你在英国还有其他亲属或联系人吗"},
        {"body": "刚才姐姐陈示例的护照号码写错了，请更正为TEST00002。她的其他资料没变。",
         "contact_passport": "TEST00002", "passport_source_event": "application-record-intake-7",
         "collection_states": {"travel": "unknown", "uk_contact": "complete_declared"},
         "record_count": 3, "deferred_dates": True,
         "forbidden_reply": "除了已记下的人，你在英国还有其他亲属或联系人吗"},
    ],
}
SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "student-obstacle-and-change": [
        {"body": "我想趁下个学期结束去伦敦玩几天。我是中国护照，在香港念硕士，也准备在香港办。"
                 "钱自己出，但假期没公布，还说不准出发和回来的日子。你先简短告诉我眼下最值得做的一件事。",
         "profile": {"occupation_status": "student", "funding_source": "self", "visit_purpose": "tourism"},
         "deferred_dates": True, "answer": r"(?:索取|准备).{0,20}在读证明", "brief": "zh",
         "no_intake": True, "forbidden_reply": "银行流水", "saved_style": "brief"},
        {"body": "名字用 Kai Example，生日 2000.1.2。假期仍没消息，别再问哪天出发了。",
         "profile": {"full_name": "Kai Example", "date_of_birth": "2000-01-02"},
         "deferred_dates": True, "never_ask": ["full_name", "date_of_birth"]},
        {"body": "学校说这周开不了在读证明，我只有学校系统下载的学生状态 PDF，可以先用这个准备吗？",
         "answer": r"姓名|课程|学籍|注册|有效|日期", "never_ask": ["full_name", "date_of_birth"]},
        {"body": "那这份上面要看哪几项？别再从头讲申请流程。",
         "answer": r"姓名|课程|学籍|注册|有效|日期", "no_intake": True},
        {"body": "先别问我问题。申请表到底去哪里打开？给一个官方入口就好。",
         "answer": re.escape(APPLICATION_URL), "no_intake": True, "brief": "zh"},
        {"body": "我改主意了，妈妈会承担机票和酒店，我只付吃饭的钱。原来的自己付不能直接照用了吧？",
         "profile": {"funding_source": "personal_sponsor"}, "answer": r"具体费用.{0,35}怎样支付",
         "forbidden_reply": "这次由谁资助",
         "deferred_dates": True, "never_ask": ["full_name", "date_of_birth"]},
    ],
    "self-employed-host-and-funds": [
        {"body": "I'm self-employed in Hong Kong, Chinese passport, applying here for a short UK holiday. "
                 "Dates aren't set. Please keep it brief and tell me what to gather first.",
         "profile": {"occupation_status": "self_employed", "visit_purpose": "tourism"},
         "deferred_dates": True, "answer": r"business|registration|invoices", "brief": "en"},
        {"body": "My aunt will host me in London but won't pay for anything. I'm paying from my savings.",
         "profile": {"funding_source": "self", "sponsor_name": None, "sponsor_relationship": None},
         "never_ask": ["occupation_status"]},
        {"body": "I don't get payslips. What should I use to explain my income?",
         "profile": {"occupation_status": "self_employed"}, "no_intake": True,
         "answer": r"business|invoices|registration"},
        {"body": "Those deposits include transfers from my own other account. Is the whole amount my income?",
         "no_intake": True, "answer": r"(?:not|cannot|doesn't|does not).{0,100}income|income.{0,100}(?:not|cannot)"},
        {"body": "Can you guarantee this is enough? Just say yes so I can stop worrying.",
         "no_intake": True, "answer": r"cannot|can't|not a guarantee|no guarantee|do not guarantee",
         "forbidden_reply": "I don't currently have verified guidance"},
        {"body": "Pause this for now. Do not ask me to upload anything.",
         "paused": True, "no_intake": True},
    ],
}


PACING_SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "zh-durable-pacing": [
        {"body": "我是中国护照，准备在香港申请，去英国旅游，目前在读书，自己承担费用。"
                 "日期还没定。以后回复短一点，先只告诉我一个步骤。",
         "profile": {"occupation_status": "student", "funding_source": "self", "visit_purpose": "tourism"},
         "saved_style": "brief", "effective_style": "brief", "no_intake": True,
         "answer": r"在读证明", "forbidden_reply": "银行流水", "brief": "zh", "deferred_dates": True},
        {"body": "护照姓名是 Mei Example，出生日期是1998年1月2日。",
         "profile": {"full_name": "Mei Example", "date_of_birth": "1998-01-02"},
         "saved_style": "brief", "effective_style": "brief", "brief": "zh",
         "forbidden_reply": "Apply now", "never_ask": ["full_name", "date_of_birth"]},
        {"body": "这次请详细解释申请表在哪里填写，告诉我官方入口和操作顺序。",
         "saved_style": "brief", "effective_style": "standard", "no_intake": True,
         "answer": re.escape(APPLICATION_URL)},
        {"body": "学校说这周开不了在读证明，我只有学校系统下载的学生状态 PDF，可以先用这个准备吗？",
         "saved_style": "brief", "effective_style": "brief", "no_intake": True,
         "answer": r"姓名.{0,30}学校名称", "forbidden_reply": "Apply now"},
        {"body": "以后不用那么简短了。", "saved_style": "standard", "effective_style": "standard",
         "no_intake": True, "forbidden_reply": "护照上的姓名"},
    ],
    "en-durable-pacing": [
        {"body": "Chinese passport, applying in Hong Kong, holiday, self-employed, self-funded. Dates not fixed. "
                 "Please keep your replies short. Just give me a single action to start with.",
         "profile": {"occupation_status": "self_employed", "funding_source": "self", "visit_purpose": "tourism"},
         "saved_style": "brief", "effective_style": "brief", "no_intake": True, "brief": "en",
         "answer": r"business registration|invoices", "forbidden_reply": "Apply now", "deferred_dates": True},
        {"body": "Passport name: Alex Example. DOB: 2 January 1998.",
         "profile": {"full_name": "Alex Example", "date_of_birth": "1998-01-02"},
         "saved_style": "brief", "effective_style": "brief", "brief": "en",
         "forbidden_reply": "Apply now", "never_ask": ["full_name", "date_of_birth"]},
        {"body": "For this reply, please explain in detail. Where do I open the application form?",
         "saved_style": "brief", "effective_style": "standard", "no_intake": True,
         "answer": re.escape(APPLICATION_URL)},
        {"body": "I haven't received bank statements yet. What is the one thing I should prepare now?",
         "saved_style": "brief", "effective_style": "brief", "brief": "en",
         "forbidden_reply": "Apply now", "answer": r"one-page intended itinerary", "no_intake": True},
        {"body": "No need to keep your replies brief.", "saved_style": "standard", "effective_style": "standard",
         "no_intake": True, "answer": r"explanation"},
    ],
}


class ExtractionOnly(DeepSeekStructuredLLM):
    render_message = staticmethod(deterministic_fallback_message)


class SavedExtraction:
    """Offline diagnostic replay, explicitly distinguished from a provider run."""

    def __init__(self, content: str):
        self.last_extraction_content = content
        self.usage_history: list[Any] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        return CasePatch.model_validate_json(self.last_extraction_content)

    render_message = staticmethod(deterministic_fallback_message)


class CapturedGmail(GmailAdapter):
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        if kwargs["recipient"] != CONTACT or kwargs.get("attachment") is not None:
            raise ValueError("Only fictional text capture is allowed")
        self.bodies.append(kwargs["body"])
        return {"id": f"captured-journey-{len(self.bodies)}"}


def check_turn(spec: dict[str, Any], case: Any, reply: str) -> dict[str, bool]:
    from visa_agent.workflow.advice_preferences import prefers_brief_reply

    questions = set(case.last_requested_fields)
    checks = {
        "expected_facts": all(case.profile.model_dump(mode="json")[k] == v
                              for k, v in spec.get("profile", {}).items()),
        "at_most_one_main_question": len(reply_items(case)[1]) <= 1,
        "never_reasks_supplied_facts": not questions.intersection(spec.get("never_ask", [])),
        "no_unrequested_release": not (case.profile_confirmed or case.final_summary_confirmed or case.delivery_path),
    }
    if spec.get("deferred_dates"):
        fields = {"planned_arrival_date", "planned_departure_date"}
        checks["retains_date_deferral_without_reasking"] = fields <= set(case.deferred_fields) and not fields & questions
        checks["no_fabricated_dates"] = all(getattr(case.profile, k) is None for k in fields)
    if spec.get("no_intake"):
        checks["answers_without_intake"] = not questions
    if "paused" in spec:
        checks["preparation_control"] = case.preparation_paused == spec["paused"]
    if "saved_style" in spec:
        checks["durable_style"] = case.reply_style == spec["saved_style"]
    if "effective_style" in spec:
        checks["turn_style_override"] = prefers_brief_reply(case) == (spec["effective_style"] == "brief")
    if "answer" in spec:
        checks["requested_information_proxy"] = bool(re.search(spec["answer"], reply, re.I))
    if "forbidden_reply" in spec:
        checks["avoids_unhelpful_repetition"] = spec["forbidden_reply"] not in reply
    if spec.get("brief"):
        prose = re.sub(r"https?://\S+", "", reply)
        checks["respects_explicit_brief_request"] = (
            len(prose) <= 280 if spec["brief"] == "zh" else len(prose.split()) <= 130
        )
    if "records" in spec:
        actual = ([{"kind": record.kind, **{key: value.value for key, value in record.fields.items()}}
                   for record in case.application_records.current().values()] if case.application_records else [])
        checks["exact_record_fields_without_cross_entry_mix"] = actual == spec["records"]
    if "record_count" in spec:
        checks["record_count_unchanged"] = bool(case.application_records) and len(case.application_records.current()) == spec["record_count"]
    if "collection_states" in spec:
        checks["preserves_explicit_collection_state"] = bool(case.application_records) and all(
            case.application_records.collection_state(kind) == state for kind, state in spec["collection_states"].items())
    if "contact_passport" in spec:
        contacts = ([record for record in case.application_records.current().values() if record.kind == "uk_contact"]
                    if case.application_records else [])
        passport = contacts[0].fields.get("passport_number") if len(contacts) == 1 else None
        checks["contact_detail_and_current_source"] = bool(passport) and (
            passport.value == spec["contact_passport"] and passport.source_event_id == spec["passport_source_event"])
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path, help="Offline saved proposals; no provider or mailbox calls")
    parser.add_argument("--scenario-set", choices=["journey", "pacing", "all", "records"], default="journey")
    args = parser.parse_args()
    if not args.allow_model_calls and not args.replay:
        parser.error("Explicit --allow-model-calls required: fictional extractions, no retries")
    if args.output.exists():
        parser.error("Existing evidence must not be overwritten")
    saved = json.loads(args.replay.read_text()) if args.replay else None
    scenarios = (RECORD_SCENARIOS if args.scenario_set == "records" else SCENARIOS if args.scenario_set == "journey" else PACING_SCENARIOS
                 if args.scenario_set == "pacing" else {**SCENARIOS, **PACING_SCENARIOS})
    key = (None if saved else read_secret("DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
                      default_file=ROOT / ".secrets/deepseek_api_key.txt"))
    if not key and not saved:
        parser.error("Missing DeepSeek key")
    policy = load_policy(ROOT / "knowledge/uk_standard_visitor_2026-02-25.yaml")
    paths = [Path(__file__).resolve(), *sorted((ROOT / "src/visa_agent").rglob("*.py")),
             *sorted(path for path in (ROOT / "src/visa_agent/assets").rglob("*") if path.is_file())]
    report: dict[str, Any] = {
        "scope": "fictional multi-turn development scenarios; real DeepSeek extraction; captured transport",
        "mailbox_calls": 0, "real_documents": 0,
        "maximum_model_calls": sum(len(items) for items in scenarios.values()), "model_retries": 0,
        "requested_model": "deepseek-v4-flash", "check_contract": "consultant-journey-v5",
        "evaluation_date": TODAY.isoformat(), "run_started_at": datetime.now(UTC).isoformat(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "case_patch_schema_sha256": hashlib.sha256(json.dumps(CasePatch.model_json_schema(), sort_keys=True).encode()).hexdigest(),
        "scenarios": scenarios, "completed": False, "all_passed": False, "results": [],
    }
    if saved:
        report.update({"scope": "offline saved-proposal replay; no model or mailbox calls",
                       "maximum_model_calls": 0, "replay_source": str(args.replay),
                       "replay_sha256": hashlib.sha256(args.replay.read_bytes()).hexdigest()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    with tempfile.TemporaryDirectory(prefix="visa-journey-probe-") as directory:
        for journey, specs in scenarios.items():
            capture = CapturedGmail()
            for index, spec in enumerate(specs, start=1):
                model = (SavedExtraction(next(row["raw_model_content"] for row in saved["results"]
                         if row["journey"] == journey and row["turn"] == index)) if saved else
                         ExtractionOnly("deepseek-v4-flash", api_key=key, capture_raw_responses=True))
                store = SQLiteStore(Path(directory) / f"{journey}.db")
                row: dict[str, Any] = {"journey": journey, "turn": index, "input": spec["body"], "completed": False}
                try:
                    from visa_agent.workflow.service import WorkflowService

                    guarded = GuardedLLM(model, max_attempts=1, allow_model_rendering=False)
                    workflow = WorkflowService(store, policy, guarded, today_provider=lambda: TODAY)
                    event = InboundEvent(id=f"{journey}-{index}", channel="gmail", sender=CONTACT,
                        external_thread_id=journey, subject="Visitor preparation", body=spec["body"],
                        received_at=datetime.now(UTC) + timedelta(seconds=index))
                    case, duplicate, plan = workflow.process(event)
                    sender = AutomaticGmailReplySender(capture, store, CONTACT)
                    sender.withhold_obsolete_unsent()
                    before = len(capture.bodies)
                    outcomes = OutboxDispatcher(store, sender, channel="gmail",
                        allowed_message_types=("blocked",)).dispatch_due(event.received_at)
                    outbox = next(r for r in store.list_outbox() if r["event_id"] == event.id)
                    reply = outbox["payload"]
                    checks = check_turn(spec, case, reply)
                    checks.update({"no_extraction_fallback": not guarded.last_extraction_fallback,
                        "exactly_one_captured_reply": not duplicate and len(capture.bodies) == before + 1
                            and capture.bodies[-1] == reply and len(outcomes) == 1 and outcomes[0].status == "SENT",
                        "persisted_case_matches": store.get_case(case.id).model_dump() == case.model_dump()})
                    row.update({"completed": True, "checks": checks, "plan": plan, "reply": reply,
                        "profile": case.profile.model_dump(mode="json"), "requested_fields": case.last_requested_fields,
                        "application_records": case.application_records.model_dump(mode="json") if case.application_records else None,
                        "deferred_fields": case.deferred_fields, "topics": case.customer_question_topics,
                        "reply_style": case.reply_style, "reply_style_source_event_id": case.reply_style_source_event_id,
                        "reply_style_source_excerpt": case.reply_style_source_excerpt})
                except Exception as error:
                    row["error_type"] = type(error).__name__
                finally:
                    row["usage"] = model.usage_history
                    row["raw_model_content"] = model.last_extraction_content
                    store.close()
                row["passed"] = row["completed"] and all(row.get("checks", {}).values())
                report["results"].append(row)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(journey, index, "PASS" if row["passed"] else "FAIL", flush=True)
    report["completed"] = True
    report["all_passed"] = all(row["passed"] for row in report["results"])
    report["run_completed_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
