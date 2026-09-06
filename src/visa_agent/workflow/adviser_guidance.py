"""Small, source-reviewed preparation steps selected from the current case, not legal decisions."""

import re
from datetime import date

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.workflow.advice_preferences import (
    _current_clauses,
    prefers_brief_reply,
    wants_no_links,
    wants_one_action,
)
from visa_agent.workflow.conversation import (
    customer_requests_next_step,
    document_list_requested,
    latest_reply_text,
    preparation_context_progress,
    quiet_preparation_resume,
)
from visa_agent.workflow.customer_questions import (
    _active_clauses,
    _next_step_targets_current_case,
    is_generic_uk_preparation_enquiry,
)
from visa_agent.workflow.guidance_freshness import CHECKED_AT, REVIEW_AFTER
from visa_agent.workflow.intent_matching import explicit_nonvisitor_route, normalize_intent_text
from visa_agent.workflow.sponsor_guidance import concise_sponsor_preparation

APPLICATION_URL = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
ROUTE_CHECK_URL = "https://www.gov.uk/check-uk-visa"
DOCUMENTS_URL = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
def _information_only_request(active: str) -> bool:
    return bool(re.search(
        r"(?:只|仅).{0,12}(?:问|说|告诉|确认|核对|列出).{0,16}(?:信息|个人资料|姓名|生日|出生|住址)|"
        r"(?:只|仅).{0,8}(?:需要|想知道).{0,12}(?:信息|个人资料)|"
        r"(?:哪|什么|哪些).{0,8}(?:个人)?信息|"
        r"(?:只|仅).{0,6}(?:问|提).{0,6}(?:一个|一项|一条|1个).{0,6}(?:问题|信息)|"
        r"\b(?:just|only) (?:tell|ask|say|list).{0,35}(?:information|details?|name|birthday|date of birth)|"
        r"\b(?:only|just) (?:need|want).{0,15}(?:information|details?)|"
        r"\b(?:what|which) (?:information|personal details)\b|"
        r"\b(?:just|only).{0,8}(?:ask|give).{0,12}(?:one|a single) question\b", active, re.I,
    ))


def _initial_material_enquiry(body: str) -> bool:
    """Current general UK-application enquiry, not a request for missing form fields."""
    # Share the policy classifier's whole-message boundary. Removing a condition,
    # quote or third-party clause first could turn a non-request into orientation.
    return not _information_only_request(body) and is_generic_uk_preparation_enquiry(body)


def _safe_unscoped_material_text(body: str) -> str:
    """Return a current own-case material request, or an empty safety boundary.

    This mailbox can receive terse follow-ups without the words ``UK visa``.  That
    does not make a third person's case, a hypothetical, a different route or a
    non-visa application safe to answer with Visitor guidance.
    """
    text = normalize_intent_text("\n".join(_active_clauses(body, split_commas=False)))
    if not text or _information_only_request(text) or explicit_nonvisitor_route(text):
        return ""
    if re.search(
        r"^(?:如果|假如|假设|除非)|\b(?:if|unless|hypothetically|assuming)\b|"
        r"(?:朋友|客户|同事|伴侣|配偶|丈夫|妻子|父亲|母亲|父母|兄弟|姐妹|家人|亲属|孩子|儿子|女儿|他|她|他们)"
        r".{0,12}(?:申请|签证|材料|需要)|"
        r"(?:替|帮|给|为).{0,6}(?:我的?)?(?:朋友|客户|同事|伴侣|配偶|父亲|母亲|父母|家人|亲属|孩子|儿子|女儿|他|她|他们)|"
        r"\b(?:my friend|my client|my colleague|my partner|my spouse|my (?:father|mother|parents?|family|relative|child|son|daughter)|he|she|they)\b"
        r".{0,28}(?:appl(?:y|ication)|needs?|documents?)|\bon behalf of\b|"
        r"大学申请|学校申请|贷款|入职|\b(?:university|college|loan|job) application\b",
        text,
        re.I,
    ):
        return ""
    return text


def _unscoped_complete_material_enquiry(body: str) -> bool:
    """Recognise an explicit full-list request in this visa-only mailbox.

    The customer may naturally reply with only ``tell me all the documents`` and
    omit the words UK or visa.  We still give route-safe orientation, but never
    pretend that a universal checklist exists.  Other-person, hypothetical,
    non-visa and named non-Visitor requests remain outside this rescue.
    """
    text = _safe_unscoped_material_text(body)
    if not text:
        return False
    material_scope = re.search(
        r"材料|资料|文件|证明|清单|\b(?:documents?|evidence|checklist|document list)\b",
        text,
        re.I,
    )
    completeness = re.search(
        r"一次性|一次说完|一次说清楚|总共|全部|所有|完整|"
        r"\b(?:in one message|all at once|in one go|complete|full|everything|all (?:the )?)\b",
        text,
        re.I,
    )
    request = re.search(
        r"说|告诉|列|整理|给|需要|准备|\b(?:tell|explain|list|give|need|prepare|provide)\b",
        text,
        re.I,
    )
    return bool(material_scope and completeness and request)


