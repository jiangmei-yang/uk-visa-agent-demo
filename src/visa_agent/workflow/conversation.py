"""Customer-facing conversation rules; no provider or mailbox dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from visa_agent.domain.models import Case, DocumentStatus, Requirement
from visa_agent.domain.rules import profile_fact_complete, required_profile_facts
from visa_agent.workflow.advice_preferences import (
    prefers_brief_reply,
    reply_style_request,
    wants_no_links,
)
from visa_agent.workflow.consultant_overview import (
    APPLICATION_URL,
    DOCUMENTS_URL,
    ROUTE_CHECK_URL,
    comprehensive_overview_requested,
    first_practical_action,
)
from visa_agent.workflow.document_preparation import reviewed_document_preparation
from visa_agent.workflow.document_purpose import is_document_purpose_question
from visa_agent.workflow.explicit_answer_scope import explicitly_answers_current_enum
from visa_agent.workflow.funding_wording import funding_label, funding_wording


def _outlook_history_start(lines: list[str], index: int) -> bool:
    """Recognise a complete quoted header, never a lone 'From:' in applicant prose."""
    labels = []
    for line in lines[index:index + 10]:
        if not line.strip():
            continue
        match = re.match(
            r"^(From|Date|Sent|To|Cc|Subject|发件人|寄件者|日期|发送时间|寄件日期|收件人|收件者|抄送|副本|主题|主旨)\s*[:：]\s*\S",
            line.strip(), re.I,
        )
        if not match:
            return False
        labels.append(match[1].lower())
        if labels[-1] in {"subject", "主题", "主旨"}:
            return (
                len(labels) in {4, 5}
                and labels[0] in {"from", "发件人", "寄件者"}
                and labels[1] in {"date", "sent", "日期", "发送时间", "寄件日期"}
                and labels[2] in {"to", "收件人", "收件者"}
                and (len(labels) == 4 or labels[3] in {"cc", "抄送", "副本"})
            )
    return False


def latest_reply_text(body: str) -> str:
    """Exclude quoted history from extraction and confirmation, preserving the raw event elsewhere."""
    lines = []
    source_lines = body.splitlines()
    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if _outlook_history_start(source_lines, index):
            break
        if re.match(
            r"^(On .+wrote:|在.+写道[：:]|[- ]*Original Message[- ]*|[- ]*Forwarded message[- ]*)$",
            stripped,
            re.I,
        ):
            break
        if not stripped.startswith(">"):
            lines.append(line)
    return "\n".join(lines).strip()


def confirmation_has_caveat(body: str) -> bool:
    return bool(
        re.search(
            r"[?？]|\b(not|don't|haven't|except|but|if|change|corrected)\b|不|没看|还没|但是|不过|如果|修改|更正|暂时",
            latest_reply_text(body),
            re.I,
        )
    )


def clear_natural_confirmation(body: str) -> bool:
    """Recognise clear assent only; receipt, questions, negation and conditions are not consent."""
    text = latest_reply_text(body)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if confirmation_has_caveat(text):
        return False
    return any(
        len(line) <= 180
        and bool(
            re.fullmatch(
                r"(?:(?:我)?(?:已)?(?:确认)?(?:以上|上述|这些|所有|全部|最终)?(?:的)?(?:资料|信息|摘要|材料清单|内容)?(?:都)?(?:正确|无误|没问题|核对无误)[，,。.!！\s]*(?:(?:请|可以|麻烦)(?:帮我)?(?:继续|整理|准备|发给我|生成材料包)[，,。.!！\s]*)?"
                r"|(?:I (?:have )?(?:reviewed and )?confirm (?:that )?(?:the )?(?:details|summary|information)(?: (?:is|are) (?:correct|accurate))?)"
                r"|(?:(?:Yes[,，]?\s+)?(?:Everything|All (?:the )?(?:details|information)) (?:is|are|looks) (?:correct|accurate|good)(?:[,.;] (?:please )?(?:proceed|prepare the pack|go ahead))?))[.!\s]*",
                line,
                re.I,
            )
        )
        for line in lines
    )


def summary_fingerprint(case: Case, *, include_documents: bool) -> str:
    payload: dict[str, object] = {"profile": case.profile.model_dump(mode="json")}
    if case.application_records is not None and (case.application_records.revisions or case.application_records.declarations):
        payload["application_records"] = case.application_records.fingerprint()
    if include_documents:
        payload["documents"] = sorted(
            (item.id, item.sha256, item.status.value)
            for item in case.documents
            if item.status != DocumentStatus.SUPERSEDED
        )
        payload["evidence"] = sorted(
            (item.fact_key, str(item.value), item.source_document_id or "")
            for item in case.evidence
            if not item.superseded
        )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


FACT_LABELS_ZH = {
    "full_name": "护照上的姓名",
    "date_of_birth": "出生日期",
    "nationality": "国籍",
    "nationality_country": "护照签发国家（国籍）",
    "application_country": "申请所在地",
    "planned_arrival_date": "计划抵英日期",
    "planned_departure_date": "计划离英日期",
    "visit_purpose": "访问目的",
    "uk_accommodation": "在英国的住宿安排",
    "estimated_trip_cost_gbp": "旅行预算（英镑）",
    "current_address": "现居住地址",
    "current_address_duration": "在现住址居住多久",
    "occupation_status": "工作或学习情况",
    "annual_income_gbp": "年收入（英镑）",
    "funding_source": "费用由谁承担",
    "sponsor_name": "资助人姓名",
    "sponsor_relationship": "与资助人的关系",
    "sponsor_is_in_uk": "资助人是否住在英国",
    "has_serious_history": "是否有拒签、违法或移民记录",
    "route_confirmed_standard_visitor": "是否按 Standard Visitor 路线准备",
}
VALUE_LABELS_ZH = {
    "tourism": "旅游",
    "conference": "参加会议",
    "business": "商务访问",
    "family_or_friends": "探亲访友",
    "student": "在读学生",
    "employed": "受雇工作",
    "self_employed": "自雇",
    "self": "本人",
    "employer_or_school": "雇主或学校",
    "personal_sponsor": "个人资助人",
}


def fact_label(case: Case, field: str) -> str:
    if field == "current_address_duration" and case.customer_language != "zh":
        return "Time living at your current home"
    return (
        FACT_LABELS_ZH.get(field, field)
        if case.customer_language == "zh"
        else field.replace("_", " ").title()
    )


def next_fact_questions(case: Case) -> list[str]:
    if case.preparation_paused:
        return []
    if (general_document_list_requested(case)
            and "next_step" not in case.customer_question_topics
            and not customer_requests_next_step(case.latest_customer_message)):
        # An overview is useful before personal intake. Apply this at the plan
        # boundary so the SENT question ledger never records hidden questions.
        return []
    priority = [
        "visit_purpose",
        "nationality_country",
        "application_country",
        "occupation_status",
        "funding_source",
        "sponsor_relationship",
        "sponsor_name",
        "sponsor_is_in_uk",
        "planned_arrival_date",
        "planned_departure_date",
        "full_name",
        "date_of_birth",
        "uk_accommodation",
        "estimated_trip_cost_gbp",
        "annual_income_gbp",
        "current_address",
        "current_address_duration",
        "has_serious_history",
        "route_confirmed_standard_visitor",
    ]
    required = required_profile_facts(case)
    ordered = priority + sorted(required - set(priority))
    missing = [
        field
        for field in ordered
        if field in required
        and not profile_fact_complete(case, field)
    ]
    actionable = [
        field
        for field in missing
        if field not in case.deferred_fields
        and not explicitly_answers_current_enum(
            latest_reply_text(case.latest_customer_message),
            field,
            directly_requested=(
                field in case.pending_question_fields or field in case.last_requested_fields
            ),
        )
    ]
    if case.question_plan is not None:
        # A stored plan may come from an older release that asked several fields
        # at once. Keep its ordering, but do not reintroduce the questionnaire.
        return _one_question_topic([field for field in case.question_plan if field in actionable])
    # One main question per turn; the delivery gate still checks every required
    # fact. A current document problem takes precedence over new intake.
    return [] if case.open_blockers() else _one_question_topic(actionable)


def _one_question_topic(fields: list[str]) -> list[str]:
    # Arrival and departure are one travel-date question, not two separate email
    # turns. Keep both in the SENT ledger because both are actually asked.
    dates = ["planned_arrival_date", "planned_departure_date"]
    sponsor_identity = ["sponsor_relationship", "sponsor_name"]
    if fields[:2] == dates:
        return dates
    if fields[:2] == sponsor_identity:
        return sponsor_identity
    return fields[:1]


def customer_requests_next_step(body: str) -> bool:
    """Permission to resume questions only, never consent to a summary or delivery."""
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", text)
    text = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", text)
    # A separate date deferral must not veto a current request to prepare other items.
    # Keep comma-linked conditions together: 'If ..., please continue' is not consent.
    for clause in re.split(r"[。！!；;\n]|(?<=[?？])\s*|\.(?:\s|$)", text):
        date_independent_request = bool(re.search(
            r"(?:(?:现在|目前|这期间)(?:我)?|我)?(?:只能|可以|能|想|希望|打算)?(?:先)?"
            r"(?:准备|整理|收集).{0,16}(?:不依赖|不需要|不用等).{0,8}(?:日期|行程)"
            r"(?:的)?(?:材料|资料|文件)?|"
            r"(?:不依赖|不需要|不用等).{0,8}(?:日期|行程).{0,16}"
            r"(?:可以|能|该|应该)?(?:先)?(?:准备|整理|收集|做)(?:什么|哪些)?|"
            r"\b(?:what|which).{0,24}(?:documents?|evidence|preparation).{0,24}"
            r"(?:without|before).{0,12}(?:the )?(?:dates?|itinerary)|"
            r"\b(?:I|we) (?:can|could|want to|would like to).{0,18}(?:prepare|organise|organize)"
            r".{0,24}(?:without|before).{0,12}(?:the )?(?:dates?|itinerary)",
            clause,
            re.I,
        ))
        if date_independent_request and not re.search(
            r"^(?:如果|假如|假设|除非)|\b(?:if|unless|hypothetically|assuming)\b",
            clause,
            re.I,
        ):
            return True
        if re.search(r"还没|尚未|暂时|先不|不要|不用|不想|不需要|不能|如果|假如|"
                     r"\b(?:not|never|haven't|if|later|tomorrow|maybe|cannot|stop)\b|"
                     r"(?:don|can|won|wouldn|couldn|shouldn)['’]t", clause, re.I):
            continue
        if re.search(
            r"下一步|接下来(?:需要|该|怎么)|还缺(?:什么|哪些)|(?:现在|已经).{0,5}(?:可以继续|准备好了)|继续问|"
            r"(?:继续|接着)(?:准备|整理|收集).{0,6}(?:材料|资料|申请)|"
            r"\bwhat(?:'s| is) next\b|\bnext step\b|\bready to (?:continue|proceed)\b|"
            r"\b(?:let['’]s|please|can we|could we) (?:continue|carry on|resume|proceed).{0,30}"
            r"(?:prepar\w*|application|documents?|evidence)\b|"
            r"(?:还有|那之后|接下来).{0,20}(?:不依赖.{0,8})?(?:可以|能|该|应该).{0,8}(?:先做|先准备|准备什么|做什么)|"
            r"\bwhat.{0,20}(?:still missing|else (?:do you need|can I (?:do|prepare)))\b|"
            r"\bwhat (?:can|could|should) I (?:do|prepare).{0,30}\bwithout\b", clause, re.I,
        ):
            return True
    return False


def current_no_intake_clause(text: str) -> bool:
    """One current preference clause, never a whole mixed request to discard.

    Shared scope filtering excludes quotes, reports, conditions and future plans.
    Full matching lets the answer compiler omit only the preference itself while
    retaining an independent application question in the same customer message.
    """
    from visa_agent.workflow.advice_preferences import _current_clauses

    clauses = _current_clauses(text)
    if len(clauses) != 1:
        return False
    return bool(re.fullmatch(
        r"(?:请|麻烦)?(?:这次|现在|目前|暂时)?(?:请|麻烦)?(?:先)?"
        r"(?:别|不要|不用|不需要)(?:再|一直|反复|继续|总是)*"
        r"(?:问|追问|询问|索取|收集)(?:我的?|本人的?)?(?:个人信息|个人资料|身份信息)(?:了|吧)?|"
        r"(?:(?:for now|now)[,，]?\s+)?(?:please\s+)?"
        r"(?:stop\s+(?:asking|requesting|collecting)|"
        r"(?:do not|don't|don’t)\s+(?:(?:keep|continue)\s+)?(?:ask(?:ing)?|request(?:ing)?|collect(?:ing)?))\s+"
        r"(?:me\s+(?:(?:for|about)\s+)?|my\s+)?(?:personal|identity)\s+(?:details|information|questions)"
        r"(?:\s+(?:for now|now|yet|in this reply))?(?:[,，]?\s+please)?",
        clauses[0], re.I,
    ))


def _current_intake_preference_clauses(body: str) -> list[str]:
    from visa_agent.workflow.advice_preferences import _current_clauses

    # "Undecided whether to apply" is a current uncertainty, not an if-clause.
    # Remove only that connector before shared scope checks; quotes/reports and
    # any actual conditional prefix remain intact. This creates no case facts.
    scoped = re.sub(r"\b((?:haven['’]t|have not).{0,8}decided) whether(?= to apply\b)", r"\1", body, flags=re.I)
    scoped = re.sub(r"((?:还没|尚未|没有).{0,4}(?:决定|确定).{0,6})(?:是否|要不要)(?=申请|办理)", r"\1", scoped)
    # A future prefix owns the sentence even when a comma precedes "先别";
    # shared preference splitting would otherwise separate it from that refusal.
    scoped = re.sub(r"(^|[。！？!?]\s*|\.\s+)(?:以后|将来|稍后|待会|届时)[^。！？!?]*", r"\1", scoped)
    return [part.strip() for clause in _current_clauses(scoped)
            if not re.search(r"^(?:以后|将来|稍后|待会|届时|later\b|eventually\b|next time\b)", clause, re.I)
            for part in re.split(r"[,，]", clause) if part.strip()]


def explicitly_requested_profile_question(body: str, field: str | None) -> bool:
    """Return whether the customer currently asks us to pose one named question.

    This is only a reply-pacing signal. It cannot supply the fact, confirm a
    summary, resume a paused case or grant delivery authority. Shared clause
    scoping removes quotes, reported speech, conditions and future instructions
    before the narrow applicant-owned request is matched.
    """
    if field is None:
        return False
    labels = {
        "full_name": r"(?:护照上的|护照)?(?:姓名|名字)|(?:passport\s+)?(?:full\s+)?name",
        "date_of_birth": r"出生日期|生日|date\s+of\s+birth|DOB|birthday",
        "current_address": r"(?:现居|居住|家庭|家里)?地址|current\s+(?:home\s+)?address|home\s+address",
    }
    label = labels.get(field)
    if label is None:
        return False
    return any(re.search(
        rf"(?:问|询问)(?:一下)?(?:我的|我)?(?:护照上的|护照)?(?:{label})|"
        rf"(?:ask|continue\s+asking|prompt)\s+me\s+(?:(?:for|about)\s+)?(?:my\s+)?(?:{label})\b|"
        rf"(?:ask|prompt)\s+(?:(?:for|about)\s+)?my\s+(?:{label})\b",
        clause,
        re.I,
    ) for clause in _current_intake_preference_clauses(body))


def consultation_only_requested(body: str) -> bool:
    """An information-first request is not an invitation to start a personal form.

    This only suppresses this reply's intake questions. It never pauses the case,
    waives missing facts, confirms a route or grants processing permission.
    """
    clauses = _current_intake_preference_clauses(body)
    # Explicitly requesting a personal question is different from asking how to
    # get started on the website. Only the former overrides information-first
    # pacing; this boolean does not select that field or grant any permission.
    if any(not re.search(r"以后|将来|稍后|待会|届时|\b(?:later|eventually|next time)\b", clause, re.I)
        and re.search(
        r"^(?:不过|但是|然后|现在|这次|目前|那|先|请|可以){0,5}(?:继续)?"
        r"(?:问|询问)(?:我的|我)?(?:护照上的|护照)?(?:姓名|名字|出生日期|生日|个人信息|个人资料)|"
        r"^(?:but\s+|then\s+)?(?:please\s+)?(?:ask|continue asking)\s+me\s+(?:(?:for|about)\s+)?"
        r"(?:my\s+)?(?:passport name|name|date of birth|DOB|personal details|personal information)\b",
        clause, re.I,
    ) for clause in clauses):
        return False
    if any(current_no_intake_clause(clause) for clause in clauses):
        return True
    return any(not re.search(
        r"不是|并非|不只是|不止|\b(?:not just|not only|do not only|don't only|don’t only)\b", clause, re.I,
    ) and re.search(
        r"(?:先|只是|只想|仅想|暂时|目前).{0,8}(?:了解|咨询|问问|看看).{0,16}"
        r"(?:流程|过程|步骤|准备|怎么办|怎么申请|怎么做|材料|签证)|"
        r"(?:还没|尚未|没有).{0,4}(?:决定|确定).{0,6}(?:申请|办理)|"
        r"\b(?:just|only|first|for now).{0,16}(?:understand|learn|find out|ask about|explore)"
        r".{0,35}(?:process|steps?|prepar\w*|documents?|visa|how to apply)\b|"
        r"\b(?:haven['’]t|have not).{0,8}decided to apply\b", clause, re.I,
    ) is not None for clause in clauses)


def preparation_context_progress(case: Case) -> bool:
    """A contact/identity correction alone is not a request for a fresh preparation guide."""
    return case.latest_preparation_action == "resume" or bool(case.latest_received_facts or set(case.latest_changes) - {
        "full_name", "date_of_birth", "current_address", "estimated_trip_cost_gbp",
    })


def quiet_preparation_resume(case: Case) -> bool:
    """A resume receipt with an explicit closing is not a request for a tutorial."""
    if (case.latest_preparation_action != "resume" or "next_step" in case.customer_question_topics
            or case.latest_received_facts or case.latest_changes or case.latest_document_names):
        return False
    text = _unquoted_reply_text(case.latest_customer_message)
    return bool(re.search(
        r"\b(?:that['’]s|that is|this is) all for (?:this (?:email|message)|now|today)\b|"
        r"(?:这封(?:邮件)?|本封(?:邮件)?|这条消息|这次|今天)(?:就|先)(?:说)?(?:这些|这样|到这里)",
        text, re.I,
    ))


def _unquoted_reply_text(body: str) -> str:
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", text)
    return re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", text)


_TRAVEL_DATE_UNKNOWN = re.compile(
    r"(?<!出生)(?:旅行|出行|行程|抵英|到英国|离英|离开英国)?(?:的)?"
    r"(?:日期|时间|行程).{0,10}(?:还没(?:有)?|尚未|仍未|依然未|未|没有|没|还是没)"
    r"(?:定|确定|定下|决定|确认|敲定|定案|最终确定)|"
    r"(?<!出生)(?:旅行|出行|行程|抵英|到英国|离英|离开英国)?(?:的)?"
    r"(?:日期|时间|行程)"
    r".{0,8}(?:待定|待确定|待确认|暂未确定|暂定不了|还不确定|不是最终版)|"
    r"(?:还没|尚未|没有|没)(?:确定|定下|决定)(?:具体的?|确切的?)?"
    r"(?<!出生)(?:出行|旅行)?日期|"
    r"(?:还没|尚未|没有|没)(?:确定|定下|决定|敲定).{0,18}"
    r"(?:出发|抵英|到英国|到达).{0,12}(?:回程|离英|离开)(?:日期|时间)?|"
    r"(?:旅行|出行|行程)?日期.{0,12}(?:稍后|之后|晚些|定了后)"
    r"(?:再)?(?:确认|告诉|补充|发给)(?:你)?|"
    r"\b(?:travel|trip|arrival|departure) dates?\b.{0,14}"
    r"(?:not|aren['’]?t).{0,8}(?:set|fixed|decided|confirmed|final)|"
    r"\b(?:travel|trip|arrival|departure) dates?\b.{0,16}"
    r"(?:undecided|unknown|tbc|to be (?:decided|confirmed))\b|"
    r"\barrival and departure dates?\b.{0,16}"
    r"(?:undecided|unknown|tbc|to be (?:decided|confirmed))\b|"
    r"(?<!birth )\b(?:my|our|the|these|those) dates?\b.{0,14}"
    r"(?:(?:(?:are|is) )?(?:still )?not (?:yet )?"
    r"(?:set|fixed|decided|confirmed|final)|"
    r"(?:(?:are|is) )?(?:still )?(?:undecided|unknown|tbc))\b|"
    r"\b(?:haven['’]?t|have not).{0,18}(?:decided|fixed|confirmed).{0,20}dates?\b|"
    r"\b(?:haven['’]?t|have not).{0,18}(?:decided|fixed|confirmed).{0,24}"
    r"(?:arrive|arrival).{0,14}(?:leave|departure)\b|"
    r"\b(?:don['’]?t|do not) know.{0,14}(?:travel |trip )?dates?\b|"
    r"\b(?:I(?:['’]ll| will)|we(?:['’]ll| will)).{0,12}(?:confirm|send|provide|share)"
    r".{0,12}(?:travel |trip )?dates?\b.{0,12}(?:later|once decided|when confirmed)",
    re.I,
)
_TRAVEL_DATE_RETRACTION = re.compile(
    r"(?:之前|原来|原先|先前|上次|旧)(?:我)?(?:提供|说|告诉|填|写|给)?(?:的)?"
    r"(?:旅行|出行|行程|抵英|离英)?(?:日期|时间|行程).{0,10}"
    r"(?:作废|无效|不算|取消|不要用|不再有效|有变|变了)|"
    r"(?:请)?(?:忽略|撤回|删除|清除|取消|作废).{0,12}"
    r"(?:之前|原来|原先|先前|上次|旧)(?:我)?(?:提供|说|告诉|填|写|给)?(?:的)?"
    r"(?:旅行|出行|行程|抵英|离英)?(?:日期|时间|行程)|"
    r"\b(?:previous|earlier|old|original) (?:travel |trip |arrival |departure )?dates?\b"
    r".{0,14}(?:no longer valid|invalid|void|cancelled|canceled|changed|should not be used)|"
    r"\b(?:please )?(?:disregard|ignore|withdraw|remove|clear|cancel)\b.{0,16}"
    r"\b(?:previous|earlier|old|original) (?:travel |trip |arrival |departure )?dates?\b|"
    r"\b(?:please )?(?:disregard|ignore|withdraw|remove|clear|cancel)\b.{0,20}"
    r"\bdates? (?:I|we) (?:gave|provided|sent|shared) (?:you )?(?:earlier|before)\b|"
    r"\bI no longer have confirmed (?:travel |trip )?dates?\b",
    re.I,
)


def _current_travel_date_statement(body: str) -> str:
    """Return direct applicant date statements, excluding quoted or hypothetical text."""
    clauses = []
    for clause in re.split(r"[。！!？?;；\n]|\.(?:\s|$)", _unquoted_reply_text(body)):
        clause = clause.strip()
        if not clause or re.search(
            r"^(?:如果|假如|假设|除非|万一|以后|将来|明天|等下|待会)|"
            r"\b(?:if|unless|suppose|assuming|hypothetically|tomorrow|next time)\b|"
            r"(?:朋友|客户|申请人|他|她).{0,10}(?:说|写|告诉|问)|"
            r"\b(?:my friend|the customer|the applicant|he|she|they)\b.{0,18}"
            r"\b(?:said|wrote|told|asked)\b",
            clause,
            re.I,
        ):
            continue
        chinese_question = re.search(
            r"(?:日期|行程).{0,10}(?:吗|么|是不是)$", clause, re.I
        ) and not re.search(r"(?:我)?不是说了|我已经(?:说过|告诉)", clause)
        english_question = re.match(
            r"^(?:are|is|do|have|should|would|could|can)\b.{0,30}\bdates?\b",
            clause,
            re.I,
        ) and not re.match(r"^(?:didn['’]?t I|haven['’]?t I)\b", clause, re.I)
        if chinese_question or english_question:
            continue
        if re.search(
            r"(?:日期|行程).{0,8}(?:不再|不是).{0,6}(?:未定|待定|不确定)|"
            r"(?:日期|行程).{0,8}(?:现在)?(?:已经|已)(?:定|确定|确认)|"
            r"\bdates?\b.{0,12}\bno longer (?:undecided|unknown|tbc)\b|"
            r"\bdates?\b\s+(?:are|is)\s+(?:now\s+)?(?:set|fixed|decided|confirmed)\b",
            clause,
            re.I,
        ):
            continue
        clauses.append(clause)
    return "\n".join(clauses)


def _travel_date_fields(text: str) -> tuple[str, ...]:
    arrival = bool(re.search(r"抵英|到英国|到达|出发|入境|\b(?:arriv|entry)", text, re.I))
    departure = bool(re.search(
        r"离英|离开英国|回程|返程|出境|\b(?:depart|leav)|"
        r"\breturn (?:date|home|journey|flight|from)",
        text,
        re.I,
    ))
    if arrival and not departure:
        return ("planned_arrival_date",)
    if departure and not arrival:
        return ("planned_departure_date",)
    return ("planned_arrival_date", "planned_departure_date")


def update_deferred_questions(case: Case, body: str) -> None:
    """Defer current unknown plans and withdraw dates the applicant invalidates."""
    case.latest_deferred_fields = []
    body = _current_travel_date_statement(body)
    broad_trip_without_plan = bool(
        re.search(r"(?:今年|明年).{0,4}(?:上半年|下半年)", body)
        and re.search(r"(?:^|[，,。])\s*(?:我)?(?:还没(?:有)?|尚未)(?:具体|详细)(?:的)?(?:规划|计划|安排|行程)", body)
    )
    explicit_unknown = bool(_TRAVEL_DATE_UNKNOWN.search(body))
    explicit_retraction = bool(_TRAVEL_DATE_RETRACTION.search(body))
    if broad_trip_without_plan or explicit_unknown or explicit_retraction:
        fields = (
            _travel_date_fields(body)
            if explicit_unknown or explicit_retraction
            else ("planned_arrival_date", "planned_departure_date")
        )
        for field in fields:
            if (explicit_unknown or explicit_retraction) and getattr(case.profile, field) is not None:
                setattr(case.profile, field, None)
                for evidence in case.active_evidence(field):
                    evidence.superseded = True
            if getattr(case.profile, field) is None:
                case.latest_deferred_fields.append(field)
                if field not in case.deferred_fields:
                    case.deferred_fields.append(field)
    if case.profile.uk_accommodation is None and re.search(
        r"(?:住宿|住处|酒店|住哪里|住哪)[^，,。；;！!?？\n]{0,12}"
        r"(?:还没|尚未|没有|没)(?:决定|确定|定下|订)|"
        r"(?:还没|尚未|没有|没)(?:决定|确定|定下|预订)"
        r"[^，,。；;！!?？\n]{0,12}(?:住宿|住处|酒店|住哪里|住哪)|"
        r"\b(?:have not|haven['’]t|not yet|still haven['’]t)[^.!?;\n]{0,18}"
        r"(?:decided|chosen|booked)[^.!?;\n]{0,18}(?:where to stay|accommodation|hotel)|"
        r"\b(?:accommodation|where (?:I|we) will stay|hotel)[^.!?;\n]{0,18}"
        r"(?:undecided|not decided|not fixed|unknown)\b",
        body,
        re.I,
    ):
        case.latest_deferred_fields.append("uk_accommodation")
        if "uk_accommodation" not in case.deferred_fields:
            case.deferred_fields.append("uk_accommodation")
    case.deferred_fields = [field for field in case.deferred_fields if getattr(case.profile, field) is None]


def received_context(case: Case) -> str:
    supplied = case.latest_received_facts
    if (prefers_brief_reply(case) and case.proactive_guidance_offered and not case.latest_changes
            and len(supplied) >= 3 and set(supplied) <= {
                "nationality", "nationality_country", "application_country", "visit_purpose",
                "occupation_status", "funding_source",
            } and {"nationality_country", "application_country"} <= set(supplied)):
        # For a short first step, confirm the application context rather than
        # reading the whole intake form back. Facts and evidence stay untouched.
        labels = {"China": "中国", "Hong Kong": "香港", "United Kingdom": "英国"}
        country, location = supplied["nationality_country"], supplied["application_country"]
        if case.customer_language == "zh":
            return f"明白，{labels.get(country, country)}护照，在{labels.get(location, location)}申请。"
        adjective = {"China": "Chinese", "United Kingdom": "British"}.get(country, country)
        return f"Got it—{adjective} passport, applying from {location}."
    # A useful answer already acknowledges the facts it explains. Keep the
    # remaining receipt (e.g. application location), not a second recital of
    # the same occupation/funding. Corrections retain their explicit receipt.
    facts = {field: value for field, value in case.latest_received_facts.items()
             if case.latest_changes or not _guidance_acknowledges_fact(case, field)}
    if case.customer_language != "zh":
        country_labels = {"China": "Chinese", "Hong Kong": "Hong Kong", "United Kingdom": "British"}
        locations = []
        if "nationality_country" in facts:
            country = facts["nationality_country"]
            locations.append(f"you hold a {country_labels.get(country, country)} passport")
        if "application_country" in facts:
            country = facts["application_country"]
            locations.append(f"you plan to apply from {country}")
        if (
            case.profile.funding_source == "personal_sponsor"
            and {"sponsor_relationship", "sponsor_name", "sponsor_is_in_uk"}.intersection(facts)
        ):
            sponsor_details = []
            relation = case.profile.sponsor_relationship
            if "funding_source" not in facts and relation in _SPONSOR_RELATION_LABELS:
                sponsor_details.append(
                    "both of your parents are sponsoring the trip"
                    if relation == "parents"
                    else f"your {_SPONSOR_RELATION_LABELS[relation][1]} is sponsoring the trip"
                )
            if "sponsor_name" in facts and case.profile.sponsor_name:
                sponsor_details.append(f"the sponsor name is {case.profile.sponsor_name}")
            if "sponsor_is_in_uk" in facts and case.profile.sponsor_is_in_uk is not None:
                subject = "they" if relation == "parents" else "the sponsor"
                sponsor_details.append(
                    f"{subject} {'lives' if subject != 'they' else 'live'} in the UK"
                    if case.profile.sponsor_is_in_uk
                    else f"{subject} {'does' if subject != 'they' else 'do'} not live in the UK"
                )
            if sponsor_details:
                locations.append(", and ".join(sponsor_details))
        phrases = {
            "visit_purpose": {
                "tourism": "you're planning a holiday in the UK",
                "conference": "you're going to the UK for a conference",
                "family_or_friends": "you're visiting family or friends in the UK",
                "business": "you're planning a business visit to the UK",
            },
            "occupation_status": {
                "employed": "you're currently employed", "student": "you're studying",
                "self_employed": "you're self-employed",
            },
            "funding_source": {
                "self": "you're paying for the trip yourself",
                "employer_or_school": "your employer or school is covering the trip",
                "personal_sponsor": "someone is helping fund your trip",
            },
        }
        if facts.get("funding_source") == case.profile.funding_source and case.profile.funding_source:
            phrases["funding_source"][case.profile.funding_source] = funding_wording(case, language="en")
            if case.profile.funding_source == "personal_sponsor":
                relation = _current_parent_sponsor_hint(case)
                if relation in _SPONSOR_RELATION_LABELS:
                    label = _SPONSOR_RELATION_LABELS[relation][1]
                    phrases["funding_source"]["personal_sponsor"] = (
                        f"your {label} will help fund the trip"
                    )
        received = locations + [values[facts[key]] for key, values in phrases.items()
                                if facts.get(key) in values]
        if received:
            message = "Thanks, I've got the starting point: " + "; ".join(received) + "."
            if set(facts) == {"nationality_country"} and not case.profile.application_country:
                message += (
                    " Where you apply matters because applying outside your passport country may require "
                    "evidence of lawful residence there, so I will check that location next."
                )
            return message
        if set(facts) == {"full_name"}:
            return "Thanks — I've noted your passport name."
        if set(facts) == {"date_of_birth"}:
            return "Thanks — I've noted your date of birth."
        recorded = []
        if "full_name" in facts:
            recorded.append("the name in your passport")
        if "nationality_country" in facts:
            recorded.append(f"your passport country ({facts['nationality_country']})")
        if "application_country" in facts:
            recorded.append(f"where you will apply ({facts['application_country']})")
        if "date_of_birth" in facts:
            recorded.append("your date of birth")
        if "uk_accommodation" in facts:
            recorded.append("your proposed accommodation")
        if "estimated_trip_cost_gbp" in facts:
            recorded.append("your estimated budget")
        if "planned_arrival_date" in facts or "planned_departure_date" in facts:
            recorded.append("your updated travel dates")
        return "I've recorded " + ", ".join(recorded) + "." if recorded else ""
    parts = []
    country_labels = {"China": "中国", "Hong Kong": "香港", "United Kingdom": "英国"}
    if "nationality_country" in facts:
        country = facts["nationality_country"]
        parts.append(f"你持{country_labels.get(country, country)}护照")
    if "application_country" in facts:
        country = facts["application_country"]
        parts.append(f"准备在{country_labels.get(country, country)}递交")
    if (
        case.profile.funding_source == "personal_sponsor"
        and {"sponsor_relationship", "sponsor_name", "sponsor_is_in_uk"}.intersection(facts)
    ):
        sponsor_details = []
        relation = case.profile.sponsor_relationship
        if "funding_source" not in facts and relation in _SPONSOR_RELATION_LABELS:
            sponsor_details.append(
                "由父母共同资助"
                if relation == "parents"
                else f"由你{_SPONSOR_RELATION_LABELS[relation][0]}资助"
            )
        if "sponsor_name" in facts and case.profile.sponsor_name:
            sponsor_details.append(f"资助人姓名记为{case.profile.sponsor_name}")
        if "sponsor_is_in_uk" in facts and case.profile.sponsor_is_in_uk is not None:
            subject = "两位" if relation == "parents" else "资助人"
            sponsor_details.append(
                f"{subject}{'住在' if case.profile.sponsor_is_in_uk else '不住在'}英国"
            )
        if sponsor_details:
            parts.append("，".join(sponsor_details))
    purposes = {"tourism": "你打算去英国旅游", "conference": "你准备去英国参加会议",
                "family_or_friends": "你打算去英国探亲访友", "business": "这次是商务访问"}
    occupations = {"employed": "你目前在工作", "student": "你目前在读书",
                   "self_employed": "你目前自己经营业务"}
    if facts.get("visit_purpose") in purposes:
        parts.append(purposes[facts["visit_purpose"]])
    if facts.get("occupation_status") in occupations:
        parts.append(occupations[facts["occupation_status"]])
    funding = {"self": "费用由你自己承担", "employer_or_school": "费用由雇主或学校承担",
               "personal_sponsor": "这次有个人资助"}
    if facts.get("funding_source") == case.profile.funding_source and case.profile.funding_source:
        funding[case.profile.funding_source] = funding_wording(case, language="zh")
        if case.profile.funding_source == "personal_sponsor":
            relation = _current_parent_sponsor_hint(case)
            if relation in _SPONSOR_RELATION_LABELS:
                label = _SPONSOR_RELATION_LABELS[relation][0]
                funding["personal_sponsor"] = f"这次由你{label}资助"
    if facts.get("funding_source") in funding:
        parts.append(funding[facts["funding_source"]])
    if parts:
        message = "明白，" + "，".join(parts) + "。"
        if set(facts) == {"nationality_country"} and not case.profile.application_country:
            message += "递交地点会影响是否要补当地合法居留证明，所以我接着确认你会从哪里申请。"
        return message
    recorded = []
    if facts and set(facts) <= {"nationality_country", "application_country"}:
        details = []
        if "nationality_country" in facts:
            country = facts["nationality_country"]
            details.append(f"你持{country_labels.get(country, country)}护照")
        if "application_country" in facts:
            country = facts["application_country"]
            details.append(f"{'会' if details else '你会'}在{country_labels.get(country, country)}递交申请")
        return "明白，" + "，".join(details) + "。"
    if set(facts) == {"full_name"}:
        return "好的，护照姓名已经记下了。"
    if set(facts) == {"date_of_birth"}:
        return "好的，出生日期也记下了。"
    if "full_name" in facts:
        recorded.append("护照姓名")
    if "nationality_country" in facts:
        country = facts["nationality_country"]
        recorded.append(f"护照签发国家（{country_labels.get(country, country)}）")
    if "application_country" in facts:
        country = facts["application_country"]
        recorded.append(f"申请所在地（{country_labels.get(country, country)}）")
    if "date_of_birth" in facts:
        recorded.append("生日")
    if "uk_accommodation" in facts:
        recorded.append("计划住宿")
    if "estimated_trip_cost_gbp" in facts:
        recorded.append("旅行预算")
    if "planned_arrival_date" in facts or "planned_departure_date" in facts:
        recorded.append("行程日期")
    return "你提供的" + "、".join(recorded) + "已记下。" if recorded else ""


def _single_question_context(case: Case, question_fields: list[str]) -> str:
    """Explain why the one next question matters without adding another question."""
    if not question_fields:
        return ""
    fields = set(question_fields)
    if {"planned_arrival_date", "planned_departure_date"} <= fields:
        key = "travel_dates"
    elif {"sponsor_relationship", "sponsor_name"} <= fields:
        key = "sponsor_identity"
    else:
        key = question_fields[0]
    if case.customer_language == "zh":
        if key == "sponsor_identity" and _current_parent_sponsor_hint(case):
            return "接着把实际资助人确认清楚，这样资助信、资金材料和关系证明才能对应起来。"
        return {
            "visit_purpose": "我先确认出行目的，因为它决定后面该按旅游、探亲访友还是商务或参会来整理材料。",
            "nationality_country": "接着核对护照签发国，因为它会影响需要先确认的入境安排。",
            "application_country": "接着确认递交地点，因为在非护照国申请时通常还要核对当地合法居留证明。",
            "occupation_status": "接着了解你的工作或学习情况，这样后面只准备与你实际情况相符的证明。",
            "funding_source": "接着确认谁承担费用，这会决定后面核对本人资金还是资助材料。",
            "sponsor_relationship": "接着确认你和资助人的关系，方便后面选择真实、能对应的关系证明。",
            "sponsor_name": "接着核对资助人姓名，之后要和对方的证件及资金材料保持一致。",
            "sponsor_identity": "接着确认资助人的身份和你们的关系，这样资助信、资金和关系证明才能对应起来。",
            "sponsor_is_in_uk": "接着确认资助人是否住在英国，因为这会影响是否还需核对其英国身份材料。",
            "travel_dates": "接着确认预计行程，方便之后把访问计划和申请表对齐。",
            "full_name": "接着核对护照姓名，之后所有材料和申请表都应使用同一写法。",
            "date_of_birth": "接下来核对出生日期，之后会和护照资料页及申请表保持一致。",
            "uk_accommodation": "接下来了解在英住宿安排，是为了把预计行程和申请表对齐。",
            "estimated_trip_cost_gbp": "接下来了解大致预算，方便核对行程开支与资金说明是否相互一致。",
            "annual_income_gbp": "接下来了解收入情况，方便把个人经济情况与资金来源说明对应起来。",
            "current_address": "接下来核对现住址，因为申请表会需要这项信息。",
            "has_serious_history": "接下来做一项常规背景核对；如有相关情况，后面会按需要转人工复核。",
        }.get(key, "")
    if key == "sponsor_identity" and _current_parent_sponsor_hint(case):
        return (
            "Next, I need to identify the actual sponsor so the support letter, financial evidence and "
            "relationship evidence all match."
        )
    return {
        "visit_purpose": (
            "I first need the purpose of the visit because it determines whether we prepare for a holiday, "
            "a family visit, business or a conference."
        ),
        "nationality_country": (
            "Next, I need the passport country because it affects the entry arrangement we should check first."
        ),
        "application_country": (
            "Next, I need the application location because applying outside your passport country will usually "
            "mean checking evidence of lawful residence there."
        ),
        "occupation_status": (
            "Next, I need your work or study situation so we only prepare evidence that fits your circumstances."
        ),
        "funding_source": (
            "Next, I need to know who will pay so we can focus on either your own funds or sponsor evidence."
        ),
        "sponsor_relationship": (
            "Next, I need your relationship to the sponsor so we can use genuine evidence that matches it."
        ),
        "sponsor_name": (
            "Next, I need the sponsor's name so it stays consistent with their ID and financial evidence."
        ),
        "sponsor_identity": (
            "Next, I need the sponsor's identity and your relationship so their letter, funds and relationship "
            "evidence can be matched."
        ),
        "sponsor_is_in_uk": (
            "Next, I need to know whether the sponsor lives in the UK because that affects whether we also check "
            "their UK status evidence."
        ),
        "travel_dates": (
            "Next, I need the intended dates so the visit plan and application form can be kept consistent."
        ),
        "full_name": (
            "Next, I need the passport name so every document and the application form use the same spelling."
        ),
        "date_of_birth": (
            "Next, I need your date of birth so the passport details and application information stay consistent."
        ),
        "uk_accommodation": (
            "Next, I need the intended UK accommodation so the visit plan and application form stay consistent."
        ),
        "estimated_trip_cost_gbp": (
            "Next, I need a rough budget so we can compare the expected trip costs with the funding explanation."
        ),
        "annual_income_gbp": (
            "Next, I need your income position so the financial circumstances and funding explanation line up."
        ),
        "current_address": "Next, I need your home address because it will be required on the application form.",
        "has_serious_history": (
            "Next is a standard background check; if anything applies, we can refer it for human review."
        ),
    }.get(key, "")


def _guidance_already_acknowledges_single_new_fact(case: Case) -> bool:
    """Avoid repeating one understood enum before the useful guidance."""
    if not case.proactive_guidance_offered:
        return False
    changed = set(case.latest_received_facts) | set(case.latest_changes)
    if len(changed) != 1:
        return False
    return _guidance_acknowledges_fact(case, next(iter(changed)))


def _guidance_acknowledges_fact(case: Case, field: str) -> bool:
    """Presentation-only deduplication against the selected reviewed advice."""
    if not case.proactive_guidance_offered:
        return False
    if field not in {"visit_purpose", "occupation_status", "funding_source"}:
        return False
    value = str(getattr(case.profile, field) or "")
    patterns = {
        ("visit_purpose", "tourism"): r"既然这次是旅游|As this is a holiday",
        ("visit_purpose", "conference"): r"这次是参会|For the conference|conference trip",
        ("visit_purpose", "family_or_friends"): (
            r"这次是探亲访友|For this visit to family or friends"
        ),
        ("occupation_status", "student"): (
            r"你还在读书|As you are studying|As a self-funded student"
        ),
        ("occupation_status", "employed"): r"你现在在职|your employment and income",
        ("occupation_status", "self_employed"): r"你自己经营业务|For your own business",
        ("funding_source", "self"): (
            r"费用由你自己承担|As you are paying for the trip|self-funded student"
        ),
        ("funding_source", "employer_or_school"): (
            r"按你说的安排.{0,20}(?:雇主|公司|学校|大学|资助单位).{0,12}"
            r"(?:承担|支付|资助)|Based on what you said, the .{0,30} will|"
            r"Since the .{0,30} is funding the trip"
        ),
        ("funding_source", "personal_sponsor"): (
            r"既然这次由.{0,12}资助|按你说的安排.{0,20}会承担|"
            r"Since your .{0,20} is funding this trip|Based on what you said, your .{0,20} will cover"
        ),
    }
    pattern = patterns.get((field, value))
    return bool(pattern and re.search(pattern, "\n".join(case.customer_answers), re.I))


DOCUMENT_LABELS_ZH = {
    "passport": "有效护照或旅行证件",
    "status_evidence": "工作、学习或自雇证明",
    "purpose_evidence": "访问目的的证明材料",
    "funding_evidence": "资金及资金来源证明",
    "legal_residence": "在申请所在地合法居留的证明",
    "sponsor_evidence": "资助人的相关证明",
    "certified_translation": "非英文或威尔士文材料的认证翻译",
}


def _canonical_source_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def _official_source_block(case: Case, sources: list[str]) -> str:
    """Present reviewed links as useful destinations, not an unlabeled URL dump."""
    existing = "\n".join(case.customer_answers)
    unique: list[str] = []
    seen: set[str] = set()
    for source in sources:
        canonical = _canonical_source_url(source)
        if canonical in seen or canonical in existing:
            continue
        seen.add(canonical)
        unique.append(source)
    if not unique:
        return ""

    labels_zh = {
        _canonical_source_url(ROUTE_CHECK_URL): "查询需要签证、ETA 还是其他入境安排",
        _canonical_source_url(APPLICATION_URL): "Standard Visitor 在线申请（确认路线后选择 Apply now）",
        _canonical_source_url(DOCUMENTS_URL): "访客签证证明材料说明",
    }
    labels_en = {
        _canonical_source_url(ROUTE_CHECK_URL): "Check whether you need a visa, ETA or another arrangement",
        _canonical_source_url(APPLICATION_URL): "Standard Visitor online application (select Apply now after checking the route)",
        _canonical_source_url(DOCUMENTS_URL): "Visitor supporting-document guidance",
    }
    labels = labels_zh if case.customer_language == "zh" else labels_en
    heading = "官方入口和材料依据：" if case.customer_language == "zh" else "Official application and guidance pages:"
    lines = [heading]
    for source in unique:
        label = labels.get(_canonical_source_url(source), "GOV.UK")
        lines.extend((f"- {label}", f"GOV.UK: {source}"))
    return "\n".join(lines)


def document_label(case: Case, item: Requirement) -> str:
    """Explain an existing requirement, without changing what evidence is accepted."""
    zh = case.customer_language == "zh"
    if item.id == "status_evidence":
        labels = {
            "student": ("在读证明", "Evidence of your student status"),
            "employed": ("在职证明", "Evidence of your employment"),
            "self_employed": ("自雇经营情况的证明", "Evidence of your self-employment"),
        }
        if case.profile.occupation_status in labels:
            return labels[case.profile.occupation_status][0 if zh else 1]
    if item.id == "purpose_evidence" and case.profile.visit_purpose == "conference":
        return "会议主办方的邀请函" if zh else "Invitation from the conference organiser"
    if item.id == "funding_evidence":
        if case.profile.funding_source == "employer_or_school":
            return ("资助单位的证明，说明承担哪些费用" if zh
                    else "Evidence from the funding organisation explaining which costs it covers")
        if case.profile.funding_source == "self":
            return ("可用资金及来源证明，例如银行流水" if zh
                    else "Evidence of available funds and their source, such as bank statements")
    return DOCUMENT_LABELS_ZH.get(item.id, item.title) if zh else item.title


def explained_document_label(case: Case, item: Requirement) -> str:
    """Explain how an already-selected item helps; never add an acceptance rule."""
    zh = case.customer_language == "zh"
    details = {
        "passport": (
            "核对身份，以及有效期是否覆盖整个计划停留期间",
            "to check your identity and validity throughout the planned stay",
        ),
        "purpose_evidence": (
            "说明这次去做什么；旅游可以先整理真实的计划行程，不把未定安排写成已预订",
            "to explain what you will do; for a holiday, outline your real plans without presenting unbooked arrangements as bookings",
        ),
        "funding_evidence": (
            "说明谁承担费用、资金从哪里来，以及你是否能使用这些钱；预算金额本身不是证明",
            "to show who pays, where the money comes from and access to it; a budget figure alone is not evidence",
        ),
        "legal_residence": (
            "说明你在递交申请的国家或地区的合法居留身份",
            "to show your lawful residence where you are applying",
        ),
        "sponsor_evidence": (
            "说明资助内容、资助能力及你们的关系；适用时还要说明资助人的英国身份",
            "to explain the support, the sponsor's means and your relationship, plus their UK status where applicable",
        ),
        "certified_translation": (
            "让原件内容可被核验；不是只翻摘要，译者声明和联系信息也需检查",
            "so the original content can be verified; check completeness, the translator's declaration and contact details",
        ),
    }
    status_details = {
        "student": ("可向学校索取抬头纸证明，说明在读及准假情况，用来支持你的学习情况说明",
                    "ask your school for a headed letter confirming enrolment and leave, to support your stated study circumstances"),
        "employed": ("可向雇主索取抬头纸证明，说明职位、薪资及任职时间，用来支持工作情况说明",
                     "ask your employer for a headed letter with your role, salary and length of employment, to support your work circumstances"),
        "self_employed": ("例如经营登记或近期发票，用来说明目前仍在经营",
                          "for example business registration or recent invoices, to explain ongoing self-employment"),
    }
    if item.id == "status_evidence" and case.profile.occupation_status in status_details:
        details[item.id] = status_details[case.profile.occupation_status]
    if item.id == "purpose_evidence" and case.profile.visit_purpose == "conference":
        details[item.id] = ("向主办方索取，用来说明活动及访问目的", "ask the organiser for it, to explain the event and purpose")
    explanation = details.get(item.id)
    return document_label(case, item) + (f" — {explanation[0 if zh else 1]}" if explanation else "")


QUESTION_TEXT_ZH = {
    "nationality": "你目前是哪国国籍？如果持有多国国籍，请把与这次申请有关的都告诉我。",
    "application_country": "你准备在哪个国家或地区递交申请？",
    "nationality_country": "你持哪个国家的护照？",
    "visit_purpose": "这次去英国主要是旅游、探亲访友，还是参加商务活动或会议？",
    "planned_arrival_date": "计划哪天抵达英国？请带上年份；没定下来也可以先告诉我。",
    "planned_departure_date": "计划哪天离开英国？请带上年份；没定下来也可以先告诉我。",
    "full_name": "方便告诉我护照上的姓名吗？",
    "date_of_birth": "你的出生日期是什么？请写完整年月日。",
    "occupation_status": "你目前在工作、读书，还是自己经营业务？",
    "funding_source": "这次旅行的费用由你自己承担，还是有人或单位资助？",
    "sponsor_relationship": "资助人和你是什么关系？",
    "sponsor_name": "资助人的姓名是什么？请按对方证件或银行材料上的写法告诉我。",
    "sponsor_is_in_uk": "这位资助人目前住在英国吗？回答“在”或“不在”就可以；如果在，后面我会再核对其英国身份材料。",
    "uk_accommodation": "在英国准备住哪里？还没确定的话也可以直接说。",
    "estimated_trip_cost_gbp": "这趟旅行大约打算花多少英镑？先给一个估计就好。",
    "annual_income_gbp": "你目前有收入吗？有的话，大约每年多少英镑？没有收入也可以直接说明。",
    "current_address": "你目前实际居住的地址是什么？这里需要的是住址，不是工作地点。",
    "current_address_duration": "你在现在的住址大概住了多久？按你记得的时间说就好，暂时记不清也可以告诉我。",
    "has_serious_history": (
        "正式核对前还要确认一项常规背景信息：你以前是否有英国或其他国家、地区的拒签、"
        "逾期停留、遣返，或刑事、重大民事记录？如果有，先告诉我大致类型和时间，我会转人工顾问复核。"
    ),
    "route_confirmed_standard_visitor": (
        "按你目前提供的信息，我们先以 Standard Visitor 作为材料准备框架，但路线不能只凭聊天决定。"
        "请先用 GOV.UK 的官方工具核对你需要 Standard Visitor visa、ETA，还是其他安排，再把结果告诉我："
        "https://www.gov.uk/check-uk-visa"
    ),
}
QUESTION_TEXT_EN = {
    "nationality": "What nationality or nationalities do you currently hold? Please include each one relevant to this application.",
    "application_country": "Which country or territory will you apply from?",
    "nationality_country": "Which country's passport do you hold?",
    "visit_purpose": "What is the main reason for your visit to the UK?",
    "planned_arrival_date": "When would you like to arrive in the UK, including the year? It's fine to say if this isn't decided yet.",
    "planned_departure_date": "When would you like to leave the UK, including the year? It's fine to say if this isn't decided yet.",
    "full_name": "What is your name as it appears in your passport?",
    "date_of_birth": "What is your full date of birth?",
    "occupation_status": "Are you currently employed, studying or self-employed?",
    "funding_source": "Who will pay for the trip?",
    "sponsor_relationship": "What is your relationship to the person sponsoring the trip?",
    "sponsor_name": "What is the sponsor's full name, as shown on their ID or financial evidence?",
    "sponsor_is_in_uk": (
        "Does this sponsor currently live in the UK? A simple yes or no is fine; if yes, "
        "we will check their UK status evidence later."
    ),
    "uk_accommodation": "Where are you planning to stay in the UK? It's fine if you haven't decided yet.",
    "estimated_trip_cost_gbp": "Roughly how much do you expect the trip to cost in pounds? An estimate is fine.",
    "annual_income_gbp": "Do you currently have an income? If so, roughly how much per year in pounds? It's fine to say if you have none.",
    "current_address": "What is your current home address? This will be needed for the application form, rather than your workplace address.",
    "current_address_duration": "About how long have you lived at your current address? Use the precision you remember; it is fine to say if you need to check.",
    "has_serious_history": (
        "Before the final check, I need to ask a standard background question: have you ever had a visa refusal, "
        "overstayed, been removed or deported, or had a criminal or serious civil matter in the UK or elsewhere? "
        "If yes, tell me the broad type and date first and I will refer it for human review."
    ),
    "route_confirmed_standard_visitor": (
        "Based on what you have told me, we can use Standard Visitor as the preparation framework, but the route "
        "should not be decided from chat alone. Please use the official GOV.UK checker to confirm whether you need "
        "a Standard Visitor visa, an ETA or another arrangement, then tell me the result: "
        "https://www.gov.uk/check-uk-visa"
    ),
}

SPONSOR_IDENTITY_QUESTION_ZH = (
    "这次由谁资助你？请告诉我你和资助人的关系，以及对方的姓名；姓名按其证件或银行材料上的写法就好。"
)
SPONSOR_IDENTITY_QUESTION_EN = (
    "Who is sponsoring this trip? Please tell me their relationship to you and their full name as it appears on "
    "their ID or financial evidence."
)

_SPONSOR_RELATION_LABELS = {
    "father": ("父亲", "father"),
    "mother": ("母亲", "mother"),
    "parents": ("父母", "parents"),
}


def _current_parent_sponsor_hint(case: Case) -> str | None:
    """Return a wording hint, never a fact update, for a current parent sponsor.

    The persisted, validated profile wins.  The message fallback is deliberately
    narrow: it only helps us avoid asking "who?" immediately after the applicant
    has just said that a parent will pay.  It does not complete the profile, alter
    requirements, or survive into another turn.
    """
    if case.profile.sponsor_relationship in _SPONSOR_RELATION_LABELS:
        return case.profile.sponsor_relationship

    text = _unquoted_reply_text(case.latest_customer_message)
    clauses = re.split(r"[。！!？？；;\n]|\.(?:\s|$)", text)
    relation_patterns = {
        "parents": (r"(?:我(?:的)?(?:父母|爸妈)|父母|爸妈)", r"my parents"),
        "father": (r"(?:我(?:的)?(?:父亲|爸爸|爸)|我爸|父亲|爸爸)", r"my (?:father|dad)"),
        "mother": (r"(?:我(?:的)?(?:母亲|妈妈|妈)|我妈|母亲|妈妈)", r"my (?:mother|mum|mom)"),
    }
    for clause in clauses:
        if re.search(
            r"(?:如果|假如|假设|若|万一|以前|过去|曾经|原来|不是|并非|不会|不由|不资助)|"
            r"\b(?:if|unless|hypothetically|used to|previously|in the past|would not|won['’]t|is not)\b|"
            r"(?:我的?)?(?:朋友|客户|同事)(?:的)?(?:旅行|行程|费用|签证)|"
            r"\bmy (?:friend|client|colleague|coworker)['’]s\b",
            clause,
            re.I,
        ):
            continue
        for relation, (zh_pattern, en_pattern) in relation_patterns.items():
            zh_assertion = (
                rf"{zh_pattern}.{{0,14}}(?:资助|承担|支付|负担|出钱).{{0,16}}"
                r"(?:我|我的|这次|本次|旅行|旅费|费用|机票|住宿)|"
                r"(?:我的|我这次|这次|本次)?(?:旅行|旅费|费用|机票|住宿)"
                rf".{{0,10}}由{zh_pattern}.{{0,8}}(?:资助|承担|支付|负担)"
            )
            en_assertion = (
                rf"\b{en_pattern}\b.{{0,18}}\b(?:will |is |are )?"
                r"(?:sponsor|pay|cover|fund)(?:s|ing)?\b.{0,24}\b(?:my|this|the)\s+"
                r"(?:trip|travel|costs?|expenses?|flights?|accommodation)\b|"
                r"\b(?:my|this|the)\s+(?:trip|travel|costs?|expenses?|flights?|accommodation)\b"
                rf".{{0,24}}\b(?:paid|covered|funded|sponsored)\s+by\s+{en_pattern}\b"
            )
            if re.search(zh_assertion, clause, re.I) or re.search(en_assertion, clause, re.I):
                return relation
    return None


def _sponsor_identity_question(case: Case, *, relationship_also_missing: bool) -> str:
    """Ask one natural sponsor question while keeping every requested field visible."""
    zh = case.customer_language == "zh"
    relation = _current_parent_sponsor_hint(case)
    if relation is None:
        return SPONSOR_IDENTITY_QUESTION_ZH if zh else SPONSOR_IDENTITY_QUESTION_EN

    label = _SPONSOR_RELATION_LABELS[relation][0 if zh else 1]
    if zh:
        if relationship_also_missing and relation == "parents":
            return (
                "你说这次由父母资助。请确认是父亲、母亲，还是两位共同承担；"
                "再把实际资助人的姓名按证件或银行材料上的写法告诉我。"
            )
        if relationship_also_missing:
            if prefers_brief_reply(case):
                return f"请把你{label}的姓名按其证件或银行材料上的写法告诉我。"
            return (
                f"我理解这次是由你的{label}资助；如果我理解没错，请把{label}的姓名"
                "按证件或银行材料上的写法告诉我。"
            )
        if relation == "parents":
            return "请把实际资助人的姓名告诉我；如果父母共同资助，请按各自证件或银行材料写两位的姓名。"
        return f"为了让资助说明和银行材料对得上，请把你{label}的姓名按其证件上的写法告诉我。"

    if relationship_also_missing and relation == "parents":
        return (
            "You mentioned that your parents will fund the trip. Is that your father, your mother, or both of "
            "them? Please also give me the actual sponsor's full name, matching their ID or financial evidence."
        )
    if relationship_also_missing:
        pronoun = "his" if relation == "father" else "her"
        if prefers_brief_reply(case):
            return f"What is your {label}'s full name as shown on {pronoun} ID or financial evidence?"
        return (
            f"I understand that your {label} will fund the trip. If I have that right, what is your {label}'s "
            f"full name as shown on {pronoun} ID or financial evidence?"
        )
    if relation == "parents":
        return (
            "What is the actual sponsor's full name? If both parents will provide funding, please give both names "
            "as shown on their ID or financial evidence."
        )
    pronoun = "his" if relation == "father" else "her"
    return (f"To match the support letter to the financial evidence, what is your {label}'s full name as shown "
            f"on {pronoun} ID?")


def _profile_question_text(case: Case, field: str) -> str:
    zh = case.customer_language == "zh"
    if field == "sponsor_name":
        return _sponsor_identity_question(case, relationship_also_missing=False)
    question = (
        QUESTION_TEXT_ZH.get(field, fact_label(case, field))
        if zh
        else QUESTION_TEXT_EN.get(field, f"Could you tell me your {fact_label(case, field).lower()}?")
    )
    if field == "route_confirmed_standard_visitor" and wants_no_links(case.latest_customer_message):
        return (
            "按你目前提供的信息，我们先以 Standard Visitor 作为材料准备框架，但路线不能只凭聊天决定。"
            "请在 GOV.UK 搜索“Check if you need a UK visa”，核对你需要 Standard Visitor visa、ETA，"
            "还是其他安排，再把结果告诉我。"
            if zh
            else "Based on what you have told me, we can use Standard Visitor as the preparation framework, but "
            "the route should not be decided from chat alone. On GOV.UK, search for 'Check if you need a UK visa', "
            "confirm whether you need a Standard Visitor visa, an ETA or another arrangement, then tell me the result."
        )
    return question


def _document_list_request_text(body: str) -> str:
    clauses = re.split(r"[。！!；;\n，,]|(?<=[?？])\s*|\.(?:\s|$)", _unquoted_reply_text(body))
    return "\n".join(clause for clause in clauses if not re.search(
        r"(?:不用|不需要|不要).{0,20}(?:清单|材料|资料)|"
        r"(?:don't|do not|no need).{0,30}(?:checklist|documents|list)", clause, re.I,
    ))


def document_list_requested(case: Case) -> bool:
    """General information needs no complete profile; personal missing items do."""
    # A declined general checklist must not veto a separate current personal request.
    text = _document_list_request_text(case.latest_customer_message)
    if not text.strip():
        return False
    if (reviewed_document_preparation(text, case.customer_language)
            and not _explicit_document_list_request(text)):
        # Asking how to obtain one letter is not asking for every missing file.
        # A separate explicit checklist request still retains its own answer.
        return False
    if is_document_purpose_question(text) and not _explicit_document_list_request(text):
        # A model can label an individual document's purpose as document_checklist.
        # Explain that document without treating the label as a collection request.
        return False
    if ({"off_topic", "unsupported", "next_step"}.intersection(case.customer_question_topics)
            and "document_checklist" not in case.customer_question_topics):
        # A separate visa question still gets keyword fallback. Exclude only clauses
        # covered by validated scope/one-step excerpts, never the whole mixed message.
        # Asking for one next item must not trigger the entire checklist by keywords.
        if not case.customer_question_exclusions:
            return False  # Older snapshots have topics but no excerpt scope.
        excerpts = [re.sub(r"\s+", " ", value).strip().casefold()
                    for value in case.customer_question_exclusions]
        clauses = re.split(r"[。！!；;\n，,]|(?<=[?？])\s*|\.(?:\s|$)", text)
        text = "\n".join(clause for clause in clauses if not any(
            excerpt in re.sub(r"\s+", " ", clause).strip().casefold()
            or re.sub(r"\s+", " ", clause).strip().casefold() in excerpt for excerpt in excerpts
        ))
    if not ("document_checklist" in case.customer_question_topics or _explicit_document_list_request(text)):
        return False
    return _general_document_list_request(text) or all((
        case.profile.visit_purpose, case.profile.nationality_country,
        case.profile.application_country, case.profile.occupation_status, case.profile.funding_source,
    ))


def _explicit_document_list_request(text: str) -> bool:
    return bool(re.search(
        r"(?:请|麻烦|想要|需要).{0,25}(?:材料清单|资料清单)|"
        r"(?:需要|准备|提供|还缺)(?:什么|哪些)(?:材料|资料)|"
        r"(?:哪些|什么|哪几)(?:类型|类别|种类)(?:的)?(?:材料|资料)|"
        r"(?:哪些|什么)(?:材料|资料)(?:类型|类别|种类)|"
        r"(?:材料|资料|文件).{0,8}(?:准备|提供|交|要).{0,6}(?:什么|哪些|哪几)"
        r"(?=(?:(?:类|种|份)(?:的)?)?(?:材料|资料|文件|清单)|(?:呢|呀|啊)?(?:[?？。.!！]|\s*$))|"
        r"(?:send|share|show|give).{0,30}(?:document checklist|document list|list of documents)|"
        r"(?:what|which|explain).{0,30}(?:types|kinds|categories) of (?:supporting )?(?:documents|evidence)|"
        r"(?:what|which) documents.{0,20}(?:need|required|prepare)", text, re.I,
    ))


def general_document_list_requested(case: Case) -> bool:
    """Only explicitly general/reference questions change the checklist's framing."""
    return document_list_requested(case) and _general_document_list_request(
        _document_list_request_text(case.latest_customer_message),
    )


