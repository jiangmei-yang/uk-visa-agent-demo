from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore


class CaptureAdapter(GmailAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "accepted"}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_mixed_correction_attachment_and_new_fact_all_reach_gmail(tmp_path, language):
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="mixed", external_thread_id="t", applicant_contact="user@example.test",
                policy_version="v", customer_language=language)
    case.profile.visit_purpose = "conference"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.latest_changes = {"visit_purpose": "conference"}
    case.latest_received_facts = {"occupation_status": "student", "funding_source": "self"}
    case.latest_document_names = ["Invitation.pdf", "Enrollment.pdf"]
    event = InboundEvent(id="mixed", external_thread_id="t", sender=case.applicant_contact,
        subject="Updated plans", body="It's a conference, not a holiday. I'm a student and paying "
        "myself. I've attached the invitation and my enrollment letter.", channel="gmail", received_at=now)
    store.commit_event(case, event, "blocked", "model draft")
    before = store.get_case(case.id).model_dump_json()
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, case.applicant_contact),
                                  channel="gmail", allowed_message_types=("blocked",))
    try:
        assert dispatcher.dispatch_due(now)[0].status == "SENT"
        body = adapter.calls[0]["body"]
        assert "Invitation.pdf" in body and "Enrollment.pdf" in body
        assert ("参加会议" if language == "zh" else "conference") in body
        assert ("你目前在读书" if language == "zh" else "you're studying") in body.casefold()
        assert ("费用由你自己承担" if language == "zh" else "paying for the trip yourself") in body
        assert not body.startswith(("Hello", "你好"))
        assert "Who will pay for the trip?" not in body
        assert store.get_case(case.id).model_dump_json() == before
        assert store.list_outbox()[0]["payload"] == body
        assert dispatcher.dispatch_due(now) == [] and len(adapter.calls) == 1
    finally:
        store.close()


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("plan", ["awaiting_profile_confirmation", "awaiting_confirmation"])
def test_automatic_confirmation_preserves_what_customer_is_confirming(
    tmp_path: Path, language: str, plan: str,
) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="user@example.test",
                policy_version="v", customer_language=language)
    case.profile.full_name = "Example Applicant"
    event = InboundEvent(id="e", external_thread_id="t", sender=case.applicant_contact,
                         subject="My application", body="Here are my details", channel="gmail",
                         received_at=now)
    store.commit_event(case, event, plan, "draft")
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "user@example.test"),
                                  channel="gmail", allowed_message_types=(plan,))
    try:
        assert dispatcher.dispatch_due(now)[0].status == "SENT"
        body = adapter.calls[0]["body"]
        assert "Example Applicant" in body
        assert store.list_outbox()[0]["payload"] == body
        document_heading = "这次整理使用的材料" if language == "zh" else "CURRENT DOCUMENTS"
        next_step = "继续准备所需材料" if language == "zh" else "continue with the supporting documents"
        if plan == "awaiting_profile_confirmation":
            assert next_step in body
            assert document_heading not in body
        else:
            assert document_heading in body
            assert next_step not in body
        assert dispatcher.dispatch_due(now) == []
    finally:
        store.close()


def test_backlog_withholds_old_drafts_without_delaying_latest_or_touching_uncertain(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="user@example.test", policy_version="v")
    for n in range(152):
        event = InboundEvent(id=f"e{n}", external_thread_id="t", sender=case.applicant_contact,
            subject="UK visit", body="Hello", channel="gmail", received_at=now)
        store.commit_event(case, event, "blocked", "draft")
    with store.connection:
        store.connection.execute("UPDATE outbox SET status='SENDING' WHERE event_id='e0'")
    adapter = CaptureAdapter()
    sender = AutomaticGmailReplySender(adapter, store, "user@example.test")
    assert sender.withhold_obsolete_unsent() == 150
    rows = store.list_outbox()
    assert next(row for row in rows if row["event_id"] == "e0")["status"] == "SENDING"
    assert all(row["attempt_count"] == 0 for row in rows)
    dispatcher = OutboxDispatcher(store, sender, channel="gmail", allowed_message_types=("blocked",))
    assert dispatcher.dispatch_due(now, limit=1)[0].status == "SENT"
    assert len(adapter.calls) == 1 and sender.withhold_obsolete_unsent() == 0
    store.close()


def test_automatic_reply_replaces_model_wording_and_leaves_final_pack_pending(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="case-auto", external_thread_id="thread", applicant_contact="User Name <user@example.test>",
                policy_version="test", customer_language="zh")
    event = InboundEvent(id="first", external_thread_id="thread", sender=case.applicant_contact,
                         subject="普通咨询", body="需要什么资料", channel="gmail", received_at=now)
    store.commit_event(case, event, "blocked", "untrusted draft claims approval")
    adapter = CaptureAdapter()
    dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "user@example.test"),
                                  channel="gmail", allowed_message_types=("blocked",))
    try:
        assert dispatcher.dispatch_due(now)[0].status == "SENT"
        assert "approval" not in adapter.calls[0]["body"]
        assert store.list_outbox()[0]["payload"] == adapter.calls[0]["body"]
        assert dispatcher.dispatch_due(now) == []
        final = event.model_copy(update={"id": "final"})
        store.commit_event(case, final, "ready", "Pack awaiting review")
        assert dispatcher.dispatch_due(now) == []
        assert next(r for r in store.list_outbox() if r["message_type"] == "ready")["status"] == "PENDING"
    finally:
        store.close()


