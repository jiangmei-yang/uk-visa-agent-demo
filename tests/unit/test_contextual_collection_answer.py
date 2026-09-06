"""Short answers require current case-local actually sent question evidence."""

import pytest

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    QuotedText,
    RecordCommand,
    TravelFields,
    TravelInput,
    apply_record_commands,
)
from visa_agent.domain.models import Case
from visa_agent.workflow.record_collection_plan import (
    collection_question_text,
    contextual_collection_declaration,
    plan_collection_follow_up,
)


def context(*, partial=False, language="en"):
    case = Case(id="fictional-context", external_thread_id="fictional-thread",
                applicant_contact="fictional@example.test", policy_version="2026-02-25",
                customer_language=language)
    if partial:
        case.application_records = apply_record_commands(
            ApplicationRecordLedger(case_id=case.id), case_id=case.id,
            event_id="trip", body="Japan", commands=[RecordCommand(
                action="add", record=TravelInput(fields=TravelFields(
                    country=QuotedText(value="Japan", source_excerpt="Japan"))))],
        )
    question = plan_collection_follow_up(case.application_records, case_id=case.id).pending[0]
    case.collection_question_event_ids[question.key] = ["question"]
    row = {"id": "outbox-1", "case_id": case.id, "event_id": "question", "status": "SENT",
           "sent_at": "2026-09-07T01:00:00+00:00", "recipient": case.applicant_contact,
           "external_thread_id": case.external_thread_id,
           "payload": collection_question_text(question, case.application_records, language)}
    return case, row


@pytest.mark.parametrize("text,state", [("没有。", "none_declared"), ("No.", "none_declared"),
                                      ("记不清。", "unknown"), ("I need to check.", "unknown")])
def test_short_answer_retains_both_current_words_and_question_link(text, state):
    case, row = context()
    result = contextual_collection_declaration(case, text, [row])
    assert result.state == state and result.kind == "travel"
    assert result.source_excerpt == text and result.question_event_id == "question"
    assert result.question_key in case.collection_question_event_ids


def test_no_more_to_partial_list_means_exhaustive_not_absent():
    case, row = context(partial=True)
    result = contextual_collection_declaration(case, "没有了。", [row])
    assert result.state == "complete_declared"
    assert len(case.application_records.current()) == 1


@pytest.mark.parametrize("text", ["Yes", "没有，但我朋友说有", "如果没有", "He said no.",
                                 "No, don't process my data.", "> 没有", "That's all."])
def test_nonanswers_extra_clauses_quotes_and_ambiguous_completion_abstain(text):
    case, row = context()
    assert contextual_collection_declaration(case, text, [row]) is None


@pytest.mark.parametrize("failure", ["unsent", "other_case", "other_recipient", "other_thread",
                                    "no_send_time", "wrong_payload", "two_targets", "scalar_question"])
def test_unreliable_or_ambiguous_question_evidence_abstains(failure):
    case, row = context()
    if failure == "unsent":
        row["status"] = "PENDING"
    elif failure == "other_case":
        row["case_id"] = "other"
    elif failure == "other_recipient":
        row["recipient"] = "other@example.test"
    elif failure == "other_thread":
        row["external_thread_id"] = "other"
    elif failure == "no_send_time":
        row["sent_at"] = None
    elif failure == "wrong_payload":
        row["payload"] = "What is your date of birth?"
    elif failure == "scalar_question":
        case.question_event_ids["date_of_birth"] = ["question"]
    else:
        question = plan_collection_follow_up(None, case_id=case.id).pending[1]
        case.collection_question_event_ids[question.key] = ["question"]
        row["payload"] += collection_question_text(question, None, "en")
    assert contextual_collection_declaration(case, "No", [row]) is None


def test_newer_sent_reply_breaks_context_even_if_created_before_question():
    case, row = context()
    newer = {**row, "id": "older-draft", "event_id": "faq", "payload": "Here is the application link.",
             "sent_at": "2026-09-07T02:00:00+00:00"}
    assert contextual_collection_declaration(case, "No", [newer, row]) is None


def test_record_change_invalidates_previous_list_question():
    case, row = context(partial=True)
    case.application_records = apply_record_commands(
        case.application_records, case_id=case.id, event_id="later-trip", body="Korea",
        commands=[RecordCommand(action="add", record=TravelInput(fields=TravelFields(
            country=QuotedText(value="Korea", source_excerpt="Korea"))))],
    )
    assert contextual_collection_declaration(case, "No", [row]) is None