def _general_document_list_request(text: str) -> bool:
    clauses = re.split(r"[。！!；;\n]|(?<=[?？])\s*|\.(?:\s|$)", text)
    list_clauses = [clause for clause in clauses if re.search(
        # document_list_requested already requires a validated checklist topic or
        # an explicit list question. Here identify general rather than personal
        # scope, without requiring a particular word order for material types.
        r"清单|材料|资料|文件|证明|还缺(?:什么|哪些)|\b(?:checklist|documents?|evidence)\b",
        clause, re.I,
    )]
    if any(re.search(
        r"(?:按|根据|结合).{0,18}(?:我|本人).{0,12}(?:情况|进度)|"
        r"(?:我|本人).{0,18}(?:还缺|待补|补交)|(?:我这次|我的)(?:申请|材料|档案)|"
        r"\b(?:my|our|own) (?:case|file|application)\b|"
        r"\b(?:I|we) (?:still )?(?:need|am missing|are missing)\b",
        clause, re.I,
    ) for clause in list_clauses if not re.search(
        r"下一步|\bnext (?:step|item|document)\b|\b(?:which|what) document.{0,35}\bnext\b",
        clause, re.I,
    ) and not (customer_requests_next_step(clause) and not _explicit_document_list_request(clause))):
        return False
    return any(re.search(r"一般|通常|常见|参考|概览|概述|\b(?:general|usual|usually|typical|normally|reference|overview)\b",
                         clause, re.I) for clause in list_clauses) or bool(re.search(
        r"(?:只|仅).{0,12}(?:一般信息|一般要求|参考信息)|"
        r"\bonly (?:looking|asking).{0,35}\bgeneral (?:information|requirements)\b", text, re.I,
    ))


