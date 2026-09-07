from copy import deepcopy
from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import Case, Document, DocumentStatus, Evidence, InboundEvent
from visa_agent.workflow.financial_replacement import (
    apply_statement_replacement,
    replacement_names,
    replacement_receipt,
)

BODY = ("护照姓名是 Lin Chen，原来的银行流水把姓拼错成了 Lin Chan。"
        "附件是更正后的 bank_statement_corrected.pdf，请用它替换之前的 bank_statement_original.pdf，"
        "账户、结单期间和金额都没变。这是我自己的账户，不是资助人的。其他信息不变，请告诉我还缺什么。")


@pytest.mark.parametrize("body", [BODY,
    BODY.replace("更正后的 ", "更正后的\n").replace("之前的 ", "之前的\n"),
    "请用 bank_statement_corrected.pdf 替换 bank_statement_original.pdf。",
    "Please replace bank_statement_original.pdf with bank_statement_corrected.pdf.",
])
def test_explicit_current_replacement_pair(body):
    assert replacement_names(body) == ("bank_statement_corrected.pdf", "bank_statement_original.pdf")


@pytest.mark.parametrize("body", [
    "不要用 new.pdf 替换 old.pdf。", "如果通过检查，请用 new.pdf 替换 old.pdf。",
    "朋友说请用 new.pdf 替换 old.pdf。", '请翻译“请用 new.pdf 替换 old.pdf”。',
    "> 请用 new.pdf 替换 old.pdf。", "On Monday, someone wrote:\n请用 new.pdf 替换 old.pdf。",
    "Please replace old.pdf with new.pdf if it is valid.",
    "请用 new.pdf 替换 old.pdf，等我确认后再操作。", "请用 new.pdf 替换 old.pdf，不要现在操作。",
    "请用 new.pdf 替换 old.pdf。请用 other.pdf 替换 old.pdf。",
    "新的文件是 new.pdf，旧的是 old.pdf。", "请用 same.pdf 替换 same.pdf。",
])
def test_noncommands_do_not_replace(body):
    assert replacement_names(body) is None


def example():
    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test",
                policy_version="test", primary_channel="gmail")
    case.profile.full_name = "Lin Chen"
    for index, filename in enumerate(("bank_statement_original.pdf", "bank_statement_corrected.pdf")):
        doc = Document(id=f"d{index}", filename=filename, kind="bank_statement", sha256=str(index)*64,
                       mime_type="application/pdf", status=DocumentStatus.ACCEPTED_FOR_REVIEW,
                       source_event_id=f"inbound-{index}", path=f"/fictional/{filename}")
        case.documents.append(doc)
        case.evidence.append(Evidence(id=f"e{index}", fact_key="financial_observation",
            value={"subject_name": "Lin Chan" if index == 0 else "Lin Chen", "kind": "closing_balance",
                   "currency": "GBP", "period": "closing", "as_of": "2026-08-31",
                   "account_reference": "1234", "amount": "12500.00"},
            source_event_id=doc.source_event_id, source_document_id=doc.id, source_excerpt="fictional statement",
            extraction_method="test", model_version="test", confidence=1))
    event = InboundEvent(id="customer-correction", channel="gmail", external_thread_id="t",
        sender=case.applicant_contact, subject="Correction", body=BODY, received_at=datetime.now(UTC))
    return case, event


def test_replacement_retains_original_and_records_customer_instruction():
    case, event = example()
    before = deepcopy(case.documents)
    assert apply_statement_replacement(case, event)
    assert case.documents[0].status == DocumentStatus.SUPERSEDED
    assert case.documents[1].supersedes_document_id == "d0"
    assert [(d.path, d.sha256) for d in case.documents] == [(d.path, d.sha256) for d in before]
    assert case.evidence[0].superseded and not case.evidence[1].superseded
    audit = case.evidence[-1]
    assert audit.source_event_id == event.id and audit.source_excerpt == BODY
    assert audit.fact_key == "document_replacement"
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert not apply_statement_replacement(case, event)


@pytest.mark.parametrize("field,value", [("account_reference", "9999"), ("account_reference", None),
    ("as_of", "2026-09-30"), ("currency", "HKD"), ("amount", "99999.00"),
    ("subject_name", "Other Person"), ("period", "monthly")])
def test_different_or_unproven_financial_identity_does_not_replace(field, value):
    case, event = example()
    case.evidence[1].value[field] = value
    before = case.model_dump_json()
    assert not apply_statement_replacement(case, event)
    assert case.model_dump_json() == before


@pytest.mark.parametrize("status", [DocumentStatus.HUMAN_REVIEW_REQUIRED, DocumentStatus.NEEDS_REPLACEMENT,
                                    DocumentStatus.SUPERSEDED])
def test_rejected_new_document_cannot_displace_old(status):
    case, event = example()
    case.documents[1].status = status
    before = case.model_dump_json()
    assert not apply_statement_replacement(case, event)
    assert case.model_dump_json() == before


@pytest.mark.parametrize("field,value", [("sender", "intruder@example.test"),
    ("sender", "fictional@example.test, intruder@example.test"), ("external_thread_id", "other"),
    ("channel", "email")])
def test_replacement_is_bound_to_customer_and_thread(field, value):
    case, event = example()
    setattr(event, field, value)
    before = case.model_dump_json()
    assert not apply_statement_replacement(case, event)
    assert case.model_dump_json() == before


def test_ambiguous_live_filenames_do_not_replace_but_retired_retry_does_not_interfere():
    case, event = example()
    duplicate = case.documents[0].model_copy(update={"id": "older-read"})
    case.documents.append(duplicate)
    assert not apply_statement_replacement(case, event)
    duplicate.status = DocumentStatus.SUPERSEDED
    assert apply_statement_replacement(case, event)


def test_regular_workflow_ingestion_applies_customer_instruction_without_new_upload(tmp_path):
    from pathlib import Path

    from visa_agent.domain.policy import load_policy
    from visa_agent.llm.offline import OfflineFixtureLLM
    from visa_agent.storage.sqlite import SQLiteStore
    from visa_agent.workflow.service import WorkflowService

    case, event = example()
    store = SQLiteStore(tmp_path / "workflow.db")
    try:
        workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                                   OfflineFixtureLLM())
        workflow._ingest_attachments(case, event)
        assert case.documents[0].status == DocumentStatus.SUPERSEDED
        assert case.evidence[-1].source_event_id == event.id
        assert not store.list_outbox()
    finally:
        store.close()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_receipt_requires_completed_audited_replacement(language):
    from visa_agent.workflow.conversation import blocked_customer_message

    case, event = example()
    case.customer_language = language
    case.latest_customer_message = BODY
    assert replacement_receipt(case) is None
    assert apply_statement_replacement(case, event)
    receipt = replacement_receipt(case)
    assert receipt and "bank_statement_corrected.pdf" in receipt
    assert receipt in blocked_customer_message(case)
    case.evidence.pop()
    assert replacement_receipt(case) is None
