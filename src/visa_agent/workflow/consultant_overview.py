"""Reviewed, case-aware preparation overview for an explicit full-list request.

The language model may recognise the request, but it never authors this guidance.
Every claim below is bounded to the reviewed GOV.UK visitor sources.  The overview
separates form information from supporting evidence so a useful reply does not
collapse into a generic document checklist.
"""

from __future__ import annotations

import re
from datetime import date

from visa_agent.domain.locations import location_key
from visa_agent.domain.models import Case, DocumentStatus
from visa_agent.workflow.guidance_freshness import CHECKED_AT, REVIEW_AFTER
from visa_agent.workflow.intent_matching import explicit_nonvisitor_route, normalize_intent_text

ROUTE_CHECK_URL = "https://www.gov.uk/check-uk-visa"
APPLICATION_URL = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
DOCUMENTS_URL = (
    "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/"
    "guide-to-supporting-documents-visiting-the-uk"
)
_ZH_COUNTRY_KEY = {"china": "中国", "hong kong": "香港", "united kingdom": "英国"}


def _zh_country(value: str | None) -> str:
    return _ZH_COUNTRY_KEY.get(location_key(value) or "", value or "")


def comprehensive_overview_requested(case: Case, body: str) -> bool:
    """Recognise an in-context request to explain the whole personal preparation.

    This is deliberately narrower than ordinary checklist detection.  It exists so
    an ``unsupported`` model label cannot erase an obvious contextual follow-up such
    as "tell me everything in one message".  Explicit other-person, other-route,
    hypothetical and declined requests remain outside this rescue.
    """
    if not (
        case.profile.visit_purpose in {"tourism", "family_or_friends", "business", "conference"}
        and case.profile.occupation_status in {"student", "employed", "self_employed"}
        and case.profile.funding_source in {"self", "personal_sponsor", "employer_or_school"}
        and location_key(case.profile.nationality_country)
        and location_key(case.profile.application_country)
    ):
        return False
    if not body or len(body) > 6000:
        return False
    from visa_agent.workflow.advice_preferences import _current_clauses

    safe_clauses: list[str] = []
    for current_clause in _current_clauses(body):
        text = re.sub(r"\s+", " ", normalize_intent_text(current_clause)).strip()
        if not text or re.search(
            r"(?:不用|不要|无需|不需要|不想|不打算|别).{0,18}(?:清单|材料|资料|文件|说清楚)|"
            r"\b(?:do not|don't|no need to|not asking (?:for|about))\b.{0,28}"
            r"(?:checklist|documents?|evidence|full list)",
            text,
            re.I,
        ):
            continue
        if explicit_nonvisitor_route(text):
            continue
        if re.search(
            r"^(?:如果|假如|假设|除非)|\b(?:if|unless|hypothetically|assuming)\b|"
            r"(?:朋友|客户|同事|伴侣|配偶|丈夫|妻子|老公|老婆|家人|亲属|弟弟|妹妹|哥哥|姐姐|父亲|母亲|爸爸|妈妈|父母|爸妈|祖父母|爷爷|奶奶|外公|外婆|姑姑|阿姨|叔叔|孩子|儿子|女儿|他|她|他们).{0,8}的(?:申请|签证|材料|档案)|"
            r"(?:朋友|客户|同事|伴侣|配偶|丈夫|妻子|老公|老婆|家人|亲属|弟弟|妹妹|哥哥|姐姐|父亲|母亲|爸爸|妈妈|父母|爸妈|祖父母|爷爷|奶奶|外公|外婆|姑姑|阿姨|叔叔|孩子|儿子|女儿|他|她|他们).{0,8}"
            r"(?:要|需要|准备|计划|想)(?:申请|办理)|"
            r"(?:替|帮|给|为).{0,5}(?:我的?)?(?:朋友|客户|同事|伴侣|配偶|丈夫|妻子|老公|老婆|家人|亲属|弟弟|妹妹|哥哥|姐姐|父亲|母亲|爸爸|妈妈|父母|爸妈|祖父母|爷爷|奶奶|外公|外婆|姑姑|阿姨|叔叔|孩子|儿子|女儿|他|她|他们)|"
            r"\b(?:my friend|my client|my colleague|my coworker|my partner|my spouse|my husband|my wife|my family|my relative|my brother|my sister|my mother|my father|my parents?|my grandparents?|my grandmother|my grandfather|my aunt|my uncle|my cousin|my child|my son|my daughter|he|she|they)\b"
            r".{0,24}(?:asked|needs?|appl(?:y|ication))|"
            r"\b(?:for|to)\s+(?:(?:my|a|the)\s+)?(?:friend|client|colleague|coworker|partner|spouse|husband|wife|family|relative|brother|sister|mother|father|parents?|grandparents?|grandmother|grandfather|aunt|uncle|cousin|child|son|daughter|him|her|them)\b|"
            r"\b(?:give|prepare).{0,20}\b(?:(?:my|a|the)\s+)?(?:friend|client|colleague|coworker|partner|spouse|husband|wife|family|relative|brother|sister|mother|father|parents?|grandparents?|grandmother|grandfather|aunt|uncle|cousin|child|son|daughter|him|her|them)\b|"
            r"入职|贷款|学校申请|\b(?:job onboarding|loan|university application)\b",
            text,
            re.I,
        ):
            continue
        safe_clauses.append(text)

    # Natural requests often split scope and completeness across a comma or
    # semicolon ("materials, process and links; put it all in one message").
    # Rejoin only clauses that have already passed every scope/negation guard.
    candidates = [*safe_clauses]
    if len(safe_clauses) > 1:
        candidates.append(" ".join(safe_clauses))
    for text in candidates:
        material_scope = bool(re.search(
            r"清单|材料|资料|文件|证明|(?:要|需要|应该)(?:填|填写|提交|提供|准备)的?信息|"
            r"\b(?:documents?|evidence|checklist|document list|information to (?:fill in|provide))\b",
            text,
            re.I,
        ))
        completeness = bool(re.search(
            r"一次性|一次说完|一次讲完|一次整理|一次说清楚|总共|全部|所有|完整|"
            r"都告诉我|完整整理|"
            r"\b(?:in one message|all at once|in one go|complete|full|everything|all (?:the|of the))\b",
            text,
            re.I,
        ))
        requested = bool(re.search(
            r"说|讲|告诉|列|整理|给|需要|准备|提交|提供|"
            r"\b(?:tell|explain|list|organise|organize|give|need|prepare|provide|submit)\b",
            text,
            re.I,
        ))
        if material_scope and completeness and requested:
            return True
    return False


