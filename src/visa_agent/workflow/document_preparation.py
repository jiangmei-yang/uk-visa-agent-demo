"""Reviewed practical help for one currently requested document, without case mutation.

Source checked 2026-09-04. The caller retains source-expiry, route, pause and
no-links controls. Office names and suggested organisation of details are practical
suggestions, not departments/forms mandated by GOV.UK. This is not a required
checklist, document acceptance decision, substitute-evidence waiver or letter writer.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from visa_agent.domain.models import Case, NextStepAdvice
    from visa_agent.domain.policy import Policy

from visa_agent.workflow.document_purpose import DOCUMENTS_SOURCE

_STUDENT = r"在读证明|在学证明|在學證明|学校证明|\b(?:enrol(?:l)?ment|student status|student) letter\b|\bletter confirming (?:my )?enrol(?:l)?ment\b"
_EMPLOYMENT = r"在职证明|雇主信|工作证明|\b(?:employment|employer(?:'s|’s)?) letter\b|\bletter from (?:my|the) employer\b"
_INVITATION = r"邀请函|邀请信|\binvitation letter\b|\bletter of invitation\b"
_SELF_EMPLOYED = r"自雇|自己经营|自己做生意|个体户|\bself[- ]employed\b|\b(?:run|own) my (?:own )?business\b"
_NO_HR = r"没有.{0,8}(?:HR|人事|雇主)|\b(?:no|without|do not have|don't have|don’t have)\s+(?:an?\s+)?(?:HR|human resources|employer)\b"
_ACTION = (
    r"找谁|向谁|谁.{0,12}(?:开|写|出具)|(?:应该|该|要)找.{0,22}(?:还是|或)|"
    r"哪里.{0,12}(?:开|办|申请|拿|索取)|怎么|怎样|如何|"
    r"(?:写|包括|包含|注明|写明).{0,10}(?:什么|哪些)|什么内容|"
    r"\b(?:who|where|how)\b.{0,100}\b(?:get|obtain|ask|request|issue|write|prepare|arrange|include|approach)\b|"
    r"\bwhat\b.{0,100}\b(?:include|write|contain|put|information|details|say)\b"
)
_UNSAFE_OR_OUTSIDE = (
    r"保证|一定能|包过|获批|过签|足够|够不够|能过吗|会被接受|能被接受|"
    r"伪造|编造|造假|虚构|假证明|假公章|倒签|改.{0,5}(?:工资|薪资|收入)|"
    r"模板|范文|"
    r"学生签证|留学签证|工作签证|工签|结婚签证|婚姻签证|配偶签证|永居|过境|医疗|付费演讲|"
    r"(?:美国|加拿大|澳洲|澳大利亚|申根|法国|新西兰).{0,8}签证|"
    r"房贷|贷款|租房申请|学校录取|"
    r"\b(?:guarantee\w*|approv\w*|sufficient|enough|accepted|eligible|qualif\w*|"
    r"fake|forg\w*|fabricat\w*|invent\w*|backdat\w*|template|mortgage|loan)\b|"
    r"\b(?:student|work|marriage|spouse|transit|Canadian|American|Australian|Schengen) visa\b|"
    r"\b(?:skilled worker|global talent|graduate visa|permanent residen\w*|medical treatment|paid engagement)\b"
)
_UNAVAILABLE = r"开不出|不给开|拒绝开|\b(?:cannot|can't|won't|refuses? to)\s+(?:get|issue|provide)\b"
_CONDITION_OR_DECLINED = (
    r"如果|假如|假设|除非|假定|不用.{0,16}(?:说|讲|告诉|解释|回答)|"
    r"(?:别|不要|不想|不必).{0,16}(?:说|讲|告诉|解释|回答|问)|"
    r"(?:不问|没问|不是问|不需要知道|不需要解释)|"
    r"\b(?:if|unless|assuming|suppose|hypothetical|not asking)\b|"
    r"\b(?:do not|don't|don’t|no need to|not asking|not interested)\b.{0,35}"
    r"\b(?:explain|tell|answer|discuss|ask|know|about)\b"
)
_THIRD_PARTY_REQUEST = (
    r"(?:朋友|同学|同事|客户|申请人|他|她)(?:说|问|想问|的问题|让我转述)|"
    r"(?:帮|替|代)(?:我的?|一位)?(?:朋友|同学|同事|客户|他|她).{0,12}(?:问|申请|准备)|"
    r"\b(?:(?:my|a|the)\s+)?(?:friend|sister|brother|client|customer|applicant)\s+"
    r"(?:asks?|asked|said|wrote|wants? to know)\b|"
    r"\bon behalf of\b|\b(?:his|her|their)\s+(?:enrol(?:l)?ment|student|employment|employer)\b|"
    r"\b(?:he|she|they)\s+(?:should|can|would|will|needs? to)\b"
)
_UK_WORK = (
    r"(?:在|去|到)英国.{0,12}(?:自雇|经营|工作|打工|开店)|"
    r"\bself[- ]employed\s+(?:in|within)\s+(?:the\s+)?(?:UK|Britain)\b|"
    r"\bwork(?:ing)?\s+(?:remotely\s+)?(?:in|for)\s+(?:the\s+)?(?:UK|Britain|a British company)\b|"
    r"\b(?:run|start|set up)\s+(?:(?:my|a|the|own)\s+)*business\s+in\s+(?:the\s+)?(?:UK|Britain)\b"
)

SCHOOL_RECORD_TOPIC = "student_online_record_obstacle_v1"
_SCHOOL = r"学校|校方|大学|\b(?:school|university|college|registry)\b"
_SCHOOL_UNAVAILABLE = (
    r"(?:学校|校方|大学).{0,18}(?:不提供|不开|不出具|开不出|不给开|拒绝开).{0,18}(?:在读|在学|证明)|"
    r"\b(?:school|university|college|registry)\b.{0,30}\b(?:does not|doesn't|cannot|can't|won't|refuses to)\s+"
    r"(?:issue|provide|write)\b.{0,35}\b(?:enrol\w*|student status)\s+letters?\b"
)
_ONLINE_RECORD = (
    r"(?:只能|只有|仅有|可以)[^。！？；;，,\n]{0,18}(?:网上|线上|在线|电子)"
    r"[^。！？；;，,\n]{0,18}(?:在读|在学|学籍)(?:记录|证明)|"
    r"\bI\s+(?:can\s+)?only\s+(?:download|have|obtain|get)\b"
    r"(?=[^.!?\n]{0,140}\b(?:enrol\w*|student status)\s+record\b)"
    r"[^.!?\n]{0,140}\b(?:online|electronic|portal)\b"
)


def _school_current(text: str) -> str:
    from visa_agent.workflow.conversation import latest_reply_text

    if not text or len(text) > 6000:
        return ""
    current = latest_reply_text(text)
    unsafe = (
        _UNSAFE_OR_OUTSIDE + "|" + _CONDITION_OR_DECLINED + "|" + _THIRD_PARTY_REQUEST + "|" + _UK_WORK
        + r"|(?:朋友|同学|同事|客户|他|她)(?:的学校|只能)|\b(?:friend's|friend’s|his|her|their)\b"
        + r"|(?:不是说|并不是|不属实|没有遇到)|\b(?:not true|not the case|have not encountered)\b"
        + r"|(?:学校|校方|大学).{0,6}(?:没有说|没说过|不是不)|\b(?:has not said|hasn't said|not sure whether)\b"
        + r"|(?:你的|其他|另一所|别人的)(?:学校|大学)|\b(?:your|another|other)\s+(?:school|university|college)\b"
        + r"|(?:哥哥|弟弟|姐姐|妹妹|朋友|同学|同事|客户|家人|父母|他|她)的(?:学校|大学)|"
        r"\b(?:brother|sister|parent|friend|client|partner)['’]s\s+(?:school|university|college)\b"
        + r"|(?:忽略|跳过|绕过|修改|无视).{0,12}(?:规则|指令|检查|审核)|"
        r"\b(?:ignore|bypass|override).{0,25}(?:instructions?|rules?|checks?|system|prompt)\b"
        + r"|customer_questions|source_excerpt|requires_human_review"
    )
    if re.search(unsafe, current, re.I):
        return ""
    current = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`', "", current)
    return re.sub(r"(?<!\w)'[^'\n]+'(?!\w)", "", current).strip()


def school_record_reported(text: str) -> bool:
    """A current own difficulty, not proof of its truth or evidence acceptance."""
    current = _school_current(text)
    return bool(re.search(_SCHOOL_UNAVAILABLE, current, re.I)
                and re.search(_ONLINE_RECORD, current, re.I))


def school_record_guidance(language: str) -> str:
    # The record checks are practical suggestions, not a list of UKVI-mandated
    # fields or a claim that a portal export substitutes for a provider's letter.
    return (
        "先看看你提到的学校网上在读记录。"
        "可以核对上面有没有你的姓名、学校名称、当前在读状态，以及记录的出具日期；"
        "这些是方便核对的内容，不是让你自行补写进文件。\n\n"
        "再向学校确认：这份下载记录有没有官方核验方式，例如验证链接或可供查询的学籍部门联系方式？"
        "保留学校下载的原文件和校方回复，不用自己制作一份“学校证明”。"
        "网上记录能说明多少情况，仍需看具体内容；不能仅凭文件名称认定它可替代学校证明，也不会因此免去其他材料核对。"
        if language == "zh" else
        "Let's start with the university's online enrolment record you mentioned. "
        "Check whether it shows your name, the university, your current enrolment status and its issue date. "
        "These are useful checks, not details to add to the file yourself.\n\n"
        "Ask the university how someone can verify the downloaded record, for example through an official verification link "
        "or a registry contact. Keep the original download and the university's response; do not create your own university letter. "
        "What the record establishes still needs to be checked. Its title alone does not make it a substitute for a university letter "
        "or remove the need to check other evidence."
    )


def reviewed_school_record(text: str, language: str) -> str | None:
    if not school_record_reported(text):
        return None
    return school_record_guidance(language) + "\nGOV.UK: " + DOCUMENTS_SOURCE + "#demonstrating-personal-circumstances"


def sent_school_record_context(case: Case, outbox: list[dict[str, Any]]) -> bool:
    """Only a fully delivered discussion is memory; a saved model draft is not."""
    event = case.guidance_events.get(SCHOOL_RECORD_TOPIC)
    return bool(event and any(
        row["case_id"] == case.id and row["event_id"] == event and row["status"] == "SENT"
        and row.get("provider_message_id") and row.get("sent_at")
        and any(school_record_guidance(language) in row["payload"] for language in ("zh", "en"))
        for row in outbox
    ))


def school_record_resolved(text: str) -> bool:
    current = _school_current(text)
    return any(re.fullmatch(
        r"(?:我的?)?(?:学校|校方|大学)(?:现在|已经|终于){1,2}(?:能开|可以开|提供了|出具了)(?:在读|在学)?证明了?|"
        r"我(?:已经|刚刚|终于)?(?:拿到|收到|取得)了?(?:学校的)?(?:学校|在读|在学)证明了?|"
        r"(?:my|the)\s+(?:school|university|college)\s+(?:has\s+)?(?:now\s+|finally\s+)?"
        r"(?:issued|provided|written|can (?:now )?(?:issue|provide))\s+(?:(?:my|an?|the)\s+)?(?:enrol\w*|student status)\s+letter|"
        r"I\s+(?:have\s+)?(?:now\s+)?(?:received|obtained|got)\s+(?:(?:my|an?|the)\s+)?"
        r"(?:(?:university|school)['’]s\s+)?(?:enrol\w*|university)\s+letter",
        clause.strip(), re.I,
    ) for clause in re.split(r"[。！!；;\n，,]|\.(?:\s|$)", current))


def school_record_unavailable(text: str) -> bool:
    """Retire outdated access advice, without calling the school issue resolved."""
    current = _school_current(text)
    return any(re.fullmatch(
        r"(?:学校的?)?(?:网上|在线|电子)(?:在读)?记录(?:我)?(?:现在|已经)?(?:打不开|无法打开|没有了|不能下载)了?|"
        r"我(?:现在|已经)?(?:打不开|无法打开|不能下载)(?:学校的?)?(?:网上|在线|电子)(?:在读)?记录了?|"
        r"I\s+(?:no longer have|can no longer access|cannot open|can't open)\s+(?:my|the)\s+"
        r"(?:online|electronic)\s+(?:enrolment\s+)?record",
        clause.strip(), re.I,
    ) for clause in re.split(r"[。！!；;\n]|(?<=[?？])\s*|\.(?:\s|$)", current))


def school_record_followup(text: str) -> bool:
    current = _school_current(text)
    if not current or re.search(_SCHOOL + r"|在读|在学|学籍|记录|\b(?:enrol\w*|records?)\b", current, re.I):
        return False
    return any(re.fullmatch(
        r"(?:那|我|现在|接下来|下一步){0,3}(?:应该|该|可以)?(?:怎么办|怎么做|做什么|准备什么)[?？]?|"
        r"what\s+(?:should|can|do)\s+I\s+(?:do|prepare)\s*(?:next|now)?\s*[?？]?",
        clause.strip(), re.I,
    ) for clause in re.split(r"[。！!；;\n]|(?<=[?？])\s*|\.(?:\s|$)", current))


def school_record_next_step(case: Case, policy: Policy, *, previously_sent: bool, today: date) -> NextStepAdvice | None:
    """Read-only follow-up after the selector's existing review/confirmation gates."""
    from visa_agent.domain.models import DocumentStatus, NextStepAdvice
    from visa_agent.workflow.advice_preferences import wants_no_links

    if (not school_record_followup(case.latest_customer_message) or not previously_sent
            or case.profile.occupation_status != "student"):
        return None
    # A fresh school statement needs its own interpretation. Never continue an
    # older obstacle across a correction, report about a different record or FAQ.
    if (not date(2026, 9, 4) <= today <= date(2026, 10, 4)
            or policy.version != "2026-02-25" or case.policy_version != policy.version
            or DOCUMENTS_SOURCE not in policy.sources or not policy.is_current(today)):
        return NextStepAdvice(kind="review", message=(
            "先复核最新官方材料说明，再继续判断这份学校记录该如何准备。" if case.customer_language == "zh" else
            "First recheck the current official evidence guidance before continuing with the school record."
        ))
    if any(evidence.fact_key == "route_confirmed_standard_visitor" and evidence.value is False
           and not evidence.superseded for evidence in case.evidence):
        return NextStepAdvice(kind="review", message=(
            "先由顾问核对适用路线，再判断学校材料的安排。" if case.customer_language == "zh" else
            "An adviser needs to check the appropriate route before choosing the school evidence."
        ))
    item = next((item for item in case.requirements if item.id == "status_evidence" and item.applicable), None)
    rule = next((rule for rule in policy.requirements if rule.id == "status_evidence"), None)
    if (item is None or item.satisfied or rule is None or item.rule_version != policy.version
            or rule.version != policy.version or rule.source_url != DOCUMENTS_SOURCE):
        return None
    if any(document.kind in rule.acceptable_evidence and document.status in {
        DocumentStatus.RECEIVED, DocumentStatus.PROCESSING, DocumentStatus.ACCEPTED_FOR_REVIEW,
    } for document in case.documents):
        return NextStepAdvice(kind="paused" if case.preparation_paused else "waiting", requirement_id=item.id,
            message=("学校相关文件已经收到，先核对现有文件，不用重发。" if case.customer_language == "zh" else
                     "The school-related file has already been received. Check the existing file; there is no need to resend it."))
    message = (
        "先打开学校那份网上在读记录，看看姓名、学校名称、当前在读状态和出具日期是否清楚。"
        "缺哪项先记下来，不要自行改文件。然后问学籍部门这份记录怎样让第三方核验。"
        "保留原文件和校方答复；能否用于说明你的情况仍需核对内容，不能直接认定它替代了学校证明。"
        if case.customer_language == "zh" else
        "Start by opening the university's online enrolment record. Check whether your name, university, current "
        "enrolment status and issue date are clear. Note anything missing without editing the file. Then ask the registry "
        "how a third party can verify it. Keep the original and the university's response; its contents still need to be "
        "checked, so it does not automatically replace a university letter."
    )
    if case.preparation_paused:
        message += ("\n\n这只是之后准备时可参考的办法，现在不用提交文件，准备仍保持暂停。" if case.customer_language == "zh" else
                    "\n\nThis is guidance for later. There is no need to send anything now; preparation remains on hold.")
    if not wants_no_links(case.latest_customer_message):
        message += "\nGOV.UK: " + rule.source_url
    return NextStepAdvice(kind="paused" if case.preparation_paused else "document", message=message, requirement_id=item.id)


