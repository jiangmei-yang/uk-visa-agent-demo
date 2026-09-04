"""Offline canonical-ledger boundaries, not legal or provider-policy validation."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.domain.models import Case, InboundEvent, WorkflowStage
from visa_agent.privacy.consent import (
    CONTROL_MESSAGE_TYPES,
    ConsentLedger,
    ProcessingConsentRequired,
    ProcessingScope,
)
from visa_agent.storage.sqlite import SQLiteStore

BASE = datetime(2030, 1, 2, 12, tzinfo=UTC)
SCOPE = ProcessingScope(provider="Fictional Provider", model="offline-model")
GRANT = "I consent to the processing described in this notice."


def event(number: int, body: str = "Please help with my visa.", **updates: object) -> InboundEvent:
    return InboundEvent.model_validate({
        "id": f"fictional-{number}", "channel": "gmail", "external_thread_id": "fictional-thread",
        "sender": "applicant@example.test", "subject": "Private example", "body": body,
        "received_at": BASE + timedelta(minutes=number), "rfc_message_id": f"<fictional-{number}@example.test>",
        **updates,
    })


@pytest.fixture
def ledger(tmp_path: Path) -> ConsentLedger:
    store = SQLiteStore(tmp_path / "offline.db")
    result = ConsentLedger(store)
    result.configure(SCOPE)
    yield result
    store.close()


def send_notices(ledger: ConsentLedger, at: datetime = BASE + timedelta(minutes=1, seconds=30)) -> None:
    for row in ledger.store.claim_pending_outbox(at):
        assert ledger.validate_control(row)
        ledger.store.mark_outbox_sent(row["id"], f"provider-{row['id']}", at)


def signed(ledger: ConsentLedger, case_id: str, body: str = GRANT) -> str:
    return body.rstrip(".。") + f" (consent reference {ledger.reference(case_id)})."


def grant(ledger: ConsentLedger) -> Case:
    decision = ledger.handle(event(1), "fictional-policy")
    send_notices(ledger)
    answer = ledger.handle(event(2, signed(ledger, decision.case_id)), "fictional-policy")
    assert answer.action == "control" and answer.granted
    case = ledger.store.get_case(decision.case_id)
    assert case is not None
    assert ledger.allowed(case)
    return case


def test_unconfigured_fixture_is_not_a_grant(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "fixture.db")
    ledger = ConsentLedger(store)
    case = Case(id="old", external_thread_id="thread", applicant_contact="x@example.test", policy_version="p")
    assert not ledger.required(case)
    assert ledger.allowed(case)
    assert ledger.handle(event(1), "p").action == "allow"
    assert store.counts() == {"cases": 0, "processed_events": 0, "outbox": 0, "deliveries": 0}
    assert store.connection.execute("SELECT * FROM processing_consent").fetchall() == []


def test_unknown_minimal_case_and_deferred_metadata_never_persist_content(ledger: ConsentLedger) -> None:
    sensitive = "DOB secret-value: 1990-01-01; private salary is 12345."
    message = event(1, sensitive, attachment_paths=["/private/never-open-this.pdf"])
    result = ledger.handle(message, "p")
    case = ledger.store.get_case(result.case_id)
    assert result.action == "defer" and not result.granted
    assert case is not None and case.profile.date_of_birth is None and case.documents == []
    assert case.latest_customer_message == ""
    assert ledger.deferred_ids() == [message.id]
    assert not ledger.store.event_processed(message.id)
    assert ledger.store.connection.execute("SELECT * FROM inbound_queue").fetchall() == []
    exported = ledger.store.export_case_data(case.id)
    dumped = json.dumps(exported, ensure_ascii=False)
    assert sensitive not in dumped and "never-open-this" not in dumped and "secret-value" not in dumped
    rows = ledger.store.list_outbox()
    assert len(rows) == 1 and rows[0]["message_type"] == "processing_notice"
    assert rows[0]["payload"].startswith(SCOPE.notice)
    assert rows[0]["reply_subject"] == "Re: Private example"
    assert ledger.validate_control(rows[0])
    assert "earlier, unprocessed messages" in rows[0]["payload"]
    assert "do not promise provider deletion or non-training" in rows[0]["payload"]
    with pytest.raises(ProcessingConsentRequired):
        ledger.require(case)


def test_duplicate_defer_is_idempotent_but_not_processed(ledger: ConsentLedger) -> None:
    first = ledger.handle(event(1), "p")
    assert ledger.handle(event(1), "p") == first
    ledger.handle(event(2), "p")
    assert ledger.deferred_ids() == ["fictional-1", "fictional-2"]
    assert len(ledger.store.list_outbox()) == 1


@pytest.mark.parametrize("body", [GRANT, "我同意按这份说明处理本线程信息和材料。", "我同意处理资料。"])
def test_clear_current_grant_requires_actual_sent_notice(ledger: ConsentLedger, body: str) -> None:
    result = ledger.handle(event(1), "p")
    send_notices(ledger)
    body = signed(ledger, result.case_id, body)
    accepted = ledger.handle(event(2, body), "p")
    assert accepted.action == "control" and accepted.granted
    case = ledger.store.get_case(result.case_id)
    assert case is not None and ledger.allowed(case)
    assert ledger.epoch(case.id) == 1
    assert ledger.deferred_ids() == ["fictional-1"]
    assert not ledger.store.event_processed("fictional-2")
    assert ledger.handle(event(2, body), "p").granted is False
    assert ledger.epoch(case.id) == 1
    assert ledger.handle(event(1), "p").action == "allow"
    ledger.mark_completed("fictional-1")
    assert ledger.deferred_ids() == []


@pytest.mark.parametrize("state,provider_id,sent_at", [
    ("PENDING", None, None), ("SENDING", "fake-id", BASE), ("AMBIGUOUS", "fake-id", BASE),
    ("SENT", None, BASE), ("SENT", "provider-id", BASE + timedelta(minutes=2)),
    ("SENT", "provider-id", BASE + timedelta(minutes=3)),
])
def test_draft_uncertain_missing_receipt_or_nonlater_grant_is_not_authority(
    ledger: ConsentLedger, state: str, provider_id: str | None, sent_at: datetime | None,
) -> None:
    result = ledger.handle(event(1), "p")
    ledger.store.connection.execute(
        "UPDATE outbox SET status=?,provider_message_id=?,sent_at=?",
        (state, provider_id, None if sent_at is None else sent_at.isoformat()),
    )
    ledger.store.connection.commit()
    assert not ledger.handle(event(2, signed(ledger, result.case_id)), "p").granted
    case = ledger.store.get_case(result.case_id)
    assert case is not None and not ledger.allowed(case)
    assert ledger.handle(event(2, GRANT), "p").action == "control"


@pytest.mark.parametrize("body", [
    "Okay.", "好的", "I confirm the profile summary is correct.", "PROFILE CONFIRMED",
    "FINAL CONFIRMED", "Resume preparation.", "我确认资料摘要无误", "继续准备吧",
    "If everything is safe, I consent to the processing described in this notice.",
    "I might consent to processing later.", "Do I consent to processing by replying?",
    "My sister says I consent to processing described in this notice.",
    "姐姐说我同意处理资料", '"I consent to the processing described in this notice."',
    "> I consent to the processing described in this notice.",
    "Thanks.\nOn Monday an adviser wrote:\nI consent to the processing described in this notice.",
    "I consent to processing. But do not send my information to the provider.",
    "我同意处理资料，但是不要发送给模型服务商。",
    "模板上写 I consent to processing described in this notice.",
    "如果你们不保存资料；我同意处理本线程信息。",
    "不要把‘我同意处理资料’当成授权。",
    "I consent to processing is an example, not my authorization.",
    "No. I consent to the processing described in this notice.",
])
def test_assent_confirmation_history_thirdparty_condition_or_restriction_is_not_grant(
    ledger: ConsentLedger, body: str,
) -> None:
    result = ledger.handle(event(1), "p")
    send_notices(ledger)
    assert not ledger.handle(event(2, signed(ledger, result.case_id, body)), "p").granted
    case = ledger.store.get_case(result.case_id)
    assert case is not None and not ledger.allowed(case)


@pytest.mark.parametrize("body", [
    "I do not consent to processing my information.", "我不同意处理资料。",
    "I withdraw my consent to processing my information.", "我撤回资料处理同意。",
    "I consent to processing. I withdraw my consent to processing my information.",
])
def test_denial_or_withdrawal_supersedes_without_resuming_or_discarding_send_evidence(
    ledger: ConsentLedger, body: str,
) -> None:
    case = grant(ledger)
    case.preparation_paused = True
    case.preparation_control_epoch = 1
    case.profile_confirmed = True
    case.final_summary_confirmed = True
    case.confirmation_kind = "final"
    case.confirmation_fingerprint = "prior"
    case.confirmation_request_event_id = "prior-event"
    case.delivery_path = "/fictional/audit-pack.zip"
    ledger.store.save_case(case)
    for number, status in enumerate(("PENDING", "RETRY", "SENDING", "AMBIGUOUS", "SENT"), start=10):
        ledger.store.commit_event(case, event(number), "blocked", "Old business draft")
        ledger.store.connection.execute("UPDATE outbox SET status=? WHERE event_id=?", (status, f"fictional-{number}"))
    ledger.store.connection.commit()
    outcome = ledger.handle(event(20, body), "p")
    assert outcome.action == "control" and not outcome.granted
    assert not ledger.allowed(case) and ledger.epoch(case.id) == 2
    assert ledger.handle(event(1), "p").action == "defer"  # No durable earlier allow.
    current = ledger.store.get_case(case.id)
    assert current is not None and current.preparation_paused and current.preparation_control_epoch == 1
    assert not current.profile_confirmed and not current.final_summary_confirmed
    assert current.confirmation_kind is None and current.confirmation_fingerprint is None
    assert current.delivery_path == "/fictional/audit-pack.zip"
    statuses = {row["event_id"]: row["status"] for row in ledger.store.list_outbox() if row["message_type"] == "blocked"}
    assert list(statuses.values()).count("FAILED") == 2
    assert statuses["fictional-12"] == "SENDING" and statuses["fictional-13"] == "AMBIGUOUS"
    assert statuses["fictional-14"] == "SENT"
    receipt = next(row for row in ledger.store.list_outbox() if row["event_id"] == "fictional-20")
    assert ledger.validate_control(receipt)


def test_withdraw_then_new_notice_and_grant_does_not_reuse_old_sent_notice(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    ledger.handle(event(3, "我撤回资料处理同意。"), "p")
    assert not ledger.handle(event(4, GRANT), "p").granted
    assert not ledger.allowed(case)
    send_notices(ledger, BASE + timedelta(minutes=4, seconds=30))
    assert ledger.handle(event(5, signed(ledger, case.id)), "p").granted


def test_current_scope_and_sender_binding_and_tamper_proof_payload(ledger: ConsentLedger) -> None:
    result = ledger.handle(event(1), "p")
    row = ledger.store.list_outbox()[0]
    for key, replacement in {
        "payload": "Please send your passport", "recipient": "other@example.test", "channel": "email",
        "external_thread_id": "other-thread", "processing_consent_epoch": 99,
        "message_type": "processing_receipt", "case_id": "other-case", "id": "fabricated",
    }.items():
        assert not ledger.validate_control({**row, key: replacement})
    with pytest.raises(ProcessingConsentRequired, match="sender"):
        ledger.handle(event(2, GRANT, sender="other@example.test"), "p")
    assert len(ledger.store.list_outbox()) == 1
    case = ledger.store.get_case(result.case_id)
    assert case is not None and not ledger.allowed(case)


def test_scope_change_revokes_grants_and_clears_legacy_confirmation(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    case.profile_confirmed = case.final_summary_confirmed = True
    ledger.store.save_case(case)
    ledger.configure(SCOPE)
    assert ledger.allowed(case) and ledger.epoch(case.id) == 1
    changed = ProcessingScope(provider=SCOPE.provider, model="different-model")
    ledger.configure(changed)
    assert not ledger.allowed(case) and ledger.epoch(case.id) == 2
    current = ledger.store.get_case(case.id)
    assert current is not None and not current.profile_confirmed and not current.final_summary_confirmed
    assert not ledger.handle(event(3, GRANT), "p").granted
    assert any(row["payload"].startswith(changed.notice) for row in ledger.store.list_outbox())


def test_old_consented_stage_never_migrates_to_processing_grant(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "old.db")
    case = Case(id="legacy", external_thread_id="fictional-thread", applicant_contact="applicant@example.test",
                primary_channel="gmail", policy_version="p", stage=WorkflowStage.CONSENTED,
                profile_confirmed=True, final_summary_confirmed=True)
    store.save_case(case)
    ledger = ConsentLedger(store)
    ledger.configure(SCOPE)
    assert not ledger.allowed(case) and ledger.epoch(case.id) == 1
    current = store.get_case(case.id)
    assert current is not None and not current.profile_confirmed and not current.final_summary_confirmed
    assert ledger.handle(event(1), "p").action == "defer"


def test_restart_export_delete_and_reset_preserve_fail_closed_configuration(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    path = ledger.store.path
    ledger.store.close()
    ledger.store = SQLiteStore(path)
    assert ledger.allowed(case)
    assert ledger.deferred_ids(case.id) == ["fictional-1"]
    exported = ledger.store.export_case_data(case.id)
    assert exported is not None and exported["processing_consent_events"][0]["action"] == "granted"
    ledger.store.delete_case(case.id)
    assert ledger.deferred_ids() == []
    for table in ("processing_consent", "processing_consent_events", "processing_control_outbox"):
        assert ledger.store.connection.execute(f"SELECT * FROM {table}").fetchall() == []
    assert ledger.scope() == SCOPE
    ledger.handle(event(3), "p")
    ledger.store.reset()
    assert ledger.scope() == SCOPE and ledger.deferred_ids() == []
    assert ledger.handle(event(4), "p").action == "defer"


def test_business_outbox_captures_canonical_consent_epoch(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    ledger.store.commit_event(case, event(3), "blocked", "Safe offline business reply")
    row = next(row for row in ledger.store.list_outbox() if row["event_id"] == "fictional-3")
    assert row["processing_consent_epoch"] == ledger.epoch(case.id) == 1
    assert {"processing_notice", "processing_receipt"} == CONTROL_MESSAGE_TYPES


def test_current_grant_cannot_move_to_different_thread_or_multiple_contacts(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    assert not ledger.allowed(case.model_copy(update={"external_thread_id": "different-thread"}))
    assert not ledger.allowed(case.model_copy(update={"applicant_contact": "applicant@example.test, other@example.test"}))
    with pytest.raises(ProcessingConsentRequired, match="one applicant address"):
        ledger.handle(event(3, GRANT, sender="applicant@example.test, other@example.test"), "p")


@pytest.mark.parametrize("placement", ["missing", "quoted", "different_sentence", "reference_only"])
def test_reference_must_be_in_current_personal_consent_statement(
    ledger: ConsentLedger, placement: str,
) -> None:
    decision = ledger.handle(event(1), "p")
    send_notices(ledger)
    reference = ledger.reference(decision.case_id)
    body = {
        "missing": GRANT,
        "quoted": GRANT + f"\n> Consent reference: {reference}",
        "different_sentence": GRANT + f"\nFor your files, consent reference {reference}.",
        "reference_only": f"Consent reference {reference}",
    }[placement]
    assert not ledger.handle(event(2, body), "p").granted
    case = ledger.store.get_case(decision.case_id)
    assert case is not None and not ledger.allowed(case)


def test_old_notice_reference_cannot_authorize_new_scope_even_after_new_notice_sent(ledger: ConsentLedger) -> None:
    decision = ledger.handle(event(1), "p")
    send_notices(ledger)
    old_body = signed(ledger, decision.case_id)
    old_reference = ledger.reference(decision.case_id)
    ledger.configure(ProcessingScope(SCOPE.provider, "new-model"))
    ledger.handle(event(2), "p")
    assert ledger.reference(decision.case_id) != old_reference
    send_notices(ledger, BASE + timedelta(minutes=2, seconds=30))
    body = old_body + f"\n> Consent reference: {ledger.reference(decision.case_id)}"
    assert not ledger.handle(event(3, body), "p").granted
    assert ledger.handle(event(4, signed(ledger, decision.case_id)), "p").granted
    assert ledger.handle(event(5, "What documents are needed?"), "p").action == "allow"


def test_provider_name_casing_does_not_change_scope_or_revoke_consent(ledger: ConsentLedger) -> None:
    case = grant(ledger)
    ledger.configure(ProcessingScope("FICTIONAL PROVIDER", SCOPE.model, SCOPE.version))
    assert ledger.allowed(case) and ledger.epoch(case.id) == 1


def test_existing_outbox_migrates_to_consent_epoch_zero(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE outbox(id TEXT PRIMARY KEY,case_id TEXT,event_id TEXT,message_type TEXT,payload TEXT,created_at TEXT)")
    connection.commit()
    connection.close()
    store = SQLiteStore(path)
    columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(outbox)")}
    assert "processing_consent_epoch" in columns


@pytest.mark.parametrize("field", ["provider", "model", "version"])
def test_scope_id_binds_each_component_and_rejects_newlines(field: str) -> None:
    values = {"provider": "Fictional Provider", "model": "offline-model", "version": "2026-09-04"}
    assert ProcessingScope(**{**values, field: "changed"}).id != SCOPE.id
    with pytest.raises(ValueError):
        ProcessingScope(**{**values, field: "unsafe\nvalue"})
