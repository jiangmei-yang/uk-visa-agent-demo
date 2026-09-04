from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from visa_agent.domain.address_evidence import (
    address_excerpt_is_other_location,
    address_value_is_grounded,
)
from visa_agent.domain.date_evidence import (
    ENGLISH_COMPLETE_DATE,
    canonical_date_value,
    date_is_grounded,
    has_calendar_day,
)
from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.domain.sponsor_evidence import SPONSOR_FIELDS, sponsor_role_is_grounded
from visa_agent.llm.ports import CasePatch, FactUpdate, LLMClient
from visa_agent.workflow.conversation import (
    blocked_customer_message,
    change_acknowledgement,
    confirmation_message,
    latest_reply_text,
    preparation_control_receipt,
    reply_items,
    waiting_acknowledgement,
)
from visa_agent.workflow.customer_questions import validated_customer_questions
from visa_agent.workflow.explicit_answer_scope import funding_source_value_is_grounded
from visa_agent.workflow.preparation_control import validated_preparation_intent

MIN_ACCEPTED_CONFIDENCE = 0.8
MAX_MODEL_ATTEMPTS = 2
MAX_REPLY_CHARACTERS = 4_000
FORBIDDEN_REPLY_CLAIMS = (
    "保证获批",
    "保证通过",
    "一定获批",
    "签证已经批准",
    "已替你提交申请",
    "your visa is approved",
    "your application is approved",
    "you are eligible",
    "guaranteed success",
    "guarantee approval",
    "documents are sufficient for approval",
    "application has been submitted",
    "ready for approval",
    "sufficient for approval",
)
SPONSOR_RELATIONSHIPS = (
    "mother",
    "father",
    "sister",
    "brother",
    "spouse",
    "partner",
    "friend",
    "employer",
)

_OTHER_PERSON = (
    r"friend|aunt|uncle|cousin|spouse|wife|husband|partner|mother|father|mom|mum|dad|"
    r"parents?|grandparents?|grandmother|grandfather|sister|brother|siblings?|child|children|"
    r"daughter|son|colleague|coworker|client|customer|employee|employer|applicant|relative"
)
_CJK_OTHER_PERSON = (
    r"朋友|姑姑|阿姨|姨妈|舅舅|叔叔|伯伯|表姐|表妹|表哥|表弟|堂姐|堂妹|"
    r"堂哥|堂弟|表亲|堂亲|配偶|妻子|丈夫|伴侣|母亲|父亲|妈妈|爸爸|父母|祖父母|"
    r"外祖父母|奶奶|爷爷|外婆|外公|姐姐|妹妹|哥哥|弟弟|兄弟|姐妹|亲属|亲戚|"
    r"孩子|子女|女儿|女兒|儿子|兒子|同学|同事|客户|客戶|雇员|僱員|雇主|資助人|申请人|申請人"
)
_HYPOTHETICAL_SCOPE = re.compile(
    r"(?:^|[,:;(。，：；（])\s*(?:(?:but|and)\s+)?(?:if|unless|assuming|supposing|suppose|imagine|"
    r"hypothetically|in\s+(?:a\s+)?hypothetical)\b|"
    r"\b(?:what\s+if|in\s+(?:a\s+)?hypothetical)\b|"
    r"(?:^|[，：；。])\s*(?:但是|不過|不过|但|而)?(?:如果|假如|假设|假設|假定|若|倘若|譬如|比如说)",
    re.I,
)
_OTHER_PERSON_SUBJECT = re.compile(
    rf"\b(?:(?:my|our|a|an|the|another)\s+(?:{_OTHER_PERSON})"
    r"(?:\s+(?:named|called)\s+[\w'-]+|\s+[\w'-]+(?:\s+[\w'-]+)?)?|he|she|they)\s+"
    r"(?:is|are|was|were|has|have|had|holds?|carries|lives?|resides?|works?|studies|"
    r"plans?|wants?|needs?|intends?|hopes?|appl(?:y|ies|ied|ying)|travels?|will|would|can|could|"
    r"says?|said|asks?|asked|wrote|told)\b|"
    rf"(?:^|[,，;:；：。])\s*(?:我(?:的)?|我们(?:的)?|我們(?:的)?|他(?:的)?|她(?:的)?|他们(?:的)?|他們(?:的)?|她们(?:的)?|她們(?:的)?|这位|這位|那位)?"
    rf"(?:{_CJK_OTHER_PERSON})(?:[㐀-鿿]{{1,4}})?(?:是|有|曾|住|居住|工作|上班|读书|讀書|打算|计划|計劃|想|要|会|會|将|將|申请|申請|出生|持)",
    re.I,
)
_OTHER_PERSON_POSSESSIVE = re.compile(
    rf"\b(?:(?:my|our|his|her|their|a|an|the)\s+(?:{_OTHER_PERSON})|"
    rf"(?:{_OTHER_PERSON}))['’]s\b|"
    r"\b(?:his|her|their)\s+(?:name|passport|nationality|citizenship|birth(?:day|\s+date)|"
    r"date\s+of\s+birth|application|address|job|occupation|income|trip|travel|arrival|departure|"
    r"budget|accommodation|funding|sponsor|immigration\s+history)\b|"
    rf"(?:我(?:的)?|我们(?:的)?|我們(?:的)?|他(?:的)?|她(?:的)?|他们(?:的)?|他們(?:的)?|她们(?:的)?|她們(?:的)?)(?:{_CJK_OTHER_PERSON})的|"
    r"(?:他|她|他们|他們|她们|她們)的(?:姓名|名字|护照|護照|国籍|國籍|生日|出生日期|"
    r"申请|申請|地址|住址|工作|职业|職業|收入|旅行|行程|预算|預算|住宿|资助人|資助人)",
    re.I,
)
_JOINT_APPLICANT_SUBJECT = re.compile(
    rf"\b(?:(?:my|our|the)\s+(?:{_OTHER_PERSON})\s+and\s+i|"
    rf"i\s+and\s+(?:(?:my|our|the)\s+)?(?:{_OTHER_PERSON}))\b|"
    rf"我(?:们|們)?和(?:我的)?(?:{_CJK_OTHER_PERSON})|"
    rf"(?:{_CJK_OTHER_PERSON})和我(?:们|們)?",
    re.I,
)
_CURRENT_APPLICANT_SUBJECT = re.compile(
    r"\b(?:i(?:'m|\s+am|\s+have|\s+had|\s+hold|\s+carry|\s+live|\s+reside|\s+work|"
    r"\s+study|\s+plan|\s+intend|\s+want|\s+will|\s+travel|\s+visit|\s+apply|\s+was\s+born)|"
    r"my\s+(?:(?:full|passport)\s+name|name|passport|nationality|citizenship|date\s+of\s+birth|birth\s*date|birthday|"
    r"application|home|address|job|occupation|income|salary|trip|travel|visit|arrival|departure|"
    r"budget|accommodation|funding|sponsor|immigration\s+history))\b|"
    r"我(?:是|有|曾|将|將|打算|计划|計劃|想|会|會|住|居住|工作|上班|读书|讀書|"
    r"学习|學習|持|申请|申請|出生|去|前往|的(?:姓名|名字|护照|護照|国籍|國籍|生日|"
    r"出生日期|申请|申請|地址|住址|工作|职业|職業|收入|旅行|行程|预算|預算|住宿|资助人|資助人))",
    re.I,
)
_CURRENT_APPLICANT_SUPPORT = re.compile(
    r"\b(?:sponsor(?:s|ing)?|fund(?:s|ing)?|financ(?:es|ing)|pay(?:s|ing)?|cover(?:s|ing)?)"
    r"\s+(?:for\s+)?(?:me|my\s+(?:trip|travel|"
    r"flights?|accommodation|costs?|expenses?))\b|"
    r"\b(?:is|are)\s+my\s+sponsors?\b|"
    r"\b(?:host|hosts|hosting|accompany|accompanies|accompanying|travel|travels|travelling|"
    r"traveling)\s+(?:with\s+)?me\b|"
    r"(?:资助|資助|支付|承担|负担|負擔|付)(?:我|我的(?:旅费|旅費|机票|機票|住宿|费用|費用))|"
    r"(?:为我|為我|帮我|幫我|替我).{0,8}(?:支付|承担|负担|負擔)|"
    r"是我的?(?:资助人|資助人)|"
    r"(?:接待|陪同)我",
    re.I,
)

