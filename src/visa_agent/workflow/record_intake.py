"""Conservative source/owner guard and exact-target planner for record proposals.

This is a bounded grammar, not general coreference or factual verification. An
unresolved current record statement is retained for human review, never guessed.
No function here grants processing consent, completeness approval or release.
"""

import hashlib
import re
from dataclasses import dataclass

from pydantic import TypeAdapter

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    CollectionDeclaration,
    RecordCommand,
    RecordFieldDeferral,
    RecordInput,
    apply_record_commands,
)
from visa_agent.domain.models import InboundEvent
from visa_agent.llm.application_records import (
    ApplicationRecordProposal,
    CollectionDeclarationProposal,
)
from visa_agent.workflow.conversation import latest_reply_text

_NONCURRENT = re.compile(
    r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|'
    r"\b(?:if|unless|suppos\w*|imagine|hypothetical\w*|fictional|pretend|"
    r"said|says|wrote|told|asked|claims?|quoted?)\b|"
    r"\b(?:an?\s+example|for\s+example|example\s*:)\b|"
    r"如果|假如|假设|假設|假定|举例|舉例|例句|假装|假裝|虚构|虛構|引用|转述|轉述|[说說]过|[说說][：:]",
    re.I,
)
_FUTURE_OR_NEGATED = re.compile(
    r"\b(?:not|never|didn't|haven't|hadn't|hasn't|will|would|might|plan\w*|intend\w*|"
    r"hope\w*|want\w*|maybe|perhaps|unsure|uncertain)\b|"
    r"没去|沒去|没到|沒到|没有去|沒有去|不曾|未曾|从未|從未|打算|准备去|準備去|将去|將去|"
    r"会去|會去|想去|可能|或许|或許|不确定|不確定",
    re.I,
)
_PAST_SELF = re.compile(
    r"\bI\s+(?:(?:have|had)\s+)?(?:previously\s+|also\s+|once\s+)?"
    r"(?:visited|travelled|traveled|went|have been|had been|stayed)\b|"
    r"\bI\s+(?:have|had)\s+been\s+to\b|"
    r"我(?:本人)?(?:曾经|曾經|曾|以前|之前|还|還|也|在)?[^。！？;；\n]{0,35}(?:去过|去過|去了|到过|到過|游览过|遊覽過)|"
    r"我(?:在)?[12]\d{3}[^。！？;；\n]{0,20}(?:去|到)",
    re.I,
)
_RELATION = r"sister|brother|mother|father|parents?|aunt|uncle|cousin|wife|husband|partner|friend|relative|contact"
_ZH_RELATION = r"姐姐|妹妹|哥哥|弟弟|父母|母亲|母親|父亲|父親|妈妈|媽媽|爸爸|朋友|亲属|親屬|亲戚|親戚|联系人|聯絡人|表姐|表妹|表哥|表弟|妻子|丈夫|伴侣|伴侶"
_CONTACT_SELF = re.compile(
    rf"\bmy\s+(?:{_RELATION})\b|(?:我(?:的)?)(?:{_ZH_RELATION})",
    re.I,
)
_UK = re.compile(r"\b(?:UK|United Kingdom|Britain|England|Scotland|Wales|Northern Ireland|London)\b|英国|英國|伦敦|倫敦", re.I)
_RESIDENCE = re.compile(r"\b(?:lives?|resides?|based|address|contact)\b|住|居住|地址|联系人|聯絡人", re.I)
_CORRECTION = re.compile(r"^(?:(?:please|a)\s+)?(?:correct|correction|amend|update|change|remove|withdraw|delete)\b|^(?:请|請|我想|我要|我来|我來)?(?:更正|改正|修改|删掉|刪掉|删除|刪除|撤回)", re.I)
_SUPPLEMENT = re.compile(r"^(?:补充|補充)(?:一下)?[，,:：\s]|^(?:to add|one more detail|additional detail)[,:]\s*", re.I)
_PRIOR_ERROR_CORRECTION = re.compile(
    r"^(?:我)?(?:刚才|剛才|之前)[^。！？?\n]{1,100}(?:写错|寫錯)(?:了)?[，,]\s*(?:请|請)?(?:更正|改正)(?:为|為|成)",
)
_WITHDRAW = re.compile(r"\b(?:remove|withdraw|delete)\b|删掉|刪掉|删除|刪除|撤回", re.I)
_COLLECTION_TOPIC = {
    "travel": re.compile(r"\b(?:travel history|past trips?|previous trips?|trips? abroad|travelled abroad|traveled abroad|visited abroad)\b|旅行记录|旅行記錄|旅行历史|旅行歷史|出境记录|出境記錄|出国|出國|出境|过去的旅行|過去的旅行", re.I),
    "uk_contact": re.compile(rf"\bUK\s+(?:relatives?|contacts?|family|friends?)\b|英国[^。！？\n]{{0,20}}(?:{_ZH_RELATION})|英國[^。！？\n]{{0,20}}(?:{_ZH_RELATION})", re.I),
}
_STATE = {
    "unknown": re.compile(r"\b(?:unsure|uncertain|cannot remember|can't remember|don't remember|do not remember|need to check)\b|不确定|不確定|记不清|記不清|不记得|不記得|需要核实|需要核實", re.I),
    "partial": re.compile(r"\b(?:more|remaining|not (?:yet )?(?:complete|the full list))\b|还有|還有|其余|其餘|剩下|没列全|沒列全|不完整", re.I),
    "none_declared": re.compile(r"\b(?:no|none|never|don't have|do not have)\b|没有|沒有|从未|從未|未曾", re.I),
    "complete_declared": re.compile(r"\b(?:full|complete|exhaustive|all of|no other|nothing else)\b|全部|列全|没有其他|沒有其他|没有别的|沒有別的", re.I),
}
_NONE_DECLARATION = {
    "travel": re.compile(r"\bI\s+(?:have\s+never\s+(?:travelled|traveled)\s+abroad|have\s+no\s+(?:past\s+)?travel\s+history)\b|我(?:从未|從未|未曾|从来没有|從來沒有)(?:出国|出國|出境)|我(?:没有|沒有)(?:出境|出国|出國|旅行)(?:记录|記錄|历史|歷史)", re.I),
    "uk_contact": re.compile(r"\bI\s+(?:have\s+no|do\s+not\s+have\s+any)\s+UK\s+contacts?\b|\bI\s+have\s+no\s+(?:relatives?\s+or\s+)?contacts?\s+in\s+the\s+UK\b|我在(?:英国|英國)(?:没有|沒有)(?:(?:亲属|親屬)(?:[或和]|[，,]\s*也(?:没有|沒有)))?(?:联系人|聯絡人)|我(?:没有|沒有)(?:英国|英國)(?:联系人|聯絡人)", re.I),
}
_COMPLETE_DECLARATION = {
    "travel": re.compile(r"\b(?:this|that)\s+is\s+my\s+(?:full|complete)\s+travel\s+history\b|\bmy\s+travel\s+history\s+is\s+(?:complete|exhaustive)\b|(?:这|這|以上)(?:就)?是我的?(?:全部|完整)(?:旅行|出境)(?:记录|記錄|历史|歷史)|我的?(?:旅行|出境)(?:记录|記錄|历史|歷史)(?:已经|已經|已)?列全", re.I),
    "uk_contact": re.compile(r"\b(?:this|that)\s+is\s+my\s+(?:full|complete)\s+list\s+of\s+UK\s+contacts\b|\bmy\s+UK\s+contacts\s+list\s+is\s+complete\b|(?:这|這|以上)(?:就)?是我的?全部(?:英国|英國)(?:联系人|聯絡人)|我的?(?:英国|英國)(?:联系人|聯絡人)(?:已经|已經|已)?列全", re.I),
}
_RECORD_INPUT: TypeAdapter[RecordInput] = TypeAdapter(RecordInput)
_NO_OTHER_CONTACTS = re.compile(
    r"\bI\s+have\s+no\s+other\s+UK\s+(?:relatives?\s+or\s+)?contacts?\b|"
    r"我在(?:英国|英國)(?:只有[^。！？?\n]{1,60}[，,])?(?:没有|沒有)其他(?:亲属或|親屬或)?(?:联系人|聯絡人)", re.I,
)