def _request_kind(text: str) -> str | None:
    # Lazy import keeps this standalone helper safe to call from conversation.py.
    from visa_agent.workflow.conversation import latest_reply_text

    if not text or len(text) > 6000:
        return None
    current = latest_reply_text(text).strip()
    # Keep qualifiers intact before considering individual clauses. A quoted,
    # conditional or unsafe request must not become direct by trimming its prefix.
    if re.search(_UNSAFE_OR_OUTSIDE + "|" + _UNAVAILABLE + "|" + _CONDITION_OR_DECLINED + "|" + _THIRD_PARTY_REQUEST + "|" + _UK_WORK,
                 current, re.I):
        return None
    current = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`', "", current)
    current = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)", "", current)
    self_employed_context = re.search(_SELF_EMPLOYED, current, re.I)
    kinds = set()
    for clause in re.split(r"[。！？!?；;\n]|\.(?:\s|$)", current):
        if clause.lstrip().startswith(">"):
            continue
        action = re.search(_ACTION, clause, re.I)
        self_employed = re.search(_SELF_EMPLOYED, clause, re.I)
        if self_employed and re.search(
            r"(?:怎么|如何|怎样).{0,16}(?:说明|证明|描述|介绍)|"
            r"\bhow\s+(?:do|should|can)\s+I\s+(?:explain|describe|document|show)\b", clause, re.I,
        ):
            kinds.add("self_employment")
            continue
        if self_employed and re.search(_NO_HR, clause, re.I) and (
            action or re.search(r"怎么办|\bwhat (?:should|can|do) I do\b", clause, re.I)
        ):
            kinds.add("self_employment")
            continue
        if not action:
            continue
        if re.search(_STUDENT, clause, re.I):
            kinds.add("student")
        if re.search(_EMPLOYMENT, clause, re.I):
            kinds.add("self_employment" if self_employed_context else "employment")
        if re.search(_INVITATION, clause, re.I):
            kinds.add("invitation")
    return next(iter(kinds)) if len(kinds) == 1 else None


def reviewed_document_preparation(text: str, language: str) -> str | None:
    school = reviewed_school_record(text, language)
    if school:
        return school
    """Return one source-bound practical explanation, or None outside this narrow set."""
    if language not in {"zh", "en"}:
        return None
    kind = _request_kind(text)
    zh = language == "zh"
    if kind == "student":
        answer = (
            "可以先问学校的学籍部门或注册处，通常他们会告诉你怎样申请在读证明；有现成模板就按学校的流程来。"
            "请学校用抬头纸确认你的在读状态，拿到后核对姓名、课程等信息。\n\n"
            "这份证明是用来说明你目前的学习情况。如果旅行涉及请假，再请学校写明实际安排；还没批准的就留待确认，"
            "不需要为了套一个模板写成已获准。准假说明是否适用，要结合你的行程，不是每名旅游申请人都有同一套格式。"
            if zh else
            "Start by asking your school's registry or student-records office how to request an enrolment letter. "
            "Use its existing process, asking for headed paper confirming enrolment and checking your name and course. "
            "Where the trip involves leave, ask the school to explain the actual arrangement, not describe unapproved leave "
            "as agreed. This explains your studies; it is not a universal fixed-format leave-letter requirement for tourists."
        )
    elif kind == "employment":
        answer = (
            "先向公司人事问一下在职证明的申请流程；没有独立人事部门，就问雇主由谁代表公司出具。"
            "信里用公司抬头纸写清职位、薪资、任职时间和公司联系方式。你可以先把这些信息整理给对方核对，省去来回修改。\n\n"
            "这封信帮助说明你的工作和收入。请假安排如果还没确定，就先不要写成已批准；后面还要和申请表及其他材料一起核对。"
            if zh else
            "Ask HR for an employer letter; without a separate HR team, ask your employer who is authorised to confirm "
            "the details. Request company-headed paper showing your role, salary, length of employment and company "
            "contact details. Give them accurate information to check, without presenting uncertain pay or leave as confirmed. "
            "The letter explains employment and income; completing a format does not establish sufficient evidence."
        )
    elif kind == "self_employment":
        answer = (
            "自己经营业务，可以先找已有的经营登记材料或近期业务发票，说明业务仍在开展。"
            "再整理一段简短说明，把你从事什么业务、收入从哪里来和现有记录对应起来，"
            "不要写出不存在的雇主或雇佣关系。GOV.UK 列出了这类自雇材料例子；"
            "这不是豁免其他材料，也不表示仅靠一份自述就能替代缺少的证据。"
            if zh else
            "For your own business, start with existing registration records or recent business invoices showing ongoing "
            "activity. A short explanation can connect what you do and where income comes from to those records. "
            "Do not invent an employer or employment relationship. GOV.UK lists these self-employment examples; "
            "this does not waive other evidence or make a personal statement a substitute for missing records."
        )
    elif kind == "invitation":
        answer = (
            "先分清邀请方：探亲访友可以请实际接待你的亲友说明安排；参加会议则向会议主办方索取邀请函。"
            "准备时可把访问目的、计划时间和地点、接待安排及联系人列给对方核对，再由对方确认。"
            "谁负担哪些费用要如实说明，不能因为对方邀请你就默认由对方资助；未确定的安排标明待定。"
            "这些是整理访问安排的建议，不是所有旅游申请都必须有邀请函，也不能代替资金等其他证据。"
            if zh else
            "Identify the inviter first: for a family or friend visit, ask the actual host to explain the arrangements; "
            "for a conference, request the organiser's invitation. Suggest they check the purpose, proposed dates and "
            "location, hosting arrangements and contact details. State who pays for what without assuming an inviter "
            "is a sponsor, and label undecided plans accordingly. This is preparation guidance, not a universal tourist "
            "invitation requirement or a replacement for other evidence."
        )
        family = bool(re.search(r"探亲|访友|姐姐|妹妹|哥哥|弟弟|亲友|住她家|住他家|"
                                r"\b(?:sister|brother|family|friend|host me)\b", text, re.I))
        conference = bool(re.search(r"会议|参会|主办方|\b(?:conference|organiser|organizer)\b", text, re.I))
        if family and not conference:
            answer = ((
                "可以请实际接待你的亲友来写，先把你们的关系、访问目的、计划时间、住宿和联系方式列给对方核对。"
                "日期还没定就注明待定，不用为了写信硬定一个日期。\n\n"
                "谁负担哪些费用要分开写清楚：提供住宿和经济资助不是一回事，不能默认邀请方也付旅行费。"
                "邀请信帮助解释访问安排，不能代替资金等其他证据。"
            ) if zh else (
                "Ask your actual host to write it. Give them your relationship, purpose of the visit, proposed dates, "
                "accommodation and contact details to check. Mark undecided dates as undecided.\n\n"
                "Describe accommodation and financial support separately, without assuming your host is your sponsor. "
                "The invitation explains the visit arrangements; it does not replace other evidence."
            ))
        return answer + "\nGOV.UK: " + DOCUMENTS_SOURCE
    else:
        return None
    return answer + "\nGOV.UK: " + DOCUMENTS_SOURCE + "#demonstrating-personal-circumstances"