def _declines_links(body: str) -> bool:
    # Reuse the current-turn scope guard so quoted or reported preferences do
    # not silently remove official sources from a new applicant request.
    from visa_agent.workflow.advice_preferences import wants_no_links

    return wants_no_links(body)


def _zh_profile_summary(case: Case) -> str:
    purpose = {
        "tourism": "这次是旅游",
        "family_or_friends": "这次是探亲访友",
        "business": "这次是商务访问",
        "conference": "这次是参加会议",
    }.get(case.profile.visit_purpose or "", "这次是访问英国")
    occupation = {
        "student": "目前在读",
        "employed": "目前在职",
        "self_employed": "目前自己经营业务",
    }.get(case.profile.occupation_status or "", "已说明目前情况")
    funding = {
        "self": "费用由自己承担",
        "personal_sponsor": "费用由个人资助",
        "employer_or_school": "费用由雇主或学校承担",
    }.get(case.profile.funding_source or "", "已说明资金安排")
    details = [
        f"你持{_zh_country(case.profile.nationality_country)}护照",
        f"准备在{_zh_country(case.profile.application_country)}递交",
        occupation,
        funding,
        purpose,
    ]
    if ({"planned_arrival_date", "planned_departure_date"}.intersection(case.deferred_fields)
            or not case.profile.planned_arrival_date or not case.profile.planned_departure_date):
        details.append("具体旅行日期还没定")
    return "、".join(details)


