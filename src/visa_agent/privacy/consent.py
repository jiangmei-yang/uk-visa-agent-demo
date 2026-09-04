"""Canonical, local processing-consent ledger; never a model interpretation.

An unconfigured store supports offline fixtures only. Live entry points must
configure their actual provider/model before accepting or processing content.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore

CONTROL_MESSAGE_TYPES = frozenset({"processing_notice", "processing_receipt"})
_PURPOSE = "UK visitor visa preparation: extract supplied facts, review documents, draft advice"
_NOTICE = """信息处理说明 / Information processing notice

为协助准备英国访客签证，我们会在本系统保存你提供的信息和文件，并把邮件及材料中的文字交给 {provider} 的 {model} 模型，用于提取资料、检查材料及准备回复。授权范围包括本线程之前尚未处理的邮件和材料。
这不是签证摘要确认、提交申请或发送材料包的授权。未经你的明确同意，我们不会开始上述处理。若同意，请用下方含授权参考码的句子回复，也可以在同封邮件中补充信息、问题或附件；这些内容会在记录授权后按原邮件接续处理。参考码用于确定你同意的是哪份说明，平时咨询不需要提供。也可以拒绝，或之后回复“我撤回资料处理同意”。撤回会停止之后的处理，不会自动删除本地记录，也不能收回已交给服务商的内容；我们不承诺服务商删除或不用于训练。你可以另行联系操作人员申请本地资料导出或删除。

To help prepare a UK visitor visa application, this system will retain the information and files you supply locally and send text from your messages and documents to {provider}, using model {model}, to extract information, review documents and prepare replies. This includes earlier, unprocessed messages and materials in this thread.
This is not confirmation of an application summary, permission to submit an application, or permission to send a document pack. We will not start this processing without your explicit agreement. To agree, reply with the sentence and consent reference below. You may include information, questions or attachments in the same email; they will be processed from the original message after consent is recorded. The reference identifies this notice and is not needed for ordinary questions. You may decline or later say “I withdraw my consent to processing my information.” Withdrawal stops further processing; it does not automatically delete local records or undo previous disclosures. We do not promise provider deletion or non-training. Contact the operator separately to request local data export or deletion.