def reference_document_label(case: Case, item: Requirement) -> str:
    """Describe reviewed evidence categories, not this applicant's unsatisfied tasks."""
    labels = {
        "passport": ("有效护照或旅行证件 — 用于核对身份及计划停留期间的有效性",
                     "Valid passport or travel document — identity and validity for the planned stay"),
        "status_evidence": ("工作、学习或自雇情况的证明 — 例如在职、在读或经营记录，按实际情况选用",
                            "Work, study or self-employment evidence — employment, enrolment or business records, as applicable"),
        "purpose_evidence": ("访问目的及计划安排 — 说明赴英做什么，未预订的安排不写成已预订",
                             "Visit purpose and intended arrangements — explain the visit without presenting unbooked plans as bookings"),
        "funding_evidence": ("可用资金及来源证明 — 说明谁承担费用、资金从哪里来以及能否使用；预算数字本身不是证明",
                             "Available funds and their source — who pays, where funds come from and access to them; a budget alone is not evidence"),
        "sponsor_evidence": ("如由他人资助：资助内容、资助能力及双方关系的证明；适用时说明资助人的英国身份",
                             "If someone else provides funding: evidence of support, means and the relationship, plus UK status where applicable"),
        "certified_translation": ("如材料不是英文或威尔士文：完整、可核验的翻译",
                                  "For documents not in English or Welsh: a full, verifiable translation"),
        "legal_residence": ("如在非国籍国申请：在申请所在地合法居留的证明",
                            "When applying outside the country of nationality: evidence of lawful residence there"),
    }
    return labels.get(item.id, (item.title, item.title))[0 if case.customer_language == "zh" else 1]


