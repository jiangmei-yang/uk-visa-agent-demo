"""Real-model regression of the ordinary Gmail scenario, isolated from live outbox."""

import argparse
import json
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from visa_agent.documents.natural import NaturalPDFReader
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService

BODY = (
    "你好，我叫 Lin Chen，现在在香港读大学，准备从香港申请英国签证去伦敦参加学术会议。"
    "计划 2026 年 11 月 9 日到英国，11 月 11\n日离开，预算大约 2500 英镑，学校承担往返交通和住宿。 "
    "我先把邀请函和在读证明发给你，在读证明附了普通 PDF 和扫描版。护照暂时不在手边，"
    "先附一份我整理的信息摘要，不是护照扫描件。\n"
    "麻烦帮我看看现有材料有什么问题，还需要补什么？我还没买机票、没订酒店，必须先买吗？ 谢谢， Lin"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    key = read_secret(
        "DEEPSEEK_API_KEY",
        file_environment_name="DEEPSEEK_API_KEY_FILE",
        default_file=Path(".secrets/deepseek_api_key.txt"),
    )
    if not key:
        parser.error("Missing DeepSeek key")
    records = []
    for repeat in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="visa-natural-journey-") as directory:
            store = SQLiteStore(Path(directory) / "case.db")
            model = DeepSeekStructuredLLM("deepseek-v4-flash", api_key=key)
            workflow = WorkflowService(
                store,
                load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                model,
                document_reader=NaturalPDFReader(model),
                today_provider=lambda: date(2026, 9, 4),
            )
            event = InboundEvent(
                id="first",
                external_thread_id="ordinary-journey",
                sender="applicant@example.test",
                subject="伦敦会议签证材料咨询",
                body=BODY,
                received_at=datetime(2026, 9, 4, 9, tzinfo=UTC),
                attachment_paths=[
                    str(Path("data/natural-document-eval") / name)
                    for name in (
                        "passport_summary.pdf",
                        "invitation.pdf",
                        "student_letter.pdf",
                        "student_letter_scan.pdf",
                    )
                ],
            )
            case, _, plan = workflow.process(event)
            reply = str(store.list_outbox()[-1]["payload"])
            checks = {
                "routine_enquiry_not_escalated": case.status.value == "DRAFT",
                "arrival_preserved": case.profile.planned_arrival_date == date(2026, 11, 9),
                "departure_preserved": case.profile.planned_departure_date == date(2026, 11, 11),
                "student_preserved": case.profile.occupation_status == "student",
                "nationality_not_invented": case.profile.nationality_country is None
                and case.profile.nationality is None,
                "date_conflict_detected": any(
                    i.code == "DATE_CONFLICT" for i in case.open_blockers()
                ),
                "identity_summary_withheld": next(
                    d for d in case.documents if d.filename == "passport_summary.pdf"
                ).status.value
                == "HUMAN_REVIEW_REQUIRED",
                "actual_ocr": any(
                    e.extraction_method == "bounded_pdf_ocr_extraction" for e in case.evidence
                ),
                "booking_question_answered": "GOV.UK:" in reply and "过境除外" in reply,
                "specific_passport_explanation": "不能代替护照" in reply,
                "at_most_three_next_actions": reply.count("\n- ") <= 3,
                "pack_withheld": not case.delivery_path and plan == "blocked",
            }
            records.append(
                {
                    "repeat": repeat + 1,
                    "step": "ordinary_attachments",
                    "checks": checks,
                    "reply": reply,
                    "reply_fallback": workflow.llm.last_render_fallback,
                    "extraction_error": workflow.llm.last_extraction_error,
                    "review_reason": case.human_review_reason,
                }
            )
            correction = event.model_copy(
                update={
                    "id": "correction",
                    "attachment_paths": [],
                    "received_at": event.received_at + timedelta(minutes=1),
                    "body": "刚才离境日期写错了，我实际是 2026 年 11 月 13 日离开英国。邀请函不用改。护照我稍后补，这份摘要不能当护照。",
                }
            )
            case, _, plan = workflow.process(correction)
            records.append(
                {
                    "repeat": repeat + 1,
                    "step": "natural_correction",
                    "checks": {
                        "correction_applied": case.profile.planned_departure_date
                        == date(2026, 11, 13),
                        "date_conflict_resolved": not any(
                            i.code == "DATE_CONFLICT" for i in case.open_blockers()
                        ),
                        "not_escalated": case.status.value == "DRAFT",
                        "pack_still_withheld": not case.delivery_path
                        and not case.final_summary_confirmed,
                    },
                    "reply": store.list_outbox()[-1]["payload"],
                }
            )
            store.close()
            print(
                "Repetition",
                repeat + 1,
                "PASS" if all(all(r["checks"].values()) for r in records[-2:]) else "FAIL",
                flush=True,
            )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    passed = all(all(r["checks"].values()) for r in records)
    args.report.write_text(
        json.dumps(
            {"synthetic": True, "all_passed": passed, "records": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