def _en_profile_summary(case: Case) -> str:
    passport_country = {
        "china": "Chinese",
        "hong kong": "Hong Kong",
        "united kingdom": "British",
    }.get(location_key(case.profile.nationality_country) or "")
    passport = (
        f"you hold a {passport_country} passport"
        if passport_country else
        f"you hold a passport issued by {case.profile.nationality_country}"
    )
    application_country = {
        "china": "China",
        "hong kong": "Hong Kong",
        "united kingdom": "the United Kingdom",
    }.get(location_key(case.profile.application_country) or "", case.profile.application_country)
    purpose = {
        "tourism": "this is a holiday",
        "family_or_friends": "you are visiting family or friends",
        "business": "this is a business visit",
        "conference": "you are attending a conference",
    }.get(case.profile.visit_purpose or "", "you are visiting the UK")
    occupation = {
        "student": "you are currently studying",
        "employed": "you are currently employed",
        "self_employed": "you are currently self-employed",
    }.get(case.profile.occupation_status or "", "you have explained your current circumstances")
    funding = {
        "self": "you will pay for the trip yourself",
        "personal_sponsor": "an individual will fund the trip",
        "employer_or_school": "your employer or school will fund the trip",
    }.get(case.profile.funding_source or "", "you have explained the funding arrangement")
    details = [
        passport,
        f"you plan to apply in {application_country}",
        occupation,
        funding,
        purpose,
    ]
    if ({"planned_arrival_date", "planned_departure_date"}.intersection(case.deferred_fields)
            or not case.profile.planned_arrival_date or not case.profile.planned_departure_date):
        details.append("the travel dates are not fixed yet")
    return ", ".join(details)


def _zh_status_evidence(case: Case) -> str:
    if case.profile.occupation_status == "student":
        return (
            "在读情况：可先向学校索取抬头纸在读证明，核对姓名、当前在读状态和出具日期；"
            "如行程占用上课时间，只写学校已经确认的请假安排。"
        )
    if case.profile.occupation_status == "employed":
        return (
            "在职情况：向雇主索取抬头纸证明，核对职位、薪资、入职时间和公司联系信息；"
            "如行程需请假，只写已经确认的安排。"
        )
    return (
        "自雇情况：整理经营登记、近期发票或其他能说明业务仍在运作的现有记录；"
        "不要把自雇情况套成普通雇员证明。"
    )


def _en_status_evidence(case: Case) -> str:
    if case.profile.occupation_status == "student":
        return (
            "Study circumstances: request a headed letter from your education provider and check your name, "
            "current enrolment and the issue date. If the trip overlaps teaching time, include only leave that "
            "the school has actually confirmed."
        )
    if case.profile.occupation_status == "employed":
        return (
            "Employment: request a headed employer letter and check your role, salary, start date and the "
            "employer's contact details. Include only leave that has actually been confirmed."
        )
    return (
        "Self-employment: gather existing business registration, recent invoices or other records showing "
        "that the business is operating. Do not force self-employment into an employee-letter format."
    )


def _zh_purpose_evidence(case: Case) -> str:
    if case.profile.visit_purpose == "conference":
        return (
            "参会安排：向主办方索取邀请函，说明活动、日期和你参加的原因；"
            "邀请函不自动证明谁承担费用。"
        )
    if case.profile.visit_purpose == "family_or_friends":
        return (
            "探亲访友安排：用简短邀请说明双方关系、访问安排和住宿；"
            "接待人不一定是资助人，两件事要分开写清楚。"
        )
    if case.profile.visit_purpose == "business":
        return (
            "商务安排：整理邀请函、会议或拜访计划，说明赴英做什么及费用安排；"
            "内容要与申请表一致。"
        )
    return (
        "旅游计划：整理一份简洁的预计行程，写准备去哪里、做什么、住在哪里；"
        "未定的日期或住宿就标注待确认，不要写成已预订。"
    )


def _en_purpose_evidence(case: Case) -> str:
    if case.profile.visit_purpose == "conference":
        return (
            "Conference arrangements: request an organiser invitation explaining the event, dates and why "
            "you are attending. An invitation does not by itself establish who will pay."
        )
    if case.profile.visit_purpose == "family_or_friends":
        return (
            "Family or friend visit: use a short invitation to explain the relationship, visit and accommodation. "
            "A host is not automatically a financial sponsor, so keep those arrangements distinct."
        )
    if case.profile.visit_purpose == "business":
        return (
            "Business arrangements: gather the invitation and meeting or visit plan, explaining the UK activity "
            "and funding. Keep it consistent with the online application."
        )
    return (
        "Holiday plan: prepare a short, truthful intended itinerary explaining where you expect to go, what you "
        "intend to do and where you expect to stay. Mark unconfirmed dates or accommodation as undecided."
    )


