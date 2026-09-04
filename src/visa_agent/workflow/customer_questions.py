"""Small reviewed answer set, not open-ended immigration advice."""

import re
from dataclasses import dataclass
from datetime import date

from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.conversation import latest_reply_text
from visa_agent.workflow.document_preparation import reviewed_document_preparation
from visa_agent.workflow.document_purpose import reviewed_document_purpose

SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
APPLICATION_SOURCE = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
PROCESSING_SOURCE = "https://www.gov.uk/guidance/visa-processing-times-applications-outside-the-uk#when-your-application-processing-time-ends"
ACTIVITIES_SOURCE = "https://www.gov.uk/standard-visitor"
MEDICAL_SOURCE = "https://www.gov.uk/standard-visitor/visit-for-medical-reasons"
# The overview's Fees section was checked on CHECKED_AT; no price is copied here.
STUDENT_SOURCE = "https://www.gov.uk/student-visa"
CHECKED_AT = date(2026, 9, 4)
REVIEW_AFTER = date(2026, 10, 4)
OTHER_ROUTE = r"学生签证|工作签证|结婚签证|\b(?:student visa|work visa|marriage visa)\b"
TRANSIT_ROUTE = r"过境|\btransit\b"


def _route_check_answer(language: str) -> str:
    return (
        "你提到的路线可能不是普通 Standard Visitor，申请安排、费用和材料要求需要先按对应路线核实，不能直接套用访问签证说明。"
        if language == "zh" else
        "The route you mentioned may not be an ordinary Standard Visitor visa; its application arrangements, fees and evidence requirements need a separate route check."
    )


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
    return _booking_guidance(language, today, transit=_mentions_current_route(body, TRANSIT_ROUTE),
                             other_route=_mentions_current_route(body, OTHER_ROUTE))


def _booking_guidance(language: str, today: date, *, transit: bool = False,
                      other_route: bool = False) -> list[str]:
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return [
            "关于提前订机票和酒店的问题，我需要先复核最新官方说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current official guidance before answering your booking question."
        ]
    if other_route:
        return [_route_check_answer(language)]
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
    for clause in _active_clauses(body):
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
    visitor_route = r"\b(?:UK|British|standard)\s+(?:standard\s+)?visitor\b|英国(?:普通)?(?:访问|访客|旅游)签证"
    for clause in _active_clauses(body):
        if _mentions_current_route(clause, OTHER_ROUTE + "|" + TRANSIT_ROUTE):
            other_route = True
        elif (_mentions_current_route(clause, visitor_route)
              and not re.search(r"如果|假如|假设|\b(?:if|unless|whether)\b", clause, re.I)):
            other_route = False
        if _overlapping_excerpt(excerpt, clause):
            matching_scopes.append(other_route)
    # Validated excerpts normally match. Keep an unmatched scope conservative.
    return any(matching_scopes) if matching_scopes else _mentions_current_route(
        body, OTHER_ROUTE + "|" + TRANSIT_ROUTE,
    )


def _question_clauses(body: str) -> list[str]:
    question = (
        r"[?？]|吗|么|如何|怎么|怎样|哪里|哪[个里]|多久|多少|何时|什么时候|几周|几个月|最早|"
        r"(?:请|麻烦|能否|可以).{0,12}(?:告诉|解释|介绍|说|发|给)|发我|给我|"
        r"\b(?:what|where|when|how|can|could|would|do|must|should)\b|"
        r"\b(?:please|send me|give me|tell me|explain)\b"
    )
    return [clause for clause in _active_clauses(body) if re.search(question, clause, re.I)]


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
    r"\b(?:university|college|mortgage|loan|job)\s+application\b|(?:大学|学校|贷款|工作)申请", re.I,
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


