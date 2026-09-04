"""Customer-facing conversation rules; no provider or mailbox dependencies."""

from __future__ import annotations

import hashlib
import json
import re

from visa_agent.domain.models import Case, DocumentStatus
from visa_agent.domain.rules import required_profile_facts


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
        and (
            getattr(case.profile, field) is None
            or (field == "route_confirmed_standard_visitor" and not getattr(case.profile, field))
        )
    ]
    actionable = [field for field in missing if field not in case.deferred_fields]
    return (actionable or missing)[: max(0, 3 - len(case.open_blockers()))]


def update_deferred_questions(case: Case, body: str) -> None:
    """Defer unanswered dates, not requirements or previously supplied facts."""
    case.latest_deferred_fields = []
    if re.search(
        r"(?:日期|时间|行程).{0,8}(?:还没|尚未|未|没有)(?:定|确定|决定)|"
        r"(?:haven't|have not).{0,15}(?:decided|fixed).{0,15}dates|"
        r"dates.{0,12}(?:not|aren't).{0,8}(?:set|fixed|decided)", body, re.I
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
        received = [values[facts[key]] for key, values in phrases.items()
                    if facts.get(key) in values]
        return "Thanks, " + " and ".join(received[:2]) + "." if received else ""
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
    if facts.get("funding_source") in funding:
        parts.append(funding[facts["funding_source"]])
    return "了解了，" + "，".join(parts[:2]) + "。" if parts else ""


DOCUMENT_LABELS_ZH = {
    "passport": "有效护照或旅行证件",
    "status_evidence": "工作、学习或自雇证明",
    "purpose_evidence": "访问目的的证明材料",
    "funding_evidence": "资金及资金来源证明",
    "legal_residence": "在申请所在地合法居留的证明",
    "sponsor_evidence": "资助人的相关证明",
    "certified_translation": "非英文或威尔士文材料的认证翻译",
}
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
}


def reply_items(case: Case) -> tuple[list[str], list[str], list[str]]:
    """Only the next few questions; completeness remains enforced by the delivery gate."""
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
    if not questions or case.documents:
        documents = [
            DOCUMENT_LABELS_ZH.get(item.id, item.title) if zh else item.title
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
    documents = documents[: max(0, 3 - len(issues) - len(questions))]
    return issues, questions, documents


def change_acknowledgement(case: Case) -> str | None:
    if not case.latest_changes:
        return None
    zh = case.customer_language == "zh"
    changes = ("；" if zh else "; ").join(
        f"{fact_label(case, key)}{'：' if zh else ': '}"
        f"{VALUE_LABELS_ZH.get(value, value) if zh else value.replace('_', ' ')}"
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


def blocked_customer_message(case: Case) -> str:
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
    if acknowledgements:
        intro = ("" if zh else " ").join(acknowledgements)
    else:
        intro = (
            "收到，我再了解一下你的情况。"
            if zh
            else "We can work through this together; you don't need to have every document ready at once."
        )
    # A concrete acknowledgement already opens the reply; do not restart the introduction.
    contextual = bool(case.latest_changes or case.latest_document_names or received_context(case))
    sections = [intro] if contextual else [greeting, intro]
    if case.latest_deferred_fields:
        sections.append(
            "日期先留空，等你确定后再补。我们先整理其他信息。"
            if zh else "We can leave the dates open for now and collect the other details first."
        )
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
    if questions:
        # Keep the grounded questions verbatim, but don't turn a short conversation
        # into a labelled form. Longer questions get their own paragraph.
        separator = "" if zh else " "
        joined = separator.join(questions)
        sections.append(joined if len(joined) <= (100 if zh else 240)
                        else "\n\n".join(questions))
    if documents:
        sections.append(
            (
                "接下来还需要这些材料；有哪份暂时拿不到，也可以直接告诉我：\n"
                if zh
                else "We'll also need these documents. Let me know if any are difficult to obtain:\n"
            )
            + "\n".join(f"- {item}" for item in documents)
        )
    return "\n\n".join(sections)


def confirmation_message(case: Case, *, profile_only: bool = False) -> str:
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
            elif zh:
                display = VALUE_LABELS_ZH.get(display, display)
            elif field in {"funding_source", "occupation_status", "visit_purpose"}:
                display = display.replace("_", " ")
            rows.append(
                f"- {fact_label(case, field)}：{display}"
                if zh
                else f"- {fact_label(case, field)}: {display}"
            )
    text = intro + "\n\n" + ("资料摘要\n" if zh else "FACTS SUMMARY\n") + "\n".join(rows)
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