@dataclass(frozen=True)
class RecordIntakePlan:
    ledger: ApplicationRecordLedger | None
    changed: bool = False
    requires_review: bool = False
    reason: str | None = None


def record_intake_receipt(plan: RecordIntakePlan, event_id: str, language: str) -> str:
    """Receipt follows persisted changes, never unaccepted model proposals."""
    if not plan.changed or plan.ledger is None:
        return ""
    zh = language == "zh"
    parts: list[str] = []
    labels = ({"period": "旅行时间", "purpose": "旅行目的", "name": "联系人姓名",
               "relationship": "联系人与你的关系", "address": "联系人地址", "passport_number": "亲属护照号码"} if zh else
              {"period": "travel period", "purpose": "trip purpose", "name": "contact's name",
               "relationship": "contact's relationship to you", "address": "contact's address", "passport_number": "relative's passport number"})
    for deferred in plan.ledger.active_field_deferrals():
        if deferred.source_event_id == event_id:
            label = labels.get(deferred.field, deferred.field)
            parts.append(f"这条记录的{label}先留待核实，不用猜；其他已提供的信息会保留。" if zh else
                         f"We can leave this entry's {label} for checking; please don't guess. The other details you supplied are retained.")
    for kind in ("travel", "uk_contact"):
        declaration = plan.ledger.latest_declarations().get(kind)
        revisions = [record for record in plan.ledger.revisions if record.kind == kind
                     and record.changed_by_event_id == event_id]
        if declaration is not None and declaration.source_event_id == event_id:
            messages = ({
                "travel": {"unknown": "旅行经历记不清的部分先留待核实，不用猜。", "partial": "已记下目前提供的旅行经历，其余记录可以之后补。",
                           "none_declared": "已记下你没有出境经历。", "complete_declared": "已记下这是你列全的旅行经历，其中缺的细节还需要核对。"},
                "uk_contact": {"unknown": "英国联系人暂时不确定的信息先留待核实。", "partial": "已记下目前提供的英国联系人，其余信息可以之后补。",
                               "none_declared": "已记下你在英国没有联系人。", "complete_declared": "已记下英国联系人已列全，具体信息还需要核对。"},
            } if zh else {
                "travel": {"unknown": "The parts of your travel history you cannot remember can wait for checking; please don't guess.",
                           "partial": "I've recorded the travel history supplied so far; you can add the remaining entries later.",
                           "none_declared": "I've noted that you have no overseas travel history.",
                           "complete_declared": "I've noted that this is your full travel history; any missing details still need checking."},
                "uk_contact": {"unknown": "The UK-contact details you are unsure about can wait for checking.",
                               "partial": "I've recorded the UK contacts supplied so far; you can add the remaining information later.",
                               "none_declared": "I've noted that you have no UK contacts.",
                               "complete_declared": "I've noted that your UK-contact list is complete; the details still need checking."},
            })
            parts.append(messages[kind][declaration.state])
        elif revisions:
            label = ("旅行记录" if kind == "travel" else "英国联系人信息") if zh else (
                "travel-history entries" if kind == "travel" else "UK-contact details")
            if all(not record.active for record in revisions):
                parts.append(f"已从当前摘要中撤下你指出的{label}。" if zh else f"I've removed the {label} you identified from the current summary.")
            elif all(record.active and (record.revision == 1 or any(
                    prior.record_id == record.record_id and prior.digest() == record.predecessor_digest
                    and all(record.fields.get(key) == value for key, value in prior.fields.items())
                    for prior in plan.ledger.revisions)) for record in revisions) and any(
                        record.revision > 1 for record in revisions):
                parts.append(f"收到，补充的{label}已记下，之前的信息也保留着。" if zh else
                             f"Thanks — I've added the extra {label} and kept the details you already provided.")
            elif any(record.revision > 1 for record in revisions):
                parts.append(f"已按你的更正更新{label}。" if zh else f"I've updated the {label} with your correction.")
            else:
                parts.append(f"你提供的{label}已记下。" if zh else f"I've recorded the {label} you provided.")
    return "\n\n".join(parts)


