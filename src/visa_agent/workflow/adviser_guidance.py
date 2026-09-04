"""Small, source-reviewed preparation steps selected from the current case, not legal decisions."""

import re
from datetime import date

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.workflow.conversation import customer_requests_next_step, latest_reply_text
from visa_agent.workflow.customer_questions import _active_clauses

APPLICATION_URL = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
ROUTE_CHECK_URL = "https://www.gov.uk/check-uk-visa"
DOCUMENTS_URL = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
CHECKED_AT = date(2026, 9, 4)
REVIEW_AFTER = date(2026, 10, 4)


def preparation_guidance(case: Case, today: date, sent_topics: set[str]) -> list[tuple[str, str]]:
    """Offer at most two useful next steps; explicit answers and problems take priority.

    Topic IDs are versioned. Only topics in actually sent replies count as already shared.
    These suggestions cannot change requirements, evidence acceptance, facts or consent.
    """
    if (not CHECKED_AT <= today <= REVIEW_AFTER or case.customer_answers or case.customer_question_topics
            or case.open_blockers()
            or case.latest_document_names or case.status != CaseStatus.DRAFT):
        return []
    current = latest_reply_text(case.latest_customer_message)
    unquoted = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", current)
    unquoted = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", unquoted)
    # Preserve comma-linked conditions and negations before selecting an affirmative
    # preparation request. A separate later-date statement does not veto that request.
    text = "\n".join(clause for clause in _active_clauses(current, split_commas=False)
                     if not re.search(
                         r"如果|假如|暂时|先不|不想|不需要|不能|尚未|还没|"
                         r"\b(?:if|not|later|tomorrow|maybe|cannot|never|stop)\b|"
                         r"(?:don|can|won|wouldn|couldn|shouldn)['’]t", clause, re.I,
                     ))
    # An unshared topic is not by itself a reason to send it now. Proactive advice
    # needs progress in intake or a current preparation request; existing profile
    # data must not turn unrelated chatter or control instructions into a brochure.
    if not (case.latest_received_facts or case.latest_changes or customer_requests_next_step(current)
            or re.search(
                r"(?:想|准备|打算|需要).{0,6}(?:申请|办理).{0,6}(?:英国|签证)|"
                r"(?:准备|整理|收集|补充).{0,8}(?:材料|资料|文件)|"
                r"(?:材料|资料|文件).{0,8}(?:准备|整理|收集)|"
                r"\b(?:prepar\w*|collect\w*|gather\w*|organis\w*|organiz\w*).{0,24}"
                r"(?:documents?|evidence|application)|"
                r"\b(?:want|need|planning) to apply.{0,20}(?:UK|visa)\b",
                text, re.I,
            )):
        return []
    if (re.search(r"(?:不用|不需要|不要|无需|不想).{0,18}(?:链接|官网|流程|材料|建议|说明)|"
                  r"(?:don't|do not|no need|stop).{0,30}(?:link|website|guidance|explain|advice)|"
                  r"\bno links?\b", unquoted, re.I)
            or re.fullmatch(r"(?:谢谢|好的|收到|了解|thanks|thank you|okay|ok)[。.!！\s]*", text, re.I)):
        return []
    profile = case.profile
    zh = case.customer_language == "zh"
    if profile.visit_purpose not in {
        "tourism", "family_or_friends", "business", "conference"
    }:
        if profile.visit_purpose is None and "route_orientation_v1" not in sent_topics:
            return [("route_orientation_v1", (
                "先确认适合的申请类别，再准备材料。是否需要签证或 ETA，要结合护照和赴英目的判断；"
                "可以先用下面的官方查询入口查看。我会根据你的情况梳理准备步骤，不用现在就把所有个人资料发来。"
                if zh else "Let's establish the right application route before collecting documents. "
                "Whether you need a visa or ETA depends on your passport and purpose; the official "
                "checker below is a starting point. We can then work out your preparation steps "
                "without asking you to send every personal detail at once."
            ) + "\nGOV.UK: " + ROUTE_CHECK_URL)]
        return []
    result: list[tuple[str, str]] = []
    if "application_overview_v1" not in sent_topics:
        result.append(("application_overview_v1", (
            "先给你申请入口：GOV.UK 页面里的 Apply now 可以开始在线填表，未填完的表格可以保存。"
            "如果需要 Standard Visitor 签证，流程是在线申请、预约签证申请中心，再按要求完成身份核验和交材料。"
            "我们这里帮你梳理信息、核对材料并整理材料包；正式递交由你在官网完成。"
            if zh else "Here is the official starting point: choose Apply now on the GOV.UK page. "
            "You can save an unfinished form. If you need a Standard Visitor visa, apply online, "
            "book a visa application centre appointment, and follow the identity and document steps. "
            "We help organise and check your preparation pack; you submit the application on the official site."
        ) + "\nGOV.UK: " + APPLICATION_URL))
    if (profile.occupation_status == "student" and profile.funding_source == "self"
            and "student_self_preparation_v1" not in sent_topics):
        result.append(("student_self_preparation_v1", (
            "材料方面，可以先准备学校的在读证明，以及能说明资金来源和可用资金的银行流水。"
            "前者帮助说明学习情况，后者用于核对旅行费用如何承担；预算数字本身不能代替资金证明。"
            if zh else "As a self-funded student, you can start with a letter confirming your enrolment "
            "and bank statements showing accessible funds and their source. These help explain your "
            "circumstances and how you will pay for the trip; a budget figure is not funding evidence."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.visit_purpose == "conference" and "conference_preparation_v1" not in sent_topics:
        result.append(("conference_preparation_v1", (
            "这次是参会，可以先向主办方索取邀请函。它用于说明你要参加的活动和访问目的；"
            "谁承担费用的证明还需要另外结合你的资助安排核对。"
            if zh else "For the conference, start by asking the organiser for an invitation letter. "
            "It helps explain the event and purpose of your visit; funding evidence still needs "
            "to match whoever is covering the costs."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#attendees-of-business-related-events-or-conferences"))
    return result[:2]
