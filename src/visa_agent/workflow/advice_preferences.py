"""Bounded current consultation preferences, never case facts or permissions.

These flags only suppress or defer advice. They cannot resume preparation,
confirm a summary, change the stored visa route or grant processing consent.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from visa_agent.domain.models import Case

from visa_agent.workflow.intent_matching import normalize_intent_text

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
    "eligibility_overview": (
        r"\b(?:eligibility|eligible|qualif(?:y|ied|ication))\b|"
        r"\b(?:Standard Visitor|visitor visa)\s+requirements?\b|"
        r"一般资格|资格要求|申请条件|是否符合"
    ),
    "biometrics": (
        r"\b(?:biometric(?:s| information)?|fingerprints?|visa application cent(?:re|er)|VAC)\b|"
        r"生物信息|指纹|签证申请中心|签证中心"
    ),
    "after_apply": (
        r"\b(?:application status|track(?:ing)?|after appl(?:y|ying)|after submission|"
        r"decision notification|correct(?:ing)?|withdraw(?:al)?|cancel(?:ling|ation)?)\b|"
        r"递交后|申请状态|查询进度|跟踪申请|决定通知|更正申请|撤回申请|取消申请"
    ),
    "timing": r"\b(?:processing times?|timing|when to apply|how early)\b|审理(?:时间|多久)?|申请时间|多久出签|提前多久",
    "translation": r"\btranslat\w*\b|翻译|译文|译者",
    "booking": r"\b(?:bookings?|flights?|hotels?|tickets?)\b|预订|机票|酒店",
    "bank_period": r"\bbank statements?\b|\bfinancial evidence\b|银行流水|流水|银行对账单|资金证明",
    "sponsor_support": (
        r"\b(?:sponsor(?:ship)?|financial support|sponsor letter)\b|"
        r"资助|担保|资助信|资助说明|资助材料|关系证明"
    ),
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
                r"[,，]\s*(?=(?:(?:但|不过)\s*)?(?:(?:请|现在|先)\s*)?"
                r"(?:不需要|不要|不用|别)|(?:but\s+)?(?:please\s+)?(?:do not|don't|no links?))",
                clause,
                flags=re.I,
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


def wants_brief_reply(body: str) -> bool:
    """Pacing only: never changes facts, consent, requirements or release gates."""
    return any(re.search(
        r"(?:请|先)?简短(?:告诉|说|回答)|\b(?:please\s+)?keep it brief\b",
        clause, re.I,
    ) and not re.search(r"不要|不用|\b(?:not|don't|do not)\b", clause, re.I)
               for clause in _current_clauses(body))


def reply_style_request(body: str) -> tuple[Literal["standard", "brief"], str, bool] | None:
    """Mode, grounded current clause, one-reply-only flag; no model authority."""
    matches: list[tuple[Literal["standard", "brief"], str, bool]] = []
    for clause in _current_clauses(body):
        if clause not in body or len(clause) > 500 or re.search(
            r"reply_style|source_event|\b(?:translate|rephrase)\b|翻译|翻譯|改写|"
            r"我不喜欢|我不希望|\bI (?:dislike|do not want)\b|"
            r"(?:ignore|override|bypass).{0,20}(?:rules|system|instructions)",
            clause, re.I,
        ):
            continue
        local = bool(re.search(r"这次|这封|这个回复|\b(?:this time|this reply|this response|this email)\b", clause, re.I))
        brief = wants_brief_reply(clause) or bool(re.search(
            r"(?:以后|之后)?(?:请|麻烦)?(?:回复|回答|说话)(?:都)?(?:简短|短)(?:一点|一些)|"
            r"(?:请|以后)(?:都)?说重点|\b(?:please\s+)?keep (?:your|the) (?:replies|answers) (?:short|brief)\b",
            clause, re.I,
        ))
        standard = bool(re.search(
            r"(?:请|这次|以后)(?:都)?详细(?:说|讲|解释|回答)|"
            r"(?:以后|之后)(?:都)?不用(?:那么)?简短|"
            r"\b(?:please\s+)?(?:explain|answer)(?:\s+this)? in detail\b|"
            r"\bno need to keep (?:it|your replies) brief\b", clause, re.I,
        ))
        if standard and re.search(r"不用(?:那么)?简短|\bno need to keep\b", clause, re.I):
            brief = False
        if re.search(r"不要.{0,5}(?:简短|详细)|\b(?:do not|don't|not to)\b", clause, re.I):
            continue
        if brief != standard:
            matches.append(("brief" if brief else "standard", clause, local))
    # Conflicting requests are not resolved by taking a convenient last phrase.
    if not matches or len({item[0] for item in matches}) > 1:
        return None
    return matches[-1]


def remember_reply_style(case: Case, event_id: str) -> None:
    request = reply_style_request(case.latest_customer_message)
    if request and not request[2]:
        case.reply_style, case.reply_style_source_excerpt = request[0], request[1]
        case.reply_style_source_event_id = event_id


def prefers_brief_reply(case: Case) -> bool:
    request = reply_style_request(case.latest_customer_message)
    return (request[0] if request else case.reply_style) == "brief"


def reply_style_only(body: str) -> bool:
    """A presentation-only instruction does not invite a new intake question."""
    return reply_style_request(body) is not None and bool(re.fullmatch(
        r"(?:以后|之后|这次|这封邮件)?(?:请|麻烦|你)?(?:都)?"
        r"(?:简短回答|回复(?:都)?(?:简短|短)一点|详细(?:解释|回答)|不用(?:那么)?简短)(?:了|吧)?|"
        r"(?:for this reply,\s*)?(?:please\s+)?(?:keep (?:it|your replies|your answers) (?:short|brief)|"
        r"explain in detail|no need to keep (?:it|your replies) brief)",
        body.strip(" \t\r\n。.!！?？"), re.I,
    ))


def wants_one_action(body: str) -> bool:
    """One practical next action this turn, never completion of omitted items."""
    return any(re.search(
        r"(?:告诉我|给我|先做|先准备).{0,16}(?:一件事|一个步骤|(?<!下)一步)|"
        r"\b(?:just|only)\s+(?:give|tell)\s+me\s+(?:the\s+)?(?:one|a single)\s+(?:thing|step|action)\b|"
        r"\b(?:what is|what's) the one thing I should (?:do|prepare)\b",
        clause, re.I,
    ) and not re.search(r"不要|不是|\b(?:not|don't|do not)\b", clause, re.I)
               for clause in _current_clauses(body))


def wants_no_links(body: str) -> bool:
    """Only a current global no-link preference, not 'do not only send links'."""
    link = r"(?:links?|urls?|websites?)"
    end = (r"(?:\s+in\s+this\s+(?:reply|response|email|message))?"
           r"(?:\s*[,，]?\s*(?:please|for now))?")
    patterns = (
        rf"^(?:but\s+)?(?:please\s+)?(?:no|without)\s+(?:any\s+)?{link}{end}$",
        rf"^(?:but\s+)?(?:please\s+)?(?:do not|don't|don’t)\s+"
        rf"(?:send|include|add|give me)\s+(?:any\s+)?{link}{end}$",
        rf"^I\s+(?:do not|don't|don’t)\s+(?:need|want)\s+(?:any\s+)?{link}{end}$",
        rf"^(?:please\s+)?(?:answer|explain)(?:\s+this)?\s+without\s+{link}{end}$",
        r"^(?:但|不过|同时)?(?:这次|这个回复|这封(?:邮件|回复)?)?(?:请|麻烦)?(?:先)?"
        r"(?:不要|不用|无需|不需要|别)(?:再)?(?:给我|发我)?"
        r"(?:发|给|加|附上|附|提供)?(?:任何)?(?:链接|网址|网站|官网链接)(?:了|吧)?"
        r"(?:[，,]\s*(?:只|仅)(?:讲|说|解释|告诉我).{1,60})?$",
    )
    # An independent own-case sentence may follow a link preference after a
    # comma. Conditions/reported scope have already been excluded as a whole.
    clauses = [normalize_intent_text(part) for clause in _current_clauses(body)
               for part in re.split(r"[,，]\s*(?=我|I\b)", clause)]
    return any(re.search(pattern, clause, re.I)
               for clause in clauses for pattern in patterns)


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
