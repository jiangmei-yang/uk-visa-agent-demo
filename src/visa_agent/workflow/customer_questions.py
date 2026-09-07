"""Small reviewed answer set, not open-ended immigration advice."""

import re
from dataclasses import dataclass
from datetime import date

from visa_agent.domain.models import Case
from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.advice_preferences import (
    _current_clauses as _preference_current_clauses,
)
from visa_agent.workflow.advice_preferences import (
    wants_no_links,
)
from visa_agent.workflow.conversation import (
    _unquoted_reply_text,
    current_no_intake_clause,
    latest_reply_text,
)
from visa_agent.workflow.document_preparation import (
    SCHOOL_RECORD_TOPIC,
    host_not_sponsor_financial_question,
    reviewed_document_preparation,
    school_record_reported,
)
from visa_agent.workflow.document_purpose import reviewed_document_purpose
from visa_agent.workflow.guidance_freshness import CHECKED_AT, REVIEW_AFTER
from visa_agent.workflow.income_clarification import (
    guarantee_answer,
    guarantee_question,
    income_answer,
    income_question,
    only_guarantee_question,
    only_income_evidence_question,
)
from visa_agent.workflow.intent_matching import (
    EXPLICIT_VISITOR_ROUTE_PATTERN,
    explicit_nonvisitor_route,
    normalize_intent_text,
)
from visa_agent.workflow.sponsor_guidance import (
    SPONSOR_QUESTION_PATTERN,
    sponsor_support_answer,
    sponsor_support_question,
    sponsor_verification_answer,
    sponsor_verification_question,
)

SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
APPLICATION_SOURCE = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
PROCESSING_SOURCE = "https://www.gov.uk/guidance/visa-processing-times-applications-outside-the-uk#when-your-application-processing-time-ends"
PROCESSING_TIMES_SOURCE = "https://www.gov.uk/guidance/visa-processing-times-applications-outside-the-uk"
ACTIVITIES_SOURCE = "https://www.gov.uk/standard-visitor"
MEDICAL_SOURCE = "https://www.gov.uk/standard-visitor/visit-for-medical-reasons"
ACADEMIC_SOURCE = "https://www.gov.uk/standard-visitor/visit-as-an-academic"
# The overview's Fees section was checked on CHECKED_AT; no price is copied here.
STUDENT_SOURCE = "https://www.gov.uk/student-visa"
ROUTE_CHECK_SOURCE = "https://www.gov.uk/check-uk-visa"
STANDARD_VISITOR_SOURCE = "https://www.gov.uk/standard-visitor"
VAC_SOURCE = "https://www.gov.uk/find-a-visa-application-centre"
AFTER_APPLY_SOURCE = "https://www.gov.uk/apply-to-come-to-the-uk/applying-online-and-getting-a-decision"
CONTACT_UKVI_SOURCE = "https://www.gov.uk/contact-ukvi-inside-outside-uk"
CANCEL_SOURCE = "https://www.gov.uk/cancel-visa"
OTHER_ROUTE = r"学生签证|工作签证|结婚签证|\b(?:student visa|work visa|marriage visa)\b"
TRANSIT_ROUTE = r"过境|\btransit\b"
ROUTE_ORIENTATION_PATTERN = (
    r"(?:旅游|访问|访客)(?:签证|路线)?.{0,20}(?:学生|留学|读书)(?:签证|路线)?|"
    r"(?:学生|留学|读书)(?:签证|路线)?.{0,20}(?:旅游|访问|访客)(?:签证|路线)?|"
    r"\b(?:visitor|tourist)(?: visa)?\b.{0,30}\b(?:student visa|study route)\b|"
    r"\b(?:student)(?: visa)?\b.{0,30}\b(?:visitor visa|tourist visa|visitor route)\b"
)


def _student_application_page_requested(body: str) -> bool:
    """A current Student application-page request may receive that official entry.

    This does not reclassify the stored case or import Student rules into the
    Standard Visitor workflow; it only prevents an explicit official-page
    question from ending at a generic route checker.
    """
    text = normalize_intent_text(body)
    student = r"(?:学生|留学)(?:签证|路线)?|\bstudent visa\b"
    if not re.search(student, text, re.I):
        return False
    named_page = bool(re.search(
        r"官网|网页|网站|链接|入口|表格|"
        r"\b(?:official (?:page|website|link)|website|link|application form)\b",
        text,
        re.I,
    ))
    apply_question = bool(re.search(
        r"(?:怎么|如何|哪里|在哪).{0,14}(?:申请|办理)|"
        r"(?:申请|办理).{0,14}(?:怎么|如何|哪里|在哪|流程|步骤)|"
        r"(?:不知道|不清楚)?(?:从)?哪里开始|"
        r"\b(?:how|where).{0,24}\bapply\b|\bapplication (?:process|steps)\b|"
        r"\bwhere (?:do I |should I )?start\b|\bhow (?:do I |should I )?get started\b",
        text,
        re.I,
    ))
    return named_page or apply_question


def _route_check_answer(language: str, today: date, *, body: str = "") -> str:
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return (
            "你提到的路线不是普通 Standard Visitor；我需要先重新核对该类别的最新官方说明，"
            "目前不能把访问签证的费用、时间或材料要求直接套用。"
            if language == "zh" else
            "The route you mentioned is not an ordinary Standard Visitor visa. I need to recheck current verified "
            "guidance for that category before giving its application, fee, timing or evidence details."
        )
    if _student_application_page_requested(body):
        answer = (
            "你问的是 Student visa 的官方申请入口，它不能套用我们当前 Standard Visitor "
            "流程的费用、时间或材料说明。可以从下面的 GOV.UK Student visa 官方页开始，"
            "再按课程和个人情况单独核对这条路线；这条咨询本身不会更改当前档案的路线。"
            if language == "zh" else
            "You asked for the official Student visa application entry. Its fees, timing and evidence rules "
            "cannot be taken from this case's Standard Visitor workflow. Start from the GOV.UK Student visa "
            "page below and check that route separately against the course and personal circumstances. This "
            "question does not itself change the route recorded for the current case."
        )
        return (
            answer
            + (f"\nGOV.UK: Student visa 官方页 — {STUDENT_SOURCE}"
               if language == "zh" else f"\nGOV.UK: Student visa official page — {STUDENT_SOURCE}")
            + (f"\nGOV.UK: 查询适用的英国签证路线 — {ROUTE_CHECK_SOURCE}"
               if language == "zh" else f"\nGOV.UK: Check which UK visa route applies — {ROUTE_CHECK_SOURCE}")
        )
    answer = (
        "你提到的路线可能不是普通 Standard Visitor，申请安排、费用和材料要求需要先按对应路线核实，不能直接套用访问签证说明。"
        if language == "zh" else
        "The route you mentioned may not be an ordinary Standard Visitor visa; its application arrangements, fees and evidence requirements need a separate route check."
    )
    return answer + "\nGOV.UK: " + ROUTE_CHECK_SOURCE


def _route_boundary_requested(text: str) -> bool:
    """A named competing route plus a current information request needs routing.

    This is intentionally independent of the model topic.  It catches terse
    subject lines such as ``ETA application fee`` as well as normal questions,
    without reacting to a historical statement that merely names an old visa.
    """
    return explicit_nonvisitor_route(text) and bool(re.search(
        r"[?？]|申请|办理|费用|收费|多少钱|多久|时间|网页|网站|入口|链接|材料|清单|"
        r"翻译|机票|酒店|预订|要求|需要什么|"
        r"\b(?:what|where|when|how|which|apply|application|fees?|costs?|price|timing|"
        r"decision|website|link|documents?|evidence|checklist|translate|translation|"
        r"book|booking|requirements?)\b",
        normalize_intent_text(text),
        re.I,
    ))


def _booking_answer(body: str, language: str, today: date) -> list[str]:
    booking = re.search(r"机票|酒店|住宿|flight|hotel|accommodation", body, re.I)
    question = re.search(
        r"(?:需要|必须|要不要|是否|能否|可以|要先).{0,12}(?:买|订|预订).{0,4}(?:机票|酒店|住宿)|"
        r"(?:机票|酒店|住宿|预订).{0,8}(?:必须|需要|要先|要不要|证明|材料|证据).{0,12}(?:[?？]|吗)|"
        r"(?:必须|需要|要不要|是否).{0,5}(?:买|订)(?:吗|[?？])|"
        r"(?:do i|must i|should i|have to|need to).{0,25}(?:book|buy|reserve|flight|hotel)|"
        r"(?:flight|hotel|booking).{0,20}(?:required|necessary|evidence|proof).{0,12}\?",
        body, re.I,
    )
    if not booking or not question:
        return []
    return _booking_guidance(
        language,
        today,
        transit=_mentions_current_route(body, TRANSIT_ROUTE),
        other_route=explicit_nonvisitor_route(body) or _mentions_current_route(body, OTHER_ROUTE),
    )


def _booking_guidance(language: str, today: date, *, transit: bool = False,
                      other_route: bool = False) -> list[str]:
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return [
            "关于提前订机票和酒店的问题，我需要先复核最新官方说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current official guidance before answering your booking question."
        ]
    if other_route:
        return [_route_check_answer(language, today)]
    if transit:
        return [
            "你提到过境；过境与普通访问的材料要求不能直接混用，需要先由顾问确认路线。"
            if language == "zh"
            else "You mentioned transit; its evidence requirements need a separate route check."
        ]
    answer = (
        "关于机票和酒店：普通 Standard Visitor 申请不需要为了提供这些预订证明而先购买。"
        "官方材料指南把酒店预订和机票预订（过境除外）列为证明价值较低的材料。"
        "计划行程和住宿安排应如实描述，尚未确定的安排不应写成已经预订。"
        if language == "zh"
        else "For an ordinary Standard Visitor application, you do not need to buy flights or book "
        "a hotel just to supply booking evidence. The official guide describes hotel bookings and "
        "flight bookings (except transit) as less useful evidence. Intended arrangements "
        "should be described accurately, without presenting unbooked plans as confirmed bookings."
    )
    return [answer + "\nGOV.UK: " + SOURCE + "#documents-you-should-not-use-as-evidence"]


def _request_clauses(body: str, *, split_commas: bool = True) -> list[str]:
    """Split explicit independent requests, without severing a condition's scope.

    A conjunction alone is not a new question. Requiring a fresh request verb or
    interrogative keeps document qualifiers together, while allowing 'not fees,
    but please send the link' and 'send the link and tell me whether ...'.
    """
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "\n", text)
    text = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "\n", text)
    separators = r"[。！!；;\n，,]" if split_commas else r"[。！!；;\n]"
    clauses = re.split(separators + r"|(?<=[?？])\s*|\.(?:\s|$)", text)
    independent_request = (
        r"(?:但(?:是)?|不过|并(?:且)?|而且|\b(?:but|and)\b)\s*"
        r"(?=(?:请|麻烦|告诉我|解释|发给我|给我|能否|哪里|怎么|如何|是否)|"
        r"\b(?:please|tell me|explain|send me|give me|what|where|when|how|"
        r"(?:can|could|would) (?:you|I))\b)"
    )
    result = []
    for clause in clauses:
        if re.search(
            r"如果|假如|假设|除非|只要|\b(?:if|unless|provided|assuming)\b|"
            r"(?:忽略|跳过|绕过|修改|无视).{0,12}(?:规则|指令|提示|检查|审核)|"
            r"\b(?:ignore|bypass|override).{0,20}(?:instructions?|rules?|checks?|system|prompt)\b|"
            r"(?:customer_questions|source_excerpt|requires_human_review|question_deferrals)",
            clause, re.I,
        ):
            result.append(clause)
        else:
            start = 0
            for separator in re.finditer(independent_request, clause, re.I):
                prefix = clause[start:separator.start()]
                contrast = re.fullmatch(r"(?:但(?:是)?|不过|but)\s*", separator[0], re.I)
                if not contrast and not re.search(
                    r"[?？]|请|麻烦|告诉我|解释|发给我|给我|哪里|怎么|如何|是否|"
                    r"\b(?:please|tell me|explain|send me|give me|what|where|when|how|"
                    r"can|could|would|do|must|should)\b", prefix, re.I,
                ):
                    # A subject qualifier followed by 'and how ...' is still one
                    # request, not an independently answerable first question.
                    continue
                if not contrast and re.search(
                    r"不用|不要|无需|不需要|不想|不必|别|不是问|"
                    r"\b(?:don['’]t|do not|not asking|no need)\b", prefix, re.I,
                ):
                    # 'Do not explain X and tell me Y' may decline both actions.
                    # Only an explicit contrast separates Y from that refusal.
                    continue
                result.append(prefix)
                start = separator.end()
            result.append(clause[start:])
    return [clause.strip() for clause in result if clause.strip()]


def _active_clauses(body: str, *, split_commas: bool = True) -> list[str]:
    """Quoted and explicitly declined requests are not fresh requests for advice."""
    declined = (
        r"(?:不用|不需要|无需|别|不要|不想|不必).{0,10}(?:发|给|说|讲|解释|介绍|告诉|知道)|"
        r"\b(?:don['’]t|do not|no need to|not asking|not interested).{0,18}"
        r"(?:send|give|explain|tell|know|discuss|about)\b|"
        r"(?:无需|不用|不需要).{0,8}(?:链接|网址|官网)|"
        r"\bno (?:links?|websites?)\b|"
        r"(?:不问|没问|不是问|不(?:想|需要|打算)问|不用回答|别回答|不要回答)|"
        r"\b(?:not asking|did not ask|didn't ask|don't answer|do not answer)\b|"
        r"(?:忽略|跳过|绕过|修改|无视).{0,12}(?:规则|指令|提示|检查|审核)|"
        r"\b(?:ignore|bypass|override).{0,20}(?:instructions?|rules?|checks?|system|prompt)\b|"
        r"(?:customer_questions|source_excerpt|requires_human_review|question_deferrals)|"
        r"\b(?:my friend|he|she|the customer)\s+(?:asked|said|wrote)\b|"
        r"(?:朋友|客户|他|她)(?:说|写道)|(?:朋友|客户)(?:问道|问过)|"
        r"\b(?:I|we)\s+(?:might|may|will)\s+ask\b|(?:以后|将来|到时).{0,8}(?:再问|会问)"
    )
    return [clause for clause in _request_clauses(body, split_commas=split_commas)
            if not re.search(declined, clause, re.I)]


