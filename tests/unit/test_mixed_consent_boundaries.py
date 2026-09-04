"""Offline mixed-consent parser and canonical-ledger regression boundaries.

Not legal-policy or provider-live verification. Notices pass through the real
OutboxDispatcher to a capture-only sender; no message leaves the test process.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.outbound import OutboxDispatcher, ReplyRequest
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.privacy import consent
from visa_agent.privacy.consent import (
    ConsentLedger,
    ProcessingConsentRequired,
    ProcessingScope,
    _control_parts,
)
from visa_agent.storage.sqlite import SQLiteStore

BASE = datetime(2030, 1, 2, 12, tzinfo=UTC)
SCOPE = ProcessingScope("fictional-provider", "offline-model", "2026-09-05")
REFERENCE = "PC-0123456789AB"
EN_GRANT = f"I consent to the processing described in this notice (consent reference {REFERENCE})."
ZH_GRANT = f"我同意按这份说明处理本线程信息和材料（授权参考码 {REFERENCE}）。"


@pytest.mark.parametrize("grant,business", [
    pytest.param(EN_GRANT, "I run a shop without employees.", id="business-without"),
    pytest.param(EN_GRANT, "I am only staying for two weeks.", id="business-only"),
    pytest.param(EN_GRANT, "I do not have an HR department.", id="business-negation"),
    pytest.param(EN_GRANT, "I work in a retail store without an HR department.", id="store-is-employment-context"),
    pytest.param(EN_GRANT, "Can I apply without an employment letter?", id="business-question"),
    pytest.param(EN_GRANT, "Do my documents need translation?", id="document-question"),
    pytest.param(EN_GRANT, "My documents are only in Chinese.", id="document-language-only"),
    pytest.param(ZH_GRANT, "我的资料只有中文，翻译需要什么？", id="zh-document-question"),
    pytest.param(ZH_GRANT, "我只住两周，没有在职证明。", id="zh-business-only-negation"),
])
def test_business_only_without_or_question_does_not_limit_processing_scope(
    grant: str, business: str,
) -> None:
    parsed = _control_parts(f"{grant} {business}")
    assert parsed.action == "granted"
    assert REFERENCE in parsed.statement
    assert parsed.business_body == business


@pytest.mark.parametrize("grant", [EN_GRANT, ZH_GRANT], ids=["en", "zh"])
@pytest.mark.parametrize("question", ["?", "？"], ids=["ascii-question", "fullwidth-question"])
@pytest.mark.parametrize("spacing", ["", " \t", "\n"], ids=["adjacent", "horizontal-space", "new-line"])
def test_question_after_reference_is_not_an_affirmative_consent_statement(
    grant: str, question: str, spacing: str,
) -> None:
    assert _control_parts(grant[:-1] + spacing + question).action != "granted"


@pytest.mark.parametrize("grant,faq", [
    (EN_GRANT, "Where can I obtain my bank statements?"),
    (ZH_GRANT, "银行流水在哪里获取？"),
])
def test_full_grant_period_before_independent_faq_remains_affirmative(grant: str, faq: str) -> None:
    parsed = _control_parts(grant + "\n" + faq)
    assert parsed.action == "granted"
    assert parsed.business_body == faq


@pytest.mark.parametrize("body,business", [
    (
        EN_GRANT[:-1] + ", my date of birth is 1992-03-04; I live at 14 Example Road, Flat 2.",
        "my date of birth is 1992-03-04; I live at 14 Example Road, Flat 2.",
    ),
    (ZH_GRANT[:-1] + "，出生日期是1992-03-04；目前住在九龙。", "出生日期是1992-03-04；目前住在九龙。"),
    (
        EN_GRANT + '\nMy name is Alex Example. My employer calls my role "Research Associate".',
        'My name is Alex Example. My employer calls my role "Research Associate".',
    ),
])
def test_removing_only_consent_span_preserves_business_punctuation_and_quoted_fact(
    body: str, business: str,
) -> None:
    parsed = _control_parts(body)
    assert parsed.action == "granted"
    assert parsed.business_body == business


@pytest.mark.parametrize("body", [
    pytest.param(f"My sister wrote:\n{EN_GRANT}", id="reported-next-line"),
    pytest.param(f"My sister wrote the following:\n{EN_GRANT}", id="reported-intro-next-line"),
    pytest.param(f"For example:\n{EN_GRANT}", id="example-next-line"),
    pytest.param(f"姐姐写道：\n{ZH_GRANT}", id="zh-reported-next-line"),
    pytest.param(f"模板内容如下：\n{ZH_GRANT}", id="zh-template-intro-next-line"),
    pytest.param(f'"{EN_GRANT}"', id="inline-quoted"),
    pytest.param(f"> {EN_GRANT}", id="email-quoted"),
    pytest.param(f"If you keep everything locally;\n{EN_GRANT}", id="condition-before-grant"),
    pytest.param(f"如果不发给服务商；\n{ZH_GRANT}", id="zh-condition-before-grant"),
    pytest.param(EN_GRANT + " Do not share my information with the provider.", id="no-sharing"),
    pytest.param(EN_GRANT + " Please do not send my information to anyone else.", id="no-sending-personal-information"),
    pytest.param(EN_GRANT + " Do not upload my passport to the model.", id="no-upload"),
    pytest.param(EN_GRANT + " Please keep my information only on this computer.", id="local-only"),
    pytest.param(EN_GRANT + " Does the provider retain my information?", id="unresolved-privacy-question"),
    pytest.param(EN_GRANT + " My consent excludes attachments.", id="excludes-attachments"),
    pytest.param(EN_GRANT + " Upload nothing to the provider.", id="upload-nothing"),
    pytest.param(
        EN_GRANT + " Please refrain from sharing my information with the provider.",
        id="refrain-from-sharing",
    ),
    pytest.param(
        EN_GRANT + " My consent is limited to this email, not earlier messages.",
        id="excludes-previous-messages",
    ),
    pytest.param(ZH_GRANT + " 不要把我的资料分享给服务商。", id="zh-no-sharing"),
    pytest.param(ZH_GRANT + " 只同意本地保存，不允许发给模型。", id="zh-local-only"),
])
def test_history_conditions_or_partial_privacy_permission_never_become_full_grant(body: str) -> None:
    assert _control_parts(body).action != "granted"


@pytest.mark.parametrize("control,action", [
    ("I withdraw my consent to processing my information.", "withdrawn"),
    ("I do not consent to processing my information.", "declined"),
    ("我撤回资料处理同意。", "withdrawn"),
    ("我不同意处理资料。", "declined"),
])
def test_refusal_or_withdrawal_wins_over_grant_and_independent_business(control: str, action: str) -> None:
    parsed = _control_parts(f"{EN_GRANT} My date of birth is 1992-03-04. {control}")
    assert parsed.action == action
    assert parsed.business_body == ""


@pytest.mark.parametrize("body", [
    "PROFILE CONFIRMED", "FINAL CONFIRMED", "Please resume preparation.",
    "我确认资料摘要无误。", "请继续准备。", "Do I need to consent to processing?",
])
def test_business_confirmation_resume_or_consent_question_is_not_processing_grant(body: str) -> None:
    parsed = _control_parts(body)
    assert parsed.action is None
    assert parsed.business_body == body


class CapturedSender:
    def __init__(self) -> None:
        self.requests: list[ReplyRequest] = []

    def send(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        return f"fictional-captured-receipt-{len(self.requests)}"


def event(number: int, body: str = "Please help with my visa.", **updates: object) -> InboundEvent:
    return InboundEvent.model_validate({
        "id": f"mixed-provider-id-{number}", "channel": "gmail",
        "external_thread_id": "mixed-fictional-thread", "sender": "applicant@example.test",
        "subject": "Fictional visa enquiry", "body": body,
        "received_at": BASE + timedelta(minutes=number),
        "rfc_message_id": f"<mixed-{number}@example.test>",
        "references": "<fictional-parent@example.test>", **updates,
    })


class LedgerJourney:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.ledger = ConsentLedger(store)
        self.ledger.configure(SCOPE)
        self.sender = CapturedSender()

    def sent_notice(self) -> tuple[str, str]:
        decision = self.ledger.handle(event(1), "fictional-policy")
        assert decision.action == "defer"
        outcomes = OutboxDispatcher(self.store, self.sender, channel="gmail").dispatch_due(
            BASE + timedelta(minutes=1, seconds=30),
        )
        assert len(outcomes) == 1 and outcomes[0].status == "SENT"
        sent = self.sender.requests[-1]
        row = next(item for item in self.store.list_outbox() if item["id"] == sent.outbox_id)
        assert row["message_type"] == "processing_notice"
        assert row["status"] == "SENT" and row["provider_message_id"]
        assert self.ledger.validate_control(row)
        assert sent.body == row["payload"]
        match = re.search(r"Consent reference: (PC-[A-F0-9]{12})", sent.body)
        assert match is not None
        statement = EN_GRANT.replace(REFERENCE, match[1])
        return decision.case_id, statement

    def mixed(self, *, has_attachments: bool = False) -> tuple[str, InboundEvent]:
        case_id, statement = self.sent_notice()
        original = event(2, statement + " My date of birth is 1992-03-04.")
        result = self.ledger.handle(original, "fictional-policy", has_attachments=has_attachments)
        assert result.action == "defer" and result.granted
        return case_id, original


@pytest.fixture
def journey(tmp_path: Path) -> Iterator[LedgerJourney]:
    store = SQLiteStore(tmp_path / "mixed-consent.db")
    try:
        yield LedgerJourney(store)
    finally:
        store.close()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_current_sent_reference_does_not_turn_a_consent_question_into_a_grant(
    journey: LedgerJourney, language: str,
) -> None:
    case_id, statement = journey.sent_notice()
    if language == "zh":
        match = re.search(r"PC-[A-F0-9]{12}", statement)
        assert match is not None
        statement = ZH_GRANT.replace(REFERENCE, match[0])
    original = event(2, statement[:-1] + " \t？")
    result = journey.ledger.handle(original, "fictional-policy")
    assert not result.granted and result.action != "allow"
    case = journey.store.get_case(case_id)
    assert case is not None and not journey.ledger.allowed(case)
    assert journey.ledger.epoch(case_id) == 0


def test_mixed_grant_is_metadata_only_until_original_id_is_refetched(journey: LedgerJourney) -> None:
    case_id, original = journey.mixed()
    row = journey.store.connection.execute(
        "SELECT * FROM processing_consent_events WHERE event_id=?", (original.id,),
    ).fetchone()
    assert row["business_pending"] == 1 and row["has_attachments"] == 0
    assert re.fullmatch(r"[a-f0-9]{64}", row["message_sha256"])
    assert "1992-03-04" not in row["excerpt"]
    case = journey.store.get_case(case_id)
    assert case is not None and case.profile.date_of_birth is None
    assert case.latest_customer_message == "" and case.documents == []
    exported = json.dumps(journey.store.export_case_data(case_id), ensure_ascii=False)
    assert "1992-03-04" not in exported
    assert original.id in journey.ledger.deferred_ids(case_id)
    assert not journey.store.event_processed(original.id)
    epoch = journey.ledger.epoch(case_id)

    replay = ConsentLedger(journey.store).handle(original, "fictional-policy")
    assert replay.action == "allow" and replay.grant_business and not replay.granted
    assert replay.business_body == "My date of birth is 1992-03-04."
    assert replay.case_id == case_id and journey.ledger.epoch(case_id) == epoch
    assert journey.store.connection.execute(
        "SELECT COUNT(*) FROM processing_consent_events WHERE event_id=?", (original.id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [
    pytest.param("body", "My date of birth is 1993-05-06.", id="current-body"),
    pytest.param("subject", "Another subject", id="subject"),
    pytest.param("received_at", BASE + timedelta(minutes=2, seconds=1), id="received-time"),
    pytest.param("rfc_message_id", "<another-rfc@example.test>", id="rfc-message-id"),
    pytest.param("references", "<another-parent@example.test>", id="references"),
    pytest.param("external_thread_id", "another-thread", id="thread"),
    pytest.param("sender", "another-applicant@example.test", id="sender"),
    pytest.param("channel", "email", id="channel"),
])
def test_same_provider_id_refetch_cannot_replace_bound_message(
    journey: LedgerJourney, field: str, value: object,
) -> None:
    case_id, original = journey.mixed()
    epoch = journey.ledger.epoch(case_id)
    before = journey.store.export_case_data(case_id)
    changed = original.model_copy(update={field: value})
    with pytest.raises(ProcessingConsentRequired):
        journey.ledger.handle(changed, "fictional-policy")
    assert journey.ledger.epoch(case_id) == epoch
    assert journey.store.export_case_data(case_id) == before
    assert original.id in journey.ledger.deferred_ids(case_id)
    assert not journey.store.event_processed(original.id)
    assert journey.ledger.handle(original, "fictional-policy").action == "allow"


@pytest.mark.parametrize("initial,replacement", [(False, True), (True, False)])
def test_refetch_attachment_presence_must_match_audited_original(
    journey: LedgerJourney, initial: bool, replacement: bool,
) -> None:
    case_id, original = journey.mixed(has_attachments=initial)
    epoch = journey.ledger.epoch(case_id)
    with pytest.raises(ProcessingConsentRequired, match="recorded original"):
        journey.ledger.handle(original, "fictional-policy", has_attachments=replacement)
    assert journey.ledger.epoch(case_id) == epoch
    assert journey.ledger.handle(original, "fictional-policy", has_attachments=initial).action == "allow"


def test_attachment_only_grant_can_defer_without_materializing_a_path(journey: LedgerJourney) -> None:
    case_id, statement = journey.sent_notice()
    original = event(2, statement)
    assert original.attachment_paths == []
    first = journey.ledger.handle(original, "fictional-policy", has_attachments=True)
    assert first.action == "defer" and first.granted
    audit = journey.store.connection.execute(
        "SELECT * FROM processing_consent_events WHERE event_id=?", (original.id,),
    ).fetchone()
    assert audit["business_pending"] == 1 and audit["has_attachments"] == 1
    assert original.id in journey.ledger.deferred_ids(case_id)
    replay = journey.ledger.handle(original, "fictional-policy", has_attachments=True)
    assert replay.action == "allow" and replay.grant_business and not replay.granted
    assert replay.business_body == ""


@pytest.mark.parametrize("transition", ["withdraw", "decline", "new-scope"])
def test_old_audited_mixed_grant_cannot_regrant_after_current_authority_changes(
    journey: LedgerJourney, transition: str,
) -> None:
    case_id, original = journey.mixed()
    if transition == "withdraw":
        decision = journey.ledger.handle(event(3, "I withdraw my consent to processing my information."), "p")
        assert decision.action == "control"
    elif transition == "decline":
        decision = journey.ledger.handle(event(3, "I do not consent to processing my information."), "p")
        assert decision.action == "control"
    else:
        journey.ledger.configure(ProcessingScope(SCOPE.provider, "new-offline-model", SCOPE.version))
    epoch = journey.ledger.epoch(case_id)
    prior_outbox = journey.store.list_outbox()
    replay = journey.ledger.handle(original, "fictional-policy")
    assert replay.action == "defer" and replay.grant_business and not replay.granted
    assert replay.business_body is None
    assert journey.ledger.epoch(case_id) == epoch
    case = journey.store.get_case(case_id)
    assert case is not None and not journey.ledger.allowed(case)
    assert original.id in journey.ledger.deferred_ids(case_id)
    if transition == "new-scope":
        # Replaying an original mixed email can request the new unknown scope,
        # but never silently reuse the old notice's grant.
        current_notices = [
            row for row in journey.store.list_outbox()
            if row["message_type"] == "processing_notice" and journey.ledger.validate_control(row)
        ]
        assert len(current_notices) == 1
        assert current_notices[0]["status"] == "PENDING"
        assert current_notices[0]["event_id"] == original.id
        assert "new-offline-model" in current_notices[0]["payload"]
        assert journey.ledger.reference(case_id) in current_notices[0]["payload"]
    else:
        # Refusal is not an invitation to keep soliciting authorization merely
        # because a previously granted mixed message remains unprocessed.
        assert journey.store.list_outbox() == prior_outbox
    after_first_replay = journey.store.list_outbox()
    assert journey.ledger.handle(original, "fictional-policy") == replay
    assert journey.store.list_outbox() == after_first_replay


def test_completed_original_is_not_promoted_into_business_a_second_time(journey: LedgerJourney) -> None:
    case_id, original = journey.mixed()
    assert journey.ledger.handle(original, "p").action == "allow"
    journey.ledger.mark_completed(original.id)
    epoch = journey.ledger.epoch(case_id)
    replay = journey.ledger.handle(original, "p")
    assert replay.action == "control" and not replay.granted and not replay.grant_business
    assert replay.business_body is None
    assert original.id not in journey.ledger.deferred_ids(case_id)
    assert journey.ledger.epoch(case_id) == epoch


def test_missing_mixed_message_hash_fails_closed(journey: LedgerJourney) -> None:
    case_id, original = journey.mixed()
    with journey.store.connection:
        journey.store.connection.execute(
            "UPDATE processing_consent_events SET message_sha256=NULL WHERE event_id=?", (original.id,),
        )
    with pytest.raises(ProcessingConsentRequired, match="recorded original"):
        journey.ledger.handle(original, "p")
    assert original.id in journey.ledger.deferred_ids(case_id)


def test_notice_text_change_invalidates_persisted_scope_even_when_labels_are_unchanged(
    journey: LedgerJourney, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id, statement = journey.sent_notice()
    assert journey.ledger.handle(event(2, statement), "p").granted
    case = journey.store.get_case(case_id)
    assert case is not None and journey.ledger.allowed(case)
    case.profile_confirmed = True
    case.final_summary_confirmed = True
    case.confirmation_kind = "final"
    case.confirmation_fingerprint = "fictional-confirmed-fingerprint"
    case.confirmation_request_event_id = "fictional-summary-event"
    case.preparation_paused = True
    case.preparation_control_epoch = 1
    case.delivery_path = "/fictional/audit-only-pack.zip"
    journey.store.save_case(case)
    statuses = ("PENDING", "RETRY", "SENDING", "AMBIGUOUS", "SENT")
    for number, status in enumerate(statuses, start=10):
        journey.store.commit_event(case, event(number), "blocked", "Fictional previous business draft")
        with journey.store.connection:
            journey.store.connection.execute("UPDATE outbox SET status=? WHERE event_id=?", (status, event(number).id))
    before_scope = journey.store.connection.execute("SELECT * FROM processing_scope").fetchone()
    before_epoch = journey.ledger.epoch(case_id)
    uncertain_before = [row for row in journey.store.list_outbox() if row["status"] in {"SENDING", "AMBIGUOUS", "SENT"}]

    monkeypatch.setattr(consent, "_NOTICE", consent._NOTICE + "\nSynthetic amended processing purpose disclosure.")
    same_labels = ProcessingScope(SCOPE.provider, SCOPE.model, SCOPE.version)
    journey.ledger.configure(same_labels)

    after_scope = journey.store.connection.execute("SELECT * FROM processing_scope").fetchone()
    assert before_scope["scope_json"] == after_scope["scope_json"]
    assert before_scope["scope_id"] != after_scope["scope_id"] == same_labels.id
    assert journey.ledger.epoch(case_id) == before_epoch + 1
    assert journey.store.connection.execute(
        "SELECT status FROM processing_consent WHERE case_id=?", (case_id,),
    ).fetchone()[0] == "unknown"
    current = journey.store.get_case(case_id)
    assert current is not None and not journey.ledger.allowed(current)
    assert not current.profile_confirmed and not current.final_summary_confirmed
    assert current.confirmation_kind is None and current.confirmation_fingerprint is None
    assert current.confirmation_request_event_id is None
    assert current.preparation_paused and current.preparation_control_epoch == 1
    assert current.delivery_path == case.delivery_path
    business_rows = {row["event_id"]: row for row in journey.store.list_outbox() if row["message_type"] == "blocked"}
    for number in (10, 11):
        assert business_rows[event(number).id]["status"] == "FAILED"
    assert [row for row in journey.store.list_outbox() if row["status"] in {"SENDING", "AMBIGUOUS", "SENT"}] == uncertain_before
    journey.ledger.configure(same_labels)
    assert journey.ledger.epoch(case_id) == before_epoch + 1


def test_pre_mixed_schema_and_legacy_audit_never_implicitly_gain_business_replay(tmp_path: Path) -> None:
    path = tmp_path / "legacy-consent.db"
    legacy = event(9, EN_GRANT + " My date of birth is 1992-03-04.")
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE processing_consent_events (
            event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, action TEXT NOT NULL,
            scope_id TEXT NOT NULL, epoch INTEGER NOT NULL, excerpt TEXT NOT NULL,
            received_at TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.execute(
            "INSERT INTO processing_consent_events(event_id,case_id,action,scope_id,epoch,excerpt,received_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (legacy.id, "legacy-case", "granted", SCOPE.id, 1, EN_GRANT, legacy.received_at.isoformat()),
        )
    store = SQLiteStore(path)
    try:
        migrated = store.connection.execute(
            "SELECT * FROM processing_consent_events WHERE event_id=?", (legacy.id,),
        ).fetchone()
        assert migrated["business_pending"] == 0
        assert migrated["message_sha256"] is None and migrated["has_attachments"] == 0
        store.save_case(Case(
            id="legacy-case", external_thread_id=legacy.external_thread_id,
            primary_channel=legacy.channel, applicant_contact=legacy.sender, policy_version="p",
        ))
        journey = LedgerJourney(store)
        case_id, statement = journey.sent_notice()
        assert case_id == "legacy-case"
        assert journey.ledger.handle(event(2, statement), "p").granted
        case = store.get_case(case_id)
        assert case is not None and journey.ledger.allowed(case)
        # A historical incomplete metadata row does not grant the old audit a
        # business permission that was never recorded in the pre-mixed schema.
        with store.connection:
            store.connection.execute(
                "INSERT INTO processing_deferred_events(event_id,case_id,channel,thread_id,received_at) "
                "VALUES (?,?,?,?,?)",
                (legacy.id, case_id, legacy.channel, legacy.external_thread_id, legacy.received_at.isoformat()),
            )
        epoch = journey.ledger.epoch(case_id)
        store.close()
        store = SQLiteStore(path)
        ledger = ConsentLedger(store)
        result = ledger.handle(legacy, "p", has_attachments=True)
        assert result.action == "control" and not result.grant_business and not result.granted
        assert result.business_body is None
        assert ledger.epoch(case_id) == epoch
        assert legacy.id in ledger.deferred_ids(case_id)
        unchanged = store.connection.execute(
            "SELECT * FROM processing_consent_events WHERE event_id=?", (legacy.id,),
        ).fetchone()
        assert dict(unchanged) == dict(migrated)
    finally:
        store.close()