def reply_items(case: Case) -> tuple[list[str], list[str], list[str]]:
    """Only the next few questions; completeness remains enforced by the delivery gate."""
    if case.preparation_paused:
        return [], [], []
    zh = case.customer_language == "zh"
    issues = []
    for issue in case.open_blockers():
        if zh and issue.code == "DATE_CONFLICT":
            evidence = case.active_evidence("invitation_event_end_date")
            end = str(evidence[-1].value) if evidence else "邀请函所列日期"
            issues.append(
                f"行程与邀请函日期不一致：你计划 {case.profile.planned_departure_date} 离英，但活动到 {end} 才结束。请告诉我实际行程，或补一份修正后的邀请函。"
            )
        elif zh and issue.code == "MISSING_CERTIFIED_TRANSLATION":
            names = ", ".join(
                doc.filename
                for doc in case.documents
                if doc.status == DocumentStatus.NEEDS_CERTIFIED_TRANSLATION
            )
            issues.append(f"还缺认证翻译：{names}。请同时保留原文，我会把翻译和原件对应起来。")
        elif zh and "Specimen is not an identity document" in issue.detail:
            names = ", ".join(
                doc.filename for doc in case.documents if doc.id in issue.related_document_ids
            )
            issues.append(
                f"{names} 是你整理的信息摘要，不能代替护照。等方便时，请补护照资料页的清晰扫描或照片；这份摘要不会被算作有效护照。"
            )
        elif zh:
            names = ", ".join(
                doc.filename for doc in case.documents if doc.id in issue.related_document_ids
            )
            issues.append(
                f"{names or '目前这些信息'}还需要人工核对，我暂时不能确认其中的内容。已收到的文件会保留，不用重新发。"
            )
        else:
            issues.append(f"{issue.title}: {issue.detail}")
    question_fields = next_fact_questions(case)
    questions = [_profile_question_text(case, key) for key in question_fields]
    if "current_address" in question_fields and case.profile.current_address:
        questions[question_fields.index("current_address")] = (
            "居住地区已经记下了，还需要能定位到你住处的细节，比如街道、楼栋或宿舍名称，以及适用的门牌、房号。"
            "方便补充一下吗？按当地实际地址写就好。" if zh else
            "Thanks, I've noted the location. Could you add the details that identify your home, "
            "such as the street, building or residence name and any applicable house or room number? "
            "Use the address as it is written locally."
        )
    if {"planned_arrival_date", "planned_departure_date"} <= set(question_fields):
        arrival_index = question_fields.index("planned_arrival_date")
        questions[arrival_index] = (
            "计划哪天到英国、哪天离开？请带上年份；日期没定的话也可以先告诉我。"
            if zh else "What dates are you planning to arrive in and leave the UK? "
            "Please include the year; if you haven't decided yet, just let me know."
        )
        del questions[question_fields.index("planned_departure_date")]
    if {"sponsor_relationship", "sponsor_name"} <= set(question_fields):
        relationship_index = question_fields.index("sponsor_relationship")
        questions[relationship_index] = _sponsor_identity_question(
            case, relationship_also_missing=True,
        )
        del questions[question_fields.index("sponsor_name")]
    # An initial enquiry should not receive the entire form and document checklist at once.
    documents = []
    requested_list = document_list_requested(case)
    paused = case.question_plan == [] and bool(case.pending_question_fields)
    quiet_turn = quiet_preparation_resume(case) or bool(case.latest_customer_message) and not (
        preparation_context_progress(case) or case.latest_document_names
        or customer_requests_next_step(case.latest_customer_message)
    )
    answering_question = bool(case.customer_question_topics or case.customer_answers)
    if general_document_list_requested(case):
        documents = [reference_document_label(case, item) for item in case.requirements if item.blocker]
    elif (requested_list or case.latest_document_names or issues or (
            not quiet_turn and not answering_question
            and ((not questions and not paused) or case.documents)
    )):
        documents = [
            explained_document_label(case, item) if requested_list else document_label(case, item)
            for item in case.requirements
            if item.applicable
            and item.blocker
            and not item.satisfied
            and not (
                item.id == "certified_translation"
                and any(
                    issue.code == "MISSING_CERTIFIED_TRANSLATION" for issue in case.open_blockers()
                )
            )
        ]
    if not requested_list:
        documents = documents[: max(0, 3 - len(issues) - len(questions))]
    if case.next_step_advice is not None and not requested_list:
        # The requested next step is already specific to the current case. Do not
        # append the automatic whole checklist; preserve an explicitly requested list.
        documents = []
    return issues, questions, documents