def test_other_recipient_is_never_automatically_contacted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "db")
    now = datetime.now(UTC)
    case = Case(id="c", external_thread_id="t", applicant_contact="other@example.test", policy_version="v")
    event = InboundEvent(id="e", external_thread_id="t", sender=case.applicant_contact,
                         subject="hello", body="help", channel="gmail", received_at=now)
    store.commit_event(case, event, "blocked", "draft")
    adapter = CaptureAdapter()
    try:
        dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, "allowed@example.test"))
        assert dispatcher.dispatch_due(now)[0].status == "FAILED"
        assert adapter.calls == []
    finally:
        store.close()


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('status', [CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION])
@pytest.mark.parametrize('revision', [1, 2])
def test_finalized_correction_gets_one_honest_receipt_without_reopening(tmp_path, language, status, revision):
    from visa_agent.domain.policy import load_policy
    from visa_agent.llm.offline import OfflineFixtureLLM
    from visa_agent.workflow.service import WorkflowService

    store = SQLiteStore(tmp_path / 'db')
    now = datetime.now(UTC)
    case = Case(id='c', external_thread_id='t', applicant_contact='user@example.test', policy_version='v',
                customer_language=language, primary_channel='gmail', status=status, delivery_path='old.zip',
                delivery_revision=revision)
    store.save_case(case)
    before = case.model_dump_json()
    event = InboundEvent(id='correction', external_thread_id='t', sender=case.applicant_contact,
        subject='My plans changed', body='日期改了，请先不要用旧资料。' if language == 'zh'
        else 'My travel dates changed. Please do not use the previous documents.', channel='gmail',
        received_at=now, rfc_message_id='<correction@example.test>')
    workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')), OfflineFixtureLLM())
    adapter = CaptureAdapter()
    sender = AutomaticGmailReplySender(adapter, store, case.applicant_contact)
    dispatcher = OutboxDispatcher(store, sender, channel='gmail', allowed_message_types=('held_update_received',))
    try:
        assert workflow.process(event)[2] == 'finalized_case_held'
        assert sender.queue_finalized_update_receipts() == 1
        assert sender.queue_finalized_update_receipts() == 0
        assert dispatcher.dispatch_due(now)[0].status == 'SENT'
        body = adapter.calls[0]['body']
        assert ('目前还没有生成或发送修订版' if language == 'zh' else 'has not been prepared or sent') in body
        assert store.get_case(case.id).model_dump_json() == before
        assert store.has_unreviewed_held_updates(case.id)
        assert store.list_outbox()[0]['in_reply_to'] == '<correction@example.test>'
        assert store.list_outbox()[0]['case_revision'] == revision
        assert store.list_outbox()[0]['payload'] == body
        assert workflow.process(event)[1]
        assert sender.queue_finalized_update_receipts() == 0
        assert dispatcher.dispatch_due(now) == [] and len(adapter.calls) == 1
    finally:
        store.close()


def test_finalized_receipt_respects_recipient_scope_and_current_case_state(tmp_path):
    store = SQLiteStore(tmp_path / 'db')
    now = datetime.now(UTC)
    case = Case(id='c', external_thread_id='t', applicant_contact='user@example.test', policy_version='v',
                primary_channel='gmail', status=CaseStatus.READY_FOR_HUMAN_REVIEW)
    store.save_case(case)
    event = InboundEvent(id='e', external_thread_id='t', sender=case.applicant_contact,
                        subject='Correction', body='Please update the dates', channel='gmail', received_at=now)
    store.record_rejected_event(event_id='e', case_id='c', thread_id='t', reason_code='FINALIZED_CASE_NEW_EVENT',
                                detail='Held', held_event=event)
    adapter = CaptureAdapter()
    try:
        assert AutomaticGmailReplySender(adapter, store, 'other@example.test').queue_finalized_update_receipts() == 0
        sender = AutomaticGmailReplySender(adapter, store, case.applicant_contact)
        assert sender.queue_finalized_update_receipts() == 1
        case.status = CaseStatus.DRAFT
        store.save_case(case)
        dispatcher = OutboxDispatcher(store, sender, channel='gmail', allowed_message_types=('held_update_received',))
        assert dispatcher.dispatch_due(now)[0].status == 'FAILED'
        assert adapter.calls == []
    finally:
        store.close()


