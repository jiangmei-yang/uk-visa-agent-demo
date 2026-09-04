"""Mixed consent is two durable phases, with real captured SENT notice evidence.

Only model extraction, document reading and the Gmail transport are substituted.
No fixture writes a granted flag or marks an outbox item SENT by hand.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.inbound_worker import InboundEventWorker
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import CaseStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.privacy.consent import ConsentLedger, ProcessingConsentRequired, ProcessingScope
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.customer_questions import APPLICATION_SOURCE
from visa_agent.workflow.review import queue_review_retry, review_fingerprint
from visa_agent.workflow.service import WorkflowService

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
SCOPE = ProcessingScope("fictional-mixed-provider", "fictional-mixed-model", "2026-09-04")
APPLICANT = "fictional-mixed-consent@example.test"
THREAD = "fictional-mixed-consent-thread"
TODAY = date(2026, 9, 4)
WITHDRAW = "I withdraw my consent to processing my information."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Mixed consent tests must not access a provider or Gmail network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def proposal(*, facts=(), questions=(), control=None):
    value = {
        "updates": [{"field": key, "value": item, "source_excerpt": excerpt, "confidence": 1}
                    for key, item, excerpt in facts],
        "customer_questions": [{"topic": topic, "source_excerpt": excerpt, "confidence": 1}
                               for topic, excerpt in questions],
        "ambiguities": [],
    }
    if control:
        value["preparation_intent"] = {
            "action": control[0], "source_excerpt": control[1], "confidence": 1,
        }
    return CasePatch.model_validate(value)


class CapturedGmail(GmailAdapter):
    def __init__(self):
        self.sent = []

    def send_reply(self, **kwargs):
        assert kwargs["recipient"] == APPLICANT
        assert kwargs["thread_id"] == THREAD and kwargs.get("attachment") is None
        self.sent.append(kwargs)
        return {"id": f"mixed-capture-{len(self.sent)}"}


class CountedModel:
    def __init__(self):
        self.patches = {}
        self.extracted = []
        self.rendered = []

    def extract_case_patch(self, event):
        self.extracted.append(event.model_copy(deep=True))
        return self.patches.get(event.id, proposal()).model_copy(deep=True)

    def render_message(self, case, plan):
        self.rendered.append((case.id, plan))
        return deterministic_fallback_message(case, plan)


class Journey:
    def __init__(self, tmp_path):
        self.path = tmp_path / "mixed-consent.db"
        self.store = SQLiteStore(self.path)
        self.ledger = ConsentLedger(self.store)
        self.ledger.configure(SCOPE)
        self.model = CountedModel()
        self.gmail = CapturedGmail()
        self.reads = []
        self.document_bytes = []
        self.clock = datetime.now(UTC) + timedelta(minutes=1)
        self.sequence = 0

    def tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock

    def event(self, body, **overrides):
        self.sequence += 1
        values = {
            "id": f"mixed-original-{self.sequence}", "channel": "gmail",
            "external_thread_id": THREAD, "sender": APPLICANT,
            "subject": f"Original applicant subject {self.sequence}", "body": body,
            "received_at": self.tick(),
            "rfc_message_id": f"<mixed-original-{self.sequence}@example.test>",
            "references": "<earlier-thread-message@example.test>",
        }
        values.update(overrides)
        return InboundEvent(**values)

    def case(self):
        case = self.store.get_case_by_thread(THREAD)
        assert case is not None
        return case

    def reopen(self):
        self.store.close()
        self.store = SQLiteStore(self.path)
        self.ledger = ConsentLedger(self.store)

    def read(self, path):
        self.reads.append(path)
        self.document_bytes.append(path.read_bytes())
        return DocumentReadResult("student_letter", "en", 1, {}, method="mixed_fixture_reader")

    def process(self, event, patch=None):
        if patch is not None:
            self.model.patches[event.id] = patch
        workflow = WorkflowService(self.store, POLICY, self.model, document_reader=self.read,
                                   today_provider=lambda: TODAY)
        return workflow.process(event)

    def rows(self, event):
        return [row for row in self.store.list_outbox() if row["event_id"] == event.id]

    def audits(self, event):
        return [dict(row) for row in self.store.connection.execute(
            "SELECT * FROM processing_consent_events WHERE event_id=?", (event.id,),
        )]

    def dispatch(self, *types):
        sender = AutomaticGmailReplySender(self.gmail, self.store, APPLICANT)
        return OutboxDispatcher(self.store, sender, channel="gmail",
                                allowed_message_types=types).dispatch_due(self.tick())

    def notice(self, *, send=True):
        before = (len(self.model.extracted), len(self.model.rendered), len(self.reads))
        event = self.event("Please explain how my information will be processed.")
        case, duplicate, plan = self.process(event)
        assert not duplicate and plan == "processing_notice"
        assert (len(self.model.extracted), len(self.model.rendered), len(self.reads)) == before
        assert not self.ledger.allowed(case)
        if send:
            outcomes = self.dispatch("processing_notice")
            reference = self.ledger.reference(case.id)
            row = next(row for row in self.store.list_outbox()
                       if row["case_id"] == case.id and row["message_type"] == "processing_notice"
                       and reference in row["payload"] and row["status"] == "SENT")
            assert any(item.outbox_id == row["id"] and item.status == "SENT" for item in outcomes)
            assert row["status"] == "SENT" and row["provider_message_id"]
            assert row["payload"] == self.gmail.sent[-1]["body"]
            assert self.ledger.reference(case.id) in self.gmail.sent[-1]["body"]
        return event

    def grant_text(self, language="en", *, reference=None):
        reference = reference or self.ledger.reference(self.case().id)
        if language == "zh":
            return f"我同意按这份说明处理本线程信息和材料（授权参考码 {reference}）。"
        return f"I consent to the processing described in this notice (consent reference {reference})."

    def pure_grant(self):
        self.notice()
        event = self.event(self.grant_text())
        decision = self.ledger.handle(event, POLICY.version)
        assert decision.action == "control" and decision.granted
        assert self.ledger.allowed(self.case())
        return event


@pytest.fixture
def journey(tmp_path):
    value = Journey(tmp_path)
    try:
        yield value
    finally:
        value.store.close()


def assert_no_confirmation(case):
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.delivery_path is None
    assert case.latest_preparation_action != "resume"


def assert_original_envelope(view, original):
    for field in ("id", "received_at", "subject", "rfc_message_id", "references", "sender",
                  "external_thread_id", "channel", "attachment_paths"):
        assert getattr(view, field) == getattr(original, field), field


@pytest.mark.parametrize("language", ["zh", "en"])
def test_mixed_grant_defers_once_then_reopens_as_original_fact_and_faq_business(journey, language):
    journey.notice()
    before_epoch = journey.ledger.epoch(journey.case().id)
    grant = journey.grant_text(language)
    name = "星野安宁" if language == "zh" else "Rowan Example"
    fact = f"我的姓名是{name}。" if language == "zh" else f"My full name is {name}."
    question = "访客签证在哪里申请？" if language == "zh" else "Where do I apply for my UK visitor visa?"
    business = fact + " " + question
    event = journey.event(grant + " " + business)
    raw = event.model_dump_json()
    first = journey.ledger.handle(event, POLICY.version)
    assert first.action == "defer" and first.granted
    assert journey.ledger.allowed(journey.case())
    assert journey.ledger.epoch(journey.case().id) == before_epoch + 1
    assert event.id in journey.ledger.deferred_ids()
    assert not journey.store.event_processed(event.id)
    assert [row["action"] for row in journey.audits(event)] == ["granted"]
    assert journey.model.extracted == journey.reads == []
    assert {row["message_type"] for row in journey.rows(event)} == {"processing_receipt"}
    deferred = journey.store.connection.execute(
        "SELECT * FROM processing_deferred_events WHERE event_id=?", (event.id,),
    ).fetchone()
    assert deferred["received_at"] == event.received_at.isoformat() and deferred["completed_at"] is None
    journey.dispatch("processing_receipt")
    receipt = next(row for row in journey.rows(event) if row["message_type"] == "processing_receipt")
    assert receipt["status"] == "SENT" and receipt["payload"] == journey.gmail.sent[-1]["body"]
    assert fact not in receipt["payload"] and question not in receipt["payload"]

    journey.reopen()
    second = journey.ledger.handle(event, POLICY.version)
    assert second.action == "allow" and second.grant_business and not second.granted
    assert second.business_body.strip() == business
    case, duplicate, plan = journey.process(event, proposal(
        facts=[("full_name", name, fact)], questions=[("application", question)],
    ))
    assert not duplicate and plan == "blocked"
    assert len(journey.model.extracted) == 1
    view = journey.model.extracted[0]
    assert_original_envelope(view, event)
    assert view.body.strip() == business and "PC-" not in view.body
    assert event.model_dump_json() == raw
    assert case.profile.full_name == name and case.latest_customer_message.strip() == business
    assert all(item.source_event_id == event.id for item in case.evidence)
    assert journey.store.event_processed(event.id) and event.id not in journey.ledger.deferred_ids()
    assert journey.ledger.epoch(case.id) == before_epoch + 1 and len(journey.audits(event)) == 1
    assert_no_confirmation(case)
    assert {row["message_type"] for row in journey.rows(event)} == {"processing_receipt", "blocked"}
    journey.dispatch("blocked")
    reply = next(row for row in journey.rows(event) if row["message_type"] == "blocked")
    assert reply["status"] == "SENT" and APPLICATION_SOURCE in journey.gmail.sent[-1]["body"]
    assert reply["provider_message_id"] != receipt["provider_message_id"]
    assert reply["reply_subject"] == f"Re: {event.subject}" and reply["in_reply_to"] == event.rfc_message_id
    assert journey.gmail.sent[-1]["body"] == reply["payload"]
    assert len(journey.gmail.sent) == 3  # Notice, control receipt, then independent business reply.
    assert journey.process(event)[1]
    assert len(journey.model.extracted) == 1 and len(journey.audits(event)) == 1


@pytest.mark.parametrize("metadata_only", [False, True])
def test_mixed_grant_attachment_waits_then_reads_original_bytes_exactly_once(journey, tmp_path, metadata_only):
    journey.notice()
    epoch = journey.ledger.epoch(journey.case().id)
    attachment = tmp_path / "ordinary-support.pdf"
    original_bytes = b"Synthetic retained supporting document, never sent to a provider"
    attachment.write_bytes(original_bytes)
    event = journey.event(journey.grant_text(), attachment_paths=[] if metadata_only else [str(attachment)])
    preview = event.model_copy(deep=True)
    first = (journey.ledger.handle(preview, POLICY.version, has_attachments=True)
             if metadata_only else journey.ledger.handle(preview, POLICY.version))
    assert first.action == "defer" and first.granted
    assert journey.reads == journey.model.extracted == []
    assert event.id in journey.ledger.deferred_ids() and not journey.store.event_processed(event.id)
    journey.reopen()
    materialized = event.model_copy(update={"attachment_paths": [str(attachment)]})
    case, duplicate, _ = journey.process(materialized)
    assert not duplicate and journey.reads == [attachment] and len(journey.model.extracted) == 1
    assert journey.document_bytes == [original_bytes]
    assert journey.model.extracted[0].id == event.id and journey.model.extracted[0].body.strip() == ""
    assert_original_envelope(journey.model.extracted[0], materialized)
    assert len(case.documents) == 1 and case.documents[0].source_event_id == event.id
    assert case.documents[0].path == str(attachment)
    assert event.id not in journey.ledger.deferred_ids() and journey.store.event_processed(event.id)
    assert_no_confirmation(case)
    assert journey.ledger.epoch(case.id) == epoch + 1
    assert journey.process(materialized)[1]
    assert len(journey.reads) == len(journey.model.extracted) == len(journey.audits(event)) == 1


@pytest.mark.parametrize("language", ["zh", "en"])
def test_pure_grant_remains_control_and_is_never_sent_to_the_extractor(journey, language):
    journey.notice()
    epoch = journey.ledger.epoch(journey.case().id)
    event = journey.event(journey.grant_text(language))
    case, duplicate, plan = journey.process(event)
    assert not duplicate and plan == "processing_receipt" and journey.ledger.allowed(case)
    assert journey.ledger.epoch(case.id) == epoch + 1
    assert not journey.store.event_processed(event.id) and event.id not in journey.ledger.deferred_ids()
    journey.reopen()
    journey.process(event)
    assert journey.model.extracted == journey.model.rendered == journey.reads == []
    assert len(journey.audits(event)) == 1
    assert_no_confirmation(journey.case())


def test_direct_workflow_first_pass_grants_but_only_the_next_pass_processes_business(journey):
    journey.notice()
    fact = "My full name is Rowan Example."
    event = journey.event(journey.grant_text() + " " + fact)
    patch = proposal(facts=[("full_name", "Rowan Example", fact)])
    first, duplicate, _ = journey.process(event, patch)
    assert not duplicate and journey.ledger.allowed(first)
    assert journey.model.extracted == journey.model.rendered == []
    assert first.profile.full_name is None and event.id in journey.ledger.deferred_ids()
    epoch = journey.ledger.epoch(first.id)
    journey.reopen()
    second, duplicate, _ = journey.process(event)
    assert not duplicate and second.profile.full_name == "Rowan Example"
    assert len(journey.model.extracted) == 1 and journey.ledger.epoch(second.id) == epoch
    assert len(journey.audits(event)) == 1 and journey.store.event_processed(event.id)


@pytest.mark.parametrize("kind,control", [
    ("withdrawn", WITHDRAW),
    ("declined", "I do not consent to processing my information."),
])
@pytest.mark.parametrize("negative_first", [True, False])
def test_withdrawal_or_decline_dominates_the_whole_mixed_envelope(
    journey, tmp_path, kind, control, negative_first,
):
    journey.notice()
    grant = journey.grant_text()
    ordered = [control, grant] if negative_first else [grant, control]
    attachment = tmp_path / "do-not-read.pdf"
    attachment.write_bytes(b"Synthetic retained bytes")
    fact = "My full name is Rowan Example."
    question = "Where do I apply for my UK visitor visa?"
    event = journey.event(" ".join([*ordered, fact, question]), attachment_paths=[str(attachment)])
    journey.process(event, proposal(facts=[("full_name", "Rowan Example", fact)],
                                    questions=[("application", question)]))
    assert not journey.ledger.allowed(journey.case())
    assert journey.model.extracted == journey.model.rendered == journey.reads == []
    assert [row["action"] for row in journey.audits(event)] == [kind]
    assert event.id not in journey.ledger.deferred_ids() and not journey.store.event_processed(event.id)
    journey.reopen()
    journey.process(event)
    assert len(journey.audits(event)) == 1 and journey.model.extracted == journey.reads == []
    assert_no_confirmation(journey.case())


@pytest.mark.parametrize("invalid", [
    "quoted", "forwarded", "conditional", "conditional_separate", "restricted_same_clause",
    "restricted_separate", "old_reference", "other_sender", "other_thread",
])
def test_business_facts_and_questions_do_not_make_invalid_consent_effective(journey, tmp_path, invalid):
    journey.notice()
    reference = journey.ledger.reference(journey.case().id)
    if invalid == "old_reference":
        journey.ledger.configure(ProcessingScope(SCOPE.provider, SCOPE.model, "2026-09-05"))
        journey.notice()
    grant = journey.grant_text(reference=reference)
    if invalid == "quoted":
        control = f'My friend wrote "{grant}"'
    elif invalid == "forwarded":
        control = "Thank you.\n\nOn Friday, Applicant wrote:\n" + grant
    elif invalid == "conditional":
        control = "If you never share anything, " + grant
    elif invalid == "conditional_separate":
        control = "Only if you never share anything. " + grant
    elif invalid == "restricted_same_clause":
        control = grant.rstrip(".") + " but do not send my information to the model provider."
    elif invalid == "restricted_separate":
        control = grant + " But do not send my information to the model provider."
    else:
        control = grant
    fact = "My full name is Rowan Example."
    question = "Where do I apply for my UK visitor visa?"
    attachment = tmp_path / "unconsented-support.pdf"
    attachment.write_bytes(b"Synthetic unopened document")
    overrides = {"attachment_paths": [str(attachment)]}
    if invalid == "other_sender":
        overrides["sender"] = "unrelated-person@example.test"
    elif invalid == "other_thread":
        overrides["external_thread_id"] = "unrelated-consent-thread"
    event = journey.event(" ".join((control, fact, question)), **overrides)
    patch = proposal(facts=[("full_name", "Rowan Example", fact)], questions=[("application", question)])
    if invalid == "other_sender":
        with pytest.raises(ProcessingConsentRequired):
            journey.process(event, patch)
    else:
        journey.process(event, patch)
    assert all(not journey.ledger.allowed(case) for case in journey.store.list_cases())
    assert journey.model.extracted == journey.model.rendered == journey.reads == []
    assert not journey.store.event_processed(event.id)
    assert journey.case().profile.full_name is None


def test_a_pending_notice_is_not_authority_for_even_a_well_formed_mixed_grant(journey):
    journey.notice(send=False)
    event = journey.event(journey.grant_text() + " My full name is Rowan Example.")
    journey.process(event)
    assert journey.gmail.sent == [] and not journey.ledger.allowed(journey.case())
    assert journey.model.extracted == journey.model.rendered == []
    assert not journey.store.event_processed(event.id)


def test_mixed_regrant_processes_new_facts_without_resuming_or_confirming_paused_preparation(journey):
    journey.pure_grant()
    pause = "Please pause all my UK visa preparation."
    paused, _, _ = journey.process(journey.event(pause), proposal(control=("pause", pause)))
    assert paused.preparation_paused
    pause_epoch = paused.preparation_control_epoch
    journey.process(journey.event(WITHDRAW))
    journey.notice()
    grant = journey.grant_text()
    resume = "Please resume my UK visa preparation."
    fact = "My full name is Rowan Example."
    business = "\n".join((fact, resume, "I confirm the profile summary", "I confirm the final summary"))
    event = journey.event(grant + "\n" + business)
    first = journey.ledger.handle(event, POLICY.version)
    assert first.action == "defer" and first.granted
    before = len(journey.model.extracted)
    journey.reopen()
    case, duplicate, _ = journey.process(event, proposal(
        facts=[("full_name", "Rowan Example", fact)], control=("resume", resume),
    ))
    assert not duplicate and len(journey.model.extracted) == before + 1
    assert case.profile.full_name == "Rowan Example"
    assert case.preparation_paused and case.preparation_control_epoch == pause_epoch
    assert case.latest_preparation_action is None
    assert_no_confirmation(case)
    assert journey.model.extracted[-1].body.strip() == business
    assert len(journey.audits(event)) == 1


def test_explicit_pause_in_mixed_grant_is_retained_while_business_facts_are_processed(journey):
    journey.notice()
    pause = "Please pause all my UK visa preparation."
    fact = "My full name is Rowan Example."
    event = journey.event(" ".join((journey.grant_text(), pause, fact)))
    first = journey.ledger.handle(event, POLICY.version)
    assert first.action == "defer" and first.granted
    case, _, _ = journey.process(event, proposal(
        facts=[("full_name", "Rowan Example", fact)], control=("pause", pause),
    ))
    assert case.profile.full_name == "Rowan Example"
    assert case.preparation_paused and case.latest_preparation_action == "pause"
    assert case.preparation_control_epoch == 1
    assert_no_confirmation(case)


def test_withdrawal_between_grant_and_business_keeps_original_pending_until_new_grant(journey):
    journey.pure_grant()
    pause = "Please pause all my UK visa preparation."
    paused, _, _ = journey.process(journey.event(pause), proposal(control=("pause", pause)))
    assert paused.preparation_paused
    journey.process(journey.event(WITHDRAW))
    journey.notice()
    fact = "My full name is Rowan Example."
    resume = "Please resume my UK visa preparation."
    original = journey.event("\n".join((journey.grant_text(), fact, resume,
                                        "I confirm the profile summary", "I confirm the final summary")))
    raw = original.model_dump_json()
    assert journey.ledger.handle(original, POLICY.version).granted
    granted_epoch = journey.ledger.epoch(journey.case().id)
    journey.process(journey.event(WITHDRAW))
    withdrawn_epoch = journey.ledger.epoch(journey.case().id)
    assert withdrawn_epoch > granted_epoch
    before_extraction = len(journey.model.extracted)
    journey.reopen()
    case, _, _ = journey.process(original, proposal(facts=[("full_name", "Rowan Example", fact)],
                                                    control=("resume", resume)))
    assert not journey.ledger.allowed(case) and len(journey.model.extracted) == before_extraction
    assert original.id in journey.ledger.deferred_ids() and not journey.store.event_processed(original.id)
    assert journey.ledger.epoch(case.id) == withdrawn_epoch and len(journey.audits(original)) == 1
    journey.pure_grant()
    current_epoch = journey.ledger.epoch(journey.case().id)
    case, duplicate, _ = journey.process(original)
    assert not duplicate and case.profile.full_name == "Rowan Example"
    assert journey.ledger.epoch(case.id) == current_epoch and len(journey.audits(original)) == 1
    assert original.model_dump_json() == raw
    assert_original_envelope(journey.model.extracted[-1], original)
    assert len(journey.model.extracted) == before_extraction + 1
    assert case.preparation_paused and case.preparation_control_epoch == paused.preparation_control_epoch
    assert_no_confirmation(case)


def test_late_replay_preserves_original_receipt_order_and_remains_held(journey):
    journey.notice()
    fact = "My full name is Rowan Example."
    original = journey.event(journey.grant_text() + " " + fact)
    raw = original.model_dump_json()
    assert journey.ledger.handle(original, POLICY.version).granted
    newer = journey.event("Where do I apply for my UK visitor visa?")
    journey.process(newer, proposal(questions=[("application", newer.body)]))
    assert journey.case().last_inbound_received_at == newer.received_at
    journey.reopen()
    case, duplicate, plan = journey.process(original, proposal(facts=[("full_name", "Rowan Example", fact)]))
    assert not duplicate and plan == "out_of_order_held"
    assert case.profile.full_name is None and [item.id for item in journey.model.extracted] == [newer.id]
    held = next(item for item in journey.store.list_held_inbound(case.id) if item["id"] == original.id)
    assert held["reason_code"] == "OUT_OF_ORDER_EVENT"
    retained = InboundEvent.model_validate_json(held["payload_json"])
    assert_original_envelope(retained, original)
    assert original.model_dump_json() == raw


def test_old_unsent_receipt_cannot_displace_or_double_send_the_new_business_reply(journey):
    journey.notice()
    question = "Where do I apply for my UK visitor visa?"
    event = journey.event(journey.grant_text() + " " + question)
    assert journey.ledger.handle(event, POLICY.version).granted
    receipt = next(row for row in journey.rows(event) if row["message_type"] == "processing_receipt")
    assert receipt["status"] == "PENDING"
    journey.process(event, proposal(questions=[("application", question)]))
    sent_before = len(journey.gmail.sent)
    journey.dispatch("processing_receipt", "blocked")
    rows = {row["message_type"]: row for row in journey.rows(event)}
    assert rows["blocked"]["status"] == "SENT"
    assert rows["processing_receipt"]["status"] != "SENT"
    assert len(journey.gmail.sent) == sent_before + 1
    assert APPLICATION_SOURCE in journey.gmail.sent[-1]["body"]


@pytest.mark.parametrize("field", ["body", "subject", "received_at"])
def test_reusing_a_granted_mixed_id_cannot_replace_its_original_body_subject_or_receipt_time(journey, field):
    journey.notice()
    original = journey.event(journey.grant_text() + " My full name is Rowan Example.")
    assert journey.ledger.handle(original, POLICY.version).granted
    epoch = journey.ledger.epoch(journey.case().id)
    replacements = {
        "body": original.body + " Please resume my UK visa preparation.",
        "subject": "Replacement subject cannot impersonate the original",
        "received_at": original.received_at + timedelta(days=1),
    }
    replacement = original.model_copy(update={field: replacements[field]})
    journey.reopen()
    with pytest.raises(ProcessingConsentRequired):
        journey.process(replacement)
    assert journey.model.extracted == journey.model.rendered == journey.reads == []
    assert journey.ledger.epoch(journey.case().id) == epoch and len(journey.audits(original)) == 1
    assert original.id in journey.ledger.deferred_ids() and not journey.store.event_processed(original.id)
    assert journey.case().profile.full_name is None


@pytest.mark.parametrize("withdraw_before_worker,tamper", [
    (False, None), (True, None), (True, "unbound"), (True, "missing_held"),
    (True, "body"), (True, "received_at"), (True, "requested_fields"),
])
def test_audited_human_review_retry_restores_mixed_business_without_restoring_authority(
    journey, withdraw_before_worker, tamper,
):
    # OUT_OF_ORDER_EVENT is intentionally not an accepted queue_review_retry
    # reason. The separate chronology test stays held. This exercises the
    # actually supported HUMAN_REVIEW_CASE_NEW_EVENT recovery, without changing
    # a received date or hand-editing a case status to bypass that boundary.
    journey.pure_grant()
    pause = "Please pause all my UK visa preparation."
    paused, _, _ = journey.process(journey.event(pause), proposal(control=("pause", pause)))
    assert paused.preparation_paused
    review_event = journey.event("There is an unresolved mismatch in my application details.")
    review_patch = proposal().model_copy(update={
        "requires_human_review": True, "ambiguities": ["Applicant detail requires operator review"],
    })
    reviewed, _, _ = journey.process(review_event, review_patch)
    assert reviewed.status == CaseStatus.HUMAN_REVIEW_REQUIRED
    journey.process(journey.event(WITHDRAW))
    journey.notice()
    old_reference = journey.ledger.reference(journey.case().id)
    fact = "My full name is Rowan Example."
    resume = "Please resume my UK visa preparation."
    business = "\n".join((fact, resume, "I confirm the profile summary", "I confirm the final summary"))
    original = journey.event(journey.grant_text() + "\n" + business)
    assert journey.ledger.handle(original, POLICY.version).granted
    held_case, duplicate, plan = journey.process(original)
    assert not duplicate and plan == "human_review_case_held"
    held = next(row for row in journey.store.list_held_inbound(held_case.id) if row["id"] == original.id)
    assert held["reason_code"] == "HUMAN_REVIEW_CASE_NEW_EVENT"
    assert InboundEvent.model_validate_json(held["payload_json"]) == original
    before_epoch = journey.ledger.epoch(held_case.id)
    actor = "Fictional local reviewer"
    reason = "Reviewed the retained applicant update; retry its original business details through normal validation."
    retry_id = queue_review_retry(
        journey.store, case_id=held_case.id, held_event_id=original.id,
        expected_fingerprint=review_fingerprint(held_case), actor=actor, reason=reason,
    )
    binding = journey.store.connection.execute(
        "SELECT * FROM review_actions WHERE retry_event_id=?", (retry_id,),
    ).fetchone()
    assert binding["held_event_id"] == original.id and binding["case_id"] == held_case.id
    assert binding["actor"] == actor and binding["reason"] == reason
    queued = next(row for row in journey.store.list_inbound_queue() if row["id"] == retry_id)
    retry = InboundEvent.model_validate_json(queued["payload_json"])
    assert retry.id != original.id and retry.body == original.body and old_reference in retry.body
    assert_original_envelope(retry, original.model_copy(update={"id": retry_id}))
    assert old_reference != journey.ledger.reference(held_case.id)
    before_extraction = len(journey.model.extracted)
    journey.model.patches[retry_id] = proposal(facts=[("full_name", "Rowan Example", fact)],
                                              control=("resume", resume))
    journey.reopen()
    if withdraw_before_worker:
        journey.process(journey.event(WITHDRAW))
        before_epoch = journey.ledger.epoch(held_case.id)
    workflow = WorkflowService(journey.store, POLICY, journey.model, document_reader=journey.read,
                               today_provider=lambda: TODAY)
    outcomes = InboundEventWorker(journey.store, workflow, channel="gmail_review").process_due(journey.tick())
    if withdraw_before_worker:
        assert len(outcomes) == 1 and outcomes[0].event_id == retry_id
        assert outcomes[0].status in {"AWAITING_CONSENT", "RETRY"}
        assert not journey.ledger.allowed(journey.case())
        assert len(journey.model.extracted) == before_extraction
        assert journey.case().profile.full_name is None and journey.case().preparation_paused
        assert_no_confirmation(journey.case())
        assert not journey.store.event_processed(retry_id)
        assert journey.ledger.epoch(held_case.id) == before_epoch
        assert len(journey.audits(original)) == 1 and journey.audits(retry) == []
        journey.pure_grant()
        before_epoch = journey.ledger.epoch(held_case.id)
        journey.clock += timedelta(minutes=2)  # Queue backoff can pass; the original receipt date cannot change.
        journey.reopen()
        if tamper:
            with journey.store.connection:
                if tamper == "unbound":
                    journey.store.connection.execute("DELETE FROM review_actions WHERE retry_event_id=?", (retry_id,))
                elif tamper == "missing_held":
                    journey.store.connection.execute("DELETE FROM held_inbound_events WHERE id=?", (original.id,))
                else:
                    replacement = {
                        "body": retry.body + " Please use a different application.",
                        "received_at": retry.received_at + timedelta(days=1),
                        "requested_fields": ["date_of_birth"],
                    }[tamper]
                    changed = retry.model_copy(update={tamper: replacement})
                    journey.store.connection.execute("UPDATE inbound_queue SET payload_json=? WHERE id=?",
                                                     (changed.model_dump_json(), retry_id))
        workflow = WorkflowService(journey.store, POLICY, journey.model, document_reader=journey.read,
                                   today_provider=lambda: TODAY)
        outcomes = InboundEventWorker(journey.store, workflow, channel="gmail_review").process_due(journey.tick())
        if tamper:
            assert outcomes == [] and len(journey.model.extracted) == before_extraction
            pending = next(row for row in journey.store.list_inbound_queue() if row["id"] == retry_id)
            assert pending["status"] == "AWAITING_CONSENT"
            assert not journey.store.resume_inbound_after_consent(
                retry_id, case_id=held_case.id, channel="gmail_review", consent_epoch=before_epoch,
                payload_json=pending["payload_json"], case_snapshot_json=journey.case().model_dump_json(),
            )
            assert journey.ledger.allowed(journey.case()) and journey.ledger.epoch(held_case.id) == before_epoch
            assert not journey.store.event_processed(retry_id) and journey.audits(retry) == []
            assert journey.case().profile.full_name is None and journey.case().preparation_paused
            assert_no_confirmation(journey.case())
            return
    assert [(item.event_id, item.status) for item in outcomes] == [(retry_id, "PROCESSED")]
    case = journey.case()
    assert len(journey.model.extracted) == before_extraction + 1
    view = journey.model.extracted[-1]
    assert view.id == retry_id and view.body == business and old_reference not in view.body
    assert_original_envelope(view, retry)
    assert case.profile.full_name == "Rowan Example"
    assert case.preparation_paused and case.preparation_control_epoch == paused.preparation_control_epoch
    assert_no_confirmation(case)
    assert journey.ledger.epoch(case.id) == before_epoch
    assert len(journey.audits(original)) == 1 and journey.audits(retry) == []
    assert journey.store.event_processed(original.id) and journey.store.event_processed(retry_id)
    assert any(item.fact_key == "full_name" and item.source_event_id == retry_id for item in case.evidence)
    retained = next(row for row in journey.store.list_held_inbound(case.id) if row["id"] == original.id)
    assert retained == held
    assert InboundEventWorker(journey.store, workflow, channel="gmail_review").process_due(journey.tick()) == []