def change_acknowledgement(case: Case) -> str | None:
    if not case.latest_changes:
        return None
    zh = case.customer_language == "zh"
    if not zh and set(case.latest_changes) == {"estimated_trip_cost_gbp"}:
        value = case.latest_changes["estimated_trip_cost_gbp"]
        if value.isdecimal():
            return f"Thanks—I've updated your total trip budget to £{int(value):,}."
    if not zh and set(case.latest_changes) == {"date_of_birth"}:
        try:
            corrected = date.fromisoformat(case.latest_changes["date_of_birth"])
        except ValueError:
            pass  # Defensive formatting only; never repair or change a stored fact.
        else:
            return f"Thanks—I've corrected your date of birth to {corrected.day} {corrected:%B %Y}."
    if not zh and set(case.latest_changes) == {"date_of_birth", "estimated_trip_cost_gbp"}:
        try:
            corrected = date.fromisoformat(case.latest_changes["date_of_birth"])
            budget = int(case.latest_changes["estimated_trip_cost_gbp"])
        except ValueError:
            pass
        else:
            return (f"Thanks—I've corrected your date of birth to {corrected.day} {corrected:%B %Y} "
                    f"and your total trip budget to £{budget:,}.")
    if set(case.latest_changes) == {"route_confirmed_standard_visitor"}:
        if case.profile.route_confirmed_standard_visitor:
            return (
                "好的，已经按你确认的 Standard Visitor 路线继续准备。"
                if zh else
                "Thanks for confirming the Standard Visitor route. I'll continue on that basis."
            )
        return (
            "好的，先取消之前的 Standard Visitor 路线确认；接下来先重新核对适用路线。"
            if zh else
            "Thanks for clarifying. I've removed the earlier Standard Visitor confirmation; "
            "the route now needs to be checked again before we continue."
        )
    if set(case.latest_changes) == {"occupation_status"}:
        occupation = case.latest_changes["occupation_status"]
        labels = {
            "student": ("目前在读", "you're currently studying", "在读证明", "an enrolment letter"),
            "employed": ("目前在职", "you're currently employed", "在职证明", "an employer letter"),
            "self_employed": ("目前自己经营业务", "you're currently self-employed", "经营情况材料", "business records"),
        }
        if occupation in labels:
            zh_status, en_status, zh_evidence, en_evidence = labels[occupation]
            obsolete = [
                ("student_letter", "之前收到的在读证明", "the enrolment letter already received"),
                ("employment_letter", "之前收到的在职证明", "the employer letter already received"),
            ]
            obsolete_notice = next((
                (zh_text, en_text)
                for kind, zh_text, en_text in obsolete
                if kind != {
                    "student": "student_letter", "employed": "employment_letter",
                    "self_employed": "self_employment_evidence",
                }[occupation]
                and any(document.kind == kind and document.status != DocumentStatus.SUPERSEDED
                        for document in case.documents)
            ), None)
            if zh:
                message = f"明白，已经按你最新说明改为{zh_status}，后面的材料会改用{zh_evidence}来核对。"
                if obsolete_notice:
                    message += f"{obsolete_notice[0]}会保留在记录里，但不再作为你当前身份的证明。"
                return message
            message = f"Thanks for clarifying. I've updated your circumstances to show that {en_status}; we'll use {en_evidence} for the current-status check."
            if obsolete_notice:
                message += f" {obsolete_notice[1].capitalize()} will remain in the case history, but it will no longer count as evidence of your current status."
            return message
    sponsor_fields = {"funding_source", "sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"}
    if "funding_source" in case.latest_changes and set(case.latest_changes) <= sponsor_fields:
        source = case.profile.funding_source
        if zh:
            if source == "self":
                return "明白，这次改为由你自己承担费用，我会按自费安排调整后面的材料。"
            if source == "employer_or_school":
                return f"明白，这次改为由{funding_label(case, language='zh')}承担费用，后面按单位资助安排准备。"
            if source == "personal_sponsor":
                relationship = {
                    "father": "父亲", "mother": "母亲", "parents": "父母",
                    "sister": "姐妹", "brother": "兄弟", "spouse": "配偶",
                    "partner": "伴侣", "friend": "朋友",
                }.get(case.profile.sponsor_relationship or "", "个人资助人")
                return f"明白，这次改为由{relationship}资助，我会按这个安排重新核对材料。"
        else:
            if source == "self":
                return "Thanks for clarifying. You will now pay for the trip yourself, so I'll adjust the preparation to a self-funded case."
            if source == "employer_or_school":
                label = funding_label(case, language="en")
                subject = "your employer or school" if label == "employer or school" else f"the {label}"
                return f"Thanks for clarifying. {subject.capitalize()} will now fund the trip, so I'll adjust the evidence plan to that arrangement."
            if source == "personal_sponsor":
                relationship = (case.profile.sponsor_relationship or "personal sponsor").replace("_", " ")
                return f"Thanks for clarifying. Your {relationship} will now sponsor the trip, so I'll recheck the preparation against that arrangement."
    changes = ("；" if zh else "; ").join(
        f"{fact_label(case, key)}{'：' if zh else ': '}"
        f"{funding_label(case, language=case.customer_language) if key == 'funding_source' and value == case.profile.funding_source else VALUE_LABELS_ZH.get(value, value) if zh else value.replace('_', ' ')}"
        for key, value in case.latest_changes.items()
    )
    if zh:
        return f"好的，已按你说的改为：{changes}。"
    return f"Thanks for clarifying. I've updated {changes}."