Notice version: {version}"""


@dataclass(frozen=True)
class ProcessingScope:
    provider: str
    model: str
    version: str = "2026-09-05"

    def __post_init__(self) -> None:
        for value in (self.provider, self.model, self.version):
            if not isinstance(value, str) or not value.strip() or len(value) > 160:
                raise ValueError("Processing scope requires bounded provider, model and version")
            if any(char in value for char in "\r\n\x00"):
                raise ValueError("Processing scope cannot contain control characters")
        object.__setattr__(self, "provider", self.provider.strip().casefold())

    @property
    def id(self) -> str:
        content = {**asdict(self), "notice": self.notice, "purpose": _PURPOSE}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    @property
    def notice(self) -> str:
        return _NOTICE.format(**asdict(self))


class ProcessingConsentRequired(ValueError):
    """No effective applicant authorization for the configured processing scope."""


@dataclass(frozen=True)
class ConsentResult:
    action: Literal["allow", "defer", "control"]
    case_id: str
    granted: bool = False
    business_body: str | None = None
    grant_business: bool = False


def _contact(value: str, channel: str) -> str:
    if channel in {"email", "email_fixture", "gmail"}:
        addresses = getaddresses([value])
        if len(addresses) != 1 or not addresses[0][1] or "@" not in addresses[0][1]:
            raise ProcessingConsentRequired("Processing authorization requires one applicant address")
        return addresses[0][1].strip().casefold()
    return value.strip().casefold()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class _ControlParts:
    action: str | None
    statement: str
    business_body: str


def _control_parts(body: str) -> _ControlParts:
    # Reuse the transport-independent quote boundary, never the raw history.
    from visa_agent.workflow.conversation import latest_reply_text

    text = latest_reply_text(body)
    # Mask quotes without moving offsets: the business view removes only a
    # verified consent span, never independent facts after its reference.
    visible = re.sub(r'"[^\"]*"|“[^”]*”|「[^」]*」|『[^』]*』|`[^`]*`|(?<!\w)\'[^\']*\'(?!\w)',
                     lambda match: re.sub(r"[^\n]", " ", match[0]), text)
    found: list[tuple[str, str, int, int]] = []
    for part in re.finditer(r"[^。.!！;；\n]+[。.!！;；\n]*", visible):
        clause = part[0].strip(" 。.!！;；\n")
        if not clause or clause.startswith(('"', "'", "“", "「")):
            continue
        reference = re.search(r"(?:consent reference|授权参考码)\s*[:：]?\s*PC-[A-F0-9]{12}\b[）)]?",
                              clause, re.I)
        control_prefix = clause[:reference.end()] if reference else clause
        if re.search(
            r"[?？]|\b(if|unless|whether|would|might|tomorrow|later)\b|如果|假如|是否|以后|将来",
            control_prefix, re.I,
        ):
            continue
        if re.search(
            r"^I\s+(?:hereby\s+)?(?:withdraw|revoke)\s+(?:my\s+)?consent\b"
            r"|^(?:please\s+)?stop\s+processing\s+my\b"
            r"|^我(?:现在)?撤回.{0,16}(?:同意|授权)|^我不再同意.{0,20}(?:处理|资料|信息)"
            r"|^请停止处理我的(?:资料|信息|材料)", clause, re.I,
        ):
            found.append(("withdrawn", clause[:320], part.start(), part.end()))
        elif re.search(
            r"^I\s+(?:do\s+not|don't|cannot|can't)\s+consent\b"
            r"|^I\s+(?:do\s+not|don't)\s+agree\b.{0,100}\b(?:processing|notice)\b"
            r"|^我(?:现在)?不同意.{0,24}(?:处理|资料|信息|说明)"
            r"|^我拒绝.{0,20}(?:处理|资料|信息)", clause, re.I,
        ):
            found.append(("declined", clause[:320], part.start(), part.end()))
        elif re.search(
            r"^I\s+(?:hereby\s+)?(?:consent|agree)\s+to\b.{0,100}\b(?:processing|notice)\b"
            r"|^我同意.{0,40}(?:处理|这份说明|资料处理)"
            r"|^我授权.{0,24}处理", clause, re.I,
        ):
            # Without a reference this can be recognised as an attempted grant,
            # but _notice_sent_before will never authorize it.
            statement = clause[:reference.end()] if reference else clause
            clause_start = part.start() + len(part[0]) - len(part[0].lstrip())
            if reference and re.match(r"\s*[?？]", visible[clause_start + reference.end():]):
                found.append(("unclear", "Processing statement is a question", part.start(), part.end()))
                continue
            if re.search(
            r"\b(?:not|don't|except|but|only|without)\b|但是|但|不过|只同意|仅同意|不要|不允许",
                statement, re.I,
            ):
                continue
            start = part.start() + len(part[0]) - len(part[0].lstrip())
            end = start + len(statement)
            if end < len(text) and text[end] in "。.!！;；，,":
                end += 1  # The consent delimiter, not punctuation in a business fact.
            found.append(("granted", statement[:320], start, end))
    # Restrictions or uncertainty never become agreement merely because a
    # separate sentence contains an affirmative phrase.
    for kind in ("withdrawn", "declined", "unclear"):
        match = next((item for item in found if item[0] == kind), None)
        if match is not None:
            return _ControlParts(match[0], match[1], "")
    report = re.search(
        r"(?:^|\n)[^\n]{0,160}(?:said|says|wrote|writes|quote|example|template|说|写道|原文|模板|示例|举例)[^\n:：]{0,60}[:：]\s*(?:\n|$)|"
        r"\b(?:quoted|quoting|do not treat)\b|不要把|不代表.{0,10}(?:同意|授权)", visible, re.I,
    )
    remainder = visible
    for _, _, start, end in reversed(found):
        remainder = remainder[:start] + " " * (end - start) + remainder[end:]
    restricted = False
    privacy_scope = (
        r"\b(?:consent|notice|provider|model|third.part\w*|computer|locally|local storage)\b|"
        r"\b(?:stor(?:e|es|ed|ing)|retain\w*|shar(?:e|es|ed|ing)|disclos\w*|upload\w*|send\w*|transmit\w*)\s+"
        r"(?:(?:my|our|the|these|those|any|personal)\s+)?(?:data|information|documents|files|attachments|nothing)\b|"
        r"\b(?:not|don't|never)\s+(?:store|retain|share|disclose|upload|send|transmit|process|read)\b|"
        r"\bprocess\w*\s+(?:my|our|the)\s+(?:data|information|documents|files|attachments|PDFs?)\b|"
        r"同意|授权|这份说明|处理.{0,8}(?:资料|信息|材料)|保存|保留|共享|服务商|模型|第三方|本地|电脑"
    )
    for remainder_clause in re.split(r"[。.!！;；\n]+", remainder):
        if re.search(privacy_scope, remainder_clause, re.I) and re.search(
            r"[?？]|\b(?:but|except|only|without|if|unless|whether|not|don't|cannot|can't|no|nothing|refrain|avoid|stop)\b|\bexclud\w*\b|"
            r"但是|不过|只同意|仅同意|如果|假如|是否|不要|不许|不得|不能|不允许|不保存", remainder_clause, re.I,
        ):
            restricted = True
        if re.fullmatch(r"\s*no\s*", remainder_clause, re.I):
            restricted = True
    if found and re.search(r"\b(?:if|unless|provided|assuming)\b|如果|假如|只要|除非", visible[:found[0][2]], re.I):
        restricted = True
    if report or restricted:
        return _ControlParts("unclear", "Processing authorization was not unambiguous", "") if found else _ControlParts(None, "", text)
    if not found:
        return _ControlParts(None, "", text)
    business = text
    for _, _, start, end in reversed(found):
        business = business[:start] + business[end:]
    business = business.strip()
    if business.casefold().strip("。.!！;；，,") in {"thanks", "thank you", "谢谢", "好的", "收到"}:
        business = ""
    return _ControlParts(found[0][0], found[0][1], business)


def _control(body: str) -> tuple[str | None, str]:
    parsed = _control_parts(body)
    return parsed.action, parsed.statement


class ConsentLedger:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def scope(self) -> ProcessingScope | None:
        row = self.store.connection.execute("SELECT scope_json FROM processing_scope WHERE singleton=1").fetchone()
        return None if row is None else ProcessingScope(**json.loads(row["scope_json"]))

    def configure(self, scope: ProcessingScope) -> None:
        with self.store.atomic_write():
            # Reconstructing the old JSON with today's notice would silently
            # recompute its hash. Compare the actual persisted scope instead.
            current = self.store.connection.execute(
                "SELECT scope_id FROM processing_scope WHERE singleton=1").fetchone()
            if current is not None and current["scope_id"] == scope.id:
                return
            self.store.connection.execute(
                "INSERT INTO processing_scope(singleton,scope_id,scope_json) VALUES (1,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET scope_id=excluded.scope_id,scope_json=excluded.scope_json",
                (scope.id, json.dumps(asdict(scope), sort_keys=True)),
            )
            for case in self.store.list_cases():
                old_epoch = self.epoch(case.id)
                self._state(case, scope, "unknown", old_epoch + 1)
                self._invalidate(case)

    def required(self, case: Case) -> bool:
        return self.scope() is not None

    def allowed(self, case: Case) -> bool:
        scope = self.scope()
        if scope is None:
            return True  # No gate configured, NOT an implicit ledger grant.
        row = self._record(case.id)
        try:
            contact = _contact(case.applicant_contact, case.primary_channel)
        except ProcessingConsentRequired:
            return False
        return bool(row is not None and row["status"] == "granted" and row["scope_id"] == scope.id
                    and row["contact"] == contact and row["channel"] == case.primary_channel
                    and row["thread_id"] == case.external_thread_id)

    def require(self, case: Case) -> None:
        if not self.allowed(case):
            raise ProcessingConsentRequired("Applicant processing consent is required for the current provider and model")

    def epoch(self, case_id: str) -> int:
        row = self._record(case_id)
        return 0 if row is None else int(row["epoch"])

    def reference(self, case_id: str) -> str:
        """Public notice identifier, not a bearer token or processing authority."""
        scope = self.scope()
        if scope is None:
            raise ProcessingConsentRequired("No processing notice is configured")
        material = f"{case_id}:{scope.id}:{self.epoch(case_id)}"
        return "PC-" + hashlib.sha256(material.encode()).hexdigest()[:12].upper()

    def _record(self, case_id: str) -> Any:
        return self.store.connection.execute("SELECT * FROM processing_consent WHERE case_id=?", (case_id,)).fetchone()

    def _state(self, case: Case, scope: ProcessingScope, status: str, epoch: int) -> None:
        self.store.connection.execute(
            "INSERT INTO processing_consent(case_id,status,scope_id,epoch,contact,channel,thread_id,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET status=excluded.status,"
            "scope_id=excluded.scope_id,epoch=excluded.epoch,contact=excluded.contact,channel=excluded.channel,"
            "thread_id=excluded.thread_id,notice_outbox_id=NULL,updated_at=excluded.updated_at",
            (case.id, status, scope.id, epoch, _contact(case.applicant_contact, case.primary_channel),
             case.primary_channel, case.external_thread_id, datetime.now(UTC).isoformat()),
        )

    def _invalidate(self, case: Case) -> None:
        case.profile_confirmed = False
        case.final_summary_confirmed = False
        case.confirmation_kind = None
        case.confirmation_fingerprint = None
        case.confirmation_request_event_id = None
        case.last_requested_fields = []
        case.question_plan = []
        case.pending_question_fields = []
        case.updated_at = datetime.now(UTC)
        self.store.save_case(case)
        # Claimed/uncertain sends retain their reconciliation evidence.
        self.store.connection.execute(
            "UPDATE outbox SET status='FAILED',next_attempt_at=NULL,last_error='Processing authorization superseded' "
            "WHERE case_id=? AND status IN ('PENDING','RETRY')", (case.id,),
        )

    def handle(self, event: InboundEvent, policy_version: str, *,
               has_attachments: bool | None = None) -> ConsentResult:
        case_id = "case-" + uuid5(NAMESPACE_URL, event.external_thread_id).hex[:12]
        scope = self.scope()
        if scope is None:
            return ConsentResult("allow", case_id)
        attachments = bool(event.attachment_paths) or bool(has_attachments)
        with self.store.atomic_write():
            case = self.store.get_case_by_thread(event.external_thread_id)
            if case is None:
                case = Case(id=case_id, external_thread_id=event.external_thread_id,
                            applicant_contact=event.sender, primary_channel=event.channel,
                            policy_version=policy_version)
                self.store.save_case(case)
            if (case.primary_channel != event.channel
                    or _contact(case.applicant_contact, case.primary_channel) != _contact(event.sender, event.channel)):
                # Neither authorize nor disclose the applicant's thread to a different sender.
                raise ProcessingConsentRequired("Processing authorization sender does not match the case")
            audited = self.store.connection.execute(
                "SELECT * FROM processing_consent_events WHERE event_id=?", (event.id,)
            ).fetchone()
            if audited is not None:
                if audited["case_id"] != case.id:
                    raise ProcessingConsentRequired("Processing control event belongs to another case")
                pending = self.store.connection.execute(
                    "SELECT * FROM processing_deferred_events WHERE event_id=? AND completed_at IS NULL",
                    (event.id,),
                ).fetchone()
                if audited["action"] == "granted" and audited["business_pending"] and pending is not None:
                    if audited["message_sha256"] != self._message_hash(event, attachments):
                        raise ProcessingConsentRequired("Mixed processing message differs from its recorded original")
                    if not self.allowed(case):
                        if self.needs_scope_notice(case):
                            self._notice(case, event, scope)
                        return ConsentResult("defer", case.id, grant_business=True)
                    parsed = _control_parts(event.body)
                    if parsed.action != "granted" or parsed.statement != audited["excerpt"]:
                        raise ProcessingConsentRequired("Mixed processing statement no longer matches its audit")
                    return ConsentResult("allow", case.id, business_body=parsed.business_body, grant_business=True)
                return ConsentResult("control", case.id)
            record = self._record(case.id)
            if record is None or record["scope_id"] != scope.id:
                self._state(case, scope, "unknown", self.epoch(case.id))
                self._invalidate(case)
            reviewed = self._reviewed_business(event, case, scope)
            if reviewed is not None:
                return reviewed
            parsed = _control_parts(event.body)
            action, excerpt = parsed.action, parsed.statement
            if action in {"declined", "withdrawn"}:
                self._state(case, scope, action, self.epoch(case.id) + 1)
                self._invalidate(case)
                self._audit(event, case, scope, action, excerpt)
                self._queue(case, event, scope, "processing_receipt", self._receipt(action))
                return ConsentResult("control", case.id)
            if action in {"granted", "unclear"}:
                if action == "granted" and self._notice_sent_before(case, event, excerpt):
                    mixed = bool(parsed.business_body or attachments)
                    self._state(case, scope, "granted", self.epoch(case.id) + 1)
                    self._invalidate(case)
                    self._audit(event, case, scope, "granted", excerpt, business_pending=mixed,
                                has_attachments=attachments)
                    if mixed:
                        self._defer(event, case)
                    self._queue(case, event, scope, "processing_receipt", self._receipt("granted"))
                    return ConsentResult("defer" if mixed else "control", case.id, granted=True)
                self._audit(event, case, scope, "grant_not_effective", excerpt)
                self._notice(case, event, scope)
                return ConsentResult("control", case.id)
            if self.allowed(case):
                return ConsentResult("allow", case.id)
            self._defer(event, case)
            self._notice(case, event, scope)
            return ConsentResult("defer", case.id)

    def _reviewed_business(self, event: InboundEvent, case: Case,
                           scope: ProcessingScope) -> ConsentResult | None:
        """An audited operator retry is not a new customer consent statement.

        Recover only the original mixed business view. The review action may
        permit intake to retry, but cannot grant processing, resume preparation
        or confirm a summary on behalf of the applicant.
        """
        review = self.store.connection.execute(
            "SELECT * FROM review_actions WHERE retry_event_id=?", (event.id,),
        ).fetchone()
        if review is None:
            return None
        audit = self.store.connection.execute(
            "SELECT * FROM processing_consent_events WHERE event_id=?",
            (review["held_event_id"],),
        ).fetchone()
        if audit is None or audit["action"] != "granted" or not audit["business_pending"]:
            return None
        held = self.store.connection.execute(
            "SELECT * FROM held_inbound_events WHERE id=? AND case_id=?",
            (review["held_event_id"], case.id),
        ).fetchone()
        if (held is None or review["case_id"] != case.id or audit["case_id"] != case.id
                or review["action_kind"] not in {"retry", "revision", "revision_update"}):
            raise ProcessingConsentRequired("Mixed processing review has no matching original")
        original = InboundEvent.model_validate_json(held["payload_json"])
        expected = original.model_copy(update={"id": event.id, "requested_fields": [], "known_profile": {}})
        if (event != expected or audit["message_sha256"] != self._message_hash(
                original, bool(original.attachment_paths))):
            raise ProcessingConsentRequired("Reviewed mixed processing message differs from its original")
        parsed = _control_parts(original.body)
        if parsed.action != "granted" or parsed.statement != audit["excerpt"]:
            raise ProcessingConsentRequired("Reviewed mixed processing statement differs from its audit")
        if not self.allowed(case):
            self._defer(event, case)
            if self.needs_scope_notice(case):
                self._notice(case, event, scope)
            return ConsentResult("defer", case.id, grant_business=True)
        return ConsentResult("allow", case.id, business_body=parsed.business_body, grant_business=True)

    def needs_scope_notice(self, case: Case) -> bool:
        """A superseded grant may need a notice; withdrawal must not be nagged.

        Configuring a scope resets status to unknown, so consult the last actual
        applicant decision as well. This predicate grants no processing authority.
        """
        scope, record = self.scope(), self._record(case.id)
        if (scope is None or record is None or record["status"] != "unknown"
                or record["scope_id"] != scope.id or record["notice_outbox_id"]):
            return False
        last = self.store.connection.execute(
            "SELECT action,scope_id FROM processing_consent_events WHERE case_id=? "
            "AND action IN ('granted','withdrawn','declined') ORDER BY epoch DESC,rowid DESC LIMIT 1",
            (case.id,),
        ).fetchone()
        return bool(last is not None and last["action"] == "granted" and last["scope_id"] != scope.id)

    def _defer(self, event: InboundEvent, case: Case) -> None:
        self.store.connection.execute(
            "INSERT OR IGNORE INTO processing_deferred_events(event_id,case_id,channel,thread_id,"
            "received_at,rfc_message_id,references_header) VALUES (?,?,?,?,?,?,?)",
            (event.id, case.id, event.channel, event.external_thread_id, event.received_at.isoformat(),
             event.rfc_message_id, event.references),
        )

    @staticmethod
    def _message_hash(event: InboundEvent, has_attachments: bool) -> str:
        from visa_agent.workflow.conversation import latest_reply_text

        original = {"body": latest_reply_text(event.body), "has_attachments": has_attachments,
                    "id": event.id, "thread": event.external_thread_id, "channel": event.channel,
                    "sender": _contact(event.sender, event.channel), "subject": event.subject,
                    "received_at": _utc(event.received_at).isoformat(),
                    "rfc_message_id": event.rfc_message_id, "references": event.references}
        return hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()

    def _notice_sent_before(self, case: Case, event: InboundEvent, statement: str) -> bool:
        if not re.search(
            r"(?:consent reference|授权参考码)\s*[:：]?\s*" + re.escape(self.reference(case.id)) + r"\b",
            statement, re.I,
        ):
            return False
        record = self._record(case.id)
        if record is None or not record["notice_outbox_id"]:
            return False
        row = self.store.connection.execute("SELECT * FROM outbox WHERE id=?", (record["notice_outbox_id"],)).fetchone()
        if row is None or row["status"] != "SENT" or not row["provider_message_id"] or not row["sent_at"]:
            return False
        if not self.validate_control(dict(row)):
            return False
        try:
            return _utc(event.received_at) > _utc(datetime.fromisoformat(row["sent_at"]))
        except (ValueError, TypeError):
            return False

    def _audit(self, event: InboundEvent, case: Case, scope: ProcessingScope, action: str, excerpt: str,
               *, business_pending: bool = False, has_attachments: bool = False) -> None:
        self.store.connection.execute(
            "INSERT INTO processing_consent_events(event_id,case_id,action,scope_id,epoch,excerpt,received_at,"
            "business_pending,message_sha256,has_attachments) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event.id, case.id, action, scope.id, self.epoch(case.id), excerpt[:320], event.received_at.isoformat(),
             int(business_pending), self._message_hash(event, has_attachments) if business_pending else None,
             int(has_attachments)),
        )
        if not business_pending:
            self.store.connection.execute(
                "UPDATE processing_deferred_events SET completed_at=? WHERE event_id=?",
                (datetime.now(UTC).isoformat(), event.id),
            )

    def _notice(self, case: Case, event: InboundEvent, scope: ProcessingScope) -> None:
        record = self._record(case.id)
        if record is not None and record["notice_outbox_id"]:
            return
        reference = self.reference(case.id)
        payload = (scope.notice + f"\n\n授权参考码 / Consent reference: {reference}\n"
                   f"我同意按这份说明处理本线程信息和材料（授权参考码 {reference}）。\n"
                   "I consent to the processing described in this notice "
                   f"(consent reference {reference}).")
        outbox_id = self._queue(case, event, scope, "processing_notice", payload)
        self.store.connection.execute(
            "UPDATE processing_consent SET notice_outbox_id=? WHERE case_id=?", (outbox_id, case.id),
        )

    @staticmethod
    def _receipt(action: str) -> str:
        if action == "granted":
            return ("已记录这份信息处理说明下的同意；本线程之前尚未处理的邮件会在后续处理。"
                    "这不会替你确认申请摘要，也不会恢复已暂停的准备。\n\n"
                    "Your processing consent has been recorded. Earlier unprocessed messages in this thread "
                    "can now be processed. This does not confirm an application summary or resume paused preparation.")
        return ("已记录你拒绝或撤回信息处理同意。后续资料处理及业务回复已停止；"
                "已有本地记录和已发送记录仍保留，不代表已删除。你可以联系操作人员申请导出或删除。\n\n"
                "Your refusal or withdrawal has been recorded. Further information processing and business replies "
                "are stopped. Existing local records and send records remain; this is not a deletion receipt. "
                "Contact the operator to request export or deletion.")

    def _queue(self, case: Case, event: InboundEvent, scope: ProcessingScope, kind: str, payload: str) -> str:
        epoch = self.epoch(case.id)
        token = f"{case.id}:{scope.id}:{epoch}:{kind}:{event.id if kind == 'processing_receipt' else ''}"
        outbox_id = "privacy-" + hashlib.sha256(token.encode()).hexdigest()[:32]
        reply_to = event.rfc_message_id or f"<{event.id}>"
        deadline = (event.received_at + timedelta(hours=24)).isoformat() if event.channel == "whatsapp_twilio" else None
        self.store.connection.execute(
            "INSERT OR IGNORE INTO outbox(id,case_id,event_id,message_type,payload,channel,recipient,"
            "external_thread_id,send_deadline,reply_subject,in_reply_to,references_header,case_revision,"
            "preparation_control_epoch,processing_consent_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (outbox_id, case.id, event.id, kind, payload, event.channel, case.applicant_contact,
             case.external_thread_id, deadline,
             event.subject if event.subject.lower().startswith("re:") else f"Re: {event.subject}", reply_to,
             " ".join(dict.fromkeys(f"{event.references or ''} {reply_to}".split())), case.delivery_revision,
             case.preparation_control_epoch, epoch),
        )
        self.store.connection.execute(
            "INSERT OR IGNORE INTO processing_control_outbox(outbox_id,case_id,scope_id,epoch,kind,payload,"
            "recipient,channel,thread_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (outbox_id, case.id, scope.id, epoch, kind, payload, case.applicant_contact,
             case.primary_channel, case.external_thread_id),
        )
        return outbox_id

    def validate_control(self, row: Mapping[str, Any]) -> bool:
        scope = self.scope()
        canonical = self.store.connection.execute(
            "SELECT * FROM processing_control_outbox WHERE outbox_id=?", (row.get("id"),),
        ).fetchone()
        if scope is None or canonical is None or row.get("message_type") not in CONTROL_MESSAGE_TYPES:
            return False
        case = self.store.get_case(canonical["case_id"])
        if case is None or canonical["scope_id"] != scope.id or canonical["epoch"] != self.epoch(case.id):
            return False
        if not all(row.get(key) == canonical[value] for key, value in (
            ("case_id", "case_id"), ("message_type", "kind"), ("payload", "payload"),
            ("recipient", "recipient"), ("channel", "channel"), ("external_thread_id", "thread_id"),
            ("processing_consent_epoch", "epoch"),
        )):
            return False
        if (case.applicant_contact != canonical["recipient"] or case.primary_channel != canonical["channel"]
                or case.external_thread_id != canonical["thread_id"]):
            return False
        return bool(row["message_type"] != "processing_notice"
                    or self._record(case.id)["notice_outbox_id"] == row["id"])

    def deferred_ids(self, case_id: str | None = None) -> list[str]:
        clause = " AND case_id=?" if case_id is not None else ""
        parameters = (case_id,) if case_id is not None else ()
        rows = self.store.connection.execute(
            "SELECT event_id FROM processing_deferred_events WHERE completed_at IS NULL" + clause
            + " ORDER BY received_at,event_id", parameters,
        ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def mark_completed(self, event_id: str) -> None:
        with self.store.atomic_write():
            self.store.connection.execute(
                "UPDATE processing_deferred_events SET completed_at=? WHERE event_id=?",
                (datetime.now(UTC).isoformat(), event_id),
            )