def is_generic_uk_preparation_enquiry(body: str, excerpt: str | None = None) -> bool:
    """Recognize a small, affirmative orientation request, not visa eligibility.

    The full current message must fit an ordinary UK preparation enquiry. A model
    cannot strip a condition, third person's case, refusal or approval question
    from its excerpt to enter this path. The result authorizes only conditional
    orientation; it supplies no applicant fact, selected route or preparation consent.
    """
    current = _normalised_excerpt(latest_reply_text(body))
    if (not current or len(current) > 500 or _OTHER_APPLICANT_FRAME.search(current)
            or _OTHER_APPLICATION_FRAME.search(current)
            or re.search(
                r"[“”「」『』\"`]|(?<!\w)[‘’]|[‘’](?!\w)|(?<!\w)'[^'\n]+'(?!\w)|"
                r"如果|假如|假设|除非|只要|不想|不用|不要|无需|不需要|先不|暂不|暂停|"
                r"获批|过签|批准|保证|门槛|存款|余额|收入|拒签|逾期|"
                r"\b(?:if|unless|assuming|suppose|whether|not|never|cannot|pause|"
                r"approval|approved|eligible|guarantee|threshold|savings|income|refusal|overstay)\b|"
                r"\b(?:don|can|won|wouldn|couldn|shouldn)['’]t\b",
                current, re.I,
            )):
        return False
    separator = r"[\s，,。.!！？?：:；;—-]*"
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
        r"(?:该|应该)?(?:从哪(?:里)?|怎么)(?:开始|入手))"
    )
    en_intro = (
        r"(?:(?:(?:I|we)\s+(?:want|need|plan|would like)\s+to\s+apply\s+for|"
        r"I(?:\s+am|['’]m)\s+applying\s+for|for)\s+)?"
        r"(?:a\s+|the\s+)?(?:UK|British)\s+(?:(?:standard\s+)?visitor\s+|tourist\s+)?visa"
    )
    en_question = (
        r"(?:what(?:\s+(?:documents?|evidence|paperwork))?\s+(?:do I|should I|will I)\s+"
        r"(?:need(?:\s+to\s+(?:prepare|provide))?|prepare|provide|get ready)|"
        r"which documents\s+(?:do I need|should I prepare)|"
        r"how\s+(?:do|should)\s+I\s+(?:start|get started|prepare)|where\s+do\s+I\s+(?:start|begin))"
    )
    patterns = [
        rf"(?:(?:你好|您好){separator})?(?:{zh_intro}){separator}(?P<question>{zh_question})"
        rf"(?:呢|呀|啊)?{separator}(?:谢谢{separator})?",
        rf"(?:(?:hello|hi){separator})?(?:{en_intro}){separator}(?P<question>{en_question})"
        rf"{separator}(?:(?:thanks|thank you){separator})?",
    ]
    for pattern in patterns:
        matched = re.fullmatch(pattern, current, re.I)
        if matched and (excerpt is None or _overlapping_excerpt(excerpt, matched.group("question"))):
            return True
    return False


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
        if (proposal.confidence >= 0.8 and excerpt
                and excerpt in _normalised_excerpt(latest_reply_text(body)) and fragments
                and (excerpt in _normalised_excerpt("\n".join(active)) or all(
                    any(fragment in _normalised_excerpt(clause) for clause in active) for fragment in fragments
                ))):
            if proposal.topic == "next_step" and not _next_step_targets_current_case(body, proposal.source_excerpt):
                continue
            if proposal.topic == "unsupported" and is_generic_uk_preparation_enquiry(body, proposal.source_excerpt):
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


