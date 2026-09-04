"""Customer-facing conversation rules; no provider or mailbox dependencies."""

from __future__ import annotations

import hashlib
import json
import re

from visa_agent.domain.models import Case, DocumentStatus
from visa_agent.domain.rules import required_profile_facts


def latest_reply_text(body: str) -> str:
    """Exclude quoted history from extraction and confirmation, preserving the raw event elsewhere."""
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
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
                r"(?:(?:我)?(?:确认)?(?:以上|上述|这些|所有|全部|最终)?(?:的)?(?:资料|信息|摘要|材料清单|内容)?(?:都)?(?:正确|无误|没问题|核对无误)[，,。.!！\s]*(?:(?:请|可以|麻烦)(?:帮我)?(?:继续|整理|准备|发给我|生成材料包)[，,。.!！\s]*)?"
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
        "planned_arrival_date",
        "planned_departure_date",
        "full_name",
        "date_of_birth",
        "occupation_status",
        "funding_source",
        "uk_accommodation",
        "estimated_trip_cost_gbp",
        "annual_income_gbp",
        "current_address",
        "has_serious_history",
        "route_confirmed_standard_visitor",
    ]
    required = required_profile_facts(case)
    ordered = priority + sorted(required - set(priority))
    return [
        field
        for field in ordered
        if field in required
        and (
            getattr(case.profile, field) is None
            or (field == "route_confirmed_standard_visitor" and not getattr(case.profile, field))
        )
    ][:3]


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
        elif zh:
            names = ", ".join(
                doc.filename for doc in case.documents if doc.id in issue.related_document_ids
            )
            issues.append(
                f"需要顾问核对的材料：{names or '目前的信息有冲突'}。我还不能可靠确认其中的内容，暂时不会把它计为已完成。"
            )
        else:
            issues.append(f"{issue.title}: {issue.detail}")
    questions = [
        QUESTION_TEXT_ZH.get(key, fact_label(case, key))
        if zh
        else QUESTION_TEXT_EN.get(key, f"Could you tell me your {fact_label(case, key).lower()}?")
        for key in next_fact_questions(case)
    ]
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
    return issues, questions, documents


def blocked_customer_message(case: Case) -> str:
    zh = case.customer_language == "zh"
    name = case.profile.full_name
    greeting = (
        (f"{name}，你好。" if name else "你好。")
        if zh
        else (f"Hello {name}," if name else "Hello,")
    )
    if case.latest_document_names:
        intro = (
            f"收到你这次发来的 {len(case.latest_document_names)} 份材料了。"
            if zh
            else f"Thanks for the {len(case.latest_document_names)} documents you've sent."
        )
    else:
        intro = (
            "可以先聊，我们一步步准备，不需要一次把所有资料凑齐。"
            if zh
            else "We can work through this together; you don't need to have every document ready at once."
        )
    issues, questions, documents = reply_items(case)
    sections = [greeting, intro]
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
        sections.append(
            ("先帮我确认这几项就好：\n" if zh else "Could you help me with these details first?\n")
            + "\n".join(f"- {item}" for item in questions)
        )
    if documents:
        sections.append(
            (
                "接下来还需要这些材料；有哪份暂时拿不到，也可以直接告诉我：\n"
                if zh
                else "We'll also need these documents. Let me know if any are difficult to obtain:\n"
            )
            + "\n".join(f"- {item}" for item in documents)
        )
    sections.append(
        "你直接回复这封邮件就可以。我会先核对信息，资料确认之前不会交付最终材料包。"
        if zh
        else "You can reply here in your own words. I'll check the details before assembling the final pack."
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
        "\n\n如果都正确，直接回复“资料都正确，可以继续”就好；有变动的话，告诉我具体改哪一项。"
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
            "\n收到你的确认后，我再整理材料包供顾问复核；这不代表签证获批，也不会替你提交申请。"
            if zh
            else "\nAfter your confirmation, I'll assemble the pack for human review. It is not an approval prediction or a submitted application."
        )
    return text
