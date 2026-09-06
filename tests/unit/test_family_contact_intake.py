"""Known-family conditional prompts are shared with the review gate, not blanket intake."""

import pytest

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    QuotedText,
    RecordCommand,
    UKContactFields,
    UKContactInput,
    apply_record_commands,
)
from visa_agent.domain.models import Case
from visa_agent.domain.record_completeness import application_record_checks
from visa_agent.workflow.record_collection_plan import (
    collection_question_text,
    contextual_record_field_deferral,
    plan_collection_follow_up,
)


def contact(relationship, passport=None):
    values = {"name": "Example Doe", "relationship": relationship, "address": "1 Example Road, London, UK"}
    if passport:
        values["passport_number"] = passport
    body = " | ".join(values.values())
    return apply_record_commands(ApplicationRecordLedger(case_id="family-fixture"), case_id="family-fixture",
        event_id="contact", body=body, commands=[RecordCommand(action="add", record=UKContactInput(fields=UKContactFields(
            **{key: QuotedText(value=value, source_excerpt=value) for key, value in values.items()}
        )))])


@pytest.mark.parametrize("relationship,known_family", [("sister", True), ("姐姐", True), ("father", True),
                                                     ("friend", False), ("contact", False), ("sister?", False)])
def test_only_an_explicitly_recognized_family_label_adds_the_conditional_question(relationship, known_family):
    ledger = contact(relationship)
    pending = plan_collection_follow_up(ledger, case_id=ledger.case_id).pending
    assert any(item.field == "passport_number" for item in pending) == known_family
    assert application_record_checks(ledger, case_id=ledger.case_id)["application_record_descriptive_fields_complete"] != known_family


@pytest.mark.parametrize("language", ["en", "zh"])
def test_conditional_question_explains_why_and_does_not_demand_a_passport_upload(language):
    ledger = contact("sister")
    question = next(item for item in plan_collection_follow_up(ledger, case_id=ledger.case_id).pending
                    if item.field == "passport_number")
    text = collection_question_text(question, ledger, language)
    assert "Example Doe" in text and "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" in text
    assert ("可能需要" if language == "zh" else "may ask") in text
    assert ("不用猜" if language == "zh" else "don't guess") in text
    assert text.count("？" if language == "zh" else "?") == 1


def test_a_supplied_relative_passport_number_is_not_asked_again():
    ledger = contact("sister", "FICTIONAL-ONLY-123")
    pending = plan_collection_follow_up(ledger, case_id=ledger.case_id).pending
    assert not any(item.field == "passport_number" for item in pending)
    assert application_record_checks(ledger, case_id=ledger.case_id)["application_record_descriptive_fields_complete"]


def test_deduplicated_source_link_and_language_switch_do_not_lose_the_sent_question():
    ledger = contact("sister")
    case = Case(id=ledger.case_id, external_thread_id="family-thread", applicant_contact="family@example.test",
                policy_version="2026-02-25", customer_language="zh", application_records=ledger)
    question = next(item for item in plan_collection_follow_up(ledger, case_id=case.id).pending if item.field == "passport_number")
    case.collection_question_event_ids[question.key] = ["sent-question"]
    payload = collection_question_text(question, ledger, "en").partition("\nGOV.UK:")[0]
    rows = [{"id": "out-question", "case_id": case.id, "event_id": "sent-question", "status": "SENT",
             "sent_at": "2026-09-07T00:00:00+00:00", "recipient": case.applicant_contact,
             "external_thread_id": case.external_thread_id, "payload": payload}]
    deferred = contextual_record_field_deferral(case, "记不清。", "answer", rows)
    assert deferred is not None and deferred.field == "passport_number"
    assert deferred.question_event_id == "sent-question" and deferred.source_excerpt == "记不清。"