def waiting_acknowledgement(case: Case) -> str | None:
    """A narrow receipt for a pure 'I'll reply later'; no case state or reminders change."""
    if (case.latest_changes or case.latest_received_facts or case.latest_document_names
            or case.customer_answers or case.open_blockers()):
        return None
    text = latest_reply_text(case.latest_customer_message).strip()
    if re.fullmatch(
        r"(?:(?:我)?(?:还没|尚未)核对(?:其他)?(?:资料|信息|材料)[，,。.\s]*)?"
        r"(?:我)?(?:晚点|稍后)(?:再)?回复[。.!！\s]*", text,
    ):
        return "好的，等你方便时再回复，我们接着准备。"
    if re.fullmatch(
        r"(?:I (?:haven't|have not) checked (?:the )?(?:other )?(?:details|information|documents) yet[.,]\s*)?"
        r"I(?:'ll| will) (?:reply|get back to you) later[.!\s]*", text, re.I,
    ):
        return "Of course. Reply when you're ready and we'll pick up from there."
    return None


def preparation_control_receipt(case: Case) -> str | None:
    if case.preparation_paused:
        if case.latest_preparation_action != "pause":
            return (
                "我们先保持暂停，之前的资料都在。等你确定要继续时，再告诉我就好。"
                if case.customer_language == "zh" else
                "We'll keep the preparation on hold and retain your earlier details. "
                "Let me know when you've decided you'd like to continue."
            )
        return (
            f"可以，材料准备先暂停，已经收到的{'信息和文件' if case.documents else '信息'}会保留。等你想继续时，直接回复我就好。"
            if case.customer_language == "zh" else
            f"Of course—we can put the preparation on hold. I'll keep the {'details and files' if case.documents else 'details'} you've sent; "
            "just reply when you'd like to pick this up again."
        )
    if case.latest_preparation_action == "resume":
        return (
            f"可以，我们接着准备，之前发过的{'信息和文件' if case.documents else '信息'}不用重发。定稿前，我会把整理后的摘要再发给你核对。"
            if case.customer_language == "zh" else
            f"Of course, let's pick this up again. You don't need to resend your earlier {'details or files' if case.documents else 'details'}. "
            "I'll send you a fresh summary to check before finalising anything."
        )
    return None