def _unscoped_material_orientation_enquiry(body: str) -> bool:
    """Recognise a safe cold-start material obligation even in a mixed request.

    ``What documents and where do I apply?`` is one customer request, not a reason
    to answer only the application-page half.  Full-list wording is also accepted
    without repeating ``UK visa`` because the mailbox is already visa-scoped.  This
    function authorises only conditional common categories, never a personal list.
    """
    text = _safe_unscoped_material_text(body)
    if not text:
        return False
    if is_generic_uk_preparation_enquiry(body):
        return True
    material_clauses = [
        clause
        for clause in _active_clauses(text, split_commas=False)
        if re.search(
            r"材料|资料|文件|证明|清单|\b(?:documents?|evidence|paperwork|checklist|document list)\b",
            clause,
            re.I,
        )
    ]
    # ``How should I translate my supporting documents?`` is a translation
    # FAQ, not an implicit request for a full evidence checklist.  Assess the
    # request inside the material-bearing clause so an unrelated ``what is the
    # fee?`` elsewhere in the email cannot supply the missing request word.
    checklist_clauses = [
        clause
        for clause in material_clauses
        if not re.search(r"翻译|译文|译员|\btranslat(?:e|ed|es|ing|ion|ions|or|ors)\b", clause, re.I)
    ]
    material_scope = bool(checklist_clauses)
    material_request = any(re.search(
        r"说|告诉|列|整理|给|需要|要|应该|准备|提供|提交|有哪些|是什么|"
        r"\b(?:tell|explain|list|give|need|prepare|provide|submit|required|what|which)\b",
        clause,
        re.I,
    ) for clause in checklist_clauses)
    if not material_scope or not material_request:
        return False
    if _unscoped_complete_material_enquiry(body):
        return True
    route_context = bool(re.search(
        r"英国|(?:签证|访客|访问|旅游)(?:申请)?|"
        r"\b(?:UK|British|visa|visitor|tourist)\b",
        text,
        re.I,
    ))
    application_companion = bool(re.search(
        r"在哪|哪里|怎么|如何|网页|网站|官网|入口|链接|流程|步骤|费用|审理时间|多久|"
        r"\b(?:where|how|website|page|link|process|steps?|fees?|costs?|timing|processing time)\b",
        text,
        re.I,
    ))
    return route_context or application_companion