def _normalised_excerpt(text: str) -> str:
    """Use identical matching for intent grounding and answer suppression."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _overlapping_excerpt(left: str, right: str) -> bool:
    left, right = _normalised_excerpt(left), _normalised_excerpt(right)
    return bool(left and right and (left in right or right in left))


def _uninformative_boundary_excerpt(text: str) -> bool:
    """Whether an unsupported excerpt carries no specific question of its own.

    Models sometimes select ``related information`` from a longer, fully reviewed
    application-page request.  That generic noun phrase must not erase the safe
    request around it.  Keep this allow-list deliberately tiny: any route, fact,
    eligibility, fee or other substantive wording remains a conservative boundary.
    """
    normalized = normalize_intent_text(_normalised_excerpt(text)).strip(" 。.!?！？，,;:；：")
    return bool(re.fullmatch(
        r"(?:相关|有关|对应|这方面的?)(?:的)?(?:信息|资讯|资料|内容)|"
        r"(?:这个|该)(?:信息|内容)|"
        r"(?:the\s+)?(?:related|relevant|corresponding)\s+(?:information|details)|"
        r"(?:information|details)\s+(?:about|on)\s+(?:this|that)|"
        r"(?:this|that)\s+(?:information|detail)",
        normalized,
        re.I,
    ))


def _mentions_current_route(body: str, route_pattern: str) -> bool:
    """Ignore only a directly negated route, not uncertainty about that route.

    "Not a student visa" is different from "not sure about a student visa".
    Keep ambiguous and hypothetical route mentions for the conservative route check.
    """
    negated_prefix = (
        r"(?:不是|并非|不(?:申请|办理)|不打算(?:申请|办理)|不想(?:申请|办理))\s*$|"
        r"\b(?:not\s+(?:(?:applying|going to apply)\s+for\s+)?(?:a\s+|the\s+)?|"
        r"(?:don't|do not)\s+(?:want|need)\s+(?:a\s+|the\s+)?)$"
    )
    for raw_clause in _active_clauses(body):
        clause = normalize_intent_text(raw_clause)
        for match in re.finditer(route_pattern, clause, re.I):
            prefix = clause[:match.start()]
            uncertain = re.search(
                r"(?:是不是|是否|不确定|不知道|不清楚|想知道|想确认|请问|如果|假如|若).{0,16}$|"
                r"\b(?:not sure|uncertain|whether|if).{0,40}$", prefix, re.I,
            )
            question = re.search(r"[?？]|吗", clause[match.end():])
            if uncertain or question or not re.search(negated_prefix, prefix, re.I):
                return True
    return False


def _request_has_other_route(body: str, excerpt: str) -> bool:
    """Use preceding route context, never a different question later in the email.

    An unqualified follow-up inherits the preceding route. Only an explicit
    visitor subject returns to visitor scope; another-route or uncertain mention
    in the same request remains conservative. Exact request containment preserves
    the protection against a model quoting only 'application fee' from a question
    about a different route.
    """
    other_route = False
    matching_scopes = []
    visitor_route = EXPLICIT_VISITOR_ROUTE_PATTERN
    for clause in _active_clauses(body):
        if (explicit_nonvisitor_route(clause)
                or _mentions_current_route(clause, OTHER_ROUTE + "|" + TRANSIT_ROUTE)):
            other_route = True
        elif (_mentions_current_route(clause, visitor_route)
              and not re.search(r"如果|假如|假设|\b(?:if|unless|whether)\b", clause, re.I)):
            other_route = False
        if _overlapping_excerpt(excerpt, clause):
            matching_scopes.append(other_route)
    # Validated excerpts normally match. Keep an unmatched scope conservative.
    return any(matching_scopes) if matching_scopes else (
        explicit_nonvisitor_route(body)
        or _mentions_current_route(body, OTHER_ROUTE + "|" + TRANSIT_ROUTE)
    )


def _question_clauses(body: str) -> list[str]:
    question = (
        r"[?？]|吗|么|如何|怎么|怎样|哪里|哪[个里]|多久|多少|何时|什么时候|几周|几个月|最早|"
        r"(?:请|麻烦|能否|可以).{0,12}(?:告诉|解释|介绍|说|发|给)|发我|给我|"
        r"\b(?:what|where|when|how|can|could|would|do|must|should)\b|"
        r"\b(?:please|send me|give me|tell me|explain)\b"
    )
    return [clause for clause in _active_clauses(body)
            if re.search(question, normalize_intent_text(clause), re.I)]


def route_orientation_question(body: str) -> bool:
    """Recognise a direct request to distinguish two named UK routes."""
    text = normalize_intent_text("\n".join(_active_clauses(body, split_commas=False)))
    if not text or re.search(
        r"^(?:请问[，, ]*)?(?:如果|假如|假设|除非)|"
        r"(?:朋友|客户|同事|哥哥|姐姐|弟弟|妹妹|他|她).{0,18}(?:问|需要|申请)|"
        r"\b(?:if|unless|hypothetically)\b|"
        r"\b(?:my friend|my client|my colleague|my brother|my sister|he|she|they)\b"
        r".{0,28}(?:asked|needs?|appl(?:y|ication))",
        text,
        re.I,
    ):
        return False
    return bool(
        re.search(ROUTE_ORIENTATION_PATTERN, text, re.I)
        and re.search(
            r"还是|哪(?:个|种|条)|什么签证|是否|不确定|不知道|确认|适合|应该|去哪里|"
            r"\b(?:which|whether|not sure|confirm|right route|appropriate|should I|where)\b",
            text,
            re.I,
        )
    )


_OTHER_APPLICANT_FRAME = re.compile(
    r"\b(?:ask(?:ing)?|enquir(?:e|ing)|inquir(?:e|ing))\s+(?:on\s+behalf\s+of|for|about)\s+"
    r"(?:(?:my|a|another|the)\s+)?(?:brother|sister|friend|partner|parent|client|applicant|customer)\b|"
    r"\bon\s+behalf\s+of\b|"
    r"\b(?:my\s+)?(?:brother|sister|friend|partner|parent|client)(?:'s|’s)\s+"
    r"(?:(?:UK|British|standard|visitor|tourist|student)\s+)*(?:visa|application|documents|case)\b|"
    r"\b(?:my\s+)?(?:brother|sister|friend|partner|parent|client)\s+"
    r"(?:wants?|plans?|needs?|is\s+(?:planning|applying)).{0,25}\b(?:visa|apply|application|visit|travel|study)\b|"
    r"\b(?:he|she|they)\s+(?:(?:should|can|would|will|needs?\s+to|wants?\s+to)\s+)?"
    r"(?:prepare|apply|proceed|start|need|want|plan)\b|"
    r"\b(?:what|which|how|where)\b.{0,20}\b(?:he|she|they)\b|"
    r"\b(?:his|her|their)\s+(?:visa|application|documents|preparation|case|next\s+step)\b|"
    r"\b(?:help|prepare|apply|do)\b.{0,25}\b(?:for\s+him|for\s+her|for\s+them|help\s+him|help\s+her)\b|"
    r"(?:替|代|帮)(?:我的?|一位)?(?:弟弟|妹妹|哥哥|姐姐|朋友|同学|家人|父母|客户|别人).{0,10}(?:问|咨询|了解|申请|准备)|"
    r"(?:弟弟|妹妹|哥哥|姐姐|朋友|同学|家人|父母|客户|别人)(?:的)?(?:英国)?(?:签证|申请)|"
    r"(?:弟弟|妹妹|哥哥|姐姐|朋友|同学|家人|父母|客户|别人)"
    r"(?:(?:想|要|打算)(?:去|到|申请|办理)|需要.{0,6}签证)|"
    r"(?:他|她|他们|她们)(?:的|现在|接下来|下一步|需要|应该|要|该|想|打算).{0,14}(?:申请|签证|准备|材料|办|做)|"
    r"(?:帮|替)(?:他|她|他们|她们).{0,12}(?:准备|申请|办|做)", re.I,
)
_OTHER_APPLICATION_FRAME = re.compile(
    OTHER_ROUTE + r"|\b(?:transit|French|Canadian|Australian|US|American)\s+visa\b|"
    r"\bvisa\s+(?:for|to)\s+(?:France|Canada|Australia|the\s+US)\b|"
    r"(?:法国|加拿大|澳洲|澳大利亚|美国|过境)(?:的)?(?:签证|申请)|"
    r"\b(?:university|college|mortgage|loan|job)\s+application\b|"
    r"\bapply(?:ing)?\s+for\s+(?:a|an|the)\s+(?:mortgage|loan|job)\b|(?:大学|学校|贷款|工作)申请", re.I,
)
_OWN_APPLICATION_FRAME = re.compile(
    r"\b(?:my|our)\s+(?:(?:own|current|existing|UK|visitor|tourist|standard)\s+)*"
    r"(?:visa|application|documents|preparation|case)\b|\b(?:as\s+for\s+me|back\s+to\s+my)\b|"
    r"(?:我(?:自己)?的|本人的|我的这份)(?:英国|旅游|访问|访客|当前|这次|现在的){0,3}(?:签证|申请|材料|情况)|"
    r"(?:回到|说回|至于|再说)(?:我(?:自己)?|本人)(?:的)?(?:申请|情况|材料)?", re.I,
)
_CURRENT_VISITOR_ROUTE = re.compile(
    r"\b(?:UK|British)\s+(?:standard\s+)?(?:visitor|tourist)(?:\s+visa|\s+application)?\b|"
    r"\bStandard\s+Visitor\b|英国(?:的)?(?:旅游|访问|访客)(?:签证|申请)?", re.I,
)
_OWN_VISIT_FRAME = re.compile(
    r"\bmy\s+(?:own\s+)?(?:UK|British)\s+(?:holiday|visit|trip)\b|"
    r"\bI\s+(?:am\s+visiting|will\s+visit|plan\s+to\s+visit|am\s+going\s+to)\s+"
    r"(?:Britain|the\s+UK)\b.{0,30}\b(?:holiday|visit|family|friends)\b|"
    r"我(?:这次|本人|自己)?(?:是|要|想|打算|计划|准备)?去英国(?:旅游|探亲|访友|访问)", re.I,
)
_OWN_SCOPE_DECLINED = re.compile(
    r"\bnot\s+(?:(?:about|discussing|asking\s+about)\s+)?(?:my|our)\s+"
    r"(?:(?:own|current|UK|visitor|tourist)\s+)*(?:visa|application|case)\b|"
    r"(?:不讨论|不谈|不问|不是问|先不说).{0,8}(?:我|本人).{0,12}(?:签证|申请|情况)", re.I,
)


def _affirmative_scope(pattern: re.Pattern[str], clause: str) -> bool:
    """A declined/negated subject is context, not a switch to that subject."""
    for match in pattern.finditer(clause):
        prefix, suffix = clause[:match.start()], clause[match.end():]
        if re.search(
            r"\b(?:not|don't|do\s+not|no\s+need\s+to)\s+"
            r"(?:(?:asking|applying|about|for|discuss|discussing|on|behalf|of|a|the)\s+){0,5}$|"
            r"(?:不是|并非|不要|不用|不问|不谈|不讨论|不申请|不办理)(?:在|要|想|说|为了|替|帮|问|讨论|关于|针对){0,4}$",
            prefix, re.I,
        ) or re.match(r"\s*(?:(?:is|are)\s+)?(?:not\s+relevant|isn't\s+relevant)|(?:的事)?(?:先不谈|不用讨论|不问)", suffix, re.I):
            continue
        return True
    return False


def _next_step_targets_current_case(body: str, excerpt: str) -> bool:
    """Keep this case's suggestion separate from another applicant or application.

    This narrow scope guard is not a general coreference parser. Explicit other-
    case context persists through dependent questions until an affirmative own-
    case anchor restores scope. Ambiguous repeated excerpts are not bound to a
    convenient occurrence. Quotes do not establish a current subject, and a mere
    family/sponsor mention does not change whose application is being discussed.
    """
    current = latest_reply_text(body)
    current = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", current)
    current = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", current)
    text, source = _normalised_excerpt(current), _normalised_excerpt(excerpt)
    locations = list(re.finditer(re.escape(source), text)) if source else []
    if not locations:
        return False
    for location in locations:
        scope = "current"
        for segment in re.finditer(r"[^。.!?？；;，,:：]+", text[:location.end()]):
            clause = segment[0].strip()
            other_person = _affirmative_scope(_OTHER_APPLICANT_FRAME, clause)
            other_application = _affirmative_scope(_OTHER_APPLICATION_FRAME, clause)
            own = _affirmative_scope(_OWN_APPLICATION_FRAME, clause)
            own_visit = _affirmative_scope(_OWN_VISIT_FRAME, clause)
            if _OWN_SCOPE_DECLINED.search(clause):
                scope = "unknown"
            if other_person:
                scope = "other_person"
            elif own_visit:
                # A host's student/work status can be background to the sender's
                # explicitly stated UK holiday; it is not necessarily a new case.
                scope = "current"
            elif other_application:
                scope = "other_application"
            elif own and (scope != "other_application" or _affirmative_scope(_CURRENT_VISITOR_ROUTE, clause)):
                scope = "current"
        if scope != "current":
            return False
    return True


_SPECIAL_REVIEWED_TOPICS = {"eligibility_overview", "biometrics", "after_apply"}


def _self_employed_account_comparison(text: str) -> bool:
    """Whether a self-employed customer contrasts personal and business accounts."""
    return bool(
        re.search(
            r"自雇|自己经营|经营(?:公司|业务)|个体|公司老板|"
            r"\b(?:self-employed|own (?:a |my )?business|business owner)\b",
            text,
            re.I,
        )
        and re.search(
            r"个人(?:账户|流水)|私人账户|\b(?:personal|private) (?:bank )?account",
            text,
            re.I,
        )
        and re.search(
            r"公司(?:账户|流水)|企业账户|对公账户|"
            r"\b(?:business|company|corporate) (?:bank )?account",
            text,
            re.I,
        )
    )


def _special_request_scope_is_safe(body: str, clause: str) -> bool:
    """Keep deterministic public guidance on this sender's current Visitor question.

    These answers explain official process and general criteria.  They never bind a
    hypothetical, a reported third-party case or another visa route to this case.
    """
    text = normalize_intent_text(clause)
    if (not text or not _next_step_targets_current_case(body, clause)
            or _request_has_other_route(body, clause)):
        return False
    return not bool(re.search(
        r"^(?:如果|假如|假设|除非|万一)|"
        r"\b(?:if|unless|hypothetically|assuming|suppose)\b|"
        r"(?:保证|包过|一定).{0,16}(?:获批|过签|批准)|"
        r"\b(?:guarantee|certain(?:ly)?|promise).{0,20}(?:approval|approved|visa)\b",
        text,
        re.I,
    ))


def _deterministic_reviewed_topics(body: str, clause: str) -> list[str]:
    """Recognise three narrow FAQs even when a model misses or broadens them.

    The caller supplies a current question clause.  This function only selects a
    reviewed answer; it cannot decide eligibility, mutate the case or infer that a
    visa has been submitted.
    """
    if not _special_request_scope_is_safe(body, clause):
        return []
    text = normalize_intent_text(clause)
    topics: list[str] = []
    visitor = bool(re.search(
        r"Standard Visitor|英国(?:普通|标准)?(?:访问|访客|旅游)(?:签证|路线)?|"
        r"\b(?:UK|British)\s+(?:(?:standard\s+)?visitor|tourist)(?:\s+visa)?\b|"
        r"\bvisitor visa\b",
        text,
        re.I,
    ))
    eligibility = bool(re.search(
        r"(?:符合|满足|有没有|有|具备).{0,10}(?:资格|条件|要求)|"
        r"(?:一般)?资格要求|(?:申请|资格)(?:条件|要求)|"
        r"(?:资格|条件|要求).{0,16}(?:是什么|有哪些|哪些|符合|满足|申请|吗)|"
        r"\b(?:am I|are we|do I|can I)\b.{0,24}\b(?:eligible|qualif(?:y|ied)|meet)\b|"
        r"\b(?:eligibility|eligible|qualif(?:y|ication)|requirements?)\b.{0,28}"
        r"\b(?:Standard Visitor|visitor visa|apply|application|meet|what|which|do I|am I)\b|"
        r"\b(?:Standard Visitor|visitor visa)\b.{0,28}\b(?:eligibility|eligible|qualif(?:y|ied)|requirements?)\b",
        text,
        re.I,
    ))
    if visitor and eligibility and not re.search(
        r"拒签|通过率|获批概率|能不能过|多大概率|"
        r"\b(?:refusal|refused|approval chances?|probability|likelihood)\b",
        text,
        re.I,
    ):
        topics.append("eligibility_overview")
    if re.search(
        r"生物信息|采集指纹|录指纹|按指纹|指纹和照片|"
        r"签证申请中心|签证中心.{0,12}(?:预约|地点|材料|带什么)|"
        r"(?:预约|地点|带什么).{0,12}签证中心|"
        r"\bbiometric(?:s| information)?\b|\bfingerprints?\b|"
        r"\bvisa application cent(?:re|er)\b|\bVAC appointment\b",
        text,
        re.I,
    ):
        topics.append("biometrics")
    after_action = bool(re.search(
        r"查(?:询)?(?:申请)?进度|跟踪(?:申请)?|申请状态|"
        r"怎么收到(?:决定|结果|通知)|(?:决定|结果).{0,10}(?:怎么|如何)(?:通知|收到)|"
        r"(?:申请|表格|信息).{0,12}(?:填错|写错|改错|修改|更正|更新)|"
        r"(?:填错|写错|修改|更正).{0,12}(?:申请|表格|信息)|"
        r"撤回(?:申请)?|取消(?:签证)?申请|"
        r"递交后.{0,16}(?:怎么办|下一步|会怎样|会发生什么)|"
        r"\b(?:track|check)\b.{0,20}\b(?:visa )?application\b|"
        r"\bapplication status\b|\bhow (?:will|do) I (?:get|receive)\b.{0,18}\bdecision\b|"
        r"\b(?:change|correct|update)\b.{0,22}\b(?:submitted )?(?:application|form|details?|information)\b|"
        r"\b(?:mistake|error)\b.{0,18}\b(?:application|form)\b|"
        r"\b(?:cancel|withdraw)\b.{0,18}\b(?:visa )?application\b|"
        r"\bafter (?:I|we)(?:'ve| have)? (?:applied|submitted)\b.{0,24}"
        r"\b(?:what happens|next|track|status|change|correct|cancel|withdraw|decision|notification)\b",
        text,
        re.I,
    ))
    if after_action:
        topics.append("after_apply")
    return topics


def is_generic_uk_preparation_enquiry(body: str, excerpt: str | None = None) -> bool:
    """Recognize a small, affirmative orientation request, not visa eligibility.

    The full current message must fit an ordinary UK preparation enquiry. A model
    cannot strip a condition, third person's case, refusal or approval question
    from its excerpt to enter this path. The result authorizes only conditional
    orientation; it supplies no applicant fact, selected route or preparation consent.
    """
    reply = latest_reply_text(body)
    # A current "no links" preference changes presentation, not whether a
    # first-time applicant asked for orientation. Reuse the reviewed preference
    # scope and remove only that clause; conditions, quotes and other applicants
    # are still excluded by the same scope helper and the boundaries below.
    if wants_no_links(reply):
        reply = ". ".join(
            clause for clause in _preference_current_clauses(reply)
            if not wants_no_links(clause)
        )
    current = _normalised_excerpt(reply)
    if (not current or len(current) > 500 or _OTHER_APPLICANT_FRAME.search(current)
            or _OTHER_APPLICATION_FRAME.search(current)
            or re.search(
                r"[“”「」『』\"`]|(?<!\w)[‘’]|[‘’](?!\w)|(?<!\w)'[^'\n]+'(?!\w)|"
                r"如果|假如|假设|除非|只要|不想|先不|暂不|暂停|"
                r"获批|过签|批准|保证|门槛|存款|余额|收入|拒签|逾期|"
                r"\b(?:if|unless|assuming|suppose|whether|never|cannot|pause|"
                r"approval|approved|eligible|guarantee|threshold|savings|income|refusal|overstay)\b|"
                r"\b(?:can|won|wouldn|couldn|shouldn)['’]t\b",
                current, re.I,
            )):
        return False
    separator = r"[\s，,、。.!！？?：:；;—-]*"
    zh_intro = (
        r"(?:(?:我)?(?:第一次|初次)?(?:想|打算|准备|要)?(?:申请|办(?:理)?))?"
        r"英国(?:的)?(?:(?:普通|标准)?(?:访问|访客|旅游))?签证|"
        r"(?:我)?(?:想|打算|准备|要)去英国(?:旅游|旅行)"
    )
    zh_question = (
        r"(?:签证)?(?:请问|麻烦问一下)?(?:"
        r"(?:需要|要)(?:先)?(?:准备|提供)?(?:些)?(?:什么|哪些|啥)(?:材料|资料|文件|东西)?|"
        r"(?:该|应该)?(?:先)?准备(?:什么|哪些|啥)(?:材料|资料|文件|东西)?|"
        r"(?:材料|资料|文件)?(?:要|该|应该)?(?:怎么|如何)准备|"
        r"(?:该|应该)?(?:从哪(?:里)?|怎么)(?:开始|入手)|"
        r"(?:不知道|不清楚)(?:该|应该)?(?:从哪(?:里)?|怎么)(?:开始|入手))"
    )
    en_intro = (
        r"(?:(?:this\s+is|it(?:\s+is|['’]s))\s+)?my\s+first\s+"
        r"(?:UK|British)\s+(?:(?:standard\s+)?visitor\s+|tourist\s+)?visa\s+application|"
        r"(?:(?:(?:I|we)\s+(?:want|need|plan|would like)\s+to\s+apply\s+for|"
        r"I(?:\s+am|['’]m)\s+applying\s+for|"
        r"(?:it(?:\s+is|['’]s)\s+)?my first time applying for|for)\s+)?"
        r"(?:a\s+|the\s+)?(?:UK|British)\s+(?:(?:standard\s+)?visitor\s+|tourist\s+)?visa"
    )
    en_question = (
        r"(?:what(?:\s+(?:documents?|evidence|paperwork))?\s+(?:do I|should I|will I)\s+"
        r"(?:need(?:\s+to\s+(?:prepare|provide))?|prepare|provide|get ready)|"
        r"what\s+(?:(?:documents?|evidence|paperwork)\s+)?to\s+(?:prepare|provide|get ready)|"
        r"which documents\s+(?:do I need|should I prepare)|"
        r"how\s+(?:do|should)\s+I\s+(?:start|get started|prepare)|where\s+do\s+I\s+(?:start|begin)|"
        r"I\s+(?:do not|don't|don’t)\s+know\s+(?:where|how)\s+to\s+(?:start|begin|get started))"
    )
    question_joiner = rf"{separator}(?:(?:以及|还有|还要|和|或|或者|and|or|also){separator})?"
    patterns = [
        rf"(?:(?:你好|您好){separator})?(?:{zh_intro}){separator}(?P<question>{zh_question})"
        rf"(?:{question_joiner}(?P<question_2>{zh_question}))?"
        rf"(?:呢|呀|啊)?{separator}(?:谢谢{separator})?",
        rf"(?:(?:hello|hi){separator})?(?:{en_intro}){separator}(?P<question>{en_question})"
        rf"(?:{question_joiner}(?P<question_2>{en_question}))?"
        rf"{separator}(?:(?:thanks|thank you){separator})?",
    ]
    for pattern in patterns:
        matched = re.fullmatch(pattern, current, re.I)
        questions = (
            [matched.group("question"), matched.group("question_2")]
            if matched else []
        )
        if matched and (
            excerpt is None
            or any(question and _overlapping_excerpt(excerpt, question) for question in questions)
        ):
            return True
    return False


def _visa_eta_start_clause(clause: str) -> bool:
    """A safe request to distinguish visa/ETA and find the official start."""
    text = normalize_intent_text(clause)
    if (not _next_step_targets_current_case(clause, clause)
            or _OTHER_APPLICANT_FRAME.search(text)
            or re.search(
                r"^(?:如果|假如|假设|除非)|获批|过签|保证|拒签|"
                r"\b(?:if|unless|hypothetically|guarantee|approval|approved|refusal)\b",
                text,
                re.I,
            )):
        return False
    visa_eta = re.search(
        r"(?:签证.{0,18}(?:ETA|电子旅行(?:许可|授权))|"
        r"(?:ETA|电子旅行(?:许可|授权)).{0,18}签证|"
        r"\bvisa\b.{0,24}\bETA\b|\bETA\b.{0,24}\bvisa\b)",
        text,
        re.I,
    )
    start = re.search(
        r"在哪|哪里|怎么|如何|从哪|开始|申请|官网|网页|入口|链接|"
        r"\b(?:where|how|start|begin|apply|application|official|website|page|link)\b",
        text,
        re.I,
    )
    return bool(visa_eta and start)


def _visa_eta_start_answer(language: str, today: date) -> str:
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return (
            "签证与 ETA 的适用范围需要先重新核对最新 GOV.UK 说明，目前不能发送可能过期的申请入口。"
            if language == "zh" else
            "I need to recheck the latest GOV.UK guidance on visas and ETAs before sending a potentially stale application route."
        )
    return (
        "是否需要 ETA 还是签证，要按护照、赴英目的和停留安排判断；先用下面的 GOV.UK 官方查询入口，不要只按名称猜。\n"
        f"GOV.UK: {ROUTE_CHECK_SOURCE}\n"
        "如果查询结果显示需要申请 Standard Visitor 签证，再从下面的官方页面选择 Apply now；"
        "这不是对你个人路线或资格的结论。\n"
        f"GOV.UK: {APPLICATION_SOURCE}"
        if language == "zh" else
        "Whether you need an ETA or a visa depends on your passport, purpose and intended stay. Start with the "
        "official GOV.UK checker rather than choosing from the label alone.\n"
        f"GOV.UK: {ROUTE_CHECK_SOURCE}\n"
        "If the checker shows that you need a Standard Visitor visa, use the official page below and select "
        "Apply now. This is not a decision about your individual route or eligibility.\n"
        f"GOV.UK: {APPLICATION_SOURCE}"
    )


_APPLICATION_QUALIFIERS = (
    r"如果|假如|假设|除非|只要|是否符合|能不能获批|能获批|保证|包过|足够|够不够|拒签|犯罪|未成年|"
    r"费用|收费|多少钱|加急|优先服务|多久|几周|几个月|退费|退款|取消|撤回|改签|"
    r"绕过|伪造|编造|造假|忽略.{0,10}(?:规则|指令|检查)|"
    r"\b(?:if|unless|assuming|suppose|provided|eligible|eligibility|qualify|qualifies|approved|approval|"
    r"guarantee\w*|sufficient|enough|refusal|refused|conviction|minor|fees?|costs?|prices?|priority|"
    r"expedite\w*|refund\w*|cancel\w*|withdraw\w*|fake|forg\w*|fabricat\w*|bypass|override)\b|"
    r"\b(?:how long|how early|how far in advance|how many weeks|how much)\b|"
    r"(?:customer_questions|source_excerpt|requires_human_review)"
    r"|(?:朋友|同学|客户|哥哥|姐姐|他|她).{0,10}(?:问我|问过|说过|写道)|"
    r"\b(?:friend|sister|brother|client|customer|he|she)\b.{0,25}\b(?:asked|said|wrote)\b"
)
_APPLICATION_VISITOR = (
    r"英国(?:(?:普通|标准)?(?:访问|访客)|旅游)?签证|"
    r"\b(?:UK|British)\s+(?:(?:standard\s+)?visitor\s+|tourist\s+)?visa\b|"
    r"\bStandard Visitor\b"
)
_EXPLICIT_VISITOR = (
    r"英国(?:(?:普通|标准)?(?:访问|访客)|旅游)签证|"
    r"\b(?:UK|British)\s+(?:(?:standard\s+)?visitor|tourist)\s+visa\b|\bStandard Visitor\b"
)
_FORM_ENTRY_REQUEST = (
    r"\bwhere\s+(?:do|can|should)\s+I\s+(?:open|find|access|fill\s+(?:in|out))\s+"
    r"(?:the|my)\s+(?:visa\s+)?application\s+form\b"
)
_APPLICATION_ENTRY_REQUEST = (
    _FORM_ENTRY_REQUEST + "|" +
    r"(?:在哪|哪里|怎么|如何).{0,14}(?:申请|办理)|"
    r"(?:申请|办理|签证).{0,18}(?:网页|网站|入口|官网|网址|链接|流程|步骤)|"
    r"(?:网页|网站|入口|官网|网址|链接).{0,18}(?:申请|办理)|"
    r"\b(?:which|what)\s+(?:web\s?page|website|site|page|link|URL)\b.{0,55}\bapply\b|"
    r"\b(?:which|what)\s+(?:web\s?page|website|site|page|link|URL)\s+(?:to|should I|do I)\s+use\b|"
    r"\bhow\s+(?:(?:do|can|should)\s+I\s+|to\s+)(?:get started|start|begin)\b|"
    r"\b(?:where|how)\b.{0,35}\bapply\b|"
    r"\b(?:apply|application|visa)\b.{0,30}\b(?:web\s?page|page|website|site|link|URL|steps?|process)\b|"
    r"\b(?:send|give|show|tell)\b.{0,45}\b(?:web\s?page|page|website|link|URL|steps?|process)\b"
)


def reviewed_application_requests(body: str, *, known_visitor_context: bool = False) -> list[str]:
    """Find explicit ordinary application-entry questions, not eligibility advice.

    This is shared by proposal normalization and the no-proposal fallback. Keep
    conditions around dependent clauses before splitting independent requests;
    never manufacture facts, selected routes or consent from an information query.
    """
    if not body or len(body) > 6000:
        return []
    result = []
    visitor_context = False
    explicit_visitor = False
    # Keep comma/semicolon/newline conditions intact until a sentence ends.
    for scope in re.split(r"[。！!]|(?<=[?？])\s*|\.(?:\s|$)", _unquoted_reply_text(body)):
        if re.search(r"医疗|治疗|付费活动|\b(?:medical treatment|paid engagements?)\b", scope, re.I):
            visitor_context = False
            explicit_visitor = False
            continue
        if (
            explicit_nonvisitor_route(scope)
            or _mentions_current_route(
                scope,
                OTHER_ROUTE
                + "|"
                + TRANSIT_ROUTE
                + r"|\b(?:graduate|spouse|family|child student) visa\b|配偶签证|毕业生签证|儿童学生签证",
            )
        ):
            visitor_context = False
            explicit_visitor = False
            continue
        if re.search(_APPLICATION_VISITOR, scope, re.I) and re.search(
            r"不再(?:申请|办理)|不打算(?:申请|办理)|不申请|不办理|"
            r"\b(?:no longer|not)\s+(?:applying|planning to apply)\b", scope, re.I,
        ):
            visitor_context = False
            explicit_visitor = False
            continue
        if re.search(_APPLICATION_QUALIFIERS, scope, re.I):
            continue
        if (_mentions_current_route(scope, _APPLICATION_VISITOR)
                and _next_step_targets_current_case(body, scope.strip())):
            visitor_context = True
            explicit_visitor = _mentions_current_route(scope, _EXPLICIT_VISITOR)
        for clause in _active_clauses(scope):
            if ((not visitor_context and not (known_visitor_context and re.fullmatch(
                    _FORM_ENTRY_REQUEST, clause.strip(" ?？.!。"), re.I)))
                    or not re.search(_APPLICATION_ENTRY_REQUEST, clause, re.I)
                    or _request_has_other_route(body, clause)
                    or not _next_step_targets_current_case(body, clause)):
                continue
            if (not explicit_visitor and re.fullmatch(
                r"(?:怎么|如何)(?:开始|着手)[?？]?|how\s+(?:(?:do|can|should)\s+I\s+|to\s+)"
                r"(?:get started|start|begin)[?？]?", clause.strip(), re.I,
            )):
                # "UK visa; how do I start?" still needs general orientation.
                # Do not replace it with a specific application-route tutorial.
                continue
            if re.search(
                r"不是问|不用|不要|别(?:再)?发|无需|不需要|以后|将来|下次|"
                r"\b(?:not asking|do not|don't|don’t|no need|later|tomorrow)\b",
                clause, re.I,
            ):
                continue
            result.append(clause)
    return result


def _general_application_proposal(body: str, excerpt: str, *, known_visitor_context: bool = False) -> bool:
    # An unsupported proposal may contain a narrow-looking substring and an
    # important qualifier. Normalize only a fully supported request, preserving
    # the raw excerpt/confidence and all unsupported-boundary precedence.
    if (_uninformative_boundary_excerpt(excerpt)
            or re.search(_APPLICATION_QUALIFIERS, excerpt, re.I)
            or _request_has_other_route(body, excerpt)):
        return False
    spans = reviewed_application_requests(body, known_visitor_context=known_visitor_context)
    if not any(_overlapping_excerpt(excerpt, span) for span in spans):
        return False
    for clause in _question_clauses(excerpt):
        if current_no_intake_clause(clause):
            continue
        if any(_overlapping_excerpt(clause, span) for span in spans):
            continue
        if re.fullmatch(r"(?:怎么|如何)(?:开始|着手)[?？]?|(?:tell me\s+)?how to get started"
                        r"(?: before asking (?:me )?for personal details)?[?.]?",
                        clause.strip(), re.I):
            continue
        return False
    return True


def _unsafe_application_proposal_scope(body: str, excerpt: str) -> bool:
    """Keep an application label from laundering a non-current or unsafe request.

    Fee and timing questions may legitimately share a clause with an application
    process request, so the broad ``_APPLICATION_QUALIFIERS`` pattern is not used
    here. Those reviewed topics retain their own bounded answers.
    """
    if not _next_step_targets_current_case(body, excerpt):
        return True
    # A condition controls its own sentence, not an independent question that
    # follows after ``?``/``.``.  The former whole-message check caused a safe
    # current application-page question to be discarded merely because the
    # customer had first said "if I continue later" about a different request.
    containing_sentences = [
        sentence
        for sentence in re.split(r"[。！!]|(?<=[?？])\s*|\.(?:\s|$)", _unquoted_reply_text(body))
        if _overlapping_excerpt(excerpt, sentence)
    ]
    if any(re.search(
        r"^(?:如果|假如|假设|除非|只要)|\b(?:if|unless|assuming|suppose|provided)\b",
        normalize_intent_text(sentence).strip(),
        re.I,
    ) and not _standalone_chinese_link_preference(sentence) for sentence in containing_sentences):
        return True
    clauses = [clause for clause in _active_clauses(body)
               if _overlapping_excerpt(excerpt, clause)]
    if not clauses:
        return True
    return any(re.search(
        r"^(?:如果|假如|假设|除非|只要)|"
        r"获批|过签|批准|保证|包过|是否符合|能否获批|够不够|拒签|"
        r"绕过|伪造|编造|造假|"
        r"\b(?:if|unless|assuming|suppose|provided|eligible|eligibility|qualify|approved|approval|"
        r"guarantee\w*|sufficient|enough|refusal|refused|fake|forg\w*|fabricat\w*|bypass|override)\b",
        normalize_intent_text(clause),
        re.I,
    ) and not _standalone_chinese_link_preference(clause) for clause in clauses)


def _scoped_fee_context(body: str, fee_clauses: list[str]) -> str:
    """Retain an own application's preceding validity, without replaying facts."""
    validity = ""
    extended_visit = ""
    contexts = []
    for clause in _active_clauses(body, split_commas=False):
        own = _next_step_targets_current_case(body, clause)
        if (_request_has_other_route(body, clause) or not own
                or _mentions_current_route(clause, r"\b(?:spouse|graduate|family) visa\b|配偶签证|毕业生签证")):
            validity = ""
            extended_visit = ""
        else:
            if _extended_visit_descriptor(clause):
                extended_visit = clause
            if re.search(_APPLICATION_VISITOR, clause, re.I) or any(
                _overlapping_excerpt(clause, item) for item in fee_clauses
            ):
                six_month = re.search(
                    r"(?:六|6)\s*个月|\b(?:six|6)[ -]months?\b", clause, re.I
                )
                long_term = re.search(
                    r"(?:两|二|五|十|2|5|10)\s*年|长期|"
                    r"\b(?:two|five|ten|2|5|10)[ -]years?\b|\blong[- ]term\b",
                    clause,
                    re.I,
                )
                if long_term:
                    validity = long_term[0]
                elif six_month:
                    validity = ""
                    extended_visit = ""
        if any(_overlapping_excerpt(clause, item) for item in fee_clauses):
            parts = [part for part in (validity, extended_visit, clause) if part]
            contexts.append("\n".join(dict.fromkeys(parts)))
    return "\n".join(contexts) or "\n".join(fee_clauses)