_SPONSOR_OR_FUNDING_FIELDS = SPONSOR_FIELDS | {"funding_source"}

_CONTROLLED_PROFILE_VALUES = {
    "visit_purpose": {"tourism", "family_or_friends", "business", "conference"},
    "occupation_status": {"employed", "student", "self_employed"},
}

_SEMANTIC_VALUE_PATTERNS: dict[str, dict[str, tuple[re.Pattern[str], ...]]] = {
    "occupation_status": {
        "student": (
            re.compile(
                r"\b(?:i(?:'m| am)|we are|(?:my|our) current (?:occupation|status) is)\s+"
                r"(?:currently\s+)?(?:an?\s+)?(?:university\s+|college\s+)?students?\b|"
                r"\bi\s+(?:currently\s+study|study|am\s+(?:currently\s+)?studying)\b|"
                r"\b(?:(?:my|our)\s+(?:wife|husband|spouse|partner)\s+and\s+i|i\s+and\s+"
                r"(?:my|our)\s+(?:wife|husband|spouse|partner))\s+are\s+(?:both\s+)?students?\b|"
                r"^(?:currently\s+)?(?:an?\s+)?(?:university\s+|college\s+)?student$",
                re.I,
            ),
            re.compile(
                r"(?:^|\b我|本人|我们|我們)(?:目前|现在|現在|当前|當前|正在|仍然|是|在)?"
                r".{0,8}(?:在读|在讀|读大学|讀大學|读书|讀書|上学|上學|学生|學生)|"
                r"^(?:目前|现在|現在|是)?(?:在读|在讀|大学生|大學生|学生|學生)$",
                re.I,
            ),
        ),
        "employed": (
            re.compile(
                r"\bi(?:'m| am)\s+(?:currently\s+)?employed\b|"
                r"\bi\s+(?:currently\s+)?(?:work|am working)\s+"
                r"(?:(?:full|part)[- ]time\s+)?(?:at|for|in|as)\b|"
                r"\bmy current (?:job|occupation|employment)(?: status)?\s+(?:is|:)\b|"
                r"\b(?:correct|change|update)\s+my\s+(?:job|occupation|employment|status)"
                r".{0,12}\b(?:to|as)\s+employed\b|"
                r"^(?:currently\s+)?employed$",
                re.I,
            ),
            re.compile(
                r"(?:^|\b我|本人)(?:目前|现在|現在|当前|當前|正在)?"
                r".{0,10}(?:受雇|在职|在職|工作|上班|任职|任職|就职|就職)|"
                r"^(?:目前|现在|現在)?(?:受雇)?(?:工作|在职|在職|上班)$|"
                r"^在.{1,20}(?:工作|上班)$",
                re.I,
            ),
        ),
        "self_employed": (
            re.compile(
                r"\bi(?:'m| am)\s+(?:currently\s+)?self[- ]employed\b|"
                r"\bi\s+(?:currently\s+)?(?:run|own|operate)\s+(?:my\s+own\s+|an?\s+)?business\b|"
                r"\bi(?:'m| am)\s+(?:a\s+)?freelancer\b|"
                r"^(?:currently\s+)?(?:self[- ]employed|freelancer)$",
                re.I,
            ),
            re.compile(
                r"(?:^|\b我|本人)(?:目前|现在|現在|是|在)?.{0,8}"
                r"(?:自雇|自僱|自己经营|自己經營|经营自己|經營自己|个体经营|"
                r"個體經營|自由职业|自由職業)|"
                r"^(?:自雇|自僱|个体经营|個體經營|自由职业|自由職業)$",
                re.I,
            ),
        ),
    },
    "visit_purpose": {
        "tourism": (
            re.compile(
                r"\b(?:i|we)\s+(?:plan|intend|want|would like|hope|am planning|are planning|"
                r"will|am going|are going)\b.{0,45}\b(?:holiday|tourism|sightseeing|vacation)\b|"
                r"\b(?:i am|i'm|we are)\s+(?:visiting|travelling|traveling|going)\b.{0,35}"
                r"\b(?:holiday|tourism|sightseeing|vacation)\b|"
                r"\b(?:visit|travel|travelling|traveling|go|going)\b.{0,30}\bfor\s+"
                r"(?:a\s+)?(?:holiday|tourism|sightseeing|vacation)\b|"
                r"\bi\b.{0,60}\b(?:apply|applying)\b.{0,25}\b(?:uk\s+)?tourist visa\b|"
                r"\bi\b.{0,70}\b(?:uk\s+)?(?:holiday|tourism|sightseeing|vacation)\b|"
                r"\b(?:my|our|this)\s+(?:main\s+)?(?:trip|visit|purpose)\b.{0,20}"
                r"\b(?:holiday|tourism|sightseeing|vacation)\b|"
                r"^(?:(?:uk|britain|england)\s+)?(?:holiday|tourism|sightseeing|vacation|tourist visit)$",
                re.I,
            ),
            re.compile(
                r"(?:我|我们|我們|这次|這次|本次).{0,10}(?:去|前往|到|打算|计划|計劃|想).{0,12}"
                r"(?:旅游|旅遊|观光|觀光|度假|自由行)|"
                r"(?:这次|這次|本次|目的).{0,8}(?:是|为|為)?.{0,6}"
                r"(?:旅游|旅遊|观光|觀光|度假|自由行)|"
                r"(?:申请|申請|办理|辦理|想办|想辦).{0,10}(?:英国|英國)?.{0,5}"
                r"(?:旅游|旅遊|自由行)(?:签证|簽證)?|"
                r"^(?:(?:主要|主要目的)(?:是|为|為)?|"
                r"(?:想|打算|计划|計劃))?(?:去|前往|到)?(?:英国|英國)?"
                r"(?:旅游|旅遊|观光|觀光|度假|自由行)$",
                re.I,
            ),
        ),
        "family_or_friends": (
            re.compile(
                r"\b(?:i|we)\s+(?:plan|intend|want|would like|will|am going|are going)?\s*"
                r"(?:to\s+)?(?:visit|see|stay with)\s+(?:my|our)\s+"
                r"(?:family|relative|relatives|friend|friends|mother|father|parents?|sister|brother|"
                r"spouse|partner|wife|husband)\b|"
                r"\bi\b.{0,70}\b(?:visit|see|stay with)\s+my\s+"
                r"(?:family|relative|friend|mother|father|parent|sister|brother|spouse|partner|wife|husband)\b|"
                r"\b(?:my|our|this)\s+(?:trip|visit|purpose)\b.{0,20}\b(?:family|friends?)\b|"
                r"^(?:family visit|visiting (?:family|friends?))$",
                re.I,
            ),
            re.compile(
                r"(?:我|我们|我們|这次|這次|本次)?.{0,8}"
                r"(?:探亲|探望|看望|拜访|拜訪).{0,10}"
                r"(?:亲人|亲属|親人|親屬|朋友|姐姐|妹妹|哥哥|弟弟|父母|妈妈|爸爸)?|"
                r"^(?:探亲|探望(?:亲人|亲属|親人|親屬|朋友|姐姐|妹妹|哥哥|弟弟))$",
                re.I,
            ),
        ),
        "business": (
            re.compile(
                r"\b(?:i|we)\s+(?:plan|intend|want|would like|will|am planning|are planning|"
                r"am going|are going)\b.{0,45}"
                r"\b(?:business trip|business meeting|meet (?:a |our )?(?:client|customer|supplier))\b|"
                r"\b(?:my|our|this)\s+(?:trip|visit|purpose)\b.{0,20}\b(?:business|client meeting)\b|"
                r"\bi\b.{0,70}\b(?:business trip|business meeting|meet (?:a |our )?(?:client|customer|supplier))\b|"
                r"^(?:business trip|business visit|client meeting)$",
                re.I,
            ),
            re.compile(
                r"(?:我|我们|我們|这次|這次|本次).{0,12}"
                r"(?:商务|商務|出差|拜访客户|拜訪客戶|客户会议|客戶會議)|"
                r"^(?:去|前往|到).{0,15}(?:商务|商務|出差|拜访客户|拜訪客戶|客户会议|客戶會議)|"
                r"^(?:商务访问|商務訪問|商务行程|商務行程|出差|客户会议|客戶會議)$",
                re.I,
            ),
        ),
        "conference": (
            re.compile(
                r"\b(?:i|we)\s+(?:will|plan|intend|want|would like|am going|are going)?\s*"
                r"(?:to\s+)?(?:attend|join|speak at|present at)\b.{0,35}"
                r"\b(?:conference|congress|symposium|seminar)\b|"
                r"\b(?:my|our|this)\s+(?:trip|visit|purpose)\b.{0,20}"
                r"\b(?:conference|congress|symposium|seminar)\b|"
                r"\bi\b.{0,70}\b(?:attend|join|speak at|present at)\b.{0,35}"
                r"\b(?:conference|congress|symposium|seminar)\b|"
                r"^(?:(?:academic|industry|professional)\s+)?(?:conference|congress|symposium|seminar)$",
                re.I,
            ),
            re.compile(
                r"(?:我|我们|我們|这次|這次|本次)?.{0,8}"
                r"(?:参加|參加|出席|参会|參會).{0,12}"
                r"(?:会议|會議|研讨会|研討會|峰会|峰會|论坛|論壇)|"
                r"^(?:学术|學術|行业|行業|专业|專業)?"
                r"(?:会议|會議|研讨会|研討會|峰会|峰會|论坛|論壇)$",
                re.I,
            ),
        ),
    },
}