def _conditional_common_evidence_orientation(case: Case, *, no_links: bool) -> str:
    """Give useful source-reviewed categories without claiming a personal list."""
    zh = case.customer_language == "zh"
    known_zh = []
    known_en = []
    if case.profile.visit_purpose in {"tourism", "family_or_friends", "business", "conference"}:
        purpose_zh = {
            "tourism": "这次是旅游",
            "family_or_friends": "这次是探亲访友",
            "business": "这次是商务访问",
            "conference": "这次是参加会议",
        }[case.profile.visit_purpose]
        purpose_en = {
            "tourism": "this is a holiday",
            "family_or_friends": "this is a visit to family or friends",
            "business": "this is a business visit",
            "conference": "this is a conference visit",
        }[case.profile.visit_purpose]
        known_zh.append(purpose_zh)
        known_en.append(purpose_en)
    if case.profile.nationality_country:
        known_zh.append(f"你持{case.profile.nationality_country}护照")
        known_en.append(f"you hold a {case.profile.nationality_country} passport")
    if case.profile.application_country:
        known_zh.append(f"准备在{case.profile.application_country}递交")
        known_en.append(f"you plan to apply in {case.profile.application_country}")
    missing_zh = [label for value, label in (
        (case.profile.nationality_country, "护照国家或地区"),
        (case.profile.visit_purpose, "赴英目的"),
        (case.profile.application_country, "申请地点"),
    ) if not value]
    missing_en = [label for value, label in (
        (case.profile.nationality_country, "passport country"),
        (case.profile.visit_purpose, "purpose"),
        (case.profile.application_country, "application location"),
    ) if not value]
    if known_zh:
        context_zh = "我先按你已经说明的情况来安排：" + "、".join(known_zh) + "。"
        context_en = "I will start with what you have already told me: " + ", ".join(known_en) + "."
        if missing_zh:
            context_zh += "我还需要核对" + "、".join(missing_zh) + "，所以下面先是有条件的准备框架。"
            context_en += (
                " I still need to confirm your " + ", ".join(missing_en)
                + ", so the preparation framework below is conditional for now."
            )
    else:
        context_zh = (
            "具体清单要看你持哪国护照、去英国做什么，以及在哪里申请。"
        )
        context_en = (
            "The right list depends on your passport country, reason for visiting and where you will apply."
        )
    purpose_item_zh = {
        "tourism": "- 一页简洁的预计旅游行程，说明城市、大致活动和住宿地区，未定内容如实标注待确认；",
        "family_or_friends": "- 说明与亲友的关系、访问和住宿安排的邀请说明；",
        "business": "- 与商务目的相符的邀请、会议或拜访安排；",
        "conference": "- 主办方邀请函及会议安排，说明活动、日期和参加原因；",
    }.get(case.profile.visit_purpose or "", "- 与赴英目的和预计安排相符的说明或材料；")
    purpose_item_en = {
        "tourism": "- a short intended itinerary covering the cities, broad activities and accommodation area, with undecided details marked as provisional;",
        "family_or_friends": "- an invitation or explanation covering the relationship, visit and accommodation arrangements;",
        "business": "- an invitation and meeting or visit plan matching the business purpose;",
        "conference": "- an organiser invitation and conference plan explaining the event, dates and reason for attending;",
    }.get(case.profile.visit_purpose or "", "- evidence or an explanation matching the purpose and intended arrangements for the visit;")
    answer = (
        "可以，先从手边已有的资料开始。\n\n"
        + context_zh
        + "\n\n通常先整理这些，再按你的情况筛选：\n"
        "- 有效护照或旅行证件；\n"
        + purpose_item_zh + "\n"
        "- 按实际情况选用在职、在读或自雇证明；\n"
        "- 说明谁承担费用、可用资金和真实来源；如由他人资助，还要说明资助内容和双方关系；\n"
        "- 非英文或威尔士文的材料，配完整、可核验的翻译；\n"
        "- 如果在护照国以外申请，准备当地合法居留证明。\n"
        "这不是所有人一模一样的必交清单；等关键情况确认后，我会把不适用的项目删掉。\n\n"
        "办理时先用官方查询入口确认需要签证还是 ETA；如果查询结果显示需要 Standard Visitor 签证，"
        "在官方在线申请页选择 Apply now，表格可以保存后继续。在线申请后预约签证申请中心，"
        "按页面要求完成身份核验并提供材料。\n"
        "6 个月 Standard Visitor 当前申请费为 £135，最早可在出发前 3 个月申请；"
        "完成在线申请、身份核验和材料提供后通常约 3 周出决定，并非保证时限或获签。"
        if zh else
        "Of course. Let's start with what you already have.\n\n"
        + context_en
        + "\n\nThese are useful starting points; we will select what fits your circumstances:\n"
        "- a valid passport or travel document;\n"
        + purpose_item_en + "\n"
        "- employment, study or self-employment evidence, as applicable;\n"
        "- who will pay, the accessible funds and their genuine source; if someone else pays, also the support "
        "arrangement and relationship;\n"
        "- a full, verifiable translation for any document you submit that is not in English or Welsh;\n"
        "- evidence of lawful residence if you apply outside your country of nationality.\n"
        "This is not a universal mandatory checklist.\n\n"
        "Use the official checker to establish whether you need a visa or ETA. If you need a Standard Visitor visa, "
        "select Apply now on the GOV.UK online application page; you can save the form and return to it. "
        "After applying online, book a visa application centre appointment to prove your identity and provide documents.\n"
        "The current fee for a 6-month Standard Visitor visa is £135. You can apply up to 3 months before travel. "
        "A decision usually takes about 3 weeks after the application, identity check and documents are complete; "
        "neither timing nor approval is guaranteed."
    )
    existing = "\n".join(case.customer_answers)
    sources = [
        (ROUTE_CHECK_URL, (
            "官方签证 / ETA 查询："
            if zh else
            "Official visa / ETA checker:"
        )),
        (APPLICATION_URL, (
            "Standard Visitor 官方在线申请页（确认路线后选择 Apply now）："
            if zh else
            "Official Standard Visitor application page (select Apply now after checking the route):"
        )),
        (DOCUMENTS_URL, (
            "GOV.UK 访客证明材料指南："
            if zh else
            "GOV.UK visitor supporting-document guide:"
        )),
    ]
    for url, lead in sources:
        if url not in existing:
            answer += "\n\n" + lead
            if not no_links:
                answer += "\nGOV.UK: " + url
    return answer


def _incomplete_personal_checklist(case: Case, body: str) -> bool:
    """A validated personal checklist may accompany new grounded profile details.

    This is not the generic-unsupported rescue: the model must already have supplied
    only a checklist intent, with current context progress and an independent own-case
    question. It offers conditional guidance while a material driver is still missing.
    """
    if (set(case.customer_question_topics) != {"document_checklist"}
            or case.customer_answers or document_list_requested(case)
            or case.profile.visit_purpose not in {"tourism", "family_or_friends", "business", "conference"}
            or case.profile.occupation_status not in {"student", "employed", "self_employed"}
            or not {"visit_purpose", "occupation_status", "funding_source", "nationality_country", "application_country"}
            .intersection(set(case.latest_received_facts) | set(case.latest_changes))
            or re.search(r"如果|假如|假设|除非|保证|获批|过签|批准|"
                         r"\b(?:if|unless|assuming|suppose|guarantee|approval|approved|eligible)\b", body, re.I)):
        return False
    for clause in _active_clauses(body, split_commas=False):
        if (re.fullmatch(
                r"(?:请问)?(?:我这次申请|我的申请|我)(?:还)?(?:需要|准备|提供)(?:哪些|什么)(?:材料|资料|文件)[？?。.!！\s]*|"
                r"(?:which|what) (?:supporting )?documents (?:do I (?:still )?need|should I prepare) "
                r"for my (?:visitor |visa )?application[?.!\s]*", clause, re.I)
                and _next_step_targets_current_case(body, clause)):
            return True
    return False


