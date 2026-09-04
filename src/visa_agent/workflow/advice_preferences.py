"""Bounded current consultation preferences, never case facts or permissions.

These flags only suppress or defer advice. They cannot resume preparation,
confirm a summary, change the stored visa route or grant processing consent.
"""

from __future__ import annotations

import re

_CONDITIONAL = (
    r"\b(?:if|unless|whether|assuming|suppose|hypothetical|provided that)\b|"
    r"\b(?:tomorrow|next week|next month)\b|"
    r"如果|假如|假设|假定|除非|只要|是否|明天|下周|下个月"
)
_REPORTED = (
    r"\b(?:friend|sister|brother|client|customer|applicant|he|she|they)\b.{0,40}"
    r"\b(?:said|says|asked|asks|wrote|writes|wants?)\b|"
    r"(?:朋友|姐姐|妹妹|哥哥|弟弟|客户|申请人|他|她)(?:说|问|写|想)|"
    r"\b(?:not asking you to|did not ask|didn't ask|never said)\b|"
    r"不是让你|没让你|不是说|没有说|不要把"
)
_HISTORICAL = (
    r"\b(?:previously|earlier I|last (?:time|week|month|year)|I used to)\b|"
    r"(?:上次|之前|以前|去年)我(?:说|问|要求|想)|我(?:上次|之前|以前|去年)(?:说|问|要求|想)"
)
_TOPICS = {
    "fees": r"\b(?:fees?|visa costs?|application costs?)\b|(?:申请|签证)?费用|申请费|签证费|收费",
    "application": r"\bapplication\s+(?:steps?|process|website|link)\b|"
                   r"\b(?:how to apply|where to apply|official (?:website|link))\b|申请(?:流程|步骤|入口|网站)|官网",
    "timing": r"\b(?:processing times?|timing|when to apply|how early)\b|审理(?:时间|多久)?|申请时间|多久出签|提前多久",
    "translation": r"\btranslat\w*\b|翻译|译文|译者",
    "booking": r"\b(?:bookings?|flights?|hotels?|tickets?)\b|预订|机票|酒店",
    "bank_period": r"\bbank statements?\b|\bfinancial evidence\b|银行流水|流水|银行对账单|资金证明",
}


def _current_clauses(body: str) -> list[str]:
    # Lazy imports avoid a cycle when the reviewed answer compiler uses this
    # helper. _active_clauses deliberately drops the refusals needed here.
    from visa_agent.workflow.conversation import latest_reply_text
    from visa_agent.workflow.customer_questions import _request_clauses

    text = latest_reply_text(body)
    text = re.sub(
        r'"[^\"]*"|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|`[^`]*`|(?<!\w)\'[^\']*\'(?!\w)',
        " ", text,
    )
    # A report introduction owns the following block, even across full stops.
    # Earlier independent instructions remain current; the reported block does not.
    report = re.search(
        r"(?m)^[^\n]{0,160}(?:said|says|wrote|writes|quote|example|template|"
        r"说|写道|转述|原文|模板|示例|举例)[^\n:：]{0,60}[:：]\s*(?:\n|$)", text, re.I,
    )
    if report:
        text = text[:report.start()]
    clauses: list[str] = []
    # Keep comma/semicolon/newline conditions intact until a complete sentence
    # ends. Splitting them first could turn a hypothetical into an instruction.
    for sentence in re.split(r"[。！？!?]|\.(?:\s|$)", text):
        if re.search(_CONDITIONAL + "|" + _REPORTED + "|" + _HISTORICAL, sentence, re.I):
            continue
        for clause in _request_clauses(sentence, split_commas=False):
            parts = re.split(
                r"[,，]\s*(?=(?:(?:请|现在|先)\s*)?(?:不需要|不要|不用|别)|"
                r"(?:please\s+)?(?:do not|don't|no links?))", clause, flags=re.I,
            )
            clauses.extend(part.strip(" 。.!！?？;；") for part in parts if part.strip())
    return clauses