_SEMANTIC_TARGET_TERMS = {
    ("occupation_status", "student"): r"students?|study(?:ing)?|学生|學生|在读|在讀|读书|讀書|读大学|讀大學",
    ("occupation_status", "employed"): r"employed|work(?:ing)?|job|受雇|在职|在職|工作|上班|任职|任職",
    ("occupation_status", "self_employed"): r"self[- ]employed|freelancer|自雇|自僱|个体经营|個體經營|自由职业|自由職業",
    ("visit_purpose", "tourism"): r"holiday|tourism|tourist(?: visa| visit)?|sightseeing|vacation|旅游|旅遊|观光|觀光|度假|自由行",
    ("visit_purpose", "family_or_friends"): r"family visit|visiting (?:family|friends?)|探亲|探望|看望",
    ("visit_purpose", "business"): r"business trip|business visit|client meeting|商务|商務|出差|客户会议|客戶會議",
    ("visit_purpose", "conference"): r"conference|congress|symposium|seminar|会议|會議|研讨会|研討會|峰会|峰會|论坛|論壇",
}


class UnsafeModelOutput(ValueError):
    pass


def _normalise_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalise_message_formatting(value: str) -> str:
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    return re.sub(r"`([^`]+)`", r"\1", value)


def _question_format_key(value: str) -> str:
    """Ignore prose punctuation only, retaining words and numeric punctuation."""
    value = unicodedata.normalize('NFKC', value).casefold()
    kept = []
    for index, char in enumerate(value):
        near_digit = (index > 0 and value[index - 1].isdigit()) or (
            index + 1 < len(value) and value[index + 1].isdigit())
        if not unicodedata.category(char).startswith('P') or near_digit:
            kept.append(char)
    return re.sub(r'\s+', ' ', ''.join(kept)).strip()


def _canonical_value(field: str, value: str | int | bool) -> str | int | bool:
    if field in {"date_of_birth", "planned_arrival_date", "planned_departure_date"} and isinstance(value, str):
        return canonical_date_value(value)
    if field == "sponsor_relationship" and isinstance(value, str):
        normalised = _normalise_evidence(value)
        matches = [item for item in SPONSOR_RELATIONSHIPS if item in normalised]
        if len(matches) == 1:
            return matches[0]
    return value


def _semantic_evidence_fragments(event: InboundEvent, update: FactUpdate) -> list[str]:
    """Limit semantic checks to clauses containing the exact model excerpt.

    Looking at the whole message would let an unrelated excerpt borrow a valid
    value from a neighbouring clause. If an excerpt spans a clause boundary,
    its own text remains the evidence and is split independently.
    """
    latest = unicodedata.normalize("NFKC", latest_reply_text(event.body))
    excerpt = _normalise_evidence(update.source_excerpt).strip(" .!?;,。！？；，")
    if not excerpt:
        return []
    separators = (
        r"[.!?;,。！？；，、\n]|\b(?:but|however|whereas|instead)\b|"
        r"但是|不过|不過|而是|(?<!不)但"
    )
    fragments = [item.strip() for item in re.split(separators, latest, flags=re.I) if item.strip()]
    matched = [item for item in fragments if excerpt in _normalise_evidence(item)]
    if matched:
        return matched
    source = update.source_excerpt.strip()
    return [source] if source else []


def _target_is_negated(fragment: str, terms: str) -> bool:
    return bool(
        re.search(
            rf"\b(?:no|not|never|no longer|neither|isn['’]?t|aren['’]?t|wasn['’]?t|"
            rf"don['’]?t|doesn['’]?t|do not|does not|rather than)\b"
            rf"(?:\W+\w+){{0,5}}\W*(?:{terms})\b|"
            rf"\b(?:{terms})\b.{{0,18}}\b(?:is|are|was|were)\s+not\b|"
            rf"(?:不(?:是|再|会|會|打算|准备|準備|想)?|并非|並非|没有|沒有|未)"
            rf".{{0,12}}(?:{terms})|(?:{terms}).{{0,8}}(?:不是|并非|並非)",
            fragment,
            re.I,
        )
    )