def capped_answer_plan(answers: list[tuple[str, str]], language: str) -> ReviewedAnswerPlan:
    """Limit reading load without silently dropping an unanswered-risk notice."""
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in answers:
        if item[1] not in seen:
            unique.append(item)
            seen.add(item[1])
    if len(unique) <= 3:
        return ReviewedAnswerPlan([answer for _, answer in unique], unique,
                                  [topic for topic, _ in unique], [])

    boundaries = [item for item in unique if item[0] in {"unsupported", "off_topic"}]
    selected = [item for item in unique if item[0] not in {"unsupported", "off_topic"}][:3 - len(boundaries)] + boundaries
    omitted = [topic for topic, answer in unique if answer not in {text for _, text in selected}]
    names = {
        "booking": ("预订安排", "bookings"), "application": ("申请步骤", "application steps"),
        "timing": ("申请时间", "timing"), "translation": ("翻译要求", "translation requirements"),
        "fees": ("申请费用", "fees"), "bank_period": ("流水时间范围", "bank statement periods"),
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
    return ReviewedAnswerPlan(result, unique, [topic for topic, _ in selected], omitted, note)


def _capped_answers(answers: list[tuple[str, str]], language: str) -> list[str]:
    return capped_answer_plan(answers, language).answers


def _reviewed_answer(topic: str, language: str, *, body: str = "") -> str:
    if topic == "application_link":
        answer = (
            "这是之前的 GOV.UK 申请入口，打开页面后选择 Apply now。"
            if language == "zh"
            else "Here's the GOV.UK application link again; select Apply now on the page."
        )
        source = APPLICATION_SOURCE
    elif topic == "application":
        answer = (
            "如果你需要申请 Standard Visitor，可以从下面的 GOV.UK 页面点 Apply now 开始。"
            "流程是出发前在线填写申请，再预约签证中心完成身份核验并提供材料；"
            "表格可以保存，之后继续填写。这是申请入口说明，不代表你的签证路线已经确认。"
            if language == "zh"
            else "If you need a Standard Visitor visa, start with Apply now on the GOV.UK page below. "
            "Apply online before travelling, then attend a visa application centre appointment "
            "to prove your identity and provide your documents. You can save the form and finish "
            "it later; this does not yet confirm which visa route you need."
        )
        source = APPLICATION_SOURCE
    elif topic == "timing":
        answer = (
            "如果你需要申请 Standard Visitor，最早可在出发前 3 个月申请。"
            "在线申请、身份核验和材料提交都完成后，通常在 3 周内收到决定；"
            "不是从开始准备材料那天起算。这只是通常审理时间，不保证按时出结果，也不代表获签承诺。"
            "这说的是签证决定时间，不是护照返还日期。"
            if language == "zh"
            else "If you need a Standard Visitor visa, you can apply up to 3 months before travel. "
            "A decision usually takes up to 3 weeks after you have applied online, proved your "
            "identity and provided your documents—not from the day you start preparing. "
            "This is not a guaranteed deadline or a promise of approval."
            " That is decision timing, not a passport-return date."
        )
        source = APPLICATION_SOURCE
    elif topic == "fees":
        answer = (
            "如果你申请的是 6 个月 Standard Visitor，GOV.UK 当前列出的签证申请费是 £135。"
            "额外购买的签证中心服务或加急服务不包含在这笔申请费内；付款时以官网显示为准。"
            "其他签证路线或有效期的费用不能直接套用这个数字。"
            if language == "zh"
            else "For a 6-month Standard Visitor visa, GOV.UK currently lists the application fee "
            "as £135. Optional visa application centre or priority services cost extra; check "
            "the official price when paying. Other visa routes or validity periods may have different fees."
        )
        source = APPLICATION_SOURCE
    elif topic == "bank_period":
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
        if re.search(r"(?:两个|多个|不同).{0,5}账户|活期.{0,12}(?:储蓄|定期)|"
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
            "我核验过的中文、中国大陆示例，接下来会选择采集生物信息的国家或地区，再确认签证中心地点。"
            "官网提示地点确认后不能更改，所以这一步要按你实际能到场的地方选，不要照着示例选。"
            "你问的账户注册顺序，我还没有核验到那一步，不能确定是否必须先注册再填表；"
            "账户设置仍以随后官网显示的步骤为准。"
            if language == "zh" else
            "\nStart at the official entry above and select your reading language; your answers must be in English. "
            "In the Simplified Chinese/mainland China example I checked, the next steps select the country or region "
            "for biometrics and confirm the visa application centre location. The page warns that the location cannot "
            "be changed after confirmation, so use a location you can actually attend, not the example. "
            "I haven't verified the account-registration step, so I can't confirm whether registration must come before "
            "filling in the form. Follow the account steps shown on the official site as you continue."
        )
        extra_source = "https://visas-immigration.service.gov.uk/apply-visa-type/visit"
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
        r"(?:(?:please|could you|can you|would you)\s+)?(?:re)?send\s+me\s+"
        r"(?:the|that|previous)\s+(?:link|website|page)(?:\s+again)?(?:\s+please)?[?.\s]*",
        text, re.I,
    ))


def _unsupported_answer(requests: str, language: str, today: date, *, other_route: bool = False) -> str:
    """Offer a reviewed verification starting point, never a personal eligibility decision."""
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
    include_unsupported: bool = True,
) -> list[str]:
    return grounded_customer_answer_plan(
        body, language, today, sent_application_guidance=sent_application_guidance,
        semantic_questions=semantic_questions, include_unsupported=include_unsupported,
    ).answers


