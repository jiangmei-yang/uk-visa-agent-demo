"""Collection follow-up planning, not a visa requirement or release decision.

The caller owns pacing, already-asked memory and contextual reply validation.
An uncertain collection stays deferred: this planner never turns it into an
absence declaration or asks the applicant to guess. Basic field coverage here
does not establish policy applicability, precision, verification or consent.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from visa_agent.domain.application_records import (
    RECORD_KINDS,
    ApplicationRecordLedger,
    CollectionDeclaration,
    DeclarationState,
    RecordFieldDeferral,
    RecordKind,
)
from visa_agent.domain.models import Case
from visa_agent.domain.record_completeness import record_intake_fields
from visa_agent.workflow.conversation import latest_reply_text


@dataclass(frozen=True)
class CollectionFollowUp:
    key: str
    kind: RecordKind
    reason: Literal["unasked", "partial", "missing_detail"]
    record_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class CollectionPlan:
    pending: tuple[CollectionFollowUp, ...]
    deferred: tuple[RecordKind, ...]

    def next_unasked(self, asked_keys: frozenset[str]) -> CollectionFollowUp | None:
        """Never repeat a pending prompt merely because an unrelated reply arrived."""
        return next((item for item in self.pending if item.key not in asked_keys), None)


def plan_collection_follow_up(
    ledger: ApplicationRecordLedger | None, *, case_id: str,
) -> CollectionPlan:
    """Plan missing statements/details without mutating or approving a case.

    Legacy absence is unasked, not a migration default of 'none'. Keys bind to
    the collection/record revision so a material correction can be reconsidered
    without unrelated scalar changes causing the same question to be repeated.
    """
    if ledger is not None and ledger.case_id != case_id:
        raise ValueError("Collection planning requires a case-local ledger")
    ledger = ledger if ledger is not None else ApplicationRecordLedger(case_id=case_id)
    pending: list[CollectionFollowUp] = []
    deferred: list[RecordKind] = []
    deferred_fields = {(item.record_id, item.field) for item in ledger.active_field_deferrals()}
    for kind in RECORD_KINDS:
        state = ledger.collection_state(kind)
        if state == "unknown":
            deferred.append(kind)
            continue
        if state == "none_declared":
            continue
        if state in {"unasked", "partial"}:
            digest = ledger.records_digest(kind)
            pending.append(CollectionFollowUp(
                key=f"{case_id}:{kind}:list:{digest}", kind=kind,
                reason="unasked" if state == "unasked" else "partial",
            ))
        for record in ledger.current().values():
            if record.kind != kind:
                continue
            for field in record_intake_fields(record):
                if field not in record.fields and (record.record_id, field) not in deferred_fields:
                    pending.append(CollectionFollowUp(
                        key=f"{case_id}:{kind}:{record.record_id}:{record.digest()}:{field}",
                        kind=kind, reason="missing_detail", record_id=record.record_id,
                        field=field,
                    ))
    return CollectionPlan(pending=tuple(pending), deferred=tuple(deferred))


def collection_question_text(
    follow_up: CollectionFollowUp, ledger: ApplicationRecordLedger | None, language: str,
) -> str:
    """One bounded question; never manufacture missing values or demand guessing."""
    zh = language == "zh"
    if follow_up.reason in {"unasked", "partial"}:
        if follow_up.kind == "travel":
            if follow_up.reason == "partial":
                return ("除了已记下的旅行，还有其他出境经历需要补充吗？记不清的部分可以先留待核实。" if zh else
                        "Are there any other trips abroad to add to the ones recorded? Anything you cannot remember can wait for checking.")
            return ("你以前有过出境旅行吗？可以先告诉我去过哪里和大概时间；没有或记不清也可以直接说。" if zh else
                    "Have you travelled abroad before? You can start with the countries and approximate dates, or tell me if you have not travelled or cannot remember.")
        return (("除了已记下的人，你在英国还有其他亲属或联系人吗？" if follow_up.reason == "partial" else
                 "你在英国有亲属或联系人吗？没有也可以直接说，不需要为了申请找一个联系人。") if zh else
                ("Are there any other UK relatives or contacts to add?" if follow_up.reason == "partial" else
                 "Do you have any relatives or contacts in the UK? It is fine if you do not; you do not need to find a contact just for the application."))
    if ledger is None or follow_up.record_id not in ledger.current():
        raise ValueError("A detail question needs its current record")
    if follow_up not in plan_collection_follow_up(ledger, case_id=ledger.case_id).pending:
        raise ValueError("A detail question must match the current case and record revision")
    record = ledger.current()[follow_up.record_id]
    label = next((record.fields[key].value for key in ("country", "name", "relationship")
                  if key in record.fields), "")
    if follow_up.field == "passport_number":
        return (
            f"你提到“{label}”是在英国的亲属。申请时可能需要这位亲属的护照号码，你方便核对后补充吗？暂时不清楚就先留待核实，不用猜，也不需要仅为这一项上传整本护照。\n"
            "GOV.UK: https://www.gov.uk/standard-visitor/apply-standard-visitor-visa" if zh else
            f"You mentioned ‘{label}’ is a relative in the UK. The application may ask for that relative's passport number; could you provide it after checking? If you are unsure, we can leave it for checking. Please don't guess or upload a whole passport just for this detail.\n"
            "GOV.UK: https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
        )
    prompts = ({"period": "大概是什么时候去的", "purpose": "当时主要是去做什么",
                "name": "对方怎么称呼", "relationship": "对方与你是什么关系", "address": "对方在英国的地址是什么"}
               if zh else {"period": "when approximately did you travel", "purpose": "what was the purpose of that trip",
                           "name": "what is their name", "relationship": "how are they related to you",
                           "address": "what is their UK address"})
    if follow_up.field not in prompts:
        raise ValueError("Unsupported collection detail question")
    return (f"关于你提到的“{label}”，{prompts[follow_up.field]}？暂时不清楚的话，我们先留待核实。" if zh else
            f"For the entry you mentioned, ‘{label}’, {prompts[follow_up.field]}? If you are unsure, we can leave it for checking.")


def _short_answer(body: str) -> tuple[str, str, bool, bool]:
    source = latest_reply_text(body).strip()
    normalized = re.sub(r"[。.!！?？\s]+$", "", source).casefold()
    unknown = normalized in {"记不清", "不记得", "不确定", "暂时不确定", "需要核实",
                             "i don't remember", "i cannot remember", "i can't remember",
                             "not sure", "i'm not sure", "unsure", "i need to check"}
    negative = normalized in {"没有", "没有了", "没了", "都没有", "no", "none", "no others", "that's all"}
    return source, normalized, unknown, negative


def _sent_collection_question(
    case: Case, outbox: list[dict[str, Any]],
) -> tuple[CollectionFollowUp, dict[str, Any]] | None:
    sent = [row for row in outbox if row["case_id"] == case.id and row["status"] == "SENT"]
    if not sent:
        return None
    if any(not row.get("sent_at") for row in sent):
        return None  # no reliable send ordering; do not guess from draft creation time
    latest = max(sent, key=lambda row: (row["sent_at"], row["id"]))
    if len({row["event_id"] for row in sent if row["sent_at"] == latest["sent_at"]}) != 1:
        return None
    if (latest.get("recipient") != case.applicant_contact
            or latest.get("external_thread_id") != case.external_thread_id):
        return None
    if any(latest["event_id"] in ids for ids in case.question_event_ids.values()):
        return None
    ledger = case.application_records or ApplicationRecordLedger(case_id=case.id)
    current = plan_collection_follow_up(ledger, case_id=case.id)
    matches = [item for item in current.pending
               if latest["event_id"] in case.collection_question_event_ids.get(item.key, [])
               # Shared source-line dedup may remove an already supplied link,
               # but must not destroy the actual sent question's context.
               and any(collection_question_text(item, ledger, language).partition("\nGOV.UK:")[0]
                       in latest["payload"] for language in ("en", "zh"))]
    if len(matches) != 1:
        return None
    return matches[0], latest


def contextual_collection_declaration(
    case: Case, body: str, outbox: list[dict[str, Any]],
) -> CollectionDeclaration | None:
    """Resolve an exact short answer to one current SENT list question, never consent."""
    source, normalized, unknown, negative = _short_answer(body)
    match = _sent_collection_question(case, outbox) if unknown or negative else None
    if match is None or match[0].reason == "missing_detail":
        return None
    question, latest = match
    ledger = case.application_records or ApplicationRecordLedger(case_id=case.id)
    if negative and normalized == "that's all" and question.reason == "unasked":
        return None
    state: DeclarationState = "unknown" if unknown else "none_declared" if question.reason == "unasked" else "complete_declared"
    if state == "complete_declared" and not any(r.kind == question.kind for r in ledger.current().values()):
        return None
    return CollectionDeclaration(
        kind=question.kind, state=state, source_excerpt=source,
        expected_records_digest=ledger.records_digest(question.kind),
        question_event_id=latest["event_id"], question_key=question.key,
    )


def contextual_record_field_deferral(
    case: Case, body: str, event_id: str, outbox: list[dict[str, Any]],
) -> RecordFieldDeferral | None:
    """Defer one missing detail on explicit uncertainty, not a generic 'no'."""
    source, _, unknown, _ = _short_answer(body)
    match = _sent_collection_question(case, outbox) if unknown else None
    if match is None or match[0].reason != "missing_detail" or case.application_records is None:
        return None
    question, latest = match
    if question.record_id is None or question.field is None:
        return None
    target = case.application_records.current()[question.record_id]
    return RecordFieldDeferral(
        case_id=case.id, record_id=target.record_id, record_digest=target.digest(), field=question.field,
        source_excerpt=source, source_event_id=event_id,
        source_body_sha256=hashlib.sha256(latest_reply_text(body).encode()).hexdigest(),
        question_event_id=latest["event_id"], question_key=question.key,
    )