def _target_is_uncertain(fragment: str, terms: str) -> bool:
    return bool(
        re.search(
            rf"\b(?:not sure|unsure|uncertain|do not know|don['’]?t know|maybe|perhaps|"
            rf"possibly|might|could be|whether|either)\b.{{0,45}}\b(?:{terms})\b|"
            rf"\b(?:{terms})\b.{{0,22}}\b(?:or|maybe|perhaps|possibly)\b|"
            rf"(?:不确定|不確定|不清楚|不知道|可能|也许|也許|或许|或許)"
            rf".{{0,30}}(?:{terms})|(?:{terms}).{{0,15}}(?:还是|還是|或者|或).{{0,15}}",
            fragment,
            re.I,
        )
    )


def _enum_value_is_grounded(event: InboundEvent, update: FactUpdate) -> bool:
    if not isinstance(update.value, str):
        return False
    allowed = _CONTROLLED_PROFILE_VALUES[update.field]
    if update.value not in allowed:
        return False
    patterns = _SEMANTIC_VALUE_PATTERNS[update.field][update.value]
    terms = _SEMANTIC_TARGET_TERMS[(update.field, update.value)]
    fragments = _semantic_evidence_fragments(event, update)
    if not fragments:
        return False

    # A repeated tiny excerpt cannot identify which occurrence the model used.
    # Require the model to quote a longer, unique correction instead.
    latest = _normalise_evidence(latest_reply_text(event.body))
    excerpt = _normalise_evidence(update.source_excerpt).strip(" .!?;,。！？；，")
    if len(excerpt) < 24 and latest.count(excerpt) > 1:
        return False

    for fragment in fragments:
        if _target_is_negated(fragment, terms) or _target_is_uncertain(fragment, terms):
            continue
        # An academic/industry conference is the specific canonical purpose,
        # even though it can also be described as business travel.
        if (
            update.field == "visit_purpose"
            and update.value == "business"
            and re.search(_SEMANTIC_TARGET_TERMS[("visit_purpose", "conference")], fragment, re.I)
        ):
            continue
        # "self-employed" contains the word employed; it must not silently
        # collapse to the different canonical status.
        if (
            update.field == "occupation_status"
            and update.value == "employed"
            and re.search(
                _SEMANTIC_TARGET_TERMS[("occupation_status", "self_employed")],
                fragment,
                re.I,
            )
        ):
            continue
        if any(pattern.search(fragment) for pattern in patterns):
            return True
    return False


_HISTORY_TERMS = (
    r"visa (?:application )?refus(?:al|ed)|(?:visa )?application (?:was )?refused|"
    r"refus(?:al|ed) (?:a )?visa|immigration (?:breach|violation|history|issue)|"
    r"overstay(?:ed|ing)?|remov(?:al|ed)|deport(?:ation|ed)|criminal (?:conviction|record|history|matter)|"
    r"convict(?:ion|ed)|serious civil (?:judgment|matter)|拒签|逾期停留|滞留|遣返|遣返|"
    r"驱逐出境|驅逐出境|移民违规|移民違規|刑事定罪|犯罪记录|犯罪記錄|"
    r"重大民事"
)


def _history_value_is_grounded(event: InboundEvent, update: FactUpdate) -> bool:
    if not isinstance(update.value, bool):
        return False
    latest = _normalise_evidence(latest_reply_text(event.body)).strip(" .!?;,。！？；，")
    if event.requested_fields == [update.field]:
        if update.value is True and re.fullmatch(r"yes|yes i have|有|是|是的", latest, re.I):
            return True
        if update.value is False and re.fullmatch(
            r"no|no i have not|no i haven['’]?t|没有|沒有|无|無",
            latest,
            re.I,
        ):
            return True

    fragments = _semantic_evidence_fragments(event, update)
    for fragment in fragments:
        if update.value is True:
            if _target_is_negated(fragment, _HISTORY_TERMS) or _target_is_uncertain(
                fragment, _HISTORY_TERMS
            ):
                continue
            if re.search(
                rf"\b(?:i\s+(?:have|had|have had|was|was previously|have been)|"
                rf"i\s+previously\s+(?:had|was)|my\s+(?:previous\s+)?"
                rf"(?:visa|application|immigration|criminal)).{{0,45}}(?:{_HISTORY_TERMS})\b|"
                rf"(?:我|本人)(?:以前|之前|曾经|曾經|在\s*\d{{4}}\s*年)?"
                rf".{{0,35}}(?:{_HISTORY_TERMS})",
                fragment,
                re.I,
            ):
                return True
            continue

        # False represents a denial of the aggregate background question, not
        # merely a denial of one category while all others remain unknown.
        categories = sum(
            bool(re.search(pattern, fragment, re.I))
            for pattern in (
                r"visa (?:application )?refus(?:al|ed)|(?:visa )?application (?:was )?refused|"
                r"refus(?:al|ed) (?:a )?visa|拒签",
                r"immigration|overstay|remov|deport|逾期|滞留|遣返|遣返|移民",
                r"criminal|convict|刑事|犯罪",
                r"civil|民事",
            )
        )
        broad_denial = re.search(
            r"\b(?:i\s+(?:have|have had)\s+(?:no|never had)\s+(?:any\s+)?(?:serious|relevant)?"
            r"\s*(?:immigration|criminal|visa|background)\s+(?:history|issues?|matters?)|"
            r"i\s+have\s+never\s+(?:had|been).{0,80}(?:refused|deported|removed|convicted|overstayed))\b|"
            r"(?:我|本人)(?:从未|從未|没有|沒有|无|無).{0,20}"
            r"(?:任何|相关|相關)?(?:严重|嚴重)?(?:签证|簽證|移民|刑事|背景)"
            r"(?:记录|記錄|问题|問題|情况|情況)",
            fragment,
            re.I,
        )
        if (broad_denial or categories >= 2) and _target_is_negated(fragment, _HISTORY_TERMS):
            return True
    return False


_STANDARD_VISITOR = r"standard visitor(?: visa| route)?|标准访客(?:签证|路线)?|標準訪客(?:簽證|路線)?"