def _zh_funding_evidence(case: Case) -> list[str]:
    if case.profile.funding_source == "personal_sponsor":
        status = (
            "如资助人在英国，还要准备其英国合法身份或居留证明。"
            if case.profile.sponsor_is_in_uk is not False else ""
        )
        return [
            "资助说明：写清谁资助谁、双方关系、这次访问、具体承担哪些费用及如何支付。",
            "资助资金与关系：分开准备能说明资助人资金能力、双方关系的真实材料。" + status,
        ]
    if case.profile.funding_source == "employer_or_school":
        return [
            "单位或学校资助：说明资助哪些费用、如何支付或报销、与你的关系，并准备能说明其可承担这些费用的材料。"
        ]
    return [
        "自费资金：整理本人名下、能显示资金可用及来源的银行流水或其他财务材料，"
        "并让预计旅行开支与资金情况能够相互解释。如有近期明显大额入账，保留真实来源说明。"
    ]


def _en_funding_evidence(case: Case) -> list[str]:
    if case.profile.funding_source == "personal_sponsor":
        status = (
            " If the sponsor is in the UK, also prepare evidence of their lawful UK status."
            if case.profile.sponsor_is_in_uk is not False else ""
        )
        return [
            "Sponsor statement: identify who supports whom, the relationship, this visit, the exact costs covered "
            "and how payment will work.",
            "Sponsor funds and relationship: separately gather truthful evidence of the sponsor's means and your "
            "relationship." + status,
        ]
    if case.profile.funding_source == "employer_or_school":
        return [
            "Employer or school funding: explain which costs are covered, whether payment is direct or reimbursed, "
            "the organisation's relationship to you and evidence that it can provide the support."
        ]
    return [
        "Self-funding: gather financial records in your own name showing accessible funds and their source, and "
        "make sure the expected trip costs can be understood alongside them. Retain truthful source evidence for any "
        "unusual recent credit."
    ]


def _zh_first_action(case: Case) -> str:
    if case.profile.occupation_status == "student" and any(
        doc.kind == "student_letter" and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
        for doc in case.documents
    ):
        return "在读证明已经收到，不用重新申请。接下来只需针对尚缺的材料和指出的具体问题补充。"
    if case.profile.visit_purpose == "conference":
        funding = (
            "；如果费用由学校或公司承担，同时请资助部门写清承担项目和支付方式"
            if case.profile.funding_source == "employer_or_school" else ""
        )
        return f"先向主办方索取会议邀请函，拿到后核对活动、日期、姓名和主办方联系信息{funding}。"
    if case.profile.funding_source == "personal_sponsor":
        return "先请资助人确认具体承担哪些费用、直接支付还是转给你，再写资助说明。"
    if case.profile.funding_source == "employer_or_school":
        return "先请资助部门确认承担项目和支付方式，再由对方出具正式说明。"
    if case.profile.occupation_status == "student":
        funding = (
            "同时可以从网银下载本人名下的正式银行流水，先看账户姓名、资金来源和可用金额是否清楚。"
            if case.profile.funding_source == "self" else ""
        )
        return (
            "先向学校申请在读证明。"
            + ("日期还没定不影响先做这一项；" if not (
                case.profile.planned_arrival_date and case.profile.planned_departure_date) else "")
            + "拿到后先核对姓名、在读状态、出具日期和学校联系信息。" + funding
        )
    if case.profile.occupation_status == "employed":
        funding = (
            "费用由你自己承担的话，也可以同步下载本人正式银行流水。"
            if case.profile.funding_source == "self" else ""
        )
        return "先向公司人事申请在职证明，拿到后核对职位、薪资、入职时间和联系信息。" + funding
    if case.profile.occupation_status == "self_employed":
        return "先找出一份当前有效的经营登记和一组近期业务记录，用来说明业务仍在运作。"
    return "先确认护照在计划停留期间有效，再开始整理其他材料。"