def _question_step_allows_preparation_guidance(case: Case, active: str, *, initial_enquiry: bool = False) -> bool:
    """A request to start preparing can be labelled next_step without being a FAQ.

    Only replace the planner's generic missing-detail introduction, never its actual
    answer about a document/review/paused state or another customer question.
    """
    step = case.next_step_advice
    if _information_only_request(active):
        return False
    # A provider may omit a plainly expressed one-action request. This only
    # selects reviewed advice for the established case; it creates no topic,
    # fact, permission or replacement for a separately answered question.
    if (set(case.customer_question_topics) <= {"next_step"}
            and (not case.customer_answers or step is not None and step.kind == "question"
                 and case.customer_answers == [step.message])
            and case.profile.visit_purpose in {"tourism", "family_or_friends", "business", "conference"}
            and case.profile.occupation_status in {"student", "employed", "self_employed"}
            and not explicit_nonvisitor_route(case.latest_customer_message)
            and any(wants_one_action(clause)
                    and _next_step_targets_current_case(case.latest_customer_message, clause)
                    for clause in _current_clauses(case.latest_customer_message))):
        return True
    if (step is None or step.kind != "question"
            or set(case.customer_question_topics) != {"next_step"}
            or case.customer_answers != [step.message]):
        return False
    if re.search(
        r"个人(?:资料|信息)|身份(?:资料|信息)|申请表(?:信息|内容)|"
        r"\b(?:personal|identity|form) (?:details|information)\b",
        active,
        re.I,
    ):
        return False
    if initial_enquiry:
        return True
    if (case.profile.visit_purpose not in {"tourism", "family_or_friends", "business", "conference"}
            or not (case.profile.occupation_status in {"student", "employed", "self_employed"}
                    or case.profile.funding_source in {"self", "personal_sponsor", "employer_or_school"})):
        return False
    for clause in _active_clauses(active, split_commas=False):
        if re.search(
            r"^(?:如果|假如|假设|if\b|suppose\b|maybe\b)|"
            r"(?:不想|不用|不要|先不|暂不|不能|无需).{0,8}(?:准备|申请|整理)|"
            r"\b(?:don't|do not|not asking|cannot).{0,15}(?:prepar\w*|organis\w*|organiz\w*|apply)",
            clause, re.I,
        ):
            continue
        if (wants_one_action(clause)
                and _next_step_targets_current_case(case.latest_customer_message, clause)
                and not explicit_nonvisitor_route(clause)):
            return True
        if re.search(
            r"\btell me what to (?:gather|prepare|collect) first\b|"
            r"(?:先|请)(?:简短)?告诉我眼下最值得做的一件事|"
            r"(?:帮我|请|想|先|开始|继续|接着|打算|下一步|该|应该|需要).{0,10}"
            r"(?:准备|整理|收集).{0,10}(?:申请|材料|资料|文件|签证|什么)|"
            r"\b(?:help me|please|let['’]s|can we|could we|want to|ready to|start|continue|"
            r"what should I|what do I need to)"
            r".{0,24}(?:prepar\w*|collect\w*|organis\w*|organiz\w*).{0,24}(?:documents?|application|evidence)",
            clause, re.I,
        ):
            return True
    return False


def _conference_organisation_preparation(case: Case, current: str) -> str:
    """Join purpose and institutional payment into one customer action.

    An invitation explains the event; it does not prove that an employer or
    school will pay.  Keeping both sides in one guidance item prevents the
    first reply from acknowledging only the conference and postponing an
    already-known funding arrangement to a later turn.
    """
    sponsor = concise_sponsor_preparation(
        current,
        case.customer_language,
        case,
        conference=True,
    )
    return sponsor + ("\nGOV.UK: " + DOCUMENTS_URL
                      + "#attendees-of-business-related-events-or-conferences")