def paused_customer_message(case: Case) -> str:
    """A receipt and requested information, never a new intake/document demand."""
    zh = case.customer_language == "zh"
    sections = []
    personal_overview = comprehensive_overview_requested(case, case.latest_customer_message)
    if acknowledgement := change_acknowledgement(case):
        sections.append(acknowledgement)
    if case.latest_document_names:
        names = ("、" if zh else ", ").join(case.latest_document_names)
        sections.append(f"收到 {names} 了，先保存在你的档案里。" if zh
                        else f"I've received {names} and kept it with your case.")
    if (
        not personal_overview
        and not _guidance_already_acknowledges_single_new_fact(case)
        and (context := received_context(case))
    ):
        sections.append(context)
    sections.extend(case.customer_answers)
    if document_list_requested(case) and not personal_overview:
        general_list = general_document_list_requested(case)
        documents = ([reference_document_label(case, item) for item in case.requirements if item.blocker]
                     if general_list else [explained_document_label(case, item) for item in case.requirements
                                          if item.applicable and item.blocker and not item.satisfied])
        if documents:
            sections.append(
                (("一般可以参考以下材料类别；具体适用项取决于申请情况，现在不是要求你提交：\n" if zh else
                  "For general reference, these are common evidence categories, not a request to send documents:\n")
                 if general_list else
                 ("如果之后继续准备，按你目前的情况可以参考这份材料清单，现在不用急着提交：\n" if zh else
                 "For when you decide to continue, this is a preparation list for your current circumstances; "
                 "there's no need to send these now:\n"))
                + "\n".join(f"- {item}" for item in documents)
            )
            sources = list(dict.fromkeys(source for item in case.requirements
                                        if item.applicable and item.blocker and not item.satisfied
                                        for source in item.source_urls))
            if (not wants_no_links(case.latest_customer_message)
                    and (source_block := _official_source_block(case, sources))):
                sections.append(source_block)
    if case.open_blockers():
        sections.append("现有资料里还有待核对的地方，先保留记录，恢复准备后再处理。" if zh else
                        "Some existing details still need checking. Those checks remain on file for when we resume.")
    if receipt := preparation_control_receipt(case):
        sections.append(receipt)
    return "\n\n".join(sections)