def _route_value_is_grounded(event: InboundEvent, update: FactUpdate) -> bool:
    if not isinstance(update.value, bool):
        return False
    latest = _normalise_evidence(latest_reply_text(event.body)).strip(" .!?;,。！？；，")
    if event.requested_fields == [update.field]:
        if update.value is True and re.fullmatch(
            rf"yes|yes i am|confirmed|(?:the )?{_STANDARD_VISITOR}|是|是的|确认|確認",
            latest,
            re.I,
        ):
            return True
        if update.value is False and re.fullmatch(
            r"no|no i am not|not yet|不是|没有|沒有|还没有|還沒有",
            latest,
            re.I,
        ):
            return True

    fragments = _semantic_evidence_fragments(event, update)
    for fragment in fragments:
        if update.value is True:
            if _target_is_negated(fragment, _STANDARD_VISITOR) or _target_is_uncertain(
                fragment, _STANDARD_VISITOR
            ):
                continue
            if re.search(
                rf"\b(?:i\s+(?:have\s+)?(?:confirmed|chosen|selected)|i\s+(?:am|will be)\s+applying|"
                rf"my\s+(?:application|chosen route|visa route)\s+is).{{0,35}}(?:{_STANDARD_VISITOR})\b|"
                rf"(?:我|这次|這次|本次)?.{{0,8}}(?:已确认|已確認|确认|確認|选择|選擇|"
                rf"按|申请|申請).{{0,25}}(?:{_STANDARD_VISITOR})|"
                rf"(?:{_STANDARD_VISITOR}).{{0,20}}(?:是我|是这次|是這次).{{0,12}}(?:申请|申請|路线|路線)",
                fragment,
                re.I,
            ):
                return True
            continue

        if re.search(
            rf"\b(?:i\s+(?:have not|haven['’]?t|did not|didn['’]?t)\s+(?:chosen|selected|confirmed)|"
            rf"i\s+(?:am|will)\s+not\s+applying|my\s+(?:application|route)\s+is\s+not)"
            rf".{{0,35}}(?:{_STANDARD_VISITOR})\b|"
            rf"(?:我|这次|這次|本次)?.{{0,8}}(?:不按|不申请|不申請|未选择|未選擇|"
            rf"还没选|還沒選|没有确认|沒有確認).{{0,22}}(?:{_STANDARD_VISITOR})|"
            rf"(?:{_STANDARD_VISITOR}).{{0,12}}(?:不是|并非|並非).{{0,15}}(?:我|这次|這次|本次)?",
            fragment,
            re.I,
        ):
            return True
    return False


def _is_offline_demo_fact_marker(event: InboundEvent, update: FactUpdate) -> bool:
    """Keep the repository's non-routable `.test` fixture contract isolated.

    Production messages must pass natural-language semantic validation. The
    bundled end-to-end fixture instead uses an HTML comment read by
    ``OfflineFixtureLLM``; it is accepted only with all of the fixture's
    non-routable identity markers and an exact field/value assignment.
    """
    if (
        "<!-- DEMO_FACTS\n" not in event.body
        or "@example.test" not in event.sender.casefold()
        or not event.external_thread_id.startswith("demo-thread-")
        or not (event.rfc_message_id or "").endswith("@example.test>")
    ):
        return False
    if update.field in _CONTROLLED_PROFILE_VALUES and (
        not isinstance(update.value, str)
        or update.value not in _CONTROLLED_PROFILE_VALUES[update.field]
    ):
        return False
    if update.field in {"has_serious_history", "route_confirmed_standard_visitor"} and not isinstance(
        update.value, bool
    ):
        return False
    value = str(update.value).casefold() if isinstance(update.value, bool) else str(update.value)
    if isinstance(update.value, bool):
        value = "true" if update.value else "false"
    return update.source_excerpt.strip() == f"{update.field}={value}"


def _controlled_profile_value_is_grounded(event: InboundEvent, update: FactUpdate) -> bool:
    if _is_offline_demo_fact_marker(event, update):
        return True
    if update.field in _CONTROLLED_PROFILE_VALUES:
        return _enum_value_is_grounded(event, update)
    if update.field == "has_serious_history":
        return _history_value_is_grounded(event, update)
    if update.field == "route_confirmed_standard_visitor":
        return _route_value_is_grounded(event, update)
    return True


def _evidence_sentences(event: InboundEvent, update: FactUpdate) -> list[str]:
    """Return current-message sentences that actually contain the model's excerpt."""
    latest = unicodedata.normalize("NFKC", latest_reply_text(event.body))
    excerpt = _normalise_evidence(update.source_excerpt).strip(" .!?;。！？；")
    if not excerpt:
        return []
    return [
        sentence.strip()
        for sentence in re.split(r"[.!?;。！？；\n]|\.(?:\s|$)", latest)
        if sentence.strip()
        and excerpt in _normalise_evidence(sentence).strip(" .!?;。！？；")
    ]


def _evidence_clauses(sentence: str, update: FactUpdate) -> list[str]:
    """Narrow a sentence at subject-changing boundaries without losing conditions."""
    excerpt = _normalise_evidence(update.source_excerpt)
    clauses = re.split(
        r"[,，]|\b(?:but|whereas|while)\b|\band(?=\s+(?:i|he|she|they)\b)|"
        r"但是|不过|不過|但|而(?=我|他|她)",
        sentence,
        flags=re.I,
    )
    matched = [clause.strip() for clause in clauses if excerpt in _normalise_evidence(clause)]
    return matched or [sentence]


def _hypothetical_controls_evidence(sentence: str, update: FactUpdate) -> bool:
    """Treat a marker as governing evidence only when it appears before that evidence."""
    text = _normalise_evidence(sentence)
    excerpt = _normalise_evidence(update.source_excerpt)
    positions = [match.start() for match in re.finditer(re.escape(excerpt), text)]
    if not positions:
        return False
    return any(
        marker.start() <= position
        for marker in _HYPOTHETICAL_SCOPE.finditer(text)
        for position in positions
    )


def _other_person_controls_clause(clause: str, update: FactUpdate) -> bool:
    """Identify facts whose grammatical owner is explicitly someone else."""
    if _JOINT_APPLICANT_SUBJECT.search(clause):
        return False
    if not (_OTHER_PERSON_SUBJECT.search(clause) or _OTHER_PERSON_POSSESSIVE.search(clause)):
        return False
    if update.field in SPONSOR_FIELDS:
        # sponsor_role_is_grounded performs the stricter cross-clause role check
        # after ordinary evidence validation.
        return False
    if update.field == "funding_source" and (
        _CURRENT_APPLICANT_SUPPORT.search(clause)
        or funding_source_value_is_grounded(clause, str(update.value))
    ):
        return False
    return not (
        update.field in {"uk_accommodation", "visit_purpose"}
        and re.search(
            r"\b(?:host|hosts|hosting|accompany|accompanies|accompanying)\s+me\b|(?:接待|陪同)我",
            clause,
            re.I,
        )
    )


def _has_independent_applicant_scope(text: str) -> bool:
    """Does any non-hypothetical clause clearly include the current applicant?"""
    for sentence in re.split(r"[.!?;。！？；\n]|\.(?:\s|$)", text):
        if not sentence.strip() or _HYPOTHETICAL_SCOPE.search(_normalise_evidence(sentence)):
            continue
        for clause in re.split(
            r"[,，]|\b(?:but|whereas|while)\b|\band(?=\s+(?:i|he|she|they)\b)|"
            r"但是|不过|不過|但|而(?=我|他|她)",
            sentence,
            flags=re.I,
        ):
            if (
                _CURRENT_APPLICANT_SUBJECT.search(clause)
                or _CURRENT_APPLICANT_SUPPORT.search(clause)
                or _JOINT_APPLICANT_SUBJECT.search(clause)
            ):
                return True
    return False


def _message_is_wholly_nonapplicant(event: InboundEvent) -> bool:
    """Recognise a message framed entirely as another person's or imagined case."""
    latest = unicodedata.normalize("NFKC", latest_reply_text(event.body))
    if _has_independent_applicant_scope(latest):
        return False
    return bool(
        _HYPOTHETICAL_SCOPE.search(_normalise_evidence(latest))
        or _OTHER_PERSON_SUBJECT.search(latest)
        or _OTHER_PERSON_POSSESSIVE.search(latest)
    )