def _en_first_action(case: Case) -> str:
    if case.profile.occupation_status == "student" and any(
        doc.kind == "student_letter" and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
        for doc in case.documents
    ):
        return ("I've received your enrolment letter, so you don't need to request it again. "
                "Next, focus on the remaining missing documents and specific issues identified.")
    if case.profile.visit_purpose == "conference":
        funding = (
            " If your school or employer is paying, also ask its funding team to state the covered costs and payment method."
            if case.profile.funding_source == "employer_or_school" else ""
        )
        return "Request the conference invitation first, then check the event, dates, your name and organiser contact details." + funding
    if case.profile.funding_source == "personal_sponsor":
        return "First ask the sponsor to confirm exactly which costs they will cover and whether they will pay directly or transfer money to you."
    if case.profile.funding_source == "employer_or_school":
        return "First ask the funding department to confirm the covered costs and payment method before it issues the formal letter."
    if case.profile.occupation_status == "student":
        action = (
            "Request the enrolment letter first. "
            + ("You can do that before fixing the travel dates; " if not (
                case.profile.planned_arrival_date and case.profile.planned_departure_date) else "")
            + "When it is ready, "
            "check your name, current status, issue date and the school's contact details."
        )
        if case.profile.funding_source == "self":
            action += (
                " You can also download formal statements for your own account and check that the account name, "
                "source of funds and accessible balance are clear."
            )
        return action
    if case.profile.occupation_status == "employed":
        action = "Request the employer letter first, then check your role, salary, start date and contact details."
        if case.profile.funding_source == "self":
            action += " If you are paying for the trip, you can download your formal bank statements at the same time."
        return action
    if case.profile.occupation_status == "self_employed":
        return "Start with current business registration and a small set of recent records showing that the business is operating."
    return "First check that the passport will remain valid throughout the intended stay, then organise the other evidence."


def first_practical_action(case: Case) -> str:
    """Return one case-specific action suitable for a short adviser reply."""
    return _zh_first_action(case) if case.customer_language == "zh" else _en_first_action(case)