def test_existing_gmail_case_recovers_missed_date_deferral_and_keeps_it_across_turns(tmp_path):
    from visa_agent.domain.policy import load_policy
    from visa_agent.llm.offline import OfflineFixtureLLM
    from visa_agent.llm.ports import CasePatch
    from visa_agent.workflow.service import WorkflowService

    class CaptureExtraction(OfflineFixtureLLM):
        requested = []

        def extract_case_patch(self, event):
            self.requested.append(event.requested_fields)
            return CasePatch(updates=[], ambiguities=[])

    path = tmp_path / 'db'
    store = SQLiteStore(path)
    case = Case(id='c', external_thread_id='t', applicant_contact='user@example.test', policy_version='v',
        primary_channel='gmail', customer_language='zh', latest_customer_message='日期没定，姓名是示例申请人。',
        last_requested_fields=['planned_arrival_date','planned_departure_date','date_of_birth'])
    case.profile.full_name = 'Example Applicant'
    store.save_case(case)
    model = CaptureExtraction()
    adapter = CaptureAdapter()
    try:
        for index in range(3):
            store.close()
            store = SQLiteStore(path)
            workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')), model)
            now = datetime.now(UTC)
            event = InboundEvent(id=f'followup-{index}', external_thread_id='t', sender=case.applicant_contact,
                subject='UK trip', body='其他资料我在整理。', channel='gmail', received_at=now)
            result, _, _ = workflow.process(event)
            dates = {'planned_arrival_date','planned_departure_date'}
            assert dates <= set(result.deferred_fields)
            assert not dates & set(model.requested[-1])
            assert not dates & set(result.last_requested_fields)
            dispatcher = OutboxDispatcher(store, AutomaticGmailReplySender(adapter, store, case.applicant_contact),
                                          channel='gmail', allowed_message_types=('blocked',))
            assert dispatcher.dispatch_due(now)[0].status == 'SENT'
            assert '哪天' not in adapter.calls[-1]['body']
            assert '抵达英国' not in adapter.calls[-1]['body']
            assert '离开英国' not in adapter.calls[-1]['body']
            assert workflow.process(event)[1]
            assert dispatcher.dispatch_due(now) == []
        assert len(adapter.calls) == 3
    finally:
        store.close()


@pytest.mark.parametrize('unsafe', [False, True])
def test_opt_in_draft_is_revalidated_and_exact_decision_persisted(tmp_path, unsafe):
    from visa_agent.workflow.conversation import reply_items

    store = SQLiteStore(tmp_path / 'db')
    now = datetime.now(UTC)
    case = Case(id='c', external_thread_id='t', applicant_contact='user@example.test', policy_version='v')
    draft = "Let's start with your travel plans.\n\n" + '\n'.join(reply_items(case)[1])
    if unsafe:
        draft += '\nNo documents are needed from you at this stage.'
    event = InboundEvent(id='e',external_thread_id='t',sender=case.applicant_contact,
                        subject='UK plans',body='Hello',channel='gmail',received_at=now)
    store.commit_event(case,event,'blocked',draft)
    adapter = CaptureAdapter()
    sender = AutomaticGmailReplySender(adapter,store,case.applicant_contact,allow_guarded_drafts=True)
    try:
        dispatcher = OutboxDispatcher(store,sender,channel='gmail',allowed_message_types=('blocked',))
        assert dispatcher.dispatch_due(now)[0].status == 'SENT'
        row = store.list_outbox()[0]
        assert row['payload'] == adapter.calls[0]['body']
        if unsafe:
            assert row['payload'] != draft and 'No documents are needed' not in row['payload']
            assert row['reply_render_mode'] == 'reviewed_fallback'
            assert 'unsupported preparation waiver' in row['reply_render_error']
        else:
            assert row['payload'] == draft
            assert row['reply_render_mode'] == 'guarded_draft'
            assert row['reply_render_error'] is None
        exported = store.export_case_data(case.id)['outbound_messages'][0]
        assert exported['reply_render_mode'] == row['reply_render_mode']
        assert dispatcher.dispatch_due(now) == [] and len(adapter.calls) == 1
    finally:
        store.close()


def test_opt_in_draft_does_not_replace_confirmation(tmp_path):
    store = SQLiteStore(tmp_path / 'db')
    now = datetime.now(UTC)
    case = Case(id='c',external_thread_id='t',applicant_contact='user@example.test',policy_version='v')
    event = InboundEvent(id='e',external_thread_id='t',sender=case.applicant_contact,
        subject='UK plans',body='Here are my details',channel='gmail',received_at=now)
    store.commit_event(case,event,'awaiting_confirmation','Uncontrolled confirmation wording')
    adapter = CaptureAdapter()
    try:
        sender = AutomaticGmailReplySender(adapter,store,case.applicant_contact,allow_guarded_drafts=True)
        assert OutboxDispatcher(store,sender).dispatch_due(now)[0].status == 'SENT'
        assert 'Uncontrolled confirmation' not in adapter.calls[0]['body']
        assert 'CURRENT DOCUMENTS' in adapter.calls[0]['body']
        assert store.list_outbox()[0]['reply_render_mode'] == 'reviewed'
    finally:
        store.close()