def _profile_update_has_nonapplicant_owner(event: InboundEvent, update: FactUpdate) -> bool:
    """Fail closed on ownership before a model proposal can alter the case profile.

    This is deliberately narrower than general coreference resolution. It catches
    explicit hypothetical and other-person frames, while sponsor facts still pass
    through the dedicated sponsor-role evidence gate below.
    """
    sentences = _evidence_sentences(event, update)
    if not sentences:
        return False
    if any(_hypothetical_controls_evidence(sentence, update) for sentence in sentences):
        return True
    clauses = [clause for sentence in sentences for clause in _evidence_clauses(sentence, update)]
    if any(_other_person_controls_clause(clause, update) for clause in clauses):
        return True
    if update.field in SPONSOR_FIELDS:
        # A sponsor is necessarily another person.  Conditions and reported
        # speech were rejected above; let the dedicated role/evidence gate
        # decide whether this person is actually this applicant's sponsor.
        return False
    return _message_is_wholly_nonapplicant(event) and not any(
        _CURRENT_APPLICANT_SUBJECT.search(clause)
        or _JOINT_APPLICANT_SUBJECT.search(clause)
        or (
            update.field in _SPONSOR_OR_FUNDING_FIELDS
            and (
                _CURRENT_APPLICANT_SUPPORT.search(clause)
                or (
                    update.field == "funding_source"
                    and funding_source_value_is_grounded(clause, str(update.value))
                )
            )
        )
        for clause in clauses
    )


def _english_birthday_is_applicant_fact(event: InboundEvent, update: FactUpdate) -> bool:
    """Conservative ownership check for named-month DOB evidence, not general NLP.

    Normalizing an English value must not newly authorize a third person's date,
    a quoted example, or an uncertain alternative. Bare dates are answers only
    when the existing workflow actually requested the applicant's date of birth.
    """
    latest = unicodedata.normalize("NFKC", latest_reply_text(event.body))
    excerpt = _normalise_evidence(update.source_excerpt).strip(" .!?;。")
    if excerpt not in _normalise_evidence(latest):
        return False
    if ("date_of_birth" in event.requested_fields
            and canonical_date_value(latest.strip()) == update.value):
        return True
    for clause in re.split(r"[.!?;。\n]", latest):
        if excerpt not in _normalise_evidence(clause):
            continue
        matches = list(ENGLISH_COMPLETE_DATE.finditer(clause))
        if len(matches) != 1 or canonical_date_value(matches[0].group()) != update.value:
            continue
        prefix, suffix = clause[:matches[0].start()].strip(), clause[matches[0].end():]
        if not re.fullmatch(
            r"(?:(?:a\s+(?:quick|small)\s+)?correction\s*:\s*)?"
            r"(?:my\s+(?:date\s+of\s+birth|birth\s*date|birthday)\s*(?:is|was|should\s+be|:|=)?|"
            r"i\s+was\s+born\s*(?:on)?|(?:date\s+of\s+birth|dob)\s*(?::|=|is)|"
            r"(?:please\s+)?(?:correct|change|update)\s+my\s+"
            r"(?:date\s+of\s+birth|birth\s*date|birthday)\s+to)\s*",
            prefix, re.I,
        ):
            continue
        if re.search(r"\b(?:or|not|maybe|incorrect|wrong|instead|uncertain)\b", suffix, re.I):
            continue
        return True
    return False