def _application_process_orientation(case: Case) -> str:
    """Add a compact application path after the immediately useful case-specific action."""
    zh = case.customer_language == "zh"
    route_confirmed = case.profile.route_confirmed_standard_visitor
    if zh:
        opening = (
            "你已确认按 Standard Visitor 准备，"
            if route_confirmed else
            "办理时，先用 GOV.UK 查询工具确认需要签证还是 ETA；"
            "如果需要 Standard Visitor 签证，"
        )
        answer = (
            opening
            + "从官方申请页选择 Apply now。表格可以中途保存；在线提交后，"
            "预约签证申请中心，再按页面要求完成身份核验和材料提供。\n\n"
            "6 个月 Standard Visitor 当前申请费为 £135；"
            "最早可在出发前 3 个月申请，完成在线申请、身份核验和材料提供后，"
            "通常约 3 周出决定，并非保证时限或获签。无需为了准备材料先买机票或订酒店。"
            "我会帮你整理和核对材料，正式递交由你在官网完成。"
        )
        sources = [] if route_confirmed else [
            "官方签证 / ETA 查询：\nGOV.UK: " + ROUTE_CHECK_URL,
        ]
        sources.append(
            "Standard Visitor 官方在线申请页（进入后选择 Apply now）：\n"
            "GOV.UK: " + APPLICATION_URL
        )
        return answer + "\n\n" + "\n".join(sources)

    opening = (
        "You have confirmed that we are preparing on the "
        "Standard Visitor route, so "
        if route_confirmed else
        "When you apply, use the GOV.UK checker to confirm whether you need a visa "
        "or an ETA. If you need a Standard Visitor visa, "
    )
    answer = (
        opening
        + "select Apply now on the official application page. You can save the form and return to it. "
        "After applying online, book a visa application centre appointment and follow the page instructions "
        "to prove your identity and provide documents.\n\n"
        "The current fee for a 6-month Standard Visitor application is £135. You can apply "
        "up to 3 months before travel, and a decision usually takes about 3 weeks after the online application, "
        "identity check and documents are complete; neither timing nor approval is guaranteed. "
        "No need to buy flights or book a hotel just for evidence. "
        "I will help organise and check the documents; you will submit on the official website."
    )
    sources = [] if route_confirmed else [
        "Official visa / ETA checker:\nGOV.UK: " + ROUTE_CHECK_URL,
    ]
    sources.append(
        "Official Standard Visitor application page (select Apply now):\nGOV.UK: " + APPLICATION_URL
    )
    return answer + "\n\n" + "\n".join(sources)


def preparation_guidance(case: Case, today: date, sent_topics: set[str]) -> list[tuple[str, str]]:
    """A link preference changes presentation, not whether useful guidance exists."""
    result = _preparation_guidance(case, today, sent_topics)
    if wants_no_links(case.latest_customer_message):
        return [(topic, "\n".join(line for line in answer.splitlines()
                                 if not line.startswith("GOV.UK: ")))
                for topic, answer in result]
    return result


