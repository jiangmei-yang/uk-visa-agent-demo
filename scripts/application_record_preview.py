"""Render fictional record QA using the production summary/PDF renderer; no model or mail."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from visa_agent.delivery.pack import _pdf
from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    QuotedText,
    RecordCommand,
    RecordFieldDeferral,
    TravelFields,
    TravelInput,
    UKContactFields,
    UKContactInput,
    application_record_rows,
    apply_record_commands,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--include-deferred-details", action="store_true",
                        help="Add explicitly synthetic contact-passport uncertainty for visual QA")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Choose a new output directory; QA never overwrites an earlier render.")
    args.output_dir.mkdir(parents=True)
    manifest = {}
    for language, country, period, name, relation, address in [
        ("en", "Japan", "May 2023", "Fictional Example", "sister",
         "Apartment 123, 999 Fictional Demonstration Road, Example District, London, ZZ1 1ZZ (fictional)"),
        ("zh", "日本", "2023年夏天", "陈示例（虚构）", "姐姐",
         "英国伦敦虚构示例区示例路999号示例大厦123室（虚构地址，不用于真实申请）"),
    ]:
        def quote(value: str) -> QuotedText:
            return QuotedText(value=value, source_excerpt=value)

        body = "\n".join([country, period, name, relation, address])
        ledger = ApplicationRecordLedger(case_id=f"fictional-{language}-record-qa")
        ledger = apply_record_commands(ledger, case_id=ledger.case_id, event_id="fictional-preview-event", body=body,
            commands=[RecordCommand(action="add", record=TravelInput(fields=TravelFields(
                country=quote(country), period=quote(period)))),
                RecordCommand(action="add", record=UKContactInput(fields=UKContactFields(
                    name=quote(name), relationship=quote(relation), address=quote(address))))])
        if args.include_deferred_details:
            contact = next(record for record in ledger.current().values() if record.kind == "uk_contact")
            statement = "I need to check." if language == "en" else "暂时不清楚，需要核实。"
            # Trusted synthetic rendering fixture only, not an applicant event,
            # sent question, provider interpretation or passed release gate.
            ledger.field_deferrals.append(RecordFieldDeferral(case_id=ledger.case_id,
                record_id=contact.record_id, record_digest=contact.digest(), field="passport_number",
                source_excerpt=statement, source_event_id="fictional-uncertainty",
                source_body_sha256=hashlib.sha256(statement.encode()).hexdigest(),
                question_event_id="fictional-question", question_key="fictional-question-key"))
            ledger = ApplicationRecordLedger.model_validate_json(ledger.model_dump_json())
        output = args.output_dir / f"fictional-records-{language}.pdf"
        _pdf(output, "Application records - fictional QA", application_record_rows(ledger, language),
             "FICTIONAL SOFTWARE QA - NOT AN APPLICATION - INTAKE INCOMPLETE")
        manifest[output.name] = {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                                 "language": language, "record_fingerprint": ledger.fingerprint()}
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