_EXTENDED_MONTHS = re.compile(
    r"(?:7|8|9|10|11|12|七|八|九|十|十一|十二)\s*(?:个)?月|"
    r"\b(?:seven|eight|nine|ten|eleven|twelve|7|8|9|10|11|12)[ -]months?\b",
    re.I,
)
_MEDICAL_VISIT = re.compile(
    r"私人医疗|医疗治疗|接受治疗|就医|看病|"
    r"\b(?:private )?medical (?:treatment|care|visit)\b|"
    r"\b(?:receive|have|undergo) (?:private )?(?:medical )?treatment\b",
    re.I,
)
_ACADEMIC_VISIT = re.compile(
    r"学术访问|访问学者|研究人员|科学家|高级医生|高级牙医|"
    r"\b(?:academic visit|visiting academic|scientist|researcher|senior doctor|"
    r"senior dentist|formal academic exchange)\b",
    re.I,
)


def _extended_visit_descriptor(text: str) -> bool:
    """Keep a bounded 7-to-12-month visit description beside its fee question."""
    if not _EXTENDED_MONTHS.search(text):
        return False
    if _MEDICAL_VISIT.search(text) or _ACADEMIC_VISIT.search(text):
        return True
    duration = _EXTENDED_MONTHS.pattern
    return bool(re.search(
        rf"(?:访问|旅行|行程|停留|会议).{{0,16}}(?:{duration})|"
        rf"(?:{duration}).{{0,5}}(?:的)?(?:访问|旅行|行程|停留|会议|访客签证)|"
        rf"\b(?:visit|travel|trip|stay|conference)\b.{{0,20}}(?:for )?(?:{duration})|"
        rf"(?:{duration})[- ](?:visit|trip|stay|visitor visa|conference)\b",
        text,
        re.I,
    ))


