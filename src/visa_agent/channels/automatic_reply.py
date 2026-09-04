"""Bounded automatic service replies; final packs still require reviewed dispatch."""

import re
from dataclasses import dataclass, replace
from email.utils import getaddresses

from visa_agent.channels.gmail import GmailAdapter, GmailReplySender
from visa_agent.channels.outbound import PermanentChannelError, ReplyRequest
from visa_agent.domain.models import Case, CaseStatus, InboundEvent
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore


@dataclass
class StoredDraft:
    text: str
    version: str = 'stored-workflow-draft'

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        raise RuntimeError('Stored drafts cannot extract applicant facts')

    def render_message(self, case: Case, plan: str) -> str:
        return self.text


class AutomaticGmailReplySender(GmailReplySender):
    def __init__(self, adapter: GmailAdapter, store: SQLiteStore, allowed_sender: str,
                 *, allow_guarded_drafts: bool = False) -> None:
        super().__init__(adapter)
        self.store = store
        self.allowed_sender = allowed_sender
        self.allow_guarded_drafts = allow_guarded_drafts

    def queue_finalized_update_receipts(self) -> int:
        """A receipt for retained corrections, never a revised pack or approval."""
        queued = 0
        rows = self.store.connection.execute(
            "SELECT id, case_id, payload_json FROM held_inbound_events "
            "WHERE reason_code='FINALIZED_CASE_NEW_EVENT' ORDER BY created_at, id",
        ).fetchall()
        with self.store.connection:
            for row in sorted(rows, key=lambda item: InboundEvent.model_validate_json(item['payload_json']).received_at):
                case = self.store.get_case(row['case_id'])
                event = InboundEvent.model_validate_json(row['payload_json'])
                addresses = [a.casefold() for _, a in getaddresses([event.sender])]
                if (case is None or case.primary_channel != 'gmail' or event.channel != 'gmail'
                        or addresses != [self.allowed_sender.casefold()]
                        or case.status not in {CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION}
                        or (case.last_inbound_received_at and event.received_at < case.last_inbound_received_at)
                        or self.store.connection.execute(
                            'SELECT 1 FROM review_actions WHERE held_event_id=?', (event.id,),
                        ).fetchone()):
                    continue
                in_reply_to = event.rfc_message_id or f'<{event.id}>'
                references = ' '.join(dict.fromkeys(f'{event.references or ""} {in_reply_to}'.split()))
                result = self.store.connection.execute(
                    "INSERT OR IGNORE INTO outbox(id,case_id,event_id,message_type,payload,channel,recipient,"
                    "external_thread_id,reply_subject,in_reply_to,references_header,case_revision,preparation_control_epoch) VALUES (?,?,?,'held_update_received',"
                    "'Receipt awaiting checked rendering','gmail',?,?,?,?,?,?,?)",
                    (f'out-{event.id}-held_update_received', case.id, event.id, event.sender,
                     event.external_thread_id, event.subject if event.subject.lower().startswith('re:')
                     else f'Re: {event.subject}', in_reply_to, references, case.delivery_revision,
                     case.preparation_control_epoch),
                )
                queued += result.rowcount
        return queued

    def _held_receipt(self, case_id: str, event_id: str) -> str:
        row = self.store.connection.execute(
            "SELECT payload_json FROM held_inbound_events WHERE id=? AND case_id=? "
            "AND reason_code='FINALIZED_CASE_NEW_EVENT' AND NOT EXISTS "
            "(SELECT 1 FROM review_actions WHERE held_event_id=held_inbound_events.id)",
            (event_id, case_id),
        ).fetchone()
        case = self.store.get_case(case_id)
        if row is None or case is None or case.status not in {
            CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.DELIVERED_AFTER_CONFIRMATION,
        }:
            raise PermanentChannelError('Held update receipt no longer matches current review state')
        event = InboundEvent.model_validate_json(row['payload_json'])
        zh = bool(re.search(r'[\u4e00-\u9fff]', event.body)) or (
            len(re.findall(r'[A-Za-z]+', event.body)) <= 4 and case.customer_language == 'zh')
        return (
            '收到这次补充的信息了，已记录下来。旧材料包的下载和后续发送已暂停；'
            '这次更新需要人工复核，目前还没有生成或发送修订版。'
            if zh else "I've received and recorded your update. Further sending "
            "and downloading of the previous pack is paused. The update needs human review; "
            "a revised pack has not been prepared or sent."
        )

    def withhold_obsolete_unsent(self) -> int:
        """Retain stale drafts as failed/withheld without consuming a provider-send slot."""
        count = 0
        with self.store.connection:
            rows = self.store.connection.execute("""
                SELECT old.id, old.recipient FROM outbox old
                WHERE old.channel='gmail' AND old.status='PENDING' AND old.attempt_count=0
                  AND old.message_type IN ('blocked','awaiting_profile_confirmation','awaiting_confirmation','held_update_received')
                  AND EXISTS (SELECT 1 FROM outbox newer
                              WHERE newer.case_id=old.case_id AND newer.rowid>old.rowid)
            """).fetchall()
            for row in rows:
                recipients = [address.casefold() for _, address in getaddresses([row["recipient"] or ""])]
                if recipients != [self.allowed_sender.casefold()]:
                    continue
                result = self.store.connection.execute("""
                    UPDATE outbox SET status='FAILED', last_error='Obsolete unsent reply withheld',
                                      next_attempt_at=NULL
                    WHERE id=? AND status='PENDING' AND attempt_count=0
                """, (row["id"],))
                count += result.rowcount
        return count

    def send(self, request: ReplyRequest) -> str:
        row = self.store.connection.execute(
            "SELECT case_id, event_id, message_type, preparation_control_epoch FROM outbox WHERE id = ?", (request.outbox_id,)
        ).fetchone()
        recipients = [address.casefold() for _, address in getaddresses([request.recipient])]
        if row is None or recipients != [self.allowed_sender.casefold()]:
            raise PermanentChannelError("Automatic reply is outside the registered sender scope")
        if request.attachment is not None or row["message_type"] == "ready":
            raise PermanentChannelError("Final delivery requires reviewed dispatch")
        latest = self.store.connection.execute(
            "SELECT id FROM outbox WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (row["case_id"],)
        ).fetchone()
        if latest["id"] != request.outbox_id:
            raise PermanentChannelError("Obsolete reply withheld in favour of current case state")
        case = self.store.get_case(row["case_id"])
        if case is None or row["message_type"] not in {
            "blocked", "awaiting_profile_confirmation", "awaiting_confirmation", "held_update_received"
        }:
            raise PermanentChannelError("Unsupported automatic reply plan")
        if row["preparation_control_epoch"] != case.preparation_control_epoch:
            raise PermanentChannelError("Reply predates a customer preparation pause or restart")
        if case.preparation_paused and row["message_type"] not in {"blocked", "held_update_received"}:
            raise PermanentChannelError("Preparation is paused; confirmation requests are withheld")
        body = (self._held_receipt(case.id, row['event_id']) if row['message_type'] == 'held_update_received'
                else deterministic_fallback_message(case, row["message_type"]))
        render_mode = 'reviewed'
        render_error = None
        if self.allow_guarded_drafts and row['message_type'] == 'blocked':
            guard = GuardedLLM(StoredDraft(request.body))
            body = guard.render_message(case, 'blocked')
            render_mode = 'reviewed_fallback' if guard.last_render_fallback else 'guarded_draft'
            render_error = guard.last_render_error
        # Persist the exact body before the side effect, including on a send-response crash.
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE outbox SET payload = ?, reply_render_mode=?, reply_render_error=? "
                "WHERE id = ? AND status = 'SENDING'",
                (body, render_mode, render_error, request.outbox_id),
            )
        return super().send(replace(request, body=body))