def validate_case_patch(event: InboundEvent, proposed: CasePatch) -> CasePatch:
    """Return only grounded, type-valid, non-conflicting candidate facts."""

    body = _normalise_evidence(event.body)
    accepted: dict[str, FactUpdate] = {}
    rejected_fields: set[str] = set()
    wholly_nonapplicant = _message_is_wholly_nonapplicant(event)
    ambiguities = [] if wholly_nonapplicant else list(proposed.ambiguities)
    # A model cannot acknowledge unresolved ambiguity while allowing automatic progression.
    requires_review = False if wholly_nonapplicant else (
        proposed.requires_human_review or bool(proposed.ambiguities)
    )
    nonapplicant_rejections = 0
    controlled_value_rejections = 0

    for update in proposed.updates:
        if _profile_update_has_nonapplicant_owner(event, update):
            # A fact about a friend, relative or imagined applicant is not an
            # ambiguity in this case and must never mutate this applicant's file.
            nonapplicant_rejections += 1
            continue
        update = update.model_copy(update={"value": _canonical_value(update.field, update.value)})
        if not _controlled_profile_value_is_grounded(event, update):
            # Literal substring membership is not semantic evidence. A model
            # cannot turn a negated, uncertain, unrelated or opposite enum into
            # a persisted fact (or use that rejected risk value to force review).
            controlled_value_rejections += 1
            continue
        if (update.field == "current_address" and isinstance(update.value, str)
                and address_excerpt_is_other_location(update.source_excerpt, update.value)):
            # Other people's or workplace details are not a home-address update.
            # An ordinary unrelated location mention leaves intake open, not held.
            continue
        if update.field == "sponsor_name" and re.fullmatch(
            r"(?:(?:my|the|our)\s+)?(?:mother|father|sister|brother|spouse|partner|friend|"
            r"employer|parent|sponsor|wife|husband)|(?:我的?|我们的?)?(?:母亲|父亲|妈妈|爸爸|"
            r"姐姐|妹妹|哥哥|弟弟|朋友|配偶|丈夫|妻子|雇主|资助人)",
            str(update.value).strip(),
            re.I,
        ):
            # A relationship is not a personal name; retain the missing-name question.
            continue
        is_date = update.field in {
            "planned_arrival_date",
            "planned_departure_date",
            "date_of_birth",
        }
        if is_date and not has_calendar_day(update.source_excerpt):
            # "November" is useful conversational context, not a precise date and
            # not a reason to lock a routine enquiry into human review.
            continue
        allow_shared_year = update.field != "date_of_birth"
        if is_date and not date_is_grounded(
            str(update.value), update.source_excerpt, allow_shared_year=allow_shared_year
        ):
            candidates = [
                sentence.strip()
                for sentence in re.split(r"[。！？!?；;]", event.body)
                if _normalise_evidence(update.source_excerpt) in _normalise_evidence(sentence)
                and date_is_grounded(str(update.value), sentence, allow_shared_year=allow_shared_year)
            ]
            if len(candidates) == 1:
                update = update.model_copy(update={"source_excerpt": candidates[0]})
        # A literal quote is necessary but not sufficient: employment/passport facts
        # must not silently become residential/application-location facts.
        cues = {
            "application_country": r"appl(?:y|ying|ication)|申请|递交|提交",
            "current_address": r"address|residen|live|living|住址|居住|住在|家在|地址",
            "nationality": r"passport|citizen|national|国籍|护照|公民|国人|\b(?:Chinese|British|American|Canadian|French|German|Indian|Australian)\b",
            "nationality_country": r"passport|citizen|national|国籍|护照|公民|国人|\b(?:Chinese|British|American|Canadian|French|German|Indian|Australian)\b",
        }
        if (
            update.field in cues
            and update.confidence >= MIN_ACCEPTED_CONFIDENCE
            and event.requested_fields != [update.field]
            and not re.search(cues[update.field], update.source_excerpt, re.I)
        ):
            # Leave the ordinary question open, rather than escalating routine missing data.
            continue
        reason: str | None = None
        field_info = CaseProfile.model_fields.get(update.field)
        if field_info is None:
            reason = f"Unsupported field proposed: {update.field}."
        elif (
            not update.source_excerpt.strip()
            or _normalise_evidence(update.source_excerpt) not in body
        ):
            reason = f"Evidence excerpt for {update.field} was not found in the inbound message."
        elif update.confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = f"Low-confidence value proposed for {update.field}."
        elif (update.field == "current_address" and isinstance(update.value, str)
              and not address_value_is_grounded(update.value, update.source_excerpt)):
            reason = "Home address details were not grounded in the customer's excerpt."
        elif is_date and not date_is_grounded(
            str(update.value), update.source_excerpt, allow_shared_year=allow_shared_year
        ):
            reason = f"Date value for {update.field} was not grounded in its excerpt."
        elif (update.field == "date_of_birth"
              and ENGLISH_COMPLETE_DATE.search(unicodedata.normalize("NFKC", update.source_excerpt))
              and not _english_birthday_is_applicant_fact(event, update)):
            reason = "English birth date was not established as a current applicant fact."
        else:
            try:
                TypeAdapter(field_info.annotation).validate_python(update.value)
            except ValidationError:
                reason = f"Invalid value proposed for {update.field}."

        if reason is not None:
            ambiguities.append(reason)
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue

        if update.field == "funding_source" and not funding_source_value_is_grounded(
            "\n".join(_evidence_sentences(event, update)),
            str(update.value),
            directly_requested=update.field in event.requested_fields,
        ):
            # A provider enum is not payer evidence. In particular, "she is not
            # sponsoring me" cannot be converted into self-funding because a
            # different person or organisation may still pay.
            continue

        if update.field in SPONSOR_FIELDS and not sponsor_role_is_grounded(
            update.field, update.value, update.source_excerpt, latest_reply_text(event.body),
            known_profile=event.known_profile, requested_fields=event.requested_fields,
        ):
            # A host/relative is not automatically this applicant's sponsor. Keep
            # ordinary unknown roles missing, without freezing unrelated intake.
            continue

        prior = accepted.get(update.field)
        if prior is not None and prior.value != update.value:
            ambiguities.append(f"Conflicting values proposed for {update.field}.")
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue
        if update.field not in rejected_fields:
            accepted[update.field] = update

    if (
        (nonapplicant_rejections or controlled_value_rejections)
        and proposed.requires_human_review
        and not proposed.ambiguities
        and not wholly_nonapplicant
    ):
        # A bare model review flag can be caused by the rejected third-party
        # history/route. Rebuild it below from facts that passed ownership.
        requires_review = False

    route_update = accepted.get("route_confirmed_standard_visitor")
    history_update = accepted.get("has_serious_history")
    if (route_update is not None and route_update.value is False) or (
        history_update is not None and history_update.value is True
    ):
        requires_review = True
    nationality_update = accepted.get("nationality") or accepted.get("nationality_country")
    if nationality_update is not None and re.search(
        r"\b(?:British\s+citizen|right\s+of\s+abode)\b|英国公民|英國公民|英国居留权|英國居留權",
        nationality_update.source_excerpt,
        re.I,
    ):
        requires_review = True

    return CasePatch(
        updates=list(accepted.values()),
        ambiguities=list(dict.fromkeys(ambiguities)),
        requires_human_review=requires_review,
        question_deferrals=[item for item in proposed.question_deferrals
            if item.confidence >= MIN_ACCEPTED_CONFIDENCE
            and item.source_excerpt.strip()
            and _normalise_evidence(item.source_excerpt) in _normalise_evidence(latest_reply_text(event.body))],
        customer_questions=validated_customer_questions(event.body, proposed.customer_questions),
        preparation_intent=validated_preparation_intent(
            event.body,
            proposed.preparation_intent,
            allow_contextual_resume=bool(event.known_profile.get("_preparation_paused")),
        ),
    )


def deterministic_fallback_message(case: Case, plan: str) -> str:
    if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
        message = (
            "这部分我还不能可靠判断，需要人工核实后才能继续，不能直接给你确定答复。你发来的信息和文件都已保留，暂时不用重新发送。"
            if case.customer_language == "zh"
            else "Thank you for explaining. This needs a human adviser to check before we continue. Your information is retained; you don't need to resend it. I haven't prepared or submitted an application."
        )
        history_reported = (case.profile.has_serious_history is True and "has_serious_history" in
                            (set(case.latest_changes) | set(case.latest_received_facts)))
        if history_reported:
            message = (
                "你补充的拒签或其他重要经历已记下。这类情况不能只看“有没有”就下结论，"
                "需要人工顾问把原决定和时间线与这次申请的说明一起核对。\n\n"
                "你现在可以先做一件事：如果手上有拒签决定书、通知或其他相关记录，请把完整文件发来；"
                "同时按“国家或地区、签证类别、发生日期、原因、后续结果”整理一段简短时间线。"
                "没记清的地方可以标注待核实，不要猜或省略。\n\n"
                "目前材料包先不定稿；已经收到的资料不用重发。"
                if case.customer_language == "zh" else
                "I've recorded the refusal or other significant history you've disclosed. This cannot be assessed "
                "from a yes-or-no flag alone: a human adviser needs to compare the original decision and timeline "
                "with the explanation for this application.\n\n"
                "One useful step now is to send the complete refusal decision, notice or other relevant record if "
                "you have it, together with a short timeline covering the country or territory, visa type, date, "
                "reason and what happened afterwards. Mark anything you cannot remember as needing verification; "
                "do not guess or leave it out.\n\n"
                "We will not finalise the pack until that review is complete. You do not need to resend anything "
                "already received."
            )
        acknowledgement = change_acknowledgement(case.model_copy(update={"latest_changes": {
            key: value for key, value in case.latest_changes.items() if key != "has_serious_history"
        }}))
        receipt = preparation_control_receipt(case)
        return "\n\n".join([*([acknowledgement] if acknowledgement else []),
                            *([receipt] if receipt else []), message, *case.customer_answers])
    if plan == "blocked":
        return blocked_customer_message(case)
    if plan == "ready":
        if case.next_step_advice is not None:
            receipt_case = case.model_copy(update={"next_step_advice": None, "customer_answers": []})
            return "\n\n".join([*case.customer_answers, deterministic_fallback_message(receipt_case, plan)])
        if case.delivery_revision > 1:
            return (
                f"已按你重新确认的信息整理成第 {case.delivery_revision} 版材料包，供顾问复核。"
                "请以这一版为准，并检查说明和信息摘要；旧版可能仍保留在之前的邮件中，不能自动撤回。"
                "这里完成的是材料修订，还没有递交或修改政府系统里的签证申请，也不代表获批。"
                if case.customer_language == "zh" else
                f"Revision {case.delivery_revision} of your preparation pack reflects your newly confirmed information "
                "and is ready for adviser review. Please use this version; any copy already sent remains in your "
                "previous email and cannot be recalled automatically. No government application has been "
                "submitted or amended, and this is not an approval prediction."
            )
        if case.customer_language == "zh":
            return "你的申请资料已整理好，供顾问复核。建议先看里面的说明和信息摘要，再逐项核对文件；如果有遗漏或需要修改的地方，直接回复告诉我。这里完成的是材料整理，还没有递交签证申请，也不代表签证获批。"
        return (
            "Your review pack is ready for human review. This is not an approval prediction or a "
            "submitted visa application."
        )
    return confirmation_message(case, profile_only=plan == "awaiting_profile_confirmation")


