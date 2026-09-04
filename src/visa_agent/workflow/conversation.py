"""Customer-facing conversation rules; no provider or mailbox dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from visa_agent.domain.models import Case, DocumentStatus, Requirement
from visa_agent.domain.rules import profile_fact_complete, required_profile_facts
from visa_agent.workflow.document_purpose import is_document_purpose_question
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
        "planned_arrival_date",
        "planned_departure_date",
        "full_name",
        "date_of_birth",
        "uk_accommodation",
        "estimated_trip_cost_gbp",
        "annual_income_gbp",
        "current_address",
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
    actionable = [field for field in missing if field not in case.deferred_fields]
    if case.question_plan is not None:
        return [field for field in case.question_plan if field in actionable]
    question_budget = 2 if case.customer_answers else 3
    return actionable[: max(0, question_budget - len(case.open_blockers()))]


def customer_requests_next_step(body: str) -> bool:
    """Permission to resume questions only, never consent to a summary or delivery."""
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", text)
    text = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", text)
    # A separate date deferral must not veto a current request to prepare other items.
    # Keep comma-linked conditions together: 'If ..., please continue' is not consent.
    for clause in re.split(r"[。！!；;\n]|(?<=[?？])\s*|\.(?:\s|$)", text):
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
            r"\bwhat.{0,15}(?:still missing|else do you need)\b", clause, re.I,
        ):
            return True
    return False


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


def update_deferred_questions(case: Case, body: str) -> None:
    """Defer unanswered dates, not requirements or previously supplied facts."""
    case.latest_deferred_fields = []
    body = latest_reply_text(body)
    broad_trip_without_plan = bool(
        re.search(r"(?:今年|明年).{0,4}(?:上半年|下半年)", body)
        and re.search(r"(?:^|[，,。])\s*(?:我)?(?:还没(?:有)?|尚未)(?:具体|详细)(?:的)?(?:规划|计划|安排|行程)", body)
    )
    if broad_trip_without_plan or re.search(
        r"(?<!出生)(?:日期|时间|行程).{0,8}(?:还没|尚未|未|没有|没)(?:定|确定|决定)|"
        r"(?:还没|尚未|没有|没)(?:确定|定下|决定)(?:具体的?|确切的?)?(?:出行|旅行)?日期|"
        r"(?:haven't|have not).{0,15}(?:decided|fixed).{0,15}dates|"
        r"dates.{0,12}(?:not|aren't).{0,8}(?:set|fixed|decided)|"
        r"dates.{0,8}(?:undecided|unknown)|"
        r"(?:don't|do not) know.{0,12}(?:travel |trip )?dates", body, re.I
    ):
        for field in ("planned_arrival_date", "planned_departure_date"):
            if getattr(case.profile, field) is None:
                case.latest_deferred_fields.append(field)
                if field not in case.deferred_fields:
                    case.deferred_fields.append(field)
    case.deferred_fields = [field for field in case.deferred_fields if getattr(case.profile, field) is None]


def received_context(case: Case) -> str:
    facts = case.latest_received_facts
    if case.customer_language != "zh":
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
        received = [values[facts[key]] for key, values in phrases.items()
                    if facts.get(key) in values]
        if received:
            return "Thanks, " + " and ".join(received[:2]) + "."
        recorded = []
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
    if facts.get("funding_source") in funding:
        parts.append(funding[facts["funding_source"]])
    if parts:
        return "了解了，" + "，".join(parts[:2]) + "。"
    recorded = []
    if "date_of_birth" in facts:
        recorded.append("生日")
    if "uk_accommodation" in facts:
        recorded.append("计划住宿")
    if "estimated_trip_cost_gbp" in facts:
        recorded.append("旅行预算")
    if "planned_arrival_date" in facts or "planned_departure_date" in facts:
        recorded.append("行程日期")
    return "你提供的" + "、".join(recorded) + "已记下。" if recorded else ""


DOCUMENT_LABELS_ZH = {
    "passport": "有效护照或旅行证件",
    "status_evidence": "工作、学习或自雇证明",
    "purpose_evidence": "访问目的的证明材料",
    "funding_evidence": "资金及资金来源证明",
    "legal_residence": "在申请所在地合法居留的证明",
    "sponsor_evidence": "资助人的相关证明",
    "certified_translation": "非英文或威尔士文材料的认证翻译",
}


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
    "application_country": "你准备在哪个国家或地区递交申请？",
    "nationality_country": "你持哪个国家的护照？",
    "visit_purpose": "这次去英国主要是旅游、探亲访友，还是参加商务活动或会议？",
    "planned_arrival_date": "计划哪天抵达英国？请带上年份；没定下来也可以先告诉我。",
    "planned_departure_date": "计划哪天离开英国？请带上年份；没定下来也可以先告诉我。",
    "full_name": "方便告诉我护照上的姓名吗？",
    "date_of_birth": "你的出生日期是什么？请写完整年月日。",
    "occupation_status": "你目前在工作、读书，还是自己经营业务？",
    "funding_source": "这次旅行的费用由你自己承担，还是有人或单位资助？",
    "uk_accommodation": "在英国准备住哪里？还没确定的话也可以直接说。",
    "estimated_trip_cost_gbp": "这趟旅行大约打算花多少英镑？先给一个估计就好。",
    "annual_income_gbp": "你目前有收入吗？有的话，大约每年多少英镑？没有收入也可以直接说明。",
    "current_address": "你目前实际居住的地址是什么？这里需要的是住址，不是工作地点。",
    "route_confirmed_standard_visitor": "我们目前只支持 Standard Visitor 材料准备。你是否已确认按这一路线准备？不确定的话先告诉我。",
}
QUESTION_TEXT_EN = {
    "application_country": "Which country or territory will you apply from?",
    "nationality_country": "Which country's passport do you hold?",
    "visit_purpose": "What is the main reason for your visit to the UK?",
    "planned_arrival_date": "When would you like to arrive in the UK, including the year? It's fine to say if this isn't decided yet.",
    "planned_departure_date": "When would you like to leave the UK, including the year? It's fine to say if this isn't decided yet.",
    "full_name": "What is your name as it appears in your passport?",
    "date_of_birth": "What is your full date of birth?",
    "occupation_status": "Are you currently employed, studying or self-employed?",
    "funding_source": "Who will pay for the trip?",
    "uk_accommodation": "Where are you planning to stay in the UK? It's fine if you haven't decided yet.",
    "estimated_trip_cost_gbp": "Roughly how much do you expect the trip to cost in pounds? An estimate is fine.",
    "annual_income_gbp": "Do you currently have an income? If so, roughly how much per year in pounds? It's fine to say if you have none.",
    "current_address": "What is your current home address? This will be needed for the application form, rather than your workplace address.",
}


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
    questions = [
        QUESTION_TEXT_ZH.get(key, fact_label(case, key))
        if zh
        else QUESTION_TEXT_EN.get(key, f"Could you tell me your {fact_label(case, key).lower()}?")
        for key in question_fields
    ]
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
    if not zh and set(case.latest_changes) == {"occupation_status"}:
        occupation = {
            "employed": "you're employed", "student": "you're studying",
            "self_employed": "you're self-employed",
        }.get(case.latest_changes["occupation_status"])
        if occupation:
            return f"Thanks for clarifying—I've noted that {occupation}."
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
    if acknowledgement := change_acknowledgement(case):
        sections.append(acknowledgement)
    if case.latest_document_names:
        names = ("、" if zh else ", ").join(case.latest_document_names)
        sections.append(f"收到 {names} 了，先保存在你的档案里。" if zh
                        else f"I've received {names} and kept it with your case.")
    if context := received_context(case):
        sections.append(context)
    sections.extend(case.customer_answers)
    if document_list_requested(case):
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
            sections.append("\n".join(f"GOV.UK: {source}" for source in sources))
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
    if (zh and not issues and not documents and not case.customer_answers
            and not case.latest_document_names and not case.latest_changes
            and set(next_fact_questions(case)) == {
                "visit_purpose", "nationality_country", "application_country"
            }):
        return (
            (f"{name}，你好，可以的。" if name else "你好，可以的。")
            + "\n\n具体要准备哪些材料，得先看你的出行目的和申请地点。"
            "你这次去英国是旅游、探亲，还是有其他安排？"
            "另外，你持哪个国家的护照，打算从哪里申请？"
            "\n\n了解这些后，我再按你的情况帮你梳理材料清单。"
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
    if context := received_context(case):
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
        intro = (
            ("好的，等你方便补充资料时，我们再接着准备。" if zh
             else "Of course. We can pick this up when you're ready to add the remaining details.")
            if case.question_plan == [] and case.pending_question_fields
            else ("收到，我再了解一下你的情况。" if zh
                  else "We can work through this together; you don't need to have every document ready at once.")
        )
    # A concrete acknowledgement already opens the reply; do not restart the introduction.
    contextual = bool(acknowledgements) or bool(case.pending_question_fields and len(questions) == 1
        and customer_requests_next_step(case.latest_customer_message))
    sections = ([intro] if contextual or (case.question_plan == [] and case.pending_question_fields
                                        and not case.customer_answers and not documents)
                else ([] if case.customer_answers or documents else [greeting, intro]))
    if not questions and not issues and not documents and not case.customer_answers and not acknowledgements:
        # Do not announce more questions when this turn has none, or restart a greeting
        # before the only useful response: acknowledgement of explicitly undecided dates.
        sections = [] if case.latest_deferred_fields else [
            "好的，已有资料会保留。有新安排或材料时，直接接着回复就好。"
            if zh else "Your existing details will stay on file. Just reply when you have new plans or documents to add."
        ]
    if case.latest_deferred_fields and (
        not case.customer_answers or case.proactive_guidance_offered or case.latest_received_facts or case.latest_changes
    ):
        sections.append(
            "日期先留空，等你确定后再补。"
            if zh else "We can leave the dates open for now; let me know when you've decided."
        )
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
    sections.extend(case.customer_answers)
    if issues:
        sections.append(
            (
                "我核对时发现下面这些地方需要你补充或确认：\n"
                if zh
                else "These points still need attention:\n"
            )
            + "\n".join(f"- {item}" for item in issues)
        )
    if documents:
        general_list = general_document_list_requested(case)
        sections.append(
            (("一般可以参考以下材料类别；具体适用项取决于申请情况：\n" if zh else
              "For general reference, common evidence categories include:\n") if general_list else (
                "接下来还需要这些材料；有哪份暂时拿不到，也可以直接告诉我：\n"
                if zh
                else "We'll also need these documents. Let me know if any are difficult to obtain:\n"
            ))
            + "\n".join(f"- {item}" for item in documents)
        )
        if document_list_requested(case):
            sections.append(
                ("这是参考概览，不是要求你现在补交材料，也不是所有申请人通用的强制清单。" if zh else
                  "This is a reference overview, not a request for you to send documents or a universal mandatory checklist.")
                 if general_list else ("这是按你的情况列出的待补材料，不是所有申请人通用的强制清单。"
                "如果行程或资助情况有变化，我会再调整。"
                if zh else "This is a preparation list for your circumstances, not a universal "
                "mandatory checklist. I'll adjust it if your travel or funding arrangements change.")
            )
            if not re.search(
                r"(?:不用|不需要|不要|无需|别).{0,12}(?:链接|网址|官网)|"
                r"\b(?:no|without) links?\b|\b(?:don't|do not).{0,12}(?:send|need).{0,12}(?:links?|websites?)",
                latest_reply_text(case.latest_customer_message), re.I,
            ):
                sources = list(dict.fromkeys(
                    source for item in case.requirements
                    if item.applicable and item.blocker and not item.satisfied
                    for source in item.source_urls
                ))
                sections.append("\n".join(f"GOV.UK: {source}" for source in sources))
    if questions:
        # Keep the grounded questions verbatim, but don't turn a short conversation
        # into a labelled form. Longer questions get their own paragraph.
        separator = "" if zh else " "
        joined = separator.join(questions)
        sections.append(joined if len(joined) <= (100 if zh else 240)
                        else "\n\n".join(questions))
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