def excluded_advice_topics(body: str) -> set[str]:
    """Explicitly declined FAQ subjects; a topic mention alone is insufficient."""
    objects = []
    for clause in _current_clauses(body):
        for pattern in (
            r"^(?:please\s+)?(?:do not|don't|don’t|no need to|stop)\s+"
            r"(?:answer(?:ing)?|explain(?:ing)?|discuss(?:ing)?|cover(?:ing)?|talk(?:ing)? about)\s+(.+)$",
            r"^(?:please\s+)?(?:skip|omit|leave out)\s+(.+)$",
            r"^I(?:\s+am|'m|’m)\s+not\s+asking\s+about\s+(.+)$",
            r"^(?:现在|这次)?(?:请|麻烦)?(?:先|暂时)?(?:不要|不用|无需|不需要|别|暂不)"
            r"(?:再|重复|继续)?(?:回答|解释|讲|说|介绍)(.+)$",
            r"^(.+?)(?:先|暂时)?(?:不用|不要|不需要|不必|暂不)(?:再)?(?:回答|解释|讲|说)(?:了|吧)?$",
            r"[,，]\s*not\s+(.+)$",
        ):
            match = re.search(pattern, clause, re.I)
            if match:
                objects.append(match[1])
    return {topic for topic, pattern in _TOPICS.items()
            if any(re.search(pattern, target, re.I) for target in objects)}


def wants_no_links(body: str) -> bool:
    """Only a current global no-link preference, not 'do not only send links'."""
    link = r"(?:links?|urls?|websites?)"
    end = (r"(?:\s+in\s+this\s+(?:reply|response|email|message))?"
           r"(?:\s*[,，]?\s*(?:please|for now))?")
    patterns = (
        rf"^(?:please\s+)?(?:no|without)\s+(?:any\s+)?{link}{end}$",
        rf"^(?:please\s+)?(?:do not|don't|don’t)\s+(?:send|include|add|give me)\s+(?:any\s+)?{link}{end}$",
        rf"^I\s+(?:do not|don't|don’t)\s+(?:need|want)\s+(?:any\s+)?{link}{end}$",
        rf"^(?:please\s+)?(?:answer|explain)(?:\s+this)?\s+without\s+{link}{end}$",
        r"^(?:这次|这个回复|这封邮件)?(?:请|麻烦)?(?:先)?(?:不要|不用|无需|不需要|别)(?:再)?(?:给我|发我)?"
        r"(?:发|给|加|附上|附|提供)?(?:任何)?(?:链接|网址|网站|官网链接)(?:了|吧)?$",
    )
    return any(re.search(pattern, clause, re.I)
               for clause in _current_clauses(body) for pattern in patterns)


def defer_previous_advice(body: str) -> bool:
    """Pause earlier consultation answers, not preparation or missing facts."""
    previous = r"(?:the\s+|my\s+)?(?:previous|earlier|old|remaining|unanswered)\s+(?:questions?|topics?|points?)"
    patterns = (
        rf"^(?:please\s+)?(?:do not|don't|don’t|stop)\s+(?:answer(?:ing)?|explain(?:ing)?|cover(?:ing)?)\s+{previous}(?:\s+(?:yet|for now))?$",
        rf"^(?:please\s+)?(?:put|leave)\s+{previous}\s+(?:aside(?:\s+for now)?|for later)$",
        r"^(?:请)?(?:先|暂时)?(?:不要|不用|别|暂不)(?:再)?(?:答|回答|解释|讲|继续)"
        r"(?:之前|刚才|前面|上次|剩下|其余)(?:的|那些)?(?:问题|咨询|部分)(?:了|吧)?$",
        r"^(?:之前|刚才|前面|上次|剩下|其余)(?:的|那些)?(?:问题|咨询|部分)"
        r"(?:都)?(?:先|暂时)?(?:放一放|放到以后|不讲|不回答|不用回答|不要回答)(?:了|吧)?$",
    )
    return any(re.search(pattern, clause, re.I)
               for clause in _current_clauses(body) for pattern in patterns)


def route_change_pending(body: str) -> bool:
    """An explicit current route change warrants review, never a route mutation."""
    visitor = r"(?:(?:a|the|standard|UK|British)\s+)*visitor\s+visa"
    other = r"(?:a\s+)?(?:student|work|marriage|spouse|transit)\s+visa"
    patterns = (
        rf"^I\s+(?:have\s+)?(?:switched|changed)\s+from\s+(?:Standard Visitor|{visitor})\s+to\s+{other}",
        rf"^I(?:\s+am|'m|’m)\s+applying\s+for\s+{other}\s+instead$",
        rf"^I(?:\s+am|'m|’m)\s+no longer\s+applying\s+for\s+{visitor}$",
        r"^I(?:\s+am|'m|’m)\s+(?:changing|switching)\s+my\s+visa\s+(?:route|category)$",
        r"^我(?:现在|这次)?(?:改申|改办|改为申请)(?:学生|留学|工作|结婚|配偶|过境)签证(?:了)?$",
        r"^(?:我|这次)(?:现在)?(?:不再申请|不办)(?:英国|普通)?(?:访问|访客|旅游)签证(?:了)?(?:[,，]|$)",
    )
    return any(re.search(pattern, clause, re.I)
               for clause in _current_clauses(body) for pattern in patterns)