class GuardedLLM:
    """Mandatory safety boundary around an interchangeable model adapter."""

    def __init__(
        self,
        delegate: LLMClient,
        *,
        max_attempts: int = MAX_MODEL_ATTEMPTS,
        allow_model_rendering: bool = True,
        on_failure: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.delegate = delegate
        self.max_attempts = max_attempts
        self.allow_model_rendering = allow_model_rendering
        self.on_failure = on_failure
        self.version = f"guarded:{getattr(delegate, 'version', 'unknown')}"
        self.last_extraction_fallback = False
        self.last_extraction_error: str | None = None
        self.last_render_fallback = False
        self.last_render_error: str | None = None

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                patch = validate_case_patch(event, self.delegate.extract_case_patch(event))
                if any(
                    reason.startswith(("Evidence excerpt for ", "Date value for "))
                    for reason in patch.ambiguities
                ):
                    raise UnsafeModelOutput("; ".join(patch.ambiguities))
                self.last_extraction_fallback = False
                self.last_extraction_error = None
                return patch
            except Exception as error:  # Provider/SDK failures must not mutate or lose the case.
                last_error = error
        assert last_error is not None
        self._report("extract_case_patch", last_error)
        self.last_extraction_fallback = True
        self.last_extraction_error = str(last_error)
        return CasePatch(
            updates=[],
            ambiguities=["Automated extraction was unavailable; manual review is required."],
            requires_human_review=True,
        )

    def render_message(self, case: Case, plan: str) -> str:
        if not self.allow_model_rendering:
            # The default live Gmail path sends the reviewed deterministic
            # composer. Do not pay for a model draft that the channel boundary
            # will intentionally replace before delivery.
            self.last_render_fallback = False
            self.last_render_error = None
            return deterministic_fallback_message(case, plan)
        if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
            self.last_render_fallback = True
            self.last_render_error = "case_requires_human_review"
            return deterministic_fallback_message(case, plan)
        if case.preparation_paused:
            # The drafting model cannot restart intake or confirmation while paused.
            self.last_render_fallback = False
            self.last_render_error = None
            return blocked_customer_message(case)
        if case.next_step_advice is not None:
            # Case-aware next steps and accompanying FAQs must survive wording.
            self.last_render_fallback = False
            self.last_render_error = None
            return deterministic_fallback_message(case, plan)
        if plan == "blocked" and (acknowledgement := waiting_acknowledgement(case)):
            self.last_render_fallback = False
            self.last_render_error = None
            return acknowledgement
        if plan == "blocked" and case.question_plan == [] and case.pending_question_fields:
            # An unanswered question is not permission for the wording model to ask it again.
            self.last_render_fallback = False
            self.last_render_error = None
            return deterministic_fallback_message(case, plan)
        if plan in {"awaiting_confirmation", "awaiting_profile_confirmation"}:
            self.last_render_fallback = False
            self.last_render_error = None
            return confirmation_message(case, profile_only=plan == "awaiting_profile_confirmation")
        try:
            message = _normalise_message_formatting(
                self.delegate.render_message(case, plan).strip()
            )
            if not message:
                raise ValueError("Model returned an empty message")
            if len(message) > MAX_REPLY_CHARACTERS:
                raise UnsafeModelOutput("Model message exceeded the configured length limit")
            normalised = message.casefold()
            if any(claim in normalised for claim in FORBIDDEN_REPLY_CLAIMS):
                raise UnsafeModelOutput("Model message contained a prohibited outcome claim")
            if any(
                placeholder in normalised
                for placeholder in ("[name]", "[applicant name]", "[your name]")
            ):
                raise UnsafeModelOutput("Model message contained an unresolved placeholder")
            if plan == "blocked":
                issues, questions, documents = reply_items(case)
                exact_items = case.customer_answers + issues + documents
                if acknowledgement := change_acknowledgement(case):
                    exact_items.append(acknowledgement)
                required_items = exact_items + questions
                if (any(item.casefold() not in normalised for item in exact_items)
                        or any(_question_format_key(item) not in _question_format_key(message)
                               for item in questions)):
                    raise UnsafeModelOutput(
                        "Model message omitted or changed a grounded next action"
                    )
                if re.search(
                    r"no documents (?:are )?(?:needed|required)|"
                    r"hold off on any further steps|won['’]t move forward with anything|"
                    r"(?:不需要|不用)(?:任何)?(?:文件|材料)", normalised,
                ):
                    raise UnsafeModelOutput("Model added an unsupported preparation waiver or global pause")
                length_budget = max(
                    420 if case.customer_language == "zh" else 1100,
                    len("\n".join(required_items)) + 180,
                )
                if len(message) > length_budget:
                    raise UnsafeModelOutput("Model buried the next action in excessive prose")
                if any(
                    phrase in normalised
                    for phrase in (
                        "没有现成的标准答案",
                        "不能给你一个确切的步骤清单",
                        "no standard answer",
                    )
                ):
                    raise UnsafeModelOutput(
                        "Model denied a preparation step already supplied in its brief"
                    )
                if re.search(
                    r"(?:时间|日期).{0,12}(?:没问题|没有问题|来得及)|\benough time\b|\bdates (?:are|look) (?:fine|acceptable)\b",
                    normalised,
                ):
                    raise UnsafeModelOutput("Model added an unsupported timing assurance")
                if case.customer_language == "zh" and not re.search(r"[\u4e00-\u9fff]", message):
                    raise UnsafeModelOutput("Model ignored the customer's language")
            if plan == "awaiting_confirmation" and "i confirm the final summary" not in normalised:
                raise UnsafeModelOutput("Model message omitted the exact confirmation statement")
            if plan == "awaiting_confirmation" and any(
                claim in normalised
                for claim in ("pack is ready", "pack has been prepared", "pack is released")
            ):
                raise UnsafeModelOutput("Model message claimed release before confirmation")
            if plan == "ready" and not (
                ("human" in normalised and "review" in normalised)
                if case.customer_language != "zh"
                else "顾问复核" in message
            ):
                raise UnsafeModelOutput("Model message omitted the human-review boundary")
            self.last_render_fallback = False
            self.last_render_error = None
            return message
        except Exception as error:
            self._report("render_message", error)
            self.last_render_fallback = True
            self.last_render_error = f"{type(error).__name__}: {str(error)[:160]}"
            return deterministic_fallback_message(case, plan)

    def _report(self, operation: str, error: Exception) -> None:
        if self.on_failure is not None:
            self.on_failure(operation, error)


def ensure_guarded(llm: LLMClient) -> GuardedLLM:
    if isinstance(llm, GuardedLLM):
        return llm
    return GuardedLLM(llm)
