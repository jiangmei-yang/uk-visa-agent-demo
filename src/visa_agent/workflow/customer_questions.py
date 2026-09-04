"""Small reviewed answer set, not open-ended immigration advice."""

import re
from datetime import date

from visa_agent.workflow.conversation import latest_reply_text

SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
APPLICATION_SOURCE = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
CHECKED_AT = date(2026, 9, 4)
REVIEW_AFTER = date(2026, 10, 4)


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
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return [
            "关于提前订机票和酒店的问题，我需要先复核最新官方说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current official guidance before answering your booking question."
        ]
    if re.search(r"过境|transit", body, re.I):
        return [
            "你提到过境；过境与普通访问的材料要求不能直接混用，需要先由顾问确认路线。"
            if language == "zh"
            else "You mentioned transit; its evidence requirements need a separate route check."
        ]
    answer = (
        "关于机票和酒店：普通 Standard Visitor 申请不需要为了提供这些预订证明而先购买。"
        "官方材料指南把酒店预订和机票预订（过境除外）列为不应作为证据提交的材料。"
        "我们先整理真实的计划行程和住宿安排；不要把尚未确定的安排写成已经预订。"
        if language == "zh"
        else "For an ordinary Standard Visitor application, you do not need to buy flights or book "
        "a hotel just to supply booking evidence. The official guide lists hotel bookings and "
        "flight bookings (except transit) as documents not to use as evidence. We can first "
        "record your intended arrangements without describing unbooked plans as confirmed bookings."
    )
    return [answer + "\nGOV.UK: " + SOURCE + "#documents-you-should-not-use-as-evidence"]


def _active_clauses(body: str) -> list[str]:
    """Quoted and explicitly declined topics are not fresh requests for advice."""
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", text)
    clauses = re.split(r"[。！!；;\n，,]|(?<=[?？])\s*|\.(?:\s|$)", text)
    declined = (
        r"(?:不用|不需要|无需|别|不要|不想|不必).{0,10}(?:发|给|说|讲|解释|介绍|告诉|知道)|"
        r"\b(?:don['’]t|do not|no need to|not asking|not interested).{0,18}"
        r"(?:send|give|explain|tell|know|discuss|about)\b|"
        r"(?:无需|不用|不需要).{0,8}(?:链接|网址|官网)|"
        r"\bno (?:links?|websites?)\b"
    )
    return [clause.strip() for clause in clauses
            if clause.strip() and not re.search(declined, clause, re.I)]


def _question_clauses(body: str) -> list[str]:
    question = (
        r"[?？]|吗|么|如何|怎么|怎样|哪里|哪[个里]|多久|何时|什么时候|几周|最早|"
        r"(?:请|麻烦|能否|可以).{0,12}(?:告诉|解释|介绍|说|发|给)|发我|给我|"
        r"\b(?:what|where|when|how|can|could|would|do|must|should)\b|"
        r"\b(?:please|send me|give me|tell me|explain)\b"
    )
    return [clause for clause in _active_clauses(body) if re.search(question, clause, re.I)]


def _reviewed_answer(topic: str, language: str) -> str:
    if topic == "application":
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
            "不是从开始准备材料那天起算，也不保证按时出结果或一定获批。"
            if language == "zh"
            else "If you need a Standard Visitor visa, you can apply up to 3 months before travel. "
            "A decision usually takes up to 3 weeks after you have applied online, proved your "
            "identity and provided your documents—not from the day you start preparing. "
            "This is not a guaranteed deadline or a promise of approval."
        )
        source = APPLICATION_SOURCE
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
    return answer + "\nGOV.UK: " + source


def grounded_customer_answers(body: str, language: str, today: date) -> list[str]:
    """Reviewed facts only, capped at three relevant answers, never a case-state update."""
    current = latest_reply_text(body)
    clauses = _question_clauses(current)
    patterns = {
        "application": (
            r"(?:申请|办理|签证).{0,8}(?:官网|网站|网址|链接|入口|流程|步骤)|"
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
    }
    requested = [topic for topic, pattern in patterns.items()
                 if any(re.search(pattern, clause, re.I) for clause in clauses)]
    # Preserve booking's cross-clause context ("I have no tickets; must I buy them?").
    active_text = "\n".join(_active_clauses(current))
    booking = _booking_answer(active_text, language, today)
    answers = list(booking)
    if requested and not CHECKED_AT <= today <= REVIEW_AFTER:
        answers.append(
            "关于你问的申请安排或材料要求，我需要先复核最新 GOV.UK 说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current GOV.UK guidance before answering your application or document question."
        )
    elif requested:
        other_route = re.search(r"过境|学生签证|工作签证|结婚签证|\b(?:transit|student visa|work visa|marriage visa)\b", active_text, re.I)
        if other_route and any(topic in {"application", "timing"} for topic in requested):
            answers.append(
                "你提到的路线可能不是普通 Standard Visitor，申请入口和办理时间需要先按对应路线核实，不能直接套用访问签证流程。"
                if language == "zh"
                else "The route you mentioned may not be an ordinary Standard Visitor visa; its application process and timing need a separate route check."
            )
            requested = [topic for topic in requested if topic == "translation"]
        answers.extend(_reviewed_answer(topic, language) for topic in requested)
    if re.search(
        r"(?:不要|不用|无需|不需要|别).{0,12}(?:链接|网址|网站|官网)|"
        r"\b(?:no links?|(?:don['’]t|do not) (?:send|need)|no need (?:for|to send)).{0,12}"
        r"(?:links?|websites?)\b|\b(?:no links?|without links?)\b",
        current, re.I,
    ):
        answers = [answer.split("\nGOV.UK:", 1)[0] for answer in answers]
        answers = [answer.replace("下面的 GOV.UK 页面", "GOV.UK 官方申请页面")
                   .replace("the GOV.UK page below", "the official GOV.UK application page")
                   for answer in answers]
    return list(dict.fromkeys(answers))[:3]