def _source_context(body: str, excerpt: str) -> str | None:
    """Never let a clipped excerpt drop a governing if/not/other-person subject."""
    if not excerpt.strip() or excerpt not in body or body.count(excerpt) != 1:
        return None
    start = body.index(excerpt)
    left = max(body.rfind(mark, 0, start) for mark in ("\n", ". ", "。", "!", "?", "！", "？", ";", "；"))
    end = start + len(excerpt)
    stops = [index for mark in ("\n", ". ", "。", "!", "?", "！", "？", ";", "；")
             if (index := body.find(mark, max(start, end - 1))) >= 0]
    return body[left + 1:min(stops) if stops else len(body)].strip()


def _travel_field_roles(proposal: ApplicationRecordProposal) -> bool:
    """Literal text must also fit the proposed field, not another trip's slot."""
    fields = proposal.record.fields.supplied()
    source = proposal.source_excerpt
    if len(list(_PAST_SELF.finditer(source))) > 1:
        return False  # ask for separate entries, never join two statement owners
    country = fields.get("country")
    if country is not None:
        if re.search(r"\d|[。！？;；\n]", country.value):
            return False
        if proposal.action == "add" and not re.search(
            r"(?:\b(?:visited|visit|went|travelled|traveled|stayed|been)\s+(?:(?:to|in|around|through)\s+)?|"
            r"(?:去过|去過|去了|到过|到過|去|到|游览过|遊覽過)\s*)" + re.escape(country.value), source, re.I,
        ):
            return False
    period = fields.get("period")
    if period is not None and not re.search(
        r"[12]\d{3}|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b|"
        r"\b(?:last|previous)\s+(?:year|summer|winter|spring|autumn)|\byears?\s+ago\b|去年|前年|年前",
        period.value, re.I,
    ):
        return False
    purpose = fields.get("purpose")
    if purpose is not None:
        explicit_purpose = re.search(
            r"(?:\b(?:for|on)\s+|\bpurpose\s*(?:is|was|to|:)?\s*|目的(?:是|为|為|改为|改為|[:：])?\s*)"
            + re.escape(purpose.value), source, re.I,
        )
        chinese_trip_purpose = bool(country is not None and re.search(r"[㐀-鿿]", purpose.value)
                                    and country.value + purpose.value in source)
        if not explicit_purpose and not chinese_trip_purpose:
            return False
    return True


