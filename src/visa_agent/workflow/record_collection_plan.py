"""Collection follow-up planning, not a visa requirement or release decision.

The caller owns pacing, already-asked memory and contextual reply validation.
An uncertain collection stays deferred: this planner never turns it into an
absence declaration or asks the applicant to guess. Basic field coverage here
does not establish policy applicability, precision, verification or consent.
"""

from dataclasses import dataclass
from typing import Literal

from visa_agent.domain.application_records import (
    RECORD_KINDS,
    ApplicationRecordLedger,
    RecordKind,
)


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
            # Baseline descriptive coverage only; conditional passport/support
            # requirements must be assessed separately, not blanket-collected.
            fields = ("country", "period", "purpose") if kind == "travel" else (
                "name", "relationship", "address",
            )
            for field in fields:
                if field not in record.fields:
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
    prompts = ({"period": "大概是什么时候去的", "purpose": "当时主要是去做什么",
                "name": "对方怎么称呼", "relationship": "对方与你是什么关系", "address": "对方在英国的地址是什么"}
               if zh else {"period": "when approximately did you travel", "purpose": "what was the purpose of that trip",
                           "name": "what is their name", "relationship": "how are they related to you",
                           "address": "what is their UK address"})
    if follow_up.field not in prompts:
        raise ValueError("Unsupported collection detail question")
    return (f"关于你提到的“{label}”，{prompts[follow_up.field]}？暂时不清楚的话，我们先留待核实。" if zh else
            f"For the entry you mentioned, ‘{label}’, {prompts[follow_up.field]}? If you are unsure, we can leave it for checking.")