def _extended_visitor_fee_kind(text: str) -> str | None:
    """Return only the two GOV.UK special-duration fee rows we have reviewed."""
    if not _extended_visit_descriptor(text):
        return None
    if _MEDICAL_VISIT.search(text):
        months = _duration_months(text)
        return "medical" if months is not None and 7 <= months <= 11 else "other_extended"
    if _ACADEMIC_VISIT.search(text) and not re.search(
        r"学术会议|会议|\b(?:academic )?conference\b", text, re.I
    ):
        months = _duration_months(text)
        return "academic" if months is not None and 7 <= months <= 12 else "other_extended"
    return "other_extended"


def _duration_months(text: str) -> int | None:
    match = _EXTENDED_MONTHS.search(text)
    if match is None:
        return None
    token = match[0].casefold()
    values = {
        "十一": 11,
        "十二": 12,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    digits = re.search(r"\d+", token)
    if digits:
        return int(digits[0])
    return next((value for label, value in values.items() if label in token), None)


def validated_customer_questions(body: str, proposals: list[CustomerQuestion]) -> list[CustomerQuestion]:
    """Keep only source-grounded current intents, without treating topics as facts.

    Literal support cannot prove semantic classification. The model still may select an
    irrelevant allowed topic; the downstream answer remains reviewed and conditional.
    Validate the whole containing clause, so a substring cannot strip away a refusal.
    """
    active = _active_clauses(body)
    accepted: dict[tuple[str, str], CustomerQuestion] = {}
    for proposal in proposals:
        excerpt = _normalised_excerpt(proposal.source_excerpt)
        fragments = [_normalised_excerpt(fragment) for fragment in _request_clauses(proposal.source_excerpt)]
        generic_orientation = is_generic_uk_preparation_enquiry(body, proposal.source_excerpt)
        if (proposal.confidence >= 0.8 and excerpt
                and excerpt in _normalised_excerpt(latest_reply_text(body)) and fragments
                and (
                    generic_orientation
                    or excerpt in _normalised_excerpt("\n".join(active))
                    or all(
                        any(fragment in _normalised_excerpt(clause) for clause in active)
                        for fragment in fragments
                    )
                )):
            matching_active = [
                clause for clause in active if _overlapping_excerpt(proposal.source_excerpt, clause)
            ]
            deterministic_special = list(dict.fromkeys(
                topic
                for clause in matching_active
                for topic in _deterministic_reviewed_topics(body, clause)
            ))
            if (proposal.topic in _SPECIAL_REVIEWED_TOPICS
                    and matching_active
                    and not any(_question_clauses(clause) for clause in matching_active)):
                # A completed-action statement (for example, "I attended my
                # biometrics") is not turned into a fresh FAQ if a model
                # mistakenly labels it as one.
                continue
            if proposal.topic == "unsupported" and len(deterministic_special) == 1:
                # A model's broad boundary must not erase an obvious, fully
                # reviewed process question in the same current clause.
                proposal = proposal.model_copy(update={"topic": deterministic_special[0]})
            elif proposal.topic in _SPECIAL_REVIEWED_TOPICS and (
                not matching_active
                or not all(_special_request_scope_is_safe(body, clause)
                           for clause in matching_active)
            ):
                # The model may identify semantics, but it cannot strip another
                # route, a hypothetical or another applicant from the question.
                proposal = proposal.model_copy(update={"topic": "unsupported"})
            if (proposal.topic in {"unsupported", "next_step", "document_checklist"}
                    and _general_application_proposal(body, proposal.source_excerpt)):
                proposal = proposal.model_copy(update={"topic": "application"})
            if (proposal.topic in {"application", "unsupported", "next_step", "document_checklist", "off_topic"}
                    and route_orientation_question(proposal.source_excerpt)):
                proposal = proposal.model_copy(update={"topic": "route_orientation"})
            if (proposal.topic == "route_orientation"
                    and not route_orientation_question(proposal.source_excerpt)):
                proposal = proposal.model_copy(update={"topic": "unsupported"})
            if (proposal.topic == "application"
                    and _unsafe_application_proposal_scope(body, proposal.source_excerpt)
                    and not _requests_previous_application_link(body)):
                # An application label cannot strip a hypothetical, another
                # applicant or an unknown qualifier from the current request.
                # Terse requests to resend the already-delivered link remain a
                # separately bounded reviewed interaction.
                proposal = proposal.model_copy(update={"topic": "unsupported"})
            if (proposal.topic in {"unsupported", "next_step", "document_checklist"}
                    and sponsor_support_question(proposal.source_excerpt)
                    and not _request_has_other_route(body, proposal.source_excerpt)):
                # A narrow sponsor-letter or relationship-evidence question has a
                # reviewed answer.  A model label cannot turn it into an empty
                # boundary or the entire case checklist.
                proposal = proposal.model_copy(update={"topic": "sponsor_support"})
            if (proposal.topic == "sponsor_support"
                    and (not sponsor_support_question(proposal.source_excerpt)
                         or _request_has_other_route(body, proposal.source_excerpt))):
                proposal = proposal.model_copy(update={"topic": "unsupported"})
            school_obstacle = (school_record_reported(body)
                and bool(re.search(r"学校|在读|在学|怎么办|\b(?:university|school|enrol\w*)\b|what should I do",
                                   proposal.source_excerpt, re.I)))
            if (proposal.topic == "next_step" and not school_obstacle
                    and not _next_step_targets_current_case(body, proposal.source_excerpt)):
                continue
            if proposal.topic in {"document_checklist", "unsupported", "next_step"} and school_obstacle:
                proposal = proposal.model_copy(update={"topic": "document_checklist"})
            if proposal.topic == "unsupported" and generic_orientation:
                # Correct only a strictly recognized generic request. Keep the raw
                # proposal object, confidence and original evidence unchanged.
                proposal = proposal.model_copy(update={"topic": "document_checklist"})
            if (proposal.topic in {"document_checklist", "unsupported", "next_step"}
                    and not _request_has_other_route(body, proposal.source_excerpt)
                    and reviewed_document_preparation(proposal.source_excerpt, "en")
                    and all(reviewed_document_preparation(clause, "en") for clause in
                            _active_clauses(body, split_commas=False)
                            if _overlapping_excerpt(proposal.source_excerpt, clause))):
                # A narrow, reviewed operational question must not turn into the
                # entire checklist or the next missing identity field. Do not
                # rescue an off-topic question or strip qualifiers from a clause.
                proposal = proposal.model_copy(update={"topic": "document_checklist"})
            if (
                proposal.topic in {"unsupported", "sponsor_support"}
                and host_not_sponsor_financial_question(body)
                and _overlapping_excerpt(proposal.source_excerpt, body)
            ):
                # The host/accommodation distinction and the host-bank question
                # are covered by one reviewed preparation answer. Do not prepend
                # a contradictory generic "unverified" notice.
                proposal = proposal.model_copy(update={"topic": "document_checklist"})
            scope_key = excerpt if proposal.topic in {"off_topic", "unsupported"} else ""
            accepted.setdefault((proposal.topic, scope_key), proposal)
    # Resolve contradictory interpretations before exposing current topics to other
    # consumers (not just FAQ rendering). A narrow answer must not revive a request
    # whose same clause was classified as outside scope or not safely answerable.
    for boundary in ("off_topic", "unsupported"):
        excerpts = [item.source_excerpt for item in accepted.values() if item.topic == boundary]
        restricted = [clause for clause in active if any(_overlapping_excerpt(excerpt, clause) for excerpt in excerpts)]
        accepted = {key: item for key, item in accepted.items()
                    if item.topic in {boundary, "off_topic"} or not any(
                        _overlapping_excerpt(item.source_excerpt, clause) for clause in restricted
                    )}
    return list(accepted.values())


@dataclass(frozen=True)
class ReviewedAnswerPlan:
    answers: list[str]
    reviewed_answers: list[tuple[str, str]]
    selected_topics: list[str]
    omitted_topics: list[str]
    omission_notice: str = ""


def _deduplicate_reviewed_source_lines(
    items: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """List a shared GOV.UK URL once while keeping answer text auditable.

    Only standalone reviewed source lines are removed. Advice prose and
    overview-labelled links take precedence and remain untouched, so this
    cannot change a claim or hide which action a link performs.
    """
    def canonical(url: str) -> str:
        return url.rstrip(".,;:，。；：").split("#", 1)[0]

    def source_url(line: str) -> str | None:
        if not re.match(r"\s*GOV\.UK:\s*", line):
            return None
        urls = re.findall(r"https?://\S+", line)
        return urls[-1] if len(urls) == 1 else None

    semantic_urls = {
        canonical(url)
        for _, answer in items
        for line in answer.splitlines()
        if source_url(line) is None
        for url in re.findall(r"https?://\S+", line)
    }
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for topic, answer in items:
        lines: list[str] = []
        for line in answer.splitlines():
            url = source_url(line)
            key = canonical(url) if url else ""
            if url and (key in seen or key in semantic_urls):
                continue
            if url:
                seen.add(key)
            lines.append(line)
        result.append((topic, "\n".join(lines).strip()))
    return result


def _order_answers_by_customer_sequence(
    answers: list[tuple[str, str]],
    questions: list[CustomerQuestion],
    body: str,
) -> list[tuple[str, str]]:
    """Keep independent reviewed answers in the order the customer asked.

    Fixed compiler sections previously put booking ahead of translation even
    when the email asked about translation first. The model still cannot author
    or rank advice: accepted source excerpts only locate each reviewed answer in
    the customer's own text. Safety route checks retain first priority.
    """
    if len(answers) < 2:
        return answers
    normalized = normalize_intent_text(latest_reply_text(body)).casefold()
    positions: dict[str, int] = {}
    aliases = {"route_orientation": "route_check"}
    for question in questions:
        topic = aliases.get(question.topic, question.topic)
        excerpt = normalize_intent_text(question.source_excerpt).casefold()
        position = normalized.find(excerpt)
        if position >= 0:
            positions[topic] = min(position, positions.get(topic, position))
    fallback_patterns = {
        "booking": r"机票|酒店|预订|\b(?:flight|hotel|booking)\b",
        "application": r"申请|网页|入口|\b(?:apply|application|website|page)\b",
        "eligibility_overview": r"资格|条件|要求|符合|\b(?:eligib|qualif|requirements?)",
        "biometrics": r"生物信息|指纹|签证中心|\b(?:biometric|fingerprint|visa application cent(?:re|er)|VAC)\b",
        "after_apply": r"查(?:询)?进度|申请状态|填错|修改|更正|撤回|取消申请|"
                       r"\b(?:track|application status|mistake|correct|withdraw|cancel)\b",
        "timing": r"多久|几周|最早|\b(?:timing|how long|weeks?|earliest)\b",
        "translation": r"翻译|译文|\btranslat",
        "fees": r"费用|申请费|\b(?:fee|cost|price)",
        "bank_period": r"流水|对账单|\bbank statement",
        "sponsor_support": r"资助|\bsponsor",
    }
    for topic, pattern in fallback_patterns.items():
        if topic not in positions and (match := re.search(pattern, normalized, re.I)):
            positions[topic] = match.start()
    indexed = list(enumerate(answers))
    indexed.sort(key=lambda item: (
        -1 if item[1][0] == "route_check" else positions.get(item[1][0], len(normalized) + item[0]),
        item[0],
    ))
    return [answer for _, answer in indexed]


def capped_answer_plan(answers: list[tuple[str, str]], language: str) -> ReviewedAnswerPlan:
    """Limit reading load without silently dropping an unanswered-risk notice."""
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in answers:
        if item[1] not in seen:
            unique.append(item)
            seen.add(item[1])
    if len(unique) <= 3:
        selected = _deduplicate_reviewed_source_lines(unique)
        return ReviewedAnswerPlan([answer for _, answer in selected], selected,
                                  [topic for topic, _ in selected], [])

    boundaries = [item for item in unique if item[0] in {"unsupported", "off_topic"}]
    selected_raw = [item for item in unique if item[0] not in {"unsupported", "off_topic"}][:3 - len(boundaries)] + boundaries
    selected = _deduplicate_reviewed_source_lines(selected_raw)
    selected_raw_answers = {text for _, text in selected_raw}
    omitted = [topic for topic, answer in unique if answer not in selected_raw_answers]
    names = {
        "booking": ("预订安排", "bookings"), "application": ("申请步骤", "application steps"),
        "route_orientation": ("签证路线确认", "visa route confirmation"),
        "eligibility_overview": ("一般资格要求", "general eligibility requirements"),
        "biometrics": ("生物信息预约", "biometrics appointment"),
        "after_apply": ("递交后的安排", "what happens after applying"),
        "timing": ("申请时间", "timing"), "translation": ("翻译要求", "translation requirements"),
        "fees": ("申请费用", "fees"), "bank_period": ("流水时间范围", "bank statement periods"),
        "sponsor_support": ("资助说明和关系材料", "sponsor support and relationship evidence"),
    }
    language_index = 0 if language == "zh" else 1
    remaining = [names.get(topic, ("其他问题", "other questions"))[language_index] for topic in omitted]
    remaining_text = ("、" if language == "zh" else ", ").join(dict.fromkeys(remaining))
    note = (
        f"这封还没有展开{remaining_text}，可以接着逐项说明。"
        if language == "zh" else
        f"I have not covered {remaining_text} in this reply; we can go through those next."
    )
    result = [answer for _, answer in selected]
    result[-1] += "\n" + note
    clean_by_raw = {raw: clean for (_, raw), (_, clean) in zip(selected_raw, selected, strict=True)}
    reviewed = [(topic, clean_by_raw.get(answer, answer)) for topic, answer in unique]
    return ReviewedAnswerPlan(result, reviewed, [topic for topic, _ in selected], omitted, note)


def _capped_answers(answers: list[tuple[str, str]], language: str) -> list[str]:
    return capped_answer_plan(answers, language).answers


def _standalone_chinese_link_preference(body: str) -> bool:
    return bool(re.fullmatch(
        r"只要(?:官方)?(?:申请)?(?:入口|链接)(?:就好|即可|就行)?", body.strip(" 。.!！?？\n"),
    ))


def _application_entry_only_requested(body: str) -> bool:
    """Current explicit presentation preference, not permission to omit other questions."""
    # In this exact standalone preference, Chinese "只要" means "only want",
    # not "provided that". Do not normalize a larger conditional sentence.
    preference_body = re.sub(
        r"(^|[。！？\n])只要((?:官方)?(?:申请)?(?:入口|链接))(?:就好|即可|就行)?(?=[。！？\n]|$)",
        r"\1只给我\2", body,
    )
    clauses = _preference_current_clauses(preference_body)
    if any(re.search(r"流程|顺序|步骤|详细|\b(?:steps|sequence|process|in detail)\b", clause, re.I)
           for clause in clauses):
        return False
    return any(re.fullmatch(
        r"(?:请)?(?:先)?(?:只要|只给我?|给我?一个)(?:官方)?(?:申请)?(?:入口|链接)(?:就好|即可|就行)?|"
        r"(?:please\s+)?(?:just|only)\s+(?:(?:give|send)\s+me\s+)?(?:the|an?)?\s*"
        r"(?:official\s+)?(?:application\s+)?(?:link|url)(?:\s+please)?",
        clause.strip(" 。.!！?？"), re.I,
    ) for clause in clauses)


def _reviewed_answer(topic: str, language: str, *, body: str = "", case: Case | None = None) -> str:
    if topic == "route_orientation":
        return (
            "先不要按名称猜路线：如果主要目的是旅游、探亲访友或符合规定的短期商务活动，可以先核对 "
            "Standard Visitor；如果主要目的是到英国就读课程，则要单独核对 Student visa。"
            "短期学习有时也可能属于 Standard Visitor 的允许活动，所以不能只凭“学习”两个字决定。\n\n"
            "最稳妥的下一步是从 GOV.UK 路线查询入口按国籍、访问目的和停留安排逐项确认。"
            "这条说明不会替你作资格结论，也不会把当前档案自动改成任何路线。"
            if language == "zh" else
            "Do not choose a route from its label alone. If the main purpose is tourism, visiting family or friends, "
            "or permitted short business activity, check the Standard Visitor route first. If the main purpose is "
            "to take a course in the UK, check the Student route separately. Some short study can be permitted as a "
            "Standard Visitor, so the word 'study' alone does not settle the route.\n\n"
            "The safest next step is to use the GOV.UK checker with your nationality, purpose and intended stay. "
            "This does not decide your eligibility or change the route recorded for your case."
        ) + (
            f"\nGOV.UK: {ROUTE_CHECK_SOURCE}\nGOV.UK: {STANDARD_VISITOR_SOURCE}\nGOV.UK: {STUDENT_SOURCE}"
        )
    if topic == "eligibility_overview":
        return (
            "先区分两件事：是否需要签证或 ETA，以及你的计划是否符合 Standard Visitor 的一般要求。"
            "GOV.UK 要求访问目的属于允许的活动，并能说明你会在访问结束后离境、本人或资助人能承担"
            "在英期间和返程费用，也不会通过频繁或连续访问把英国作为主要住所。\n\n"
            "我可以按这些标准帮你核对材料和需要进一步确认的地方，但不能只凭一封邮件替你作出个人资格结论，"
            "也不能承诺申请结果。先用官方查询入口按护照、赴英目的和停留安排确认需要签证还是 ETA。\n"
            f"GOV.UK: 签证 / ETA 官方查询 — {ROUTE_CHECK_SOURCE}\n"
            f"GOV.UK: Standard Visitor 一般要求 — {STANDARD_VISITOR_SOURCE}"
            if language == "zh" else
            "There are two separate questions: whether you need a visa or ETA, and whether your plans meet "
            "the general Standard Visitor requirements. GOV.UK says the visit must be for a permitted activity, "
            "and you must be able to show that you will leave at the end of the visit, can cover your stay and "
            "return journey yourself or through funding, and will not make the UK your main home through frequent "
            "or successive visits.\n\n"
            "I can help compare your evidence with that framework and identify points to verify, but I cannot decide "
            "your personal eligibility or promise an outcome from one message. Use the official checker with your "
            "passport, purpose and intended stay to establish whether you need a visa or ETA.\n"
            f"GOV.UK: Visa / ETA checker — {ROUTE_CHECK_SOURCE}\n"
            f"GOV.UK: Standard Visitor overview — {STANDARD_VISITOR_SOURCE}"
        )
    if topic == "biometrics":
        return (
            "如果官方查询结果显示你需要 Standard Visitor 签证，先在线提交申请，再按申请流程预约签证申请中心。"
            "预约时需要用护照或旅行证件核验身份、采集指纹和照片，并按申请页面要求提供材料。"
            "签证中心可能不在你所在的国家或地区，所以预约前要确认自己实际能够到场；具体时段和要带的文件以实时申请及"
            "签证中心通知为准。\n"
            f"GOV.UK: Standard Visitor 在线申请和生物信息步骤 — {APPLICATION_SOURCE}\n"
            f"GOV.UK: 查询签证申请中心 — {VAC_SOURCE}"
            if language == "zh" else
            "If the official checker shows that you need a Standard Visitor visa, submit the online application "
            "and then book a visa application centre appointment through the application process. At the appointment, "
            "you prove your identity with your passport or travel document, provide fingerprints and a photograph, "
            "and provide the documents requested by the application. A centre may be in another country, so confirm "
            "that you can attend; follow the live application and centre notice for the exact slot and documents to bring.\n"
            f"GOV.UK: Standard Visitor application and biometrics steps — {APPLICATION_SOURCE}\n"
            f"GOV.UK: Find a visa application centre — {VAC_SOURCE}"
        )
    if topic == "after_apply":
        text = normalize_intent_text(body)
        asks_change = bool(re.search(
            r"填错|写错|改错|修改|更正|更新|"
            r"\b(?:change|correct|update|mistake|error)\b",
            text,
            re.I,
        ))
        asks_cancel = bool(re.search(
            r"撤回|取消(?:签证)?(?:申请)?|退费|"
            r"\b(?:cancel|withdraw|refund)\b",
            text,
            re.I,
        ))
        asks_progress = bool(re.search(
            r"进度|状态|跟踪|决定|结果|通知|递交后|下一步|"
            r"\b(?:track|status|decision|notification|what happens|what next|after (?:I|we))\b",
            text,
            re.I,
        )) or not (asks_change or asks_cancel)
        sections: list[str] = []
        if asks_progress:
            sections.append(
                (
                    "处理时间从在线申请、身份核验和材料提供都完成后起算；Standard Visitor 通常在 3 周内收到决定。"
                    "Home Office 会用邮件或信件通知已作出决定并说明下一步。如果仍在当前公布的处理时间内，"
                    "官方说明通常不需联系 UKVI 查询进度；没看到邮件时也要查看垃圾邮件箱。\n"
                    f"GOV.UK: 境外申请处理时间 — {PROCESSING_TIMES_SOURCE}\n"
                    f"GOV.UK: 递交后与决定通知 — {AFTER_APPLY_SOURCE}"
                ) if language == "zh" else (
                    "Processing starts after the application, identity check and supporting evidence have all been "
                    "provided. A Standard Visitor decision usually takes up to 3 weeks. The Home Office will send a "
                    "letter or email when a decision has been made and explain what to do next. GOV.UK says you usually "
                    "do not need to contact UKVI to track an application that is still within the current processing time; "
                    "check the spam or junk folder if the decision email has not appeared.\n"
                    f"GOV.UK: Processing times for applications outside the UK — {PROCESSING_TIMES_SOURCE}\n"
                    f"GOV.UK: After applying and getting a decision — {AFTER_APPLY_SOURCE}"
                )
            )
        if asks_change:
            sections.append(
                (
                    "如果申请已经递交而你发现信息有误，不要假定再发一封说明就会自动改表；GOV.UK 要求通过 UKVI "
                    "联系入口处理递交后的修改。说明申请信息和需更正的项目，再按官方回复操作。\n"
                    f"GOV.UK: 联系 UKVI — {CONTACT_UKVI_SOURCE}"
                ) if language == "zh" else (
                    "If the application has already been submitted and you find an error, do not assume that sending "
                    "a separate note automatically changes the form. GOV.UK directs post-submission changes through "
                    "the UKVI contact route. Identify the application and the item to correct, then follow the official response.\n"
                    f"GOV.UK: Contact UKVI — {CONTACT_UKVI_SOURCE}"
                )
            )
        if asks_cancel:
            sections.append(
                (
                    "仍在等待决定时可以申请撤回；退款取决于 UKVI 收到撤回时的处理阶段，不是撤回就一定退费。"
                    "如果是境外预约递交，官方指引要求从注册邮件的链接登录申请账户操作，并另行取消签证中心预约；"
                    "UKVI 收到撤回后不能再叫停撤回。\n"
                    f"GOV.UK: 撤回签证申请 — {CANCEL_SOURCE}"
                ) if language == "zh" else (
                    "You can withdraw while you are still waiting for a decision. Any refund depends on the processing "
                    "stage when UKVI receives the cancellation; withdrawal does not guarantee a refund. For an overseas "
                    "appointment application, GOV.UK directs you to sign in from the link in the registration email and "
                    "cancel the visa application centre appointment separately. A cancellation cannot be stopped once UKVI receives it.\n"
                    f"GOV.UK: Cancel a visa application — {CANCEL_SOURCE}"
                )
            )
        return "\n\n".join(sections)
    if topic == "application_link":
        if wants_no_links(body):
            answer = (
                "按你的偏好，这封不附链接。正式入口名称是 GOV.UK Standard Visitor 在线申请页，"
                "进入后选择 Apply now。"
                if language == "zh" else
                "As requested, I have left the link out of this reply. The formal entry is the GOV.UK "
                "Standard Visitor online application page; select Apply now when you open it."
            )
        else:
            answer = (
                "这是之前的 GOV.UK 申请入口（Standard Visitor 在线申请页），"
                "打开页面后选择 Apply now。"
                if language == "zh"
                else "Here's the GOV.UK application link again—the Standard Visitor online application page; "
                "select Apply now there."
            )
        source = APPLICATION_SOURCE
    elif topic == "application":
        no_links = wants_no_links(body)
        if _application_entry_only_requested(body) and not no_links:
            answer = (
                "如果需要 Standard Visitor 签证，可以从这个 GOV.UK 官方页面开始申请。"
                if language == "zh" else
                "If you need a Standard Visitor visa, start from this official GOV.UK application page."
            )
        elif language == "zh":
            location = (
                "正式入口是 GOV.UK 的 Standard Visitor 在线申请页"
                if no_links else
                "正式入口是下方 GOV.UK Standard Visitor 在线申请页"
            )
            answer = (
                f"路线核对后，如果确实需要 Standard Visitor 签证，{location}，进入后选择 Apply now。"
                "办理顺序是：在线填写申请（可中途保存）→预约签证中心→按页面指示核验身份并提供材料。"
            )
        else:
            location = (
                "the GOV.UK Standard Visitor online application page"
                if no_links else
                "the GOV.UK Standard Visitor online application page below"
            )
            answer = (
                f"After checking the route, if you need a Standard Visitor visa, the formal entry is {location}; "
                "select Apply now there. The sequence is: complete the online form (you can save it) → book a visa "
                "application centre appointment → follow the page instructions to prove your identity and provide documents."
            )
        source = APPLICATION_SOURCE
    elif topic == "timing":
        answer = (
            "Standard Visitor 最早可在出发前 3 个月申请。在线申请、身份核验和材料提交都完成后，"
            "通常在 3 周内收到决定；不是从开始准备材料那天起算。这不保证按时出结果，也不是获签承诺。"
            if language == "zh"
            else "You can apply for a Standard Visitor visa up to 3 months before travel. "
            "A decision usually takes up to 3 weeks after you have applied online, proved your "
            "identity and provided your documents—not from the day you start preparing. "
            "This is not a guaranteed deadline or a promise of approval."
        )
        source = APPLICATION_SOURCE
    elif topic == "fees":
        extended_kind = _extended_visitor_fee_kind(body)
        if extended_kind == "medical":
            return (
                "如果是因私人医疗治疗申请一次最长 11 个月的 Standard Visitor，GOV.UK 当前列出的"
                "申请费是 £234。这个金额只适用于符合该医疗特例的长期停留，不是普通 6 个月访客签证"
                "费用；还要按医疗访问页核对额外资格和医生或顾问信等证明。付款前请再以该官方页显示为准。"
                if language == "zh" else
                "For a Standard Visitor application for private medical treatment lasting up to 11 months, "
                "GOV.UK currently lists a £234 application fee. This figure is only for a qualifying "
                "extended medical visit, not an ordinary 6-month visit. Check the additional eligibility "
                "and doctor or consultant letter requirements on the medical-visit page, and recheck the "
                "official price before paying."
            ) + "\nGOV.UK: " + MEDICAL_SOURCE
        if extended_kind == "academic":
            return (
                "如果申请人符合学者、科学家、研究人员或高级医生/牙医的特别条件，申请最长 12 个月的 "
                "Standard Visitor，GOV.UK 当前列出的申请费是 £234。这不是参加一次学术会议就自动"
                "适用的 12 个月类别；还要按学术访问页核对长期停留的额外资格和证明。付款前请再以该"
                "官方页显示为准。"
                if language == "zh" else
                "For an applicant who meets the special conditions for an academic, scientist, researcher, "
                "senior doctor or senior dentist, GOV.UK currently lists a £234 fee for a Standard Visitor "
                "visa lasting up to 12 months. Attending an academic conference does not by itself qualify "
                "someone for this 12-month provision. Check the additional eligibility and evidence on the "
                "academic-visit page, and recheck the official price before paying."
            ) + "\nGOV.UK: " + ACADEMIC_SOURCE
        if extended_kind == "other_extended":
            return (
                "你问到的停留时间超过普通 6 个月范围，不能直接套用普通访客费用。Standard Visitor 只在"
                "特定医疗或符合条件的学术访问等情况下可以申请更长停留；要先确认访问活动和适用的"
                "最长时限，再在对应官方页面核对费用。"
                if language == "zh" else
                "The stay you asked about is longer than the ordinary 6-month period, so the ordinary "
                "visitor fee must not be applied automatically. A longer Standard Visitor stay is available only in "
                "specific circumstances, such as qualifying medical or academic visits. Confirm the activity "
                "and permitted maximum period before checking the corresponding official fee."
            ) + "\nGOV.UK: " + STANDARD_VISITOR_SOURCE
        if re.search(
            r"(?:两|二|五|十|2|5|10)\s*年|长期|\b(?:two|five|ten|2|5|10)[ -]years?\b|\blong[- ]term\b",
            body, re.I,
        ):
            return (
                "你问的是长期访问签证费用，不能套用六个月签证的金额。"
                "请在下方 GOV.UK 页面的 Visa fees 表中核对对应有效期那一行；"
                "签证有效期也不等于每次可以连续停留的时长。"
                if language == "zh" else
                "You asked about a long-term visitor visa, so the six-month visa fee is not the right figure. "
                "Check the row for the requested validity in the Visa fees table on the GOV.UK page below. "
                "Visa validity is not the same as the permitted length of each stay."
            ) + "\nGOV.UK: " + APPLICATION_SOURCE
        answer = (
            "按 6 个月 Standard Visitor 计算，GOV.UK 当前列出的申请费是 £135；签证中心增值或加急服务"
            "另外收费。付款前再以官网显示为准；其他路线或有效期不能直接套用这个数字。"
            if language == "zh"
            else "For a 6-month Standard Visitor visa, GOV.UK currently lists a £135 application fee. "
            "Optional visa application centre or priority services cost extra; check the official price before paying. "
            "Do not apply this figure to another route or validity period."
        )
        source = APPLICATION_SOURCE
    elif topic == "sponsor_support":
        return sponsor_support_answer(body, language, case)
    elif topic == "bank_period":
        clarification = income_answer(income_question(body) or "", language,
            self_employed=bool(case and case.profile.occupation_status == "self_employed"))
        if clarification:
            return clarification + "\nGOV.UK: " + SOURCE + "#demonstrating-personal-circumstances"
        self_employed_accounts = _self_employed_account_comparison(body)
        if self_employed_accounts:
            answer = (
                "自雇情况下，两类记录的作用不同，不宜只按“二选一”理解：个人账户用来说明你本人"
                "可以实际动用的旅行资金；如果收入从公司账户转入个人账户，或需要解释经营收入和资金来源，"
                "可以配合相关公司记录、经营登记或近期发票，并把对应转账串联起来。公司账户余额本身不等于你个人"
                "可支配的旅行资金，也不是所有自雇申请人都必须一律同时交两类流水；要按实际资金路径选择能说明问题的记录。"
                if language == "zh" else
                "For a self-employed applicant, the two records serve different purposes rather than being a simple "
                "either-or choice. Personal-account statements help show funds you can actually use for the trip. If "
                "income moves from the company account to the personal account, or business records are needed to explain "
                "the source of earnings, use the relevant company records alongside business registration or recent invoices "
                "and connect the matching transfers. A company-account balance is not automatically personal money available "
                "for the trip, and every self-employed applicant is not automatically required to submit both types; use the "
                "records that explain the actual path of the funds."
            )
        else:
            answer = (
                "可以先整理相关账户的正式对账单，让账户持有人、资金来源和资金进出记录看得清楚。"
                "还要说明你是否可以使用这些钱，结合旅行支出核对；余额本身不能说明全部情况。"
                if language == "zh"
                else "Start with official statements for the relevant accounts showing the account holder, "
                "where the funds come from and the transactions. They also need to explain whether you can "
                "access the money for the trip; a balance alone does not explain all of that."
            )
        if re.search(r"余额证明|存款证明|\bbalance (?:certificate|confirmation|letter)\b|"
                     r"\bcertificate of (?:balance|deposit)\b", body, re.I):
            answer = (
                "可以这样区分：流水能展示一段时间的资金进出；如果余额证明只写某个时点的金额，"
                "它本身就不能解释钱从哪里来、平时怎样使用。两份文件不一定能互相替代，要看实际内容。"
                if language == "zh" else
                "The distinction is the information shown: a bank statement records transactions over a period. "
                "If a balance certificate only states an amount at a point in time, it does not by itself explain "
                "the source or use of the money. The documents are not necessarily interchangeable; check their actual content. "
            ) + answer
        # The topic covers financial evidence, not only statement periods. Answer
        # the period subquestion when present, rather than leading every funds reply with it.
        if re.search(r"个月|哪几|月份|多久|多长|追溯|时段|跨度|多少.{0,4}(?:月|年)|"
                     r"\b(?:months?|years?|period)\b|how.{0,15}(?:far|long).{0,10}back", body, re.I):
            answer = (
                "普通 Standard Visitor 的这份官方材料指南没有统一规定银行流水必须提供几个月。"
                "不能只凭月份数量判断材料是否足够。"
                if language == "zh" else
                "For an ordinary Standard Visitor application, the official guide does not set one fixed number of months for everyone. "
                "The number of months alone does not establish whether the evidence is sufficient. "
            ) + answer
        if not self_employed_accounts and re.search(r"(?:两个|多个|不同).{0,5}账户|活期.{0,12}(?:储蓄|定期)|"
                     r"\b(?:two|both|several|different|multiple)\s+accounts?\b|"
                     r"current account.{0,25}savings account|split.{0,35}accounts?", body, re.I):
            answer += (
                "涉及不同账户时，可以按账户分别整理对账单，说明账户之间的资金往来，避免把同一笔钱重复计算。"
                "如果有定期或其他支取限制，再向银行核实何时可支取。"
                if language == "zh" else
                " If using money from different accounts, keep separate statements for each account and "
                "explain transfers between them so the same money is not counted twice. If any savings "
                "have withdrawal restrictions, check their availability with the bank."
            )
        answer += (
            "这些是整理材料的建议，实际文件还需要核对。"
            if language == "zh" else
            " These are preparation suggestions; the actual records still need checking."
        )
        source = SOURCE + "#demonstrating-personal-circumstances"
    else:
        answer = (
            "提交的文件如果不是英语或威尔士语，需要附上可由 Home Office 独立核验的完整翻译。"
            "译文要包含译者的准确性声明、翻译日期、译者全名和签名，以及联系方式。"
            "只翻译摘要或没有这些信息的译文，不符合这份官方说明。"
            if language == "zh"
            else "Documents you submit that are not in English or Welsh need a full translation "
            "that the Home Office can independently verify. Include the translator's accuracy "
            "statement, translation date, full name and signature, and contact details. "
            "A summary alone does not meet that requirement."
        )
        source = SOURCE
    extra_source: str | None = None
    if topic == "timing" and re.search(r"护照|\bpassport\b", body, re.I):
        answer += (
            "\nGOV.UK 当前的 Standard Visitor 页面说明，护照或旅行证件在签证中心预约当天退回。"
            "如果你实际把护照留在了签证中心，另一份官方处理时间说明要求等收到联系后再回中心。"
            "不能用通常 3 周的决定时间推算你个人的护照返还日。"
            if language == "zh" else
            "\nThe current GOV.UK Standard Visitor page says your passport or travel document is returned "
            "on the appointment day. If you actually left it at a visa application centre, the official "
            "processing-time guidance says to wait until you are contacted before returning. "
            "The usual 3-week decision time cannot establish your individual passport-return date."
        )
        extra_source = PROCESSING_SOURCE
    elif topic in {"application", "application_link"} and re.search(
        r"注册|创建.{0,5}(?:账号|账户)|(?:账号|账户).{0,10}(?:先|后|填)|"
        r"\b(?:register|registration|sign up)\b|\b(?:create|set up).{0,20}account\b", body, re.I,
    ):
        answer += (
            "\n可以先从上面的官方入口选择阅读语言；题目可以显示中文，但答案必须用英文填写。"
            "递交线上申请后，申请流程会引导你预约签证申请中心并提供生物信息；"
            "签证中心有可能位于另一个国家，实际地点和可预约时段要以实时申请和中心通知为准。"
            "你问的账户注册顺序，我还没有核验到那一步，不能确定是否必须先注册再填表；"
            "账户设置仍以随后官网显示的步骤为准。"
            if language == "zh" else
            "\nStart at the official entry above and select your reading language; your answers must be in English. "
            "After you submit the online application, the application process guides you to book a visa application "
            "centre appointment and provide biometrics. A centre may be in another country; follow the live application "
            "and centre notice for the actual location and available appointment slots. "
            "I haven't verified the account-registration step, so I can't confirm whether registration must come before "
            "filling in the form. Follow the account steps shown on the official site as you continue."
        )
        extra_source = VAC_SOURCE
    elif topic == "translation" and re.search(r"朋友|自己|\b(?:friend|myself|self[- ]translate)\b", body, re.I):
        answer += (
            "\n仅凭是朋友或自己翻译，不能判断译件是否合格；还要检查实际完整译件及其可核验性。"
            "我不能保证这样的译件会被接受，也不会只根据译者与你的关系作判断。"
            if language == "zh" else
            "\nWho translated it—a friend or you—does not by itself establish whether it meets the requirements. "
            "We need to check the actual full translation and whether it can be independently verified; "
            "I cannot guarantee acceptance or decide from the relationship alone."
        )
    elif topic == "bank_period" and re.search(
        r"网银|银行\s*[Aa][Pp][Pp]|下载|电子(?:版|对账单|流水)|纸质|哪里.{0,8}(?:流水|对账单)|"
        r"(?:online|mobile) banking|download|paper(?:less| copies| statements)?|"
        r"(?:where|how).{0,25}(?:get|obtain|request)|哪里.{0,8}(?:拿|获取|索取)", body, re.I,
    ):
        answer += (
            "\n获取方面，可以先在网银或银行 App 里找正式电子对账单；没有下载入口的话，向银行索取。"
            "不代表任何下载文件都会被接受，也不要用余额截图代替这些记录。"
            if language == "zh" else
            "\nTo obtain them, look for official electronic statements in your online banking or bank app; "
            "if downloads are unavailable, request statements from your bank. This is not a guarantee "
            "that any downloaded file will be accepted, and a balance screenshot is not a substitute for those records."
        )
    return answer + "\nGOV.UK: " + source + ("\nGOV.UK: " + extra_source if extra_source else "")


def _requests_previous_application_link(body: str) -> bool:
    """Resolve only a standalone short reference, never another named website.

    The caller must separately establish that the application link was actually sent.
    Restricting the whole active turn prevents a school/hotel link request from borrowing
    visa context just because it happens in an existing application conversation.
    """
    clauses = _active_clauses(body)
    if len(clauses) != 1:
        return False
    text = clauses[0].strip()
    return bool(re.fullmatch(
        r"(?:(?:请|麻烦)(?:你)?)?(?:把)?(?:刚才的?|之前的?|那个|这个|上面的?)?"
        r"(?:网址|链接|网页|入口)(?:再)?(?:发给|发|给)(?:我)(?:一下|一遍|一次)?[？?。.\s]*|"
        r"(?:(?:请|麻烦)(?:你)?)?(?:再)?(?:发给|发|给)我(?:一下)?"
        r"(?:刚才的?|之前的?|那个|这个|上面的?)?(?:网址|链接|网页|入口)[？?。.\s]*|"
        r"(?:(?:请|麻烦)(?:你)?)?(?:刚才的?|之前的?|那个|这个|上面的?)?"
        r"(?:申请)?(?:网址|链接|网页|入口)(?:可以|能否|能不能|可不可以)?(?:再)?"
        r"(?:发给我|发|给我)(?:一下|一遍|一次)?(?:吗)?[？?。.\s]*|"
        r"(?:(?:please|could you|can you|would you)\s+)?(?:re)?send\s+me\s+"
        r"(?:the|that|previous)\s+(?:link|website|page)(?:\s+again)?(?:\s+please)?[?.\s]*|"
        r"(?:(?:please|could you|can you|would you)\s+)?(?:re)?send\s+(?:me\s+)?"
        r"(?:the\s+|that\s+|previous\s+)?(?:application\s+)?(?:link|website|page)"
        r"(?:\s+again)?(?:\s+please)?[?.\s]*",
        text, re.I,
    ))


def _unsupported_answer(requests: str, language: str, today: date, *, other_route: bool = False) -> str:
    """Offer a reviewed verification starting point, never a personal eligibility decision."""
    if sponsor_verification_question(requests) and not other_route:
        return sponsor_verification_answer(requests, language)
    if (CHECKED_AT <= today <= REVIEW_AFTER
            and _mentions_current_route(requests, r"学生签证|\bstudent visa\b")
            and re.search(r"申请费|签证费|费用|收费|多少钱|\b(?:fees?|costs?|price|charges?)\b", requests, re.I)
            and not re.search(
                r"儿童学生|儿童留学|\bchild\s+student\b|"
                r"(?:加拿大|美国|澳洲|澳大利亚|法国|新西兰)(?:的)?学生签证|"
                r"\b(?:Canadian|US|American|Australian|French|New Zealand)\s+student visa\b|"
                r"\bstudent visa.{0,12}\b(?:for|to|in)\s+(?:Canada|the US|Australia|France|New Zealand)\b",
                requests, re.I,
            )
            and not _mentions_current_route(
                requests, r"工作签证|结婚签证|\b(?:work visa|marriage visa)\b|" + TRANSIT_ROUTE,
            )):
        # The question itself names Student, even when the caller correctly marks
        # it as another route. Supply only that category's reviewed starting point.
        return (
            "关于你另问的学生签证费用：这是另一签证类别，不能套用访问签证费用。"
            "请查看下方 GOV.UK 学生签证页面的 Fees 部分核实对应费用；这里不引用金额，"
            "以该类别官网显示的信息为准。这条咨询不表示你要更改当前申请路线。"
            if language == "zh" else
            "On your separate question about the Student visa fee: this is a different visa category, "
            "so you cannot use the visitor visa fee. Check the Fees section of the GOV.UK Student visa "
            "page below for that category. I am not quoting an amount here; use the information on "
            "that official page. This question does not itself change your current application route."
        ) + "\nGOV.UK: " + STUDENT_SOURCE
    if (CHECKED_AT <= today <= REVIEW_AFTER and not other_route
            and not explicit_nonvisitor_route(requests)
            and not _mentions_current_route(requests, OTHER_ROUTE + "|" + TRANSIT_ROUTE)):
        purpose = reviewed_document_purpose(requests, language)
        if re.search(r"医疗|治疗|\b(?:medical|treatment)\b", requests, re.I):
            return (
                "你问的医疗访问有专门的要求，不能直接按普通旅游材料来判断。"
                "可以先看下面 GOV.UK 的医疗访问页面，按其中的治疗安排、费用和证明要求逐项核对。"
                "我目前不能确认你的具体治疗计划是否符合这一路线，仍需要单独核实。"
                if language == "zh" else
                "Medical visits have specific requirements, so an ordinary holiday checklist is not enough. "
                "Start with the GOV.UK medical-visit page below and check its treatment-arrangement, "
                "funding and evidence requirements. I cannot reliably confirm whether your particular "
                "treatment plan qualifies; that still needs a separate check."
            ) + "\nGOV.UK: " + MEDICAL_SOURCE
        if _asks_about_uk_work(requests):
            work_answer = (
                "关于在英国工作：GOV.UK 说明，Standard Visitor 通常不能为英国公司做有偿或无偿工作，"
                "也不能自雇；获准的付费活动或活动参与等例外有特定条件。"
                "不能只凭兼职或短期就判断符合例外。请先对照官网允许活动的说明核对具体工作，"
                "我目前不能确认你的安排是否被允许。"
                if language == "zh" else
                "On working in the UK: GOV.UK says Standard Visitors generally cannot do paid or unpaid "
                "work for a UK company or be self-employed, apart from permitted paid engagements or events. "
                "A job being part-time or short-term does not establish an exception. Check the official "
                "permitted-activities guidance against the specific work; I cannot reliably confirm "
                "that your arrangement is allowed."
            ) + "\nGOV.UK: " + ACTIVITIES_SOURCE
            return "\n\n".join([purpose, work_answer]) if purpose else work_answer
        if (re.search(
                r"存款|余额|储蓄|资金|存[了有着]?[零一二三四五六七八九十百千万两\d,.，]+(?:元|英镑|人民币|块钱|镑)|"
                r"\b(?:savings|bank balance|funds)\b", requests, re.I,
            ) and re.search(
                r"获批|过签|通过|拿到签证|够不够|够吗|足够|"
                r"\b(?:approval|approved|qualify|enough|sufficient)\b", requests, re.I,
            )):
            return (
                "不能单凭存款金额判断申请结果，需要结合可用资金、资金来源和旅行费用核对相关材料。"
                "这不是一个最低存款门槛，也不是对个人申请结果的预测。"
                if language == "zh" else
                "A savings figure alone cannot establish the application outcome. The accessible funds, "
                "their source and the expected trip costs need to be considered together when checking "
                "the evidence. This is not a minimum balance rule or a prediction about an individual application."
            ) + "\nGOV.UK: " + SOURCE + "#demonstrating-personal-circumstances"
        if purpose:
            return purpose
    if other_route or explicit_nonvisitor_route(requests):
        return _route_check_answer(language, today, body=requests)
    return (
        "你问的这点，我目前没有核验过的依据，不能直接给你确定答复。"
        "这项需要另行核实，暂时不能据此判断材料是否符合要求。"
        if language == "zh" else
        "I don't currently have verified guidance to answer that point reliably. "
        "That point needs a separate check before using it to assess whether your evidence meets the requirements."
    )


def _asks_about_uk_work(text: str) -> bool:
    """Require a link between the work and the UK, not two nearby keywords.

    'For my UK visa, explain being self-employed in Hong Kong' is evidence
    preparation, not an intention to work in the UK. This selector cannot decide
    whether any activity is permitted; it only chooses the relevant explanation.
    """
    return bool(re.search(
        r"(?:在|去|到)英国(?:(?:旅行|旅游|访问|期间|时|短期|可以|能|做|从事|一份|有偿|无偿|的公司)){0,6}"
        r"(?:工作|兼职|打工|自雇)|"
        r"(?:工作|兼职|打工|自雇)(?:能否|是否|可以|能|在)?(?:去|到|在)英国|"
        r"\b(?:work(?:ing)?|jobs?|employment|self-employed)\b"
        r"(?:\s+(?:part[- ]time|full[- ]time|remotely|paid|unpaid|temporarily))?\s+"
        r"(?:in\s+(?:the\s+)?(?:UK|Britain)|for\s+(?:a\s+|the\s+)?(?:UK|British)\s+company|"
        r"(?:during|while on)\s+(?:a\s+|my\s+)?UK\s+visit)\b|"
        r"\b(?:in|visiting)\s+(?:the\s+)?(?:UK|Britain)\s*[,，]?\s+"
        r"(?:can|could|may|will|would)\s+I\s+(?:work|be self-employed)\b|"
        r"\bas\s+(?:a\s+)?Standard Visitor\s*[,，]?\s+(?:can|could|may)\s+I\s+work\b",
        text, re.I,
    ))


def grounded_customer_answers(
    body: str, language: str, today: date, *, sent_application_guidance: bool = False,
    semantic_questions: list[CustomerQuestion] | None = None,
    include_unsupported: bool = True, case: Case | None = None,
) -> list[str]:
    return grounded_customer_answer_plan(
        body, language, today, sent_application_guidance=sent_application_guidance,
        semantic_questions=semantic_questions, include_unsupported=include_unsupported, case=case,
    ).answers


def grounded_customer_answer_plan(
    body: str, language: str, today: date, *, sent_application_guidance: bool = False,
    semantic_questions: list[CustomerQuestion] | None = None,
    include_unsupported: bool = True, case: Case | None = None,
) -> ReviewedAnswerPlan:
    """Reviewed facts only, capped at three relevant answers, never a case-state update."""
    current = latest_reply_text(body)
    semantic = validated_customer_questions(current, semantic_questions or [])
    if (CHECKED_AT <= today <= REVIEW_AFTER and case is not None
            and case.profile.occupation_status == "self_employed"):
        # A covered preparation question does not also need a generic refusal.
        # Match the entire proposed excerpt; keep unrelated unsupported requests.
        semantic = [item for item in semantic if not (
            item.topic == "unsupported" and only_income_evidence_question(item.source_excerpt)
        )]
    if (CHECKED_AT <= today <= REVIEW_AFTER and only_guarantee_question(current)
            and not _request_has_other_route(current, current)):
        semantic = [item for item in semantic if not (
            item.topic == "unsupported" and only_guarantee_question(item.source_excerpt)
        )]
    # A visa-versus-ETA request that also asks where to start has one dedicated,
    # reviewed answer below.  Model labels for that same clause are redundant
    # proposals, not a second independent question.  Keeping an ``unsupported``
    # or generic route label here used to append a contradictory dead-end after
    # the useful checker + conditional Apply-now guidance.
    semantic = [
        item for item in semantic
        if not (
            item.topic in {"application", "route_orientation", "unsupported"}
            and _visa_eta_start_clause(item.source_excerpt)
        )
    ]
    semantic = [
        item.model_copy(update={"topic": "unsupported"})
        if item.topic != "unsupported" and _route_boundary_requested(item.source_excerpt)
        else item
        for item in semantic
    ]
    off_topic_excerpts = [item.source_excerpt for item in semantic if item.topic == "off_topic"]
    off_topic_clauses = [clause for clause in _active_clauses(current) if any(
        _overlapping_excerpt(excerpt, clause) for excerpt in off_topic_excerpts
    )]
    # A non-visa request must not be turned into immigration advice by either
    # a competing model proposal or keyword matching of words such as "application".
    semantic = [item for item in semantic if item.topic == "off_topic" or not any(
        _overlapping_excerpt(item.source_excerpt, clause) for clause in off_topic_clauses
    )]
    known_visitor_context = bool(case and case.profile.visit_purpose in {
        "tourism", "family_or_friends", "business", "conference",
    })
    reviewed_application_spans = reviewed_application_requests(current, known_visitor_context=known_visitor_context)
    semantic = [item.model_copy(update={"topic": "application"})
                if item.topic == "unsupported" and re.fullmatch(
                    _FORM_ENTRY_REQUEST, item.source_excerpt.strip(" ?？.!。"), re.I)
                and _general_application_proposal(
                    current, item.source_excerpt, known_visitor_context=known_visitor_context)
                else item for item in semantic]
    active_clauses = _active_clauses(current)
    # If an unsupported proposal is only the words ``related information``
    # inside a reviewed application-page request, it contributes no separate
    # question and should not create a spurious failure notice. A standalone
    # generic request in another clause remains unsupported and is reported.
    semantic = [
        item for item in semantic
        if not (
            item.topic == "unsupported"
            and _uninformative_boundary_excerpt(item.source_excerpt)
            and any(
                _overlapping_excerpt(item.source_excerpt, clause)
                and any(_overlapping_excerpt(span, clause) for span in reviewed_application_spans)
                for clause in active_clauses
            )
        )
    ]
    unsupported_excerpts = [item.source_excerpt for item in semantic if item.topic in {"unsupported", "off_topic"}]
    unsupported_clauses = []
    for clause in active_clauses:
        overlapping = [excerpt for excerpt in unsupported_excerpts if _overlapping_excerpt(excerpt, clause)]
        if not overlapping:
            continue
        # A bare ``related information`` proposal has no independent semantic
        # authority. If the containing clause has a reviewed application-page
        # request, preserve that request. Any substantive unsupported excerpt
        # still protects the whole overlapping clause as before.
        safe_application = any(
            _overlapping_excerpt(span, clause) for span in reviewed_application_spans
        )
        if safe_application and all(_uninformative_boundary_excerpt(excerpt) for excerpt in overlapping):
            continue
        unsupported_clauses.append(clause)
    # Two different proposals about one question are not independent evidence that
    # it is answerable. Unknown scope takes priority over a narrower canned answer.
    semantic = [item for item in semantic if item.topic in {"unsupported", "off_topic"} or not any(
        _overlapping_excerpt(item.source_excerpt, clause) for clause in unsupported_clauses
    )]
    clauses = _question_clauses(current)
    broad_active_clauses = _active_clauses(current, split_commas=False)
    visa_eta_start_clauses = [clause for clause in broad_active_clauses
                              if _visa_eta_start_clause(clause)]
    route_boundary_clauses = [
        clause for clause in broad_active_clauses
        if _route_boundary_requested(clause)
        and not any(_overlapping_excerpt(clause, safe) for safe in visa_eta_start_clauses)
    ]
    # A classified unsupported question must not accidentally receive a narrower
    # keyword answer (for example a ten-year fee answered with the six-month fee).
    clauses = [clause for clause in clauses if not any(
        _overlapping_excerpt(excerpt, clause)
        for excerpt in unsupported_excerpts
        if not _uninformative_boundary_excerpt(excerpt)
    )]
    general_application = [clause for clause in reviewed_application_spans if not any(
        _overlapping_excerpt(excerpt, clause)
        for excerpt in unsupported_excerpts
        if not _uninformative_boundary_excerpt(excerpt)
    )]
    patterns = {
        "route_orientation": ROUTE_ORIENTATION_PATTERN,
        "application": (
            r"(?:申请|办理|签证).{0,8}(?:官网|网站|网页|网址|链接|入口|流程|步骤)|"
            r"(?:官网|网址).{0,8}(?:申请|在哪|是什么)|"
            r"(?:怎么|如何|哪里|在哪).{0,6}(?:申请|办(?:理)?(?:英国)?签证)|"
            r"(?:application|apply|visa).{0,24}(?:website|link|process|steps)|"
            r"\bwhere.{0,28}\bapply\b|\bhow(?!\s+(?:early|far|long|many)).{0,28}\bapply\b|"
            r"\bofficial.{0,10}(?:website|link)\b"
        ),
        "eligibility_overview": (
            r"资格|条件|要求|符合|满足|"
            r"\b(?:eligibility|eligible|qualif(?:y|ied|ication)|requirements?)\b"
        ),
        "biometrics": (
            r"生物信息|指纹|签证申请中心|签证中心|"
            r"\b(?:biometric(?:s| information)?|fingerprints?|visa application cent(?:re|er)|VAC)\b"
        ),
        "after_apply": (
            r"进度|申请状态|跟踪|决定|结果|通知|填错|写错|修改|更正|"
            r"撤回|取消(?:签证)?申请|"
            r"\b(?:track|application status|decision|notification|mistake|error|correct|withdraw|cancel)\b"
        ),
        "timing": (
            r"(?:最早|提前多久|提前几个月|什么时候).{0,14}(?:申请|办理)|"
            r"(?:申请|签证|审理|办理|结果|出签).{0,14}(?:多久|几周|多长|何时)|"
            r"(?:多久|几周|多长时间).{0,12}(?:出签|出结果|拿到|审理|签证)|"
            r"\b(?:when|how early|how far in advance).{0,28}\bapply\b|"
            r"\b(?:how long|how many weeks).{0,35}(?:visa|decision|process)|"
            r"\bprocessing time\b|\b(?:visa|decision).{0,16}(?:take|weeks)\b"
        ),
        "translation": r"翻译|译文|译者|中文(?:材料|文件)|translat|non-English|not in English|Chinese documents",
        "fees": (
            r"签证(?:申请)?费|申请费|(?:签证|申请).{0,8}(?:多少钱|费用|收费)|"
            r"(?:多少钱|费用|收费).{0,8}(?:签证|申请)|"
            r"\b(?:visa|application).{0,15}(?:fee|cost|price)|"
            r"\b(?:fee|cost|price).{0,20}(?:visa|application)|"
            r"\bhow much.{0,25}(?:visa|apply)\b"
        ),
        "bank_period": (
            r"(?:流水|银行对账单).{0,18}(?:几个月|多久|多长|几月|[一二三四五六七八九十两\d]+个月)|"
            r"(?:几个月|多久|多长|几月|[一二三四五六七八九十两\d]+个月).{0,12}(?:流水|银行对账单)|"
            r"(?:个人|私人).{0,16}(?:公司|企业|对公).{0,16}(?:账户|流水).{0,10}(?:哪个|哪些|都要|两个|怎么)|"
            r"(?:公司|企业|对公).{0,16}(?:个人|私人).{0,16}(?:账户|流水).{0,10}(?:哪个|哪些|都要|两个|怎么)|"
            r"\bbank statements?.{0,25}(?:months?|how far|period)|"
            r"\b(?:months?|how far back|what period).{0,25}bank statements?\b|"
            r"\b(?:personal|private).{0,24}(?:business|company|corporate).{0,24}accounts?\b.{0,18}"
            r"(?:which|both|either|statements?|use|submit)"
        ),
        "sponsor_support": SPONSOR_QUESTION_PATTERN,
    }
    bank_excerpts = [item.source_excerpt for item in semantic if item.topic == "bank_period"]
    timing_excerpts = [item.source_excerpt for item in semantic if item.topic == "timing"]
    requested = [topic for topic, pattern in patterns.items()
                 if topic not in _SPECIAL_REVIEWED_TOPICS
                 if any((sponsor_support_question(clause) if topic == "sponsor_support"
                         else bool(re.search(pattern, normalize_intent_text(clause), re.I))) and not (
                     topic == "timing"
                     and any(_overlapping_excerpt(excerpt, clause) for excerpt in bank_excerpts)
                     and not any(_overlapping_excerpt(excerpt, clause) for excerpt in timing_excerpts)
                 ) for clause in clauses)]
    deterministic_special_clauses: dict[str, list[str]] = {
        topic: [] for topic in _SPECIAL_REVIEWED_TOPICS
    }
    for clause in broad_active_clauses:
        if not _question_clauses(clause):
            continue
        for topic in _deterministic_reviewed_topics(current, clause):
            deterministic_special_clauses[topic].append(clause)
            if topic not in requested:
                requested.append(topic)
    broad_account_context = "\n".join(broad_active_clauses)
    if (_self_employed_account_comparison(broad_account_context)
            and any(_question_clauses(clause) for clause in broad_active_clauses)
            and any(_next_step_targets_current_case(current, clause)
                    for clause in broad_active_clauses if _question_clauses(clause))
            and not explicit_nonvisitor_route(broad_account_context)
            and "bank_period" not in requested):
        requested.append("bank_period")
    if route_orientation_question(current) and "route_orientation" not in requested:
        requested.insert(0, "route_orientation")
    semantic_topics = {item.topic for item in semantic}
    for item in semantic:
        if item.topic not in {"booking", "document_checklist", "next_step", "unsupported", "off_topic"} and item.topic not in requested:
            requested.append(item.topic)
    previous_link_requested = (not off_topic_excerpts and sent_application_guidance
                               and _requests_previous_application_link(current))
    if (previous_link_requested or general_application) and "application" not in requested:
        requested.insert(0, "application")
    # Preserve booking's cross-clause context ("I have no tickets; must I buy them?").
    active_text = "\n".join(clause for clause in _active_clauses(current) if clause not in off_topic_clauses)
    booking_text = "\n".join(clause for clause in _active_clauses(current) if not any(
        _overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts
    ))
    # Do not borrow "where can I obtain..." from an independent question.
    other_question_excerpts = [item.source_excerpt for item in semantic if item.topic != "bank_period"]
    bank_text = "\n".join(clause for clause in _active_clauses(current, split_commas=False)
                          if clause not in unsupported_clauses
                          and not any(_overlapping_excerpt(excerpt, clause) for excerpt in other_question_excerpts)
                          and (any(_overlapping_excerpt(excerpt, clause) for excerpt in bank_excerpts)
                               or re.search(r"银行|流水|对账单|网银|账户|存款|\bbank(?:ing)?\b|\b(?:statements?|accounts?|savings)\b", clause, re.I)))
    if _self_employed_account_comparison(broad_account_context):
        # Occupation and account-choice context may span two short sentences.
        # Keep both so the reviewed answer can distinguish the purpose of each
        # account without depending on a model to quote the whole message.
        bank_text = broad_account_context
    translation_excerpts = [item.source_excerpt for item in semantic if item.topic == "translation"]
    translation_text = "\n".join(clause for clause in _active_clauses(current, split_commas=False)
        if not any(_overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts + off_topic_excerpts)
        and (re.search(patterns["translation"], clause, re.I)
             or any(_overlapping_excerpt(excerpt, clause) for excerpt in translation_excerpts)))
    application_excerpts = [item.source_excerpt for item in semantic if item.topic == "application"] + general_application
    application_text = "\n".join(clause for clause in _active_clauses(current)
        if not any(_overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts + off_topic_excerpts)
        and (re.search(patterns["application"], clause, re.I)
             or any(_overlapping_excerpt(excerpt, clause) for excerpt in application_excerpts)))
    active_booking_clauses = _active_clauses(booking_text)
    has_booking_subject = any(
        re.search(r"机票|酒店|住宿|flight|hotel|accommodation", clause, re.I)
        for clause in active_booking_clauses
    )
    booking_clauses = [
        clause for clause in active_booking_clauses
        if re.search(r"机票|酒店|住宿|flight|hotel|accommodation", clause, re.I)
        or (
            has_booking_subject
            and re.search(
                r"(?:必须|需要|要不要|是否|能否|可以|要先).{0,8}(?:买|订|预订)|"
                r"(?:买|订|预订).{0,8}(?:吗|[?？])|"
                r"\b(?:must|should|need to|have to|do I).{0,18}(?:buy|book|reserve)\b",
                clause,
                re.I,
            )
        )
    ]
    booking_scopes = [
        _request_has_other_route(active_text, clause) for clause in booking_clauses
    ]
    visitor_booking_text = "\n".join(
        clause for clause, outside_scope in zip(booking_clauses, booking_scopes, strict=True)
        if not outside_scope
    )
    booking = _booking_answer(visitor_booking_text, language, today)
    if not booking and "booking" in semantic_topics:
        # The model chooses a topic, never supplies the legal answer or a URL.
        booking = _booking_guidance(
            language,
            today,
            transit=_mentions_current_route(active_text, TRANSIT_ROUTE),
            other_route=(all(booking_scopes) if booking_scopes else
                         explicit_nonvisitor_route(active_text)
                         or _mentions_current_route(active_text, OTHER_ROUTE)),
        )
    answers = []
    if visa_eta_start_clauses:
        answers.append(("route_check", _visa_eta_start_answer(language, today)))
    if route_boundary_clauses or any(booking_scopes):
        route_context = "\n".join(route_boundary_clauses)
        answers.append(("route_check", _route_check_answer(language, today, body=route_context)))
    answers.extend(("booking", answer) for answer in booking)
    # A source-grounded model topic is only a proposal. An individual document's
    # purpose is not a request for an entire missing-documents checklist.
    if CHECKED_AT <= today <= REVIEW_AFTER:
        # A missing provider topic must not turn a directly answerable question
        # into an empty acknowledgement. These explanations create no case facts.
        if not off_topic_excerpts and not _request_has_other_route(active_text, current):
            kind = income_question(current)
            if kind and "bank_period" not in requested:
                clarification = income_answer(kind, language,
                    self_employed=bool(case and case.profile.occupation_status == "self_employed"))
                if clarification:
                    answers.append(("income_clarification", clarification + "\nGOV.UK: " + SOURCE))
            if guarantee_question(current):
                answers.append(("no_guarantee", guarantee_answer(language) + "\nGOV.UK: " + SOURCE))
        direct_preparation = reviewed_document_preparation(current, language)
        if (direct_preparation and not off_topic_excerpts
                and not _request_has_other_route(active_text, current)):
            # Reviewed, narrow FAQ fallback also works when extraction omits a
            # question entirely. It does not invent an LLM proposal or case fact.
            answers.append((SCHOOL_RECORD_TOPIC if school_record_reported(current) else "document_preparation",
                            direct_preparation))
        for item in semantic:
            if item.topic == "document_checklist" and not _request_has_other_route(active_text, item.source_excerpt):
                purpose = (reviewed_document_preparation(item.source_excerpt, language)
                           or reviewed_document_purpose(item.source_excerpt, language))
                if purpose:
                    answers.append(("document_purpose", purpose))
    if requested and not CHECKED_AT <= today <= REVIEW_AFTER:
        answers.append(("guidance",
            "关于你问的申请安排或材料要求，我需要先复核最新 GOV.UK 说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current GOV.UK guidance before answering your application or document question."
        ))
    elif requested:
        for topic in requested:
            topic_excerpts = [item.source_excerpt for item in semantic if item.topic == topic]
            if topic == "application":
                topic_excerpts += general_application
            topic_clauses = list(dict.fromkeys(
                [clause for clause in clauses
                 if re.search(patterns.get(topic, r"(?!)"), normalize_intent_text(clause), re.I)]
                + deterministic_special_clauses.get(topic, [])
                + [clause for clause in _active_clauses(current)
                   if any(_overlapping_excerpt(excerpt, clause) for excerpt in topic_excerpts)]
            ))
            if topic == "application" and previous_link_requested:
                topic_clauses = _active_clauses(current)
            context = (bank_text if topic == "bank_period" else translation_text if topic == "translation"
                       else application_text if topic == "application" else active_text
                       if topic == "sponsor_support" else "\n".join(topic_clauses))
            if topic == "application" and (wants_no_links(current) or _application_entry_only_requested(current)):
                # The answer still needs the current display preference even
                # though application_text deliberately contains only the request clause.
                context = current
            if topic == "fees":
                context = _scoped_fee_context(current, topic_clauses)
            if topic in {
                "application", "eligibility_overview", "biometrics", "after_apply", "timing", "fees",
                "bank_period", "sponsor_support", "translation",
            }:
                scopes = [_request_has_other_route(active_text, clause) for clause in topic_clauses]
                if any(scopes):
                    if not any(name == "route_check" for name, _ in answers):
                        answers.append(("route_check", _route_check_answer(
                            language,
                            today,
                            body="\n".join(clause for clause, outside_scope in zip(
                                topic_clauses, scopes, strict=True,
                            ) if outside_scope),
                        )))
                    if all(scopes):
                        continue
                    context = "\n".join(clause for clause in _active_clauses(context)
                                        if not _request_has_other_route(active_text, clause))
            answers.append((topic, _reviewed_answer(
                "application_link" if topic == "application" and previous_link_requested else topic,
                language, body=context, case=case,
            )))
    deterministic_sponsor_boundaries = [
        clause for clause in _active_clauses(current, split_commas=False)
        if sponsor_verification_question(clause)
    ]
    if ("unsupported" in semantic_topics or deterministic_sponsor_boundaries) and include_unsupported:
        route_answer_present = any(topic == "route_check" for topic, _ in answers)
        unsupported_boundaries = []
        for item in semantic:
            if item.topic != "unsupported":
                continue
            boundary = _unsupported_answer(
                item.source_excerpt,
                language,
                today,
                other_route=_request_has_other_route(active_text, item.source_excerpt),
            )
            # Work-route checklist questions compile to the same route boundary
            # already present above; a Student-fee answer is different reviewed
            # information and must remain. Compare the actual compiled text
            # instead of dropping every question merely labelled another route.
            if route_answer_present and boundary == _route_check_answer(
                language, today, body=item.source_excerpt,
            ):
                continue
            unsupported_boundaries.append(boundary)
        boundaries = dict.fromkeys([
            *unsupported_boundaries,
            *(_unsupported_answer(
                clause, language, today,
                other_route=_request_has_other_route(active_text, clause),
            ) for clause in deterministic_sponsor_boundaries),
        ])
        if boundaries:
            answers.append(("unsupported", "\n\n".join(boundaries)))
    if "off_topic" in semantic_topics:
        answers.append(("off_topic",
            "这件事不在我核对的签证资料范围里，我不想凭印象给你一个不可靠的答案。"
            "你的英国签证档案不会因为这条消息改变；想继续准备时，直接接着这封邮件就行。"
            if language == "zh" else
            "That is outside the visa-document work I can reliably check, and I do not want to guess. "
            "This message has not changed your UK visa case; when you are ready, just continue in this email thread."
        ))
    if any(re.search(
        r"(?:不要|不用|无需|不需要|别).{0,12}(?:链接|网址|网站|官网)|"
        r"\b(?:no links?|(?:don['’]t|do not) (?:send|need)|no need (?:for|to send)).{0,12}"
        r"(?:links?|websites?)\b|\b(?:no links?|without links?)\b",
        clause, re.I,
    ) for clause in _request_clauses(current)):
        answers = [(topic, re.sub(r"(?m)^[ \t]*GOV\.UK:[^\n]*(?:\n|$)", "", answer).strip())
                   for topic, answer in answers]
        answers = [(topic, answer.replace("下面的 GOV.UK 页面", "GOV.UK 官方申请页面")
                   .replace("the GOV.UK page below", "the official GOV.UK application page"))
                   for topic, answer in answers]
    answers = _order_answers_by_customer_sequence(answers, semantic, current)
    return capped_answer_plan(answers, language)