def blocked_customer_message(case: Case) -> str:
    if case.preparation_paused:
        return paused_customer_message(case)
    if acknowledgement := waiting_acknowledgement(case):
        return acknowledgement
    zh = case.customer_language == "zh"
    name = case.profile.full_name
    issues, questions, documents = reply_items(case)
    requested_list = document_list_requested(case)
    general_list = general_document_list_requested(case) if requested_list else False
    personal_overview = (
        not general_list
        and comprehensive_overview_requested(case, case.latest_customer_message)
    )
    greeting = (
        (f"{name}，你好。" if name else "你好。")
        if zh
        else (f"Hello {name}," if name else "Hello,")
    )
    acknowledgements = []
    if acknowledgement := change_acknowledgement(case):
        acknowledgements.append(acknowledgement)
    if case.latest_document_names:
        names = "、".join(case.latest_document_names) if zh else ", ".join(case.latest_document_names)
        acknowledgements.append(
            f"收到你发来的 {names} 了。"
            if zh
            else f"I've received {names}."
        )
    if (
        not personal_overview
        and not _guidance_already_acknowledges_single_new_fact(case)
        and (context := received_context(case))
    ):
        if acknowledgements:
            context = context.removeprefix("了解了，").removeprefix("Thanks, ")
            context = context[0].upper() + context[1:]
        acknowledgements.append(context)
    if (not acknowledgements and not case.customer_answers and not issues
            and re.search(
                r"(?:还没|尚未|还没有).{0,8}(?:核对|检查|看过).{0,8}(?:摘要|信息)|"
                r"(?:haven't|have not).{0,12}(?:checked|reviewed).{0,12}(?:summary|details)",
                latest_reply_text(case.latest_customer_message), re.I,
            )):
        acknowledgements.append(
            "先不用确认，我们把还缺的信息补上，再一起核对。"
            if zh else "There's no need to confirm yet. Let's fill in the missing details first, "
            "then you can review the summary."
        )
    if acknowledgements:
        intro = ("" if zh else " ").join(acknowledgements)
    elif (case.pending_question_fields and len(questions) == 1
          and customer_requests_next_step(case.latest_customer_message)):
        intro = "可以，我们先补一项。" if zh else "Sure. Let's take one detail at a time."
    else:
        missing_context = [label for field, label in (
            ("visit_purpose", "出行目的"), ("application_country", "申请地点"),
        ) if not getattr(case.profile, field)]
        intro = (
            ("好的，等你方便补充资料时，我们再接着准备。" if zh
             else "Of course. We can pick this up when you're ready to add the remaining details.")
            if case.question_plan == [] and case.pending_question_fields
            else (("具体要准备哪些材料，还要结合你的" + "和".join(missing_context) + "来安排。"
                   if missing_context else "我们接着把申请需要的信息整理好。") if zh
                  else "We can work through this together; you don't need to have every document ready at once.")
        )
    # A concrete acknowledgement already opens the reply; do not restart the introduction.
    contextual = bool(acknowledgements) or bool(case.pending_question_fields and len(questions) == 1
        and customer_requests_next_step(case.latest_customer_message))
    sections = ([intro] if contextual or (case.question_plan == [] and case.pending_question_fields
                                        and not case.customer_answers and not documents
                                        and not personal_overview)
                else ([] if case.customer_answers or documents or personal_overview else [greeting, intro]))
    if not questions and not issues and not documents and not case.customer_answers and not acknowledgements:
        # Do not announce more questions when this turn has none, or restart a greeting
        # before the only useful response: acknowledgement of explicitly undecided dates.
        sections = [] if case.latest_deferred_fields else [
            "好的，已有资料会保留。有新安排或材料时，直接接着回复就好。"
            if zh else "Your existing details will stay on file. Just reply when you have new plans or documents to add."
        ]
        if (request := reply_style_request(case.latest_customer_message)) is not None:
            scope = ("这次" if request[2] else "之后") if zh else ("this reply" if request[2] else "future replies")
            sections = [
                f"好的，{scope}我会简短说重点。" if zh and request[0] == "brief" else
                f"好的，{scope}需要展开的地方我会解释清楚。" if zh else
                f"Of course—I'll keep {scope} brief." if request[0] == "brief" else
                f"Of course—I'll give more explanation where it helps in {scope}."
            ]
    if not personal_overview and case.latest_deferred_fields and (
        not case.customer_answers or case.proactive_guidance_offered or case.latest_received_facts or case.latest_changes
    ):
        if {"planned_arrival_date", "planned_departure_date"}.intersection(case.latest_deferred_fields):
            sections.append(
                ("日期先留空，确定后再补；现在可以先准备其他材料。" if zh else
                 "We'll leave the dates open and add them when decided; other preparation can start now.")
                if prefers_brief_reply(case) else
                "没问题，日期先留空。材料准备阶段先不追问日期；正式提交前再填写并统一核对预计行程。"
                if zh else "No problem—I'll leave the dates open for now. I will not keep asking you for the "
                "dates while we prepare. Add and cross-check the intended itinerary before submitting the form."
            )
        if "uk_accommodation" in case.latest_deferred_fields:
            sections.append(
                "住宿安排先记为待确认，不需要为了申请现在就订酒店；正式提交前再按实际计划更新。"
                if zh else "I have marked the accommodation as undecided. You do not need to book a hotel now just for the application; update the intended arrangement before submitting."
            )
        if "current_address_duration" in case.latest_deferred_fields:
            sections.append("现住址住了多久先留待核实，不用猜；可以看看租约或搬家记录，想起后再补。" if zh else
                            "We'll leave the time at your current address for checking. Don't guess; a tenancy agreement or moving records may help when you return to it.")
    elif (case.deferred_fields and not questions and not issues and not documents and not case.customer_answers
          and not case.pending_question_fields
          and not quiet_preparation_resume(case)
          and customer_requests_next_step(case.latest_customer_message)):
        sections.append(
            "日期确定后再告诉我就好，已经提供的信息会保留。具体日期补齐前，还不能完成最终核对。"
            if zh else "Let me know when your dates are decided; the details you've already provided are retained. "
            "The final check will remain on hold until the dates are supplied."
        )
    if case.latest_preparation_action == "resume" and (receipt := preparation_control_receipt(case)):
        if (quiet_preparation_resume(case) and not acknowledgements and not questions
                and not issues and not documents and not case.customer_answers):
            sections = []
        sections.insert(0, receipt)
    visible_customer_answers = [
        answer for answer in case.customer_answers
        if not (
            case.next_step_advice is not None
            and case.next_step_advice.kind == "question"
            and answer == case.next_step_advice.message
            and questions
        )
    ]
    sections.extend(visible_customer_answers)
    if issues:
        sections.append(
            (
                "我核对时发现下面这些地方需要你补充或确认：\n"
                if zh
                else "These points still need attention:\n"
            )
            + "\n".join(f"- {item}" for item in issues)
        )
    if personal_overview:
        # WorkflowService has already added the source-fresh, case-aware
        # overview to customer_answers. Do not append the terse blocker list.
        pass
    elif documents:
        sections.append(
            (("一般可以参考以下材料类别；具体适用项取决于申请情况：\n" if zh else
              "For general reference, common evidence categories include:\n") if general_list else (
                "可以，我先按你目前的情况把材料列清楚；有哪份暂时拿不到，直接告诉我就行：\n"
                if zh
                else "Here is the document plan for your current circumstances. Tell me if any item is difficult to obtain:\n"
            ))
            + "\n".join(f"- {item}" for item in documents)
        )
        if requested_list:
            if not general_list and not case.proactive_guidance_offered:
                sections.append(
                    ("建议从这里开始：" if zh else "A practical place to start: ")
                    + first_practical_action(case)
                )
            sections.append(
                ("这是参考概览，不是要求你现在补交材料，也不是所有申请人通用的强制清单。" if zh else
                  "This is a reference overview, not a request for you to send documents or a universal mandatory checklist.")
                 if general_list else ("这是按你的情况列出的待补材料，不是所有申请人通用的强制清单。"
                "如果行程或资助情况有变化，我会再调整。"
                if zh else "This is a preparation list for your circumstances, not a universal "
                "mandatory checklist. I'll adjust it if your travel or funding arrangements change.")
            )
            if not wants_no_links(case.latest_customer_message):
                sources = list(dict.fromkeys(
                    source for item in case.requirements
                    if item.applicable and item.blocker and not item.satisfied
                    for source in item.source_urls
                ))
                if source_block := _official_source_block(case, sources):
                    sections.append(source_block)
    if questions:
        # Keep the grounded questions verbatim, but don't turn a short conversation
        # into a labelled form. Longer questions get their own paragraph.
        separator = "" if zh else " "
        joined = separator.join(questions)
        question_text = (joined if len(joined) <= (100 if zh else 240)
                         else "\n\n".join(questions))
        follow_up = (
            _single_question_context(case, next_fact_questions(case))
            if not prefers_brief_reply(case) and len(questions) == 1 and (
                case.latest_received_facts
                or case.latest_changes
                or case.next_step_advice is not None and case.next_step_advice.kind == "question"
                or visible_customer_answers
                or documents
                or issues
            )
            else ""
        )
        if follow_up:
            sections.append(follow_up + "\n" + question_text)
        elif (not prefers_brief_reply(case) and len(questions) == 1
              and (visible_customer_answers or documents or issues)):
            lead = (
                "我接下来会按你的答案把这份清单改成个人版本。先确认一个关键点："
                if zh else
                "I'll tailor the next step to your answer. One key point first:"
            )
            sections.append(lead + "\n" + question_text)
        else:
            sections.append(question_text)
    return "\n\n".join(sections)


def confirmation_message(case: Case, *, profile_only: bool = False) -> str:
    if case.preparation_paused:
        return paused_customer_message(case)
    zh = case.customer_language == "zh"
    intro = (
        "我把目前的信息整理在下面，请看看有没有记错或遗漏。"
        if zh
        else "I've brought your details together below. Please check that I've understood them correctly."
    )
    rows = []
    for field, value in case.profile.model_dump(mode="json").items():
        if value is not None and not (field == "nationality" and case.profile.nationality_country):
            display = str(value)
            if isinstance(value, bool):
                display = ("是" if value else "否") if zh else ("Yes" if value else "No")
            elif field == "funding_source":
                display = funding_label(case, language=case.customer_language)
            elif zh:
                display = VALUE_LABELS_ZH.get(display, display)
            elif field in {"funding_source", "occupation_status", "visit_purpose"}:
                display = display.replace("_", " ")
            rows.append(
                f"- {fact_label(case, field)}：{display}"
                if zh
                else f"- {fact_label(case, field)}: {display}"
            )
    text = ("\n\n".join(case.customer_answers) + "\n\n" if case.customer_answers else "")
    if case.latest_preparation_action == "resume" and (receipt := preparation_control_receipt(case)):
        text = receipt + "\n\n" + text
    if case.application_records is not None:
        from visa_agent.domain.application_records import application_record_rows

        rows.extend(application_record_rows(case.application_records, case.customer_language))
    text += intro + "\n\n" + ("资料摘要\n" if zh else "FACTS SUMMARY\n") + "\n".join(rows)
    if not profile_only:
        text += "\n\n" + ("这次整理使用的材料\n" if zh else "CURRENT DOCUMENTS\n")
        text += "\n".join(
            f"- {doc.filename}"
            for doc in case.documents
            if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
        )
    text += (
        "\n\n麻烦核对一下，尤其是姓名和日期。都准确的话，告诉我已核对无误；有哪项不对，直接告诉我怎么改。"
        if zh
        else "\n\nIf everything is correct, you can simply reply 'Everything is correct, please proceed.' Otherwise, tell me what needs changing."
    )
    if profile_only:
        text += (
            "\n确认后，我们再继续准备所需材料。"
            if zh
            else "\nOnce confirmed, we'll continue with the supporting documents."
        )
    else:
        text += (
            "\n确认后，我再把这些资料整理好供顾问复核。这里是申请材料准备，不会替你递交签证申请，也不代表签证获批。"
            if zh
            else "\nAfter your confirmation, I'll assemble the pack for human review. It is not an approval prediction or a submitted application."
        )
    return text