def _preparation_guidance(case: Case, today: date, sent_topics: set[str]) -> list[tuple[str, str]]:
    """Offer at most two useful next steps; explicit answers and problems take priority.

    Topic IDs are versioned. Only topics in actually sent replies count as already shared.
    These suggestions cannot change requirements, evidence acceptance, facts or consent.
    """
    current = latest_reply_text(case.latest_customer_message)
    no_links = wants_no_links(current)
    current_omissions = [
        item
        for item in case.pending_advice
        if item.source_body == case.latest_customer_message
        and item.offered_notice
        and item.offered_notice in case.customer_answers
        and not item.deferred_by_event_id
    ]
    if current_omissions:
        # The capped answer queue has explicitly told the customer which topic
        # will be continued later. A proactive brochure must not contradict
        # that promise by leaking the omitted answer (for example, a fee value).
        return []
    active = "\n".join(_active_clauses(current, split_commas=False))
    unscoped_material_request = _unscoped_material_orientation_enquiry(current)
    # Once the visit purpose is known, use the case-specific preparation path
    # below instead of restarting a cold-start orientation brochure. A purpose
    # extracted from this same first email is still cold-start context when the
    # other route/material drivers remain unknown.
    current_facts = set(case.latest_received_facts) | set(case.latest_changes)
    same_turn_first_purpose = (
        "visit_purpose" in current_facts
        and not all((
            case.profile.nationality_country,
            case.profile.application_country,
            case.profile.occupation_status,
            case.profile.funding_source,
        ))
    )
    unscoped_material = unscoped_material_request and (
        case.profile.visit_purpose is None or same_turn_first_purpose
    )
    unscoped_complete = _unscoped_complete_material_enquiry(current)
    explicit_unscoped_material = unscoped_complete or bool(re.search(
        r"材料|资料|文件|证明|清单|\b(?:documents?|evidence|paperwork|checklist|document list)\b",
        normalize_intent_text(current),
        re.I,
    ))
    initial_enquiry = _initial_material_enquiry(current) or unscoped_material
    personal_checklist = _incomplete_personal_checklist(case, current)
    question_preparation = _question_step_allows_preparation_guidance(case, active, initial_enquiry=initial_enquiry)
    initial_topics = set(case.customer_question_topics)
    safe_combined_material = (
        unscoped_material
        and not {"unsupported", "off_topic", "next_step"}.intersection(initial_topics)
    )
    initial_checklist = ((initial_topics <= {"document_checklist"}
                          or question_preparation or safe_combined_material)
                         and not document_list_requested(case) and initial_enquiry)
    if (case.preparation_paused or quiet_preparation_resume(case)
            or not CHECKED_AT <= today <= REVIEW_AFTER
            or (case.customer_answers and not question_preparation and not initial_checklist)
            or (case.customer_question_topics and not initial_checklist and not question_preparation and not personal_checklist)
            or case.open_blockers()
            or case.latest_document_names or case.status != CaseStatus.DRAFT):
        return []
    # Preserve comma-linked conditions and negations before selecting an affirmative
    # preparation request. A separate later-date statement does not veto that request.
    preparation_clauses = (_active_clauses("\n".join(_current_clauses(current))) if no_links
                           else _active_clauses(current, split_commas=False))
    text = "\n".join(clause for clause in preparation_clauses
                     if not re.search(
                         r"如果|假如|暂时|先不|不想|不需要|不能|尚未|还没|"
                         r"\b(?:if|not|later|tomorrow|maybe|cannot|never|stop)\b|"
                         r"(?:don|can|won|wouldn|couldn|shouldn)['’]t", clause, re.I,
                     ))
    # An unshared topic is not by itself a reason to send it now. Proactive advice
    # needs progress in intake or a current preparation request; existing profile
    # data must not turn unrelated chatter or control instructions into a brochure.
    if not (question_preparation or preparation_context_progress(case) or customer_requests_next_step(current)
            or re.search(
                r"(?:想|准备|打算|需要).{0,6}(?:申请|办理?).{0,6}(?:英国|签证)|"
                r"(?:准备|整理|收集|补充).{0,8}(?:材料|资料|文件)|"
                r"(?:材料|资料|文件).{0,8}(?:准备|整理|收集)|"
                r"\b(?:prepar\w*|collect\w*|gather\w*|organis\w*|organiz\w*).{0,24}"
                r"(?:documents?|evidence|application)|"
                r"\b(?:want|need|planning) to apply.{0,20}(?:UK|visa)\b",
                text, re.I,
            ) or (initial_checklist and (unscoped_complete or re.search(
                r"(?:英国|UK|British).{0,24}(?:签证|旅游|旅行|visa|visit|trip)|"
                r"(?:visa|visit|trip).{0,16}(?:UK|Britain)", text, re.I,
            )))):
        return []
    if (any(re.search(r"(?:不用|不需要|不要|无需|不想)[^，,;；。\n]{0,18}(?:流程|材料|建议|说明)|"
                      r"(?:don't|do not|no need|stop)[^,;\n]{0,30}(?:guidance|explain|advice)", clause, re.I)
            for clause in _current_clauses(current))
            or re.fullmatch(r"(?:谢谢|好的|收到|了解|thanks|thank you|okay|ok)[。.!！\s]*", text, re.I)):
        return []
    profile = case.profile
    zh = case.customer_language == "zh"
    result: list[tuple[str, str]] = []
    if initial_checklist and (
        "route_orientation_v1" not in sent_topics
        # Compatibility for persisted v1 cases: an explicit new materials
        # request receives the richer contract even if the old terse text was
        # delivered. Unrelated follow-ups never enter ``initial_checklist``.
        or (unscoped_material and explicit_unscoped_material)
    ):
        result.append(("route_orientation_v1", _conditional_common_evidence_orientation(
            case,
            no_links=no_links,
        )))
        # This orientation already answers the material part of a cold combined
        # enquiry and adapts the purpose line to any same-message fact. When the
        # reply already carries application/fee/timing answers, do not append a
        # second proactive topic. A pure preparation enquiry can still receive
        # one case-specific action below (for example sponsor arrangements).
        if case.customer_answers:
            return result
    if not initial_checklist and profile.visit_purpose not in {
        "tourism", "family_or_friends", "business", "conference"
    }:
        if profile.visit_purpose is None and "route_orientation_v1" not in sent_topics:
            return [("route_orientation_v1", (
                "先确认适合的申请类别，再准备材料。是否需要签证或 ETA，要结合护照和赴英目的判断；"
                "可以先在 GOV.UK 的 Check if you need a UK visa 页面查看，不用现在就把所有个人资料发来。"
                if zh else "Let's establish the right application route before collecting documents. "
                "Whether you need a visa or ETA depends on your passport and purpose; the official "
                "checker on GOV.UK is a starting point. We can then work out your preparation steps "
                "without asking you to send every personal detail at once."
            ) + "\nGOV.UK: " + ROUTE_CHECK_URL)]
        return []
    if (not initial_checklist and "application_overview_v1" not in sent_topics
            and not prefers_brief_reply(case) and not wants_one_action(current)):
        result.append(("application_overview_v1", _application_process_orientation(case)))
    # Existing combined student advice covers both components. Do not re-send
    # either component merely because a deployment now has more granular topics.
    covered = set(sent_topics)
    if "student_self_preparation_v1" in covered:
        covered.update({"student_enrolment_preparation_v1", "self_funding_preparation_v1"})
    if "family_personal_sponsor_preparation_v1" in covered:
        covered.update({"family_visit_preparation_v1", "personal_sponsor_preparation_v1"})
    if "conference_organisation_funding_preparation_v1" in covered:
        covered.update({"conference_preparation_v1", "organisation_funding_preparation_v1"})
    candidates: list[tuple[str, str]] = []
    if profile.funding_source == "personal_sponsor" and "personal_sponsor_preparation_v1" not in covered:
        family = profile.visit_purpose == "family_or_friends"
        sponsor_text = concise_sponsor_preparation(current, case.customer_language, case)
        if family:
            sponsor_text = ("这次探亲访友，可以先和亲友核对访问和住宿安排；接待你的人不一定就是资助人。"
                            if zh else "For your visit to family or friends, agree the visit and accommodation plans "
                            "with them first; your host is not necessarily your sponsor. ") + sponsor_text
        candidates.append(("family_personal_sponsor_preparation_v1" if family else "personal_sponsor_preparation_v1",
                           sponsor_text))
    if profile.visit_purpose == "family_or_friends" and "family_visit_preparation_v1" not in covered:
        family_text = (
            "探亲访友可以先和对方核对你们的关系、访问安排，以及准备住在哪里。"
            "也可以请对方用一封简短的邀请说明把这些安排写清楚，帮助解释访问目的。"
            "对方是否提供住宿或承担费用要单独确认，不能仅凭是亲友就当作资助人。"
            if zh else "For a visit to family or friends, start by checking your relationship, visit plans "
            "and where you expect to stay with them. A short invitation explaining those arrangements "
            "can help set out the purpose of the trip. "
            "Check separately whether they will provide accommodation or pay any costs; being your host "
            "does not by itself mean they are funding you."
        )
        if profile.funding_source == "self":
            family_text = (
                "这次是探亲访友，可以请亲友写一封简短的邀请说明，把你们的关系、访问目的和住宿安排交代清楚。"
                "费用按你说的由自己承担，邀请说明里不用把接待写成经济资助。"
                if zh else "For this visit to family or friends, you can start by asking for a short invitation "
                "explaining your relationship, the purpose of the visit and the accommodation plans. "
                "You have said you will pay for the trip, so the invitation should not describe your host as funding it."
            )
            if not profile.uk_accommodation:
                family_text += ("住宿安排可以再和对方核对。" if zh else
                                " You can agree the accommodation arrangements with them next.")
        candidates.append(("family_visit_preparation_v1", family_text
                           + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    combined_conference_funding = (
        profile.visit_purpose == "conference"
        and profile.funding_source == "employer_or_school"
        and "conference_preparation_v1" not in covered
        and "organisation_funding_preparation_v1" not in covered
        and not wants_one_action(current)
    )
    if combined_conference_funding:
        candidates.append((
            "conference_organisation_funding_preparation_v1",
            _conference_organisation_preparation(case, current),
        ))
    if (profile.occupation_status == "student" and profile.funding_source == "self"
            and not wants_one_action(current)
            and not {"student_enrolment_preparation_v1", "self_funding_preparation_v1"} & covered):
        candidates.append(("student_self_preparation_v1", (
            "材料方面，可以先准备学校的在读证明，以及能说明资金来源和可用资金的银行流水。"
            "前者帮助说明学习情况，后者用于核对旅行费用如何承担；预算数字本身不能代替资金证明。"
            if zh else "As a self-funded student, you can start with a letter confirming your enrolment "
            "and bank statements showing accessible funds and their source. These help explain your "
            "circumstances and how you will pay for the trip; a budget figure is not funding evidence."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "student" and "student_enrolment_preparation_v1" not in covered:
        enrolment_text = (
            "你还在读书，可以先向学校索取在读证明，用来说明目前的学习情况。"
            "如果行程涉及请假，也可以请学校说明相应安排。"
            if zh else "As you are studying, you can ask your school for a letter confirming your enrolment. "
            "It helps explain your current circumstances; if the trip involves leave from your course, "
            "ask the school to explain that arrangement too."
        )
        if profile.funding_source is None:
            enrolment_text += ("即使费用由谁承担还没决定，也不妨碍先准备这一部分。" if zh else
                               " You can start this part even before deciding who will pay.")
        candidates.append(("student_enrolment_preparation_v1", enrolment_text
                           + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "employed" and "employment_preparation_v1" not in covered:
        candidates.append(("employment_preparation_v1", (
            "你现在在职，可以先向公司人事索取一封用公司抬头纸出具的在职证明，写明职位、薪资和入职时间。"
            "它能说明目前的工作和收入情况；准备时顺便核对这些信息是否与申请表一致。"
            if zh else "You can start by asking HR for a letter on company headed paper with your role, "
            "salary and how long you have worked there. It helps explain your employment and income; "
            "check that these details match what you put in the application."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "self_employed" and "self_employment_preparation_v1" not in covered:
        candidates.append(("self_employment_preparation_v1", (
            "你自己经营业务，可以先找现有的经营登记材料或近期业务发票，说明业务确实在持续开展。"
            "不必把自己套进普通雇员的在职证明格式；我们要把你从事什么业务、收入从哪里来说明白。"
            if zh else "For your own business, start with business registration documents or recent invoices "
            "that show it is still operating. You do not need to force your circumstances into an employee-letter "
            "format; the aim is to explain what you do and where your income comes from."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    if (profile.visit_purpose == "conference" and "conference_preparation_v1" not in covered
            and not combined_conference_funding):
        candidates.append(("conference_preparation_v1", (
            "这次是参会，可以先向主办方索取邀请函。它用于说明你要参加的活动和访问目的；"
            "谁承担费用的证明还需要另外结合你的资助安排核对。"
            if zh else "For the conference, start by asking the organiser for an invitation letter. "
            "It helps explain the event and purpose of your visit; funding evidence still needs "
            "to match whoever is covering the costs."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#attendees-of-business-related-events-or-conferences"))
    if profile.funding_source == "self" and "self_funding_preparation_v1" not in covered:
        candidates.append(("self_funding_preparation_v1", (
            "费用由你自己承担，可以先整理显示可用资金及其来源的银行流水，和预计旅行支出放在一起核对。"
            "重点是说明这趟旅行怎样负担，而不是只报一个预算数字。"
            if zh else "As you are paying for the trip, gather bank statements showing accessible funds "
            "and where they came from, then compare them with the costs you expect. The aim is to explain "
            "how you will afford the visit, not just to give a budget figure."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    if (profile.funding_source == "employer_or_school"
            and "organisation_funding_preparation_v1" not in covered
            and not combined_conference_funding):
        candidates.append((
            "organisation_funding_preparation_v1",
            concise_sponsor_preparation(current, case.customer_language, case),
        ))
    if (profile.visit_purpose == "tourism"
            and "tourism_itinerary_preparation_v1" not in covered
            and (question_preparation
                 or "visit_purpose" in set(case.latest_received_facts) | set(case.latest_changes))):
        candidates.append(("tourism_itinerary_preparation_v1", (
            "既然这次是旅游，可以先做一页简洁的预计行程：写清打算去哪些城市、大致做什么，"
            "以及准备住在哪个地区。现在不需要为了材料先买机票或订酒店；没定的内容就标注待确认，"
            "正式提交前再和申请表统一核对。"
            if zh else
            "As this is a holiday, start with a one-page intended itinerary: the cities you expect to visit, "
            "what you broadly plan to do and the area where you expect to stay. You do not need to buy flights "
            "or book a hotel now merely for evidence. Mark undecided details as provisional and align them with "
            "the application before submission."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    # Funding just supplied or changed should not be buried behind an unrelated
    # occupational overview. Personal support/family context already lead above.
    changed_fields = set(case.latest_received_facts) | set(case.latest_changes)
    location_changed = bool({"nationality_country", "application_country"} & changed_fields)
    if ((location_changed or question_preparation) and "residence_preparation_v1" not in covered
            and any(item.id == "legal_residence" and item.applicable and not item.satisfied
                    for item in case.requirements)):
        residence = (
            "还有一项和申请地点有关：你可以先找出当地的居留文件，核对上面的姓名、身份类别和有效期。"
            "我们要用它说明你在申请地的合法居留身份；具体放哪份进材料包，要看你实际持有的文件。"
            if zh else "Given your passport country and where you will apply, check your evidence of lawful residence there. "
            "Find the document recording your current residence status and check its name, status and validity. "
            "It explains your residence where you apply; the location of your school or employer alone does not establish it. "
            "The appropriate evidence depends on the document you hold."
        )
        candidates.append(("residence_preparation_v1", residence + "\nGOV.UK: " + DOCUMENTS_URL
                           + "#demonstrating-personal-circumstances"))
    funding_changed = "funding_source" in changed_fields
    if "visit_purpose" in changed_fields and profile.visit_purpose == "conference":
        candidates.sort(key=lambda item: item[0] not in {
            "conference_preparation_v1", "conference_organisation_funding_preparation_v1",
        })
    elif ("visit_purpose" in changed_fields and profile.visit_purpose == "tourism"
          and not {"occupation_status", "funding_source"}.intersection(changed_fields)):
        candidates.sort(key=lambda item: item[0] != "tourism_itinerary_preparation_v1")
    elif funding_changed and profile.funding_source == "employer_or_school":
        candidates.sort(key=lambda item: item[0] not in {
            "organisation_funding_preparation_v1", "conference_organisation_funding_preparation_v1",
        })
    elif location_changed and not {"visit_purpose", "occupation_status", "funding_source"} & changed_fields:
        candidates.sort(key=lambda item: item[0] != "residence_preparation_v1")
    if candidates:
        result.append(candidates[0])
    # Lead with the useful action for this applicant, not a generic process lecture.
    # Keep topic identities unchanged so already-sent guidance is not sent again.
    result.sort(key=lambda item: item[0] == "application_overview_v1")
    return result[:2]