def plan_record_intake(
    event: InboundEvent, ledger: ApplicationRecordLedger | None, *, case_id: str,
    records: list[ApplicationRecordProposal], declarations: list[CollectionDeclarationProposal],
    contextual_declaration: CollectionDeclaration | None = None,
    contextual_field_deferral: RecordFieldDeferral | None = None,
) -> RecordIntakePlan:
    """Called only after workflow identity/consent checks, on the latest body."""
    if not records and not declarations and contextual_declaration is None and contextual_field_deferral is None:
        return RecordIntakePlan(ledger)
    original = ledger or ApplicationRecordLedger(case_id=case_id)
    body = latest_reply_text(event.body)
    commands: list[RecordCommand] = []
    accepted_declarations: list[CollectionDeclarationProposal] = []
    try:
        for proposal in records:
            context = _source_context(body, proposal.source_excerpt)
            if context is None or _NONCURRENT.search(context):
                continue
            kind = proposal.record.kind
            if kind == "travel" and re.search(rf"我(?:的)?(?:{_ZH_RELATION})", context):
                continue
            if kind == "travel" and re.search(rf"\bmy\s+(?:{_RELATION})['’]s\b", context, re.I):
                continue
            self_owned = bool(_PAST_SELF.search(context) if kind == "travel" else
                              _CONTACT_SELF.search(context) and _UK.search(context) and _RESIDENCE.search(context))
            supplementing = proposal.action == "amend" and bool(_SUPPLEMENT.search(context))
            natural_correction = proposal.action == "amend" and bool(_PRIOR_ERROR_CORRECTION.search(context))
            if (supplementing or natural_correction) and (
                    re.search(re.escape(context) + r"\s*[?？]", body)
                    or re.search(r"(?:不要|别|別|不是|并非|並非)|\b(?:not|don't|do not|might|would|will)\b", context, re.I)):
                continue
            correcting = proposal.action != "add" and bool(
                _CORRECTION.search(context) or supplementing or natural_correction)
            if proposal.action != "add" and not correcting:
                continue
            if not self_owned and not correcting:
                continue
            if proposal.confidence < .8:
                raise ValueError("Application record statement needs confidence/ownership review")
            if proposal.action == "add" and _FUTURE_OR_NEGATED.search(context):
                continue
            target = None
            fields = proposal.record.fields.supplied()
            if proposal.action != "add":
                reference = proposal.target_reference
                if reference is None or not reference.source_excerpt.strip() or reference.source_excerpt not in proposal.source_excerpt:
                    raise ValueError("Application record correction has no grounded target")
                if (proposal.action == "withdraw") != bool(_WITHDRAW.search(context)):
                    raise ValueError("Application record correction action does not match its source")
                if re.search(r"\b(?:do not|don't)\s+(?:remove|delete|correct|change)|(?:不要|别|別)(?:删|刪|改|撤)", context, re.I):
                    raise ValueError("Application record correction contains a conflicting instruction")
                identifying = {"country", "period"} if kind == "travel" else {"name", "relationship", "address"}
                # The model's `value` is only an untrusted hint and has zero
                # authority. Resolve literal words in the current quote against
                # case-local records; equal best matches remain ambiguous.
                scored = []
                for candidate in original.current().values():
                    if candidate.kind != kind:
                        continue
                    matches = [fact for name, fact in candidate.fields.items() if name in identifying
                               and re.search(r"(?<![A-Za-z])" + re.escape(fact.value) + r"(?![A-Za-z])",
                                             reference.source_excerpt)]
                    if any(re.search(r"(?:\bnot\s+|不是|并非|並非)" + re.escape(fact.value), context, re.I) for fact in matches):
                        raise ValueError("Negated record reference cannot select a correction target")
                    if matches:
                        scored.append((len(matches), candidate))
                best = max((score for score, _ in scored), default=0)
                targets = [candidate for score, candidate in scored if score == best]
                if len(targets) != 1:
                    raise ValueError("Application record correction needs a unique existing target")
                target = targets[0]
                if supplementing and any(name in target.fields and target.fields[name].value != value.value
                                         for name, value in fields.items()):
                    raise ValueError("Supplement conflicts with an existing value; request an explicit correction")
                if any(re.search(r"(?:\bnot\s+|不是|并非|並非|不要)" + re.escape(value.value),
                                 context, re.I) for value in fields.values()):
                    raise ValueError("Negated application record value cannot be accepted")
                # Drop only exact unchanged echoes from an already-grounded
                # target. They cannot replace their original source. Any new or
                # changed field still needs literal current-message evidence.
                unsupported_echoes = [name for name, value in fields.items()
                                      if name in target.fields and target.fields[name].value == value.value
                                      and value.value not in value.source_excerpt]
                cues = {"country": r"\bcountry\b|国家|國家", "period": r"\b(?:date|time|period)\b|时间|時間|日期",
                        "purpose": r"\bpurpose\b|目的", "name": r"\bname\b|姓名|名字",
                        "relationship": r"\brelationship\b|关系|關係", "address": r"\baddress\b|地址",
                        "phone": r"\b(?:phone|telephone)\b|电话|電話", "passport_number": r"\bpassport\b|护照|護照",
                        "support_details": r"\b(?:support|host|pay)\b|资助|資助|接待"}
                if any(re.search(cues[name], context, re.I) for name in unsupported_echoes):
                    raise ValueError("An explicitly targeted field has only an unsupported old-value echo")
                fields = {name: value for name, value in fields.items()
                          if name not in target.fields or target.fields[name].value != value.value}
                if proposal.action == "amend" and not fields:
                    if unsupported_echoes:
                        raise ValueError("Correction has no source-grounded changed field")
                    continue
            if any(value.source_excerpt not in proposal.source_excerpt for value in fields.values()):
                raise ValueError("Application record fields span different source statements")
            trusted = _RECORD_INPUT.validate_python({"kind": kind, "fields": {
                name: value.model_dump() for name, value in fields.items()}})
            checked_proposal = proposal.model_copy(deep=True)
            checked_proposal.record.fields = type(proposal.record.fields).model_validate({
                name: value.model_dump() for name, value in fields.items()})
            if kind == "travel" and not _travel_field_roles(checked_proposal):
                raise ValueError("Application travel fields need role or separate-entry review")
            if any(re.search(r"(?:\bnot\s+|不是|并非|並非|不要)" + re.escape(value.value),
                             context, re.I) for value in fields.values()):
                raise ValueError("Negated application record value cannot be accepted")
            commands.append(RecordCommand(action=proposal.action, record=trusted,
                target_id=target.record_id if target else None,
                expected_revision_digest=target.digest() if target else None,
                change_excerpt=proposal.source_excerpt if target else None))
        for assertion in declarations:
            context = _source_context(body, assertion.source_excerpt)
            if context is None or _NONCURRENT.search(context):
                continue
            if not _COLLECTION_TOPIC[assertion.kind].search(context):
                continue
            # Do not import a friend's absence/uncertainty as this applicant's.
            if not re.search(r"\bI\b|\bmy\s+(?:(?:full|complete)\s+)?(?:travel history|UK contacts)\b|我", context, re.I):
                continue
            if re.search(rf"\bmy\s+(?:{_RELATION})['’]s\b|我(?:的)?(?:{_ZH_RELATION})", context, re.I):
                continue
            if re.search(r"\b(?:want|intend|plan|hope|might|would|will)\b|打算|想说|想說|准备说|準備說", context, re.I):
                continue
            explicit_uncertainty = bool(_STATE["unknown"].search(context))
            no_other_contacts = (assertion.kind == "uk_contact" and bool(_NO_OTHER_CONTACTS.search(context))
                                 and not re.search(re.escape(context) + r"\s*[?？]", body))
            if no_other_contacts and assertion.state in {"none_declared", "complete_declared"}:
                # 'No others' asserts list scope, not an empty list. Bind the
                # whole current sentence, retaining its subject and UK scope,
                # rather than a model's clipped 'no other contacts' fragment.
                # Applying complete_declared still requires a nonempty ledger.
                assertion = assertion.model_copy(update={"state": "complete_declared", "source_excerpt": context})
            if explicit_uncertainty and re.search(r"\b(?:not|no longer)\s+(?:unsure|uncertain)|不是不确定|不是不確定", context, re.I):
                raise ValueError("Negated uncertainty cannot become a deferred collection")
            if explicit_uncertainty and assertion.state == "partial":
                # A partial list and an inability to remember can coexist. The
                # source's explicit uncertainty controls deferral; this neither
                # invents a record nor asserts absence/exhaustiveness. Keep the
                # raw model proposal unchanged for diagnostics.
                assertion = assertion.model_copy(update={"state": "unknown"})
            if assertion.confidence < .8 or not _STATE[assertion.state].search(context):
                raise ValueError("Application collection assertion does not match its source")
            if assertion.state in {"none_declared", "complete_declared"} and explicit_uncertainty:
                raise ValueError("Uncertain application collection cannot be marked absent or complete")
            if assertion.state == "none_declared" and not _NONE_DECLARATION[assertion.kind].search(context):
                raise ValueError("Application collection absence is not explicitly stated")
            if assertion.state == "complete_declared" and not (
                    _COMPLETE_DECLARATION[assertion.kind].search(context) or no_other_contacts):
                raise ValueError("Application collection completeness is not explicitly stated")
            if assertion.state == "complete_declared" and re.search(r"\bnot\b|没列全|沒列全|不完整", context, re.I):
                raise ValueError("Negated completeness cannot become a full-list assertion")
            accepted_declarations.append(assertion)
        if contextual_declaration is not None and (commands or accepted_declarations
                    or contextual_declaration.source_excerpt != body.strip()
                    or not contextual_declaration.question_event_id
                    or contextual_declaration.expected_records_digest != original.records_digest(contextual_declaration.kind)):
            raise ValueError("Contextual collection answer must match its unchanged question snapshot")
        if contextual_field_deferral is not None and (
            commands or accepted_declarations or contextual_declaration is not None
            or contextual_field_deferral.case_id != case_id
            or contextual_field_deferral.source_event_id != event.id
            or contextual_field_deferral.source_excerpt != body.strip()
            or contextual_field_deferral.source_body_sha256 != hashlib.sha256(body.encode()).hexdigest()
        ):
            raise ValueError("Contextual field deferral must match this current answer")
        if not commands and not accepted_declarations and contextual_declaration is None and contextual_field_deferral is None:
            return RecordIntakePlan(ledger)
        # Resolve declaration digests against the exact post-command snapshot;
        # neither digest nor server-assigned record ID comes from the model.
        preview = apply_record_commands(original, case_id=case_id, event_id=event.id, body=body, commands=commands)
        assertions = [CollectionDeclaration(kind=item.kind, state=item.state, source_excerpt=item.source_excerpt,
                       expected_records_digest=preview.records_digest(item.kind)) for item in accepted_declarations]
        if contextual_declaration is not None:
            assertions.append(contextual_declaration)
        updated = apply_record_commands(original, case_id=case_id, event_id=event.id, body=body,
                                         commands=commands, declarations=assertions)
        if contextual_field_deferral is not None:
            target = updated.current().get(contextual_field_deferral.record_id)
            if target is None or target.digest() != contextual_field_deferral.record_digest:
                raise ValueError("Contextual field deferral has a stale record target")
            same_event = [item for item in updated.field_deferrals if item.source_event_id == event.id]
            if same_event and same_event != [contextual_field_deferral]:
                raise ValueError("Contextual field deferral event cannot be reinterpreted")
            if not same_event:
                updated.field_deferrals.append(contextual_field_deferral)
            updated = ApplicationRecordLedger.model_validate(updated.model_dump(mode="json"))
        return RecordIntakePlan(updated, changed=updated.fingerprint() != original.fingerprint())
    except ValueError as error:
        return RecordIntakePlan(ledger, requires_review=True, reason=str(error))