def grounded_customer_answer_plan(
    body: str, language: str, today: date, *, sent_application_guidance: bool = False,
    semantic_questions: list[CustomerQuestion] | None = None,
    include_unsupported: bool = True,
) -> ReviewedAnswerPlan:
    """Reviewed facts only, capped at three relevant answers, never a case-state update."""
    current = latest_reply_text(body)
    semantic = validated_customer_questions(current, semantic_questions or [])
    off_topic_excerpts = [item.source_excerpt for item in semantic if item.topic == "off_topic"]
    off_topic_clauses = [clause for clause in _active_clauses(current) if any(
        _overlapping_excerpt(excerpt, clause) for excerpt in off_topic_excerpts
    )]
    # A non-visa request must not be turned into immigration advice by either
    # a competing model proposal or keyword matching of words such as "application".
    semantic = [item for item in semantic if item.topic == "off_topic" or not any(
        _overlapping_excerpt(item.source_excerpt, clause) for clause in off_topic_clauses
    )]
    unsupported_excerpts = [item.source_excerpt for item in semantic if item.topic in {"unsupported", "off_topic"}]
    unsupported_clauses = [clause for clause in _active_clauses(current) if any(
        _overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts
    )]
    # Two different proposals about one question are not independent evidence that
    # it is answerable. Unknown scope takes priority over a narrower canned answer.
    semantic = [item for item in semantic if item.topic in {"unsupported", "off_topic"} or not any(
        _overlapping_excerpt(item.source_excerpt, clause) for clause in unsupported_clauses
    )]
    clauses = _question_clauses(current)
    # A classified unsupported question must not accidentally receive a narrower
    # keyword answer (for example a ten-year fee answered with the six-month fee).
    clauses = [clause for clause in clauses if not any(
        _overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts
    )]
    patterns = {
        "application": (
            r"(?:申请|办理|签证).{0,8}(?:官网|网站|网页|网址|链接|入口|流程|步骤)|"
            r"(?:官网|网址).{0,8}(?:申请|在哪|是什么)|"
            r"(?:怎么|如何|哪里|在哪).{0,6}(?:申请|办(?:理)?(?:英国)?签证)|"
            r"(?:application|apply|visa).{0,24}(?:website|link|process|steps)|"
            r"\bwhere.{0,28}\bapply\b|\bhow(?!\s+(?:early|far|long|many)).{0,28}\bapply\b|"
            r"\bofficial.{0,10}(?:website|link)\b"
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
            r"\bbank statements?.{0,25}(?:months?|how far|period)|"
            r"\b(?:months?|how far back|what period).{0,25}bank statements?\b"
        ),
    }
    bank_excerpts = [item.source_excerpt for item in semantic if item.topic == "bank_period"]
    timing_excerpts = [item.source_excerpt for item in semantic if item.topic == "timing"]
    requested = [topic for topic, pattern in patterns.items()
                 if any(re.search(pattern, clause, re.I) and not (
                     topic == "timing"
                     and any(_overlapping_excerpt(excerpt, clause) for excerpt in bank_excerpts)
                     and not any(_overlapping_excerpt(excerpt, clause) for excerpt in timing_excerpts)
                 ) for clause in clauses)]
    semantic_topics = {item.topic for item in semantic}
    for item in semantic:
        if item.topic not in {"booking", "document_checklist", "next_step", "unsupported", "off_topic"} and item.topic not in requested:
            requested.append(item.topic)
    previous_link_requested = (not off_topic_excerpts and sent_application_guidance
                               and _requests_previous_application_link(current))
    if previous_link_requested and "application" not in requested:
        requested.insert(0, "application")
    # Preserve booking's cross-clause context ("I have no tickets; must I buy them?").
    active_text = "\n".join(clause for clause in _active_clauses(current) if clause not in off_topic_clauses)
    booking_text = "\n".join(clause for clause in _active_clauses(current) if not any(
        _overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts
    ))
    # Do not borrow "where can I obtain..." from an independent question.
    other_question_excerpts = [item.source_excerpt for item in semantic if item.topic != "bank_period"]
    bank_text = "\n".join(clause for clause in _active_clauses(current)
                          if clause not in unsupported_clauses
                          and not any(_overlapping_excerpt(excerpt, clause) for excerpt in other_question_excerpts)
                          and (any(_overlapping_excerpt(excerpt, clause) for excerpt in bank_excerpts)
                               or re.search(r"银行|流水|对账单|网银|账户|存款|\bbank(?:ing)?\b|\b(?:statements?|accounts?|savings)\b", clause, re.I)))
    translation_excerpts = [item.source_excerpt for item in semantic if item.topic == "translation"]
    translation_text = "\n".join(clause for clause in _active_clauses(current, split_commas=False)
        if not any(_overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts + off_topic_excerpts)
        and (re.search(patterns["translation"], clause, re.I)
             or any(_overlapping_excerpt(excerpt, clause) for excerpt in translation_excerpts)))
    application_excerpts = [item.source_excerpt for item in semantic if item.topic == "application"]
    application_text = "\n".join(clause for clause in _active_clauses(current)
        if not any(_overlapping_excerpt(excerpt, clause) for excerpt in unsupported_excerpts + off_topic_excerpts)
        and (re.search(patterns["application"], clause, re.I)
             or any(_overlapping_excerpt(excerpt, clause) for excerpt in application_excerpts)))
    booking = _booking_answer(booking_text, language, today)
    if not booking and "booking" in semantic_topics:
        # The model chooses a topic, never supplies the legal answer or a URL.
        booking = _booking_guidance(language, today, transit=_mentions_current_route(active_text, TRANSIT_ROUTE),
                                    other_route=_mentions_current_route(active_text, OTHER_ROUTE))
    answers = [("booking", answer) for answer in booking]
    # A source-grounded model topic is only a proposal. An individual document's
    # purpose is not a request for an entire missing-documents checklist.
    if CHECKED_AT <= today <= REVIEW_AFTER:
        direct_preparation = reviewed_document_preparation(current, language)
        if (direct_preparation and not off_topic_excerpts
                and not _request_has_other_route(active_text, current)):
            # Reviewed, narrow FAQ fallback also works when extraction omits a
            # question entirely. It does not invent an LLM proposal or case fact.
            answers.append(("document_preparation", direct_preparation))
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
            topic_clauses = list(dict.fromkeys(
                [clause for clause in clauses if re.search(patterns.get(topic, r"(?!)"), clause, re.I)]
                + [clause for clause in _active_clauses(current)
                   if any(_overlapping_excerpt(excerpt, clause) for excerpt in topic_excerpts)]
            ))
            if topic == "application" and previous_link_requested:
                topic_clauses = _active_clauses(current)
            context = (bank_text if topic == "bank_period" else translation_text if topic == "translation"
                       else application_text if topic == "application" else "\n".join(topic_clauses))
            if topic in {"application", "timing", "fees", "bank_period"}:
                scopes = [_request_has_other_route(active_text, clause) for clause in topic_clauses]
                if any(scopes):
                    if not any(name == "route_check" for name, _ in answers):
                        answers.append(("route_check", _route_check_answer(language)))
                    if all(scopes):
                        continue
                    context = "\n".join(clause for clause in _active_clauses(context)
                                        if not _request_has_other_route(active_text, clause))
            answers.append((topic, _reviewed_answer(
                "application_link" if topic == "application" and previous_link_requested else topic,
                language, body=context,
            )))
    if "unsupported" in semantic_topics and include_unsupported:
        boundaries = dict.fromkeys(_unsupported_answer(
            item.source_excerpt, language, today,
            other_route=_request_has_other_route(active_text, item.source_excerpt),
        ) for item in semantic if item.topic == "unsupported")
        answers.append(("unsupported", "\n\n".join(boundaries)))
    if "off_topic" in semantic_topics:
        answers.append(("off_topic",
            "这个问题不属于英国签证准备，我这边没法给你可靠的答案或链接。"
            "如果你还有签证材料方面的问题，可以接着问。"
            if language == "zh" else
            "That question is outside UK visa preparation, so I can't give you a reliable answer or link here. "
            "If you have another question about your visa documents, feel free to ask."
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
    return capped_answer_plan(answers, language)