def comprehensive_case_overview(
    case: Case,
    today: date,
    *,
    include_first_action: bool = True,
) -> str:
    """Return the complete reviewed overview for the applicant's current context."""
    zh = case.customer_language == "zh"
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return (
            "我可以按你的情况一次整理完整清单，但这部分引用的 GOV.UK 说明已经到了复核日期。"
            "我先不沿用旧要求或旧链接；核验最新官网后再给你完整版本。你已经提供的信息都会保留，不用重发。"
            if zh else
            "I can organise the complete list around your circumstances, but the GOV.UK guidance used for this "
            "answer has reached its review date. I will not reuse the old requirements or links until they have "
            "been rechecked. Your existing details are retained; you do not need to resend them."
        )
    no_links = _declines_links(case.latest_customer_message)
    applying_outside_passport_country = (
        location_key(case.profile.application_country) != location_key(case.profile.nationality_country)
    )
    dates_open = ({"planned_arrival_date", "planned_departure_date"}.intersection(case.deferred_fields)
                  or not case.profile.planned_arrival_date or not case.profile.planned_departure_date)

    if zh:
        route = (
            "下面按你已确认的 Standard Visitor 准备路线来说明；这是材料准备，不是获批判断。"
            if case.profile.route_confirmed_standard_visitor else
            "下面先按 Standard Visitor 的准备框架说明；是否需要签证或 ETA，仍要用官方路线入口核对。"
        )
        sections = [
            f"可以。我按你目前告诉我的情况一次整理清楚：{_zh_profile_summary(case)}。{route}"
            "这是一份按目前已知情况整理的准备框架，不是所有申请人统一必交清单；"
            "申请表中的个别问题仍以你实际打开的页面为准。申请表要填的信息和要准备的证明材料不是一回事，我分开列。",
            "一、在线申请表要提前准备的信息\n"
            "- 护照个人资料；\n"
            "- 预计抵英和离英日期、停留时长、准备住在哪里；"
            + ("日期现在可以保留为待确认，正式提交前再统一核对；\n" if dates_open else "\n")
            + "- 预计总费用、现住址及居住时间；\n"
            "- 父母姓名和出生日期（如知道）、本人年收入（如有）；\n"
            "- 视个人情况准备近 10 年旅行记录，并如实回答实时申请表询问的移民及其他历史问题。"
            "最终以你打开的表格为准；没有的项目如实填写，不要为了填满而猜测。",
        ]
        evidence = [
            "有效护照或旅行证件：有效期要覆盖计划在英国停留的时间。",
        ]
        if applying_outside_passport_country:
            evidence.append(
                f"{_zh_country(case.profile.application_country)}"
                "合法居留证明：因为你不是在护照国递交，要用实际持有的"
                "当地身份或居留文件说明可在当地申请；学校证件本身不自动等于居留证明。"
            )
        evidence.extend([_zh_status_evidence(case), _zh_purpose_evidence(case)])
        evidence.extend(_zh_funding_evidence(case))
        evidence.append(
            "翻译：不是英文或威尔士文的文件要附完整、可独立核验的翻译；"
            "译文要有准确性确认、翻译日期、译者姓名和签名、联系方式。"
        )
        sections.extend([
            "二、按你目前情况重点准备的证明材料\n"
            + "\n".join(f"{index}. {item}" for index, item in enumerate(evidence, start=1)),
            "两个容易走弯路的地方\n"
            "- 当前 GOV.UK 访客支持材料指南没有为所有申请人规定统一的最低存款或固定流水月数；要看可用资金、来源和旅行开支是否能对得上。\n"
            "- 不需要为了提供证明而先买机票或订酒店；官方指南把普通访客的机票、酒店预订单列为证明价值较低的材料（过境情形另论）。",
        ])
        if include_first_action:
            sections.append("最先做的一步：" + _zh_first_action(case))
        if no_links:
            sections.append(
                "官方页面可在 GOV.UK 搜索：Check if you need a UK visa、Apply for a Standard Visitor visa，"
                "以及 Visiting the UK: guide to supporting documents。"
            )
        else:
            sections.append(
                "官方入口（都是 GOV.UK）\n"
                f"- 查询是否需要签证或 ETA：{ROUTE_CHECK_URL}\n"
                f"- 在线申请（进入后选择 Apply now）：{APPLICATION_URL}\n"
                f"- 访客材料说明：{DOCUMENTS_URL}"
            )
        return "\n\n".join(sections)

    route = (
        "I am using the Standard Visitor route you confirmed as the preparation framework. "
        "This is document preparation, not a prediction of approval."
        if case.profile.route_confirmed_standard_visitor else
        "I am using the Standard Visitor framework for preparation. Use the official checker to establish "
        "whether you need a visa or ETA."
    )
    sections = [
        f"Yes. Based on what you have told me, {_en_profile_summary(case)}. {route} "
        "This is a preparation framework based on what is currently known, not one universal mandatory list for every "
        "applicant; individual form questions still depend on the live form. The information for the online form and "
        "the evidence to prepare are not the same thing, so I have separated them.",
        "1. Information to prepare for the online form\n"
        "- Passport details.\n"
        "- Intended arrival and departure dates, length of stay and where you expect to stay."
        + (" Keep undecided dates pending and make them consistent before submission.\n" if dates_open else "\n")
        + "- Estimated total trip cost, current home address and how long you have lived there.\n"
        "- Your parents' names and dates of birth, if known, and your annual income, if any.\n"
        "- Depending on your circumstances, travel history for the past 10 years and truthful answers to the "
        "immigration and other history questions shown in the live application form. Use the form you actually "
        "open as the final reference; do not guess simply to fill a field.",
    ]
    evidence = [
        "Valid passport or travel document: it must remain valid throughout the intended UK stay.",
    ]
    if applying_outside_passport_country:
        evidence.append(
            f"Evidence of lawful residence in {case.profile.application_country}: because you plan to apply outside "
            "your passport country, use the local identity or residence document you actually hold. A student card "
            "does not by itself establish immigration status."
        )
    evidence.extend([_en_status_evidence(case), _en_purpose_evidence(case)])
    evidence.extend(_en_funding_evidence(case))
    evidence.append(
        "Translation: anything not in English or Welsh needs a full, independently verifiable "
        "translation with confirmation of accuracy, the translation date, the translator's full name and signature, "
        "and contact details."
    )
    sections.extend([
        "2. Evidence to prepare for your circumstances\n"
        + "\n".join(f"{index}. {item}" for index, item in enumerate(evidence, start=1)),
        "Two common ways to waste time or money\n"
        "- The current GOV.UK visitor supporting-documents guide does not set one universal minimum balance or fixed "
        "statement period for every applicant. The available "
        "funds, their source and expected trip costs need to make sense together.\n"
        "- You do not need to buy flights or book hotels merely to provide booking evidence. The official guide describes "
        "ordinary flight and hotel bookings as less useful evidence, apart from transit.",
    ])
    if include_first_action:
        sections.append("Your first practical step: " + _en_first_action(case))
    if no_links:
        sections.append(
            "On GOV.UK, look for: Check if you need a UK visa; Apply for a Standard Visitor visa; and "
            "Visiting the UK: guide to supporting documents."
        )
    else:
        sections.append(
            "Official GOV.UK pages\n"
            f"- Check whether you need a visa or ETA: {ROUTE_CHECK_URL}\n"
            f"- Apply online (select Apply now): {APPLICATION_URL}\n"
            f"- Supporting-document guidance: {DOCUMENTS_URL}"
        )
    return "\n\n".join(sections)
