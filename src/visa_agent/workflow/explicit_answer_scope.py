"""Conservative current-turn recognition for already answered enum questions.

This module is a pacing guard, not an extractor.  It prevents the renderer from
immediately asking for an enum the customer has just stated when a model omitted
that update.  It deliberately does not create evidence or mutate the profile;
the normal grounded extraction path remains the only way to persist a fact.
"""

from __future__ import annotations

import re
import unicodedata

_HYPOTHETICAL_OR_REPORTED = re.compile(
    r"\b(?:if|unless|suppose|assuming|hypothetically|what if)\b|"
    r"\b(?:said|says|asked|asks|wrote|told me)\b|"
    r"如果|假如|假设|假設|若|譬如|比如|听说|聽說|说他|說他|说她|說她",
    re.I,
)
_UNCERTAIN_OR_CHOICE = re.compile(
    r"\b(?:maybe|perhaps|possibly|not sure|undecided|either|or|i think|considering)\b|"
    r"也许|也許|可能|不确定|不確定|还没定|還沒定|或者|還是|考虑中|可能会|可能會",
    re.I,
)
_OTHER_APPLICANT = re.compile(
    r"\b(?:my|our|his|her|their|a|the)\s+"
    r"(?:friend|aunt|uncle|cousin|spouse|wife|husband|partner|mother|father|"
    r"parent|sister|brother|colleague|client|customer|applicant)\b|"
    r"\b(?:he|she|they)\s+(?:is|are|works?|studies|plans?|wants?|will|would)\b|"
    r"(?:我(?:的)?|我們的|我们的|他的|她的|他們的|他们的)"
    r"(?:朋友|姑姑|阿姨|叔叔|舅舅|配偶|妻子|丈夫|伴侣|母亲|父亲|"
    r"妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|同事|客户|客戶|申请人|申請人)",
    re.I,
)
_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "visit_purpose": (
        re.compile(r"\b(?:tourism|sightseeing|holiday|leisure)\b|旅游|旅遊|观光|觀光", re.I),
        re.compile(r"\b(?:conference|business conference|seminar)\b|参加会议|參加會議|参会|參會", re.I),
        re.compile(r"\bbusiness (?:trip|visit)\b|商务访问|商務訪問|出差", re.I),
        re.compile(
            r"\bvisit(?:ing)?\s+(?:my\s+)?(?:family|friends?|relatives?)\b|"
            r"探亲访友|探親訪友|看望(?:家人|亲友|親友|朋友)",
            re.I,
        ),
    ),
    "occupation_status": (
        re.compile(r"\b(?:i(?:['’]?m| am)\s+)?(?:a\s+)?student\b|我?(?:目前)?(?:在)?读书|讀書|在读|在讀|学生|學生", re.I),
        re.compile(r"\b(?:i(?:['’]?m| am)\s+)?(?:employed|in employment|working full[- ]?time)\b|我?(?:目前)?在职|受雇|全职工作", re.I),
        re.compile(r"\b(?:i(?:['’]?m| am)\s+)?self[- ]?employed\b|我?(?:目前)?(?:是)?自雇|自僱|自己经营|自己經營", re.I),
    ),
    "funding_source": (
        re.compile(
            r"\b(?:i(?:['’]?ll| will)?\s+(?:pay|cover)|self[- ]?funded|paying\s+myself)\b|"
            r"(?:费用|費用|旅费|旅費)(?:由我)?自理|"
            r"(?:费用|費用|旅费|旅費).{0,10}(?:自己|本人)(?:承担|承擔|支付|负担|負擔)|"
            r"(?:自己|本人)(?:承担|承擔|支付|负担|負擔).{0,10}(?:费用|費用|旅费|旅費)",
            re.I,
        ),
        re.compile(
            r"\bmy\s+(?:employer|company|school|university)\s+(?:will\s+)?(?:pay|cover|fund)|"
            r"(?:公司|雇主|僱主|学校|學校|大学|大學).{0,12}(?:承担|承擔|支付|资助|資助).{0,12}(?:费用|費用|旅费|旅費)",
            re.I,
        ),
        re.compile(
            r"\bmy\s+(?:mother|father|parents?|sister|brother|spouse|partner|friend)\s+"
            r"(?:will\s+)?(?:pay|cover|fund).{0,16}\b(?:my\s+)?(?:trip|travel|costs?|expenses?)\b|"
            r"(?:母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|伴侣|朋友)"
            r".{0,10}(?:资助我|資助我|承担我|承擔我|支付我).{0,10}(?:旅行|旅费|旅費|费用|費用)",
            re.I,
        ),
    ),
}

_DIRECT_ANSWER = {
    "visit_purpose": re.compile(
        r"(?:(?:my\s+)?(?:main\s+)?purpose\s+is\s+|for\s+)?"
        r"(?:tourism|sightseeing|(?:a\s+)?holiday|leisure|(?:a\s+)?conference|"
        r"(?:a\s+)?business\s+(?:trip|visit)|visit(?:ing)?\s+(?:family|friends?|relatives?))|"
        r"(?:去(?:英国|英國))?(?:旅游|旅遊|观光|觀光|参加会议|參加會議|参会|參會|"
        r"商务访问|商務訪問|探亲访友|探親訪友)",
        re.I,
    ),
    "occupation_status": re.compile(
        r"(?:a\s+)?(?:student|employed|self[- ]?employed)|学生|學生|在读|在讀|在职|受雇|自雇|自僱",
        re.I,
    ),
    "funding_source": re.compile(
        r"self[- ]?funded|(?:my\s+)?(?:employer|company|school|university)|"
        r"(?:my\s+)?(?:mother|father|parents?|sister|brother|spouse|partner|friend)|"
        r"自费|自費|(?:费用|費用|旅费|旅費)(?:由我)?自理|自己承担|自己承擔|公司|雇主|僱主|学校|學校|大学|大學|"
        r"母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|伴侣|朋友",
        re.I,
    ),
}

_CURRENT_FIELD_CONTEXT = {
    "visit_purpose": re.compile(
        r"\bi(?:['’]?m| am| will| plan| intend| want to)\b.{0,48}"
        r"\b(?:tourism|sightseeing|holiday|leisure|conference|seminar|business\s+(?:trip|visit)|"
        r"visit(?:ing)?\s+(?:family|friends?|relatives?))\b|"
        r"\bthis(?:\s+(?:trip|visit))?\s+is\s+(?:a\s+)?(?:holiday|conference|business visit)\b|"
        r"(?:(?:我|本人)(?:打算|计划|計劃|准备|準備|想去|要去|会去|會去|是去|去)|"
        r"这次|這次|此次|本次).{0,20}"
        r"(?:旅游|旅遊|观光|觀光|参加会议|參加會議|参会|參會|"
        r"商务访问|商務訪問|探亲访友|探親訪友)",
        re.I,
    ),
    "occupation_status": re.compile(
        r"\bi(?:['’]?m| am)\s+(?:currently\s+)?(?:a\s+)?"
        r"(?:student|employed|self[- ]?employed|working full[- ]?time)\b|"
        r"(?:我|本人)(?:目前)?(?:是|在)?(?:读书|讀書|在读|在讀|学生|學生|在职|受雇|"
        r"全职工作|自雇|自僱|自己经营|自己經營)",
        re.I,
    ),
    "funding_source": re.compile(
        r"\b(?:i(?:['’]?ll| will)?\s+(?:pay|cover)|paying\s+myself|self[- ]?funded)\b|"
        r"\bmy\s+(?:employer|company|school|university|mother|father|parents?|sister|brother|"
        r"spouse|partner|friend)\s+(?:will\s+)?(?:pay|cover|fund)\b|"
        r"(?:费用|費用|旅费|旅費)(?:由我)?自理|"
        r"(?:费用|費用|旅费|旅費).{0,14}(?:自己|本人|公司|雇主|僱主|学校|學校|"
        r"大学|大學|母亲|父亲|妈妈|爸爸|父母|配偶|伴侣|朋友).{0,14}"
        r"(?:承担|承擔|支付|负担|負擔|资助|資助)|"
        r"(?:自己|本人|公司|雇主|僱主|学校|學校|大学|大學|母亲|父亲|妈妈|爸爸|"
        r"父母|配偶|伴侣|朋友).{0,14}(?:承担|承擔|支付|负担|負擔|资助|資助).{0,14}"
        r"(?:费用|費用|旅费|旅費|我)",
        re.I,
    ),
}

_FUNDING_VALUE_PATTERNS: dict[str, re.Pattern[str]] = {
    "self": re.compile(
        r"\bself[- ]?funded\b|"
        r"\bI(?:['’]m| am)\s+paying\s+from\s+my\s+(?:own\s+)?savings\b|"
        r"\bI(?:['’]?ll| will| am going to)?\s+(?:pay|cover|fund)\b.{0,28}"
        r"(?:myself|my own (?:funds?|money|savings|account|trip|travel|costs?|expenses?)|from my own)|"
        r"\b(?:will|am going to)\s+(?:pay|cover|fund)\b.{0,28}\bmyself\b|"
        r"\b(?:paying|covering|funding)\b.{0,18}\bmyself\b|"
        r"自费|自費|(?:费用|費用|旅费|旅費)(?:由我)?自理|"
        r"(?:费用|費用|旅费|旅費|机票|機票|住宿|钱|錢).{0,14}"
        r"(?:由)?(?:我)?(?:自己|本人).{0,8}(?:承担|承擔|支付|负担|負擔|付|出)|"
        r"(?:我)?(?:自己|本人).{0,8}(?:承担|承擔|支付|负担|負擔|付|出).{0,14}"
        r"(?:费用|費用|旅费|旅費|机票|機票|住宿)",
        re.I,
    ),
    "employer_or_school": re.compile(
        r"\bmy\s+(?:employer|company|school|university)\b.{0,24}"
        r"(?:will\s+|is\s+going\s+to\s+|is\s+)?(?:pay(?:ing)?|cover(?:ing)?|fund(?:ing)?|sponsor(?:ing)?)\b.{0,28}"
        r"(?:me|(?:my|the|this)\s+(?:trip|travel|costs?|expenses?|flights?|accommodation)|"
        r"flights?|accommodation|"
        r"(?:directly\s+)?for\s+(?:my\s+|the\s+)?(?:trip|travel|costs?|expenses?|flights?|accommodation))|"
        r"(?:我的)?(?:公司|雇主|僱主|学校|學校|大学|大學|单位|單位).{0,18}"
        r"(?:承担|承擔|支付|负担|負擔|资助|資助).{0,18}"
        r"(?:我|这次|這次|旅行|旅费|旅費|费用|費用|机票|機票|住宿)|"
        r"(?:我|这次|這次|旅行|旅费|旅費|费用|費用).{0,18}"
        r"(?:由)?(?:我的)?(?:公司|雇主|僱主|学校|學校|大学|大學|单位|單位).{0,12}"
        r"(?:承担|承擔|支付|资助|資助)",
        re.I,
    ),
    "personal_sponsor": re.compile(
        r"\bmy\s+(?:mother|father|parents?|mum|mom|dad|sister|brother|spouse|wife|husband|partner|friend|relative)\b"
        r".{0,24}(?:will\s+|is\s+going\s+to\s+|is\s+)?(?:pay(?:ing)?|cover(?:ing)?|fund(?:ing)?|sponsor(?:ing)?)\b.{0,32}"
        r"(?:me|(?:my|the|this)\s+(?:trip|travel|costs?|expenses?|flights?|accommodation))|"
        r"\b(?:someone|an individual|a family member|a relative|a friend)\b.{0,20}"
        r"(?:will\s+|is\s+going\s+to\s+|is\s+)?(?:pay(?:ing)?|cover(?:ing)?|fund(?:ing)?|sponsor(?:ing)?)\b.{0,24}(?:me|my\s+(?:trip|costs?))|"
        r"\bmy\s+(?:mother|father|parents?|mum|mom|dad|sister|brother|spouse|wife|husband|partner|friend|relative)\s+is\s+my\s+sponsor\b|"
        r"(?:我的)?(?:母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|丈夫|妻子|伴侣|朋友|亲友|亲属|家人).{0,18}"
        r"(?:承担|承擔|支付|负担|負擔|资助|資助).{0,18}"
        r"(?:我|这次|這次|旅行|旅费|旅費|费用|費用|机票|機票|住宿)|"
        r"(?:我|这次|這次|旅行|旅费|旅費|费用|費用).{0,18}"
        r"(?:由)?(?:我的)?(?:母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|丈夫|妻子|伴侣|朋友|亲友|亲属|家人).{0,12}"
        r"(?:承担|承擔|支付|资助|資助)",
        re.I,
    ),
}

_DIRECT_FUNDING_VALUE = {
    "self": re.compile(r"self[- ]?funded|myself|自费|自費|我自己|本人", re.I),
    "employer_or_school": re.compile(
        r"(?:my\s+)?(?:employer|company|school|university)|公司|雇主|僱主|学校|學校|大学|大學|单位|單位",
        re.I,
    ),
    "personal_sponsor": re.compile(
        r"(?:my\s+)?(?:mother|father|parents?|mum|mom|dad|sister|brother|spouse|wife|husband|partner|friend|relative)|"
        r"母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|丈夫|妻子|伴侣|朋友|亲友|亲属|家人|个人资助人|個人資助人",
        re.I,
    ),
}

_NEGATED_FUNDING = re.compile(
    r"(?:钱|錢)(?:不|并非|並非|不是)(?:由我)?(?:自己|本人)出|"
    r"\b(?:not|never|isn['’]?t|aren['’]?t|doesn['’]?t|don['’]?t|won['’]?t|will not)\b"
    r".{0,18}\b(?:pay|paying|cover|covering|fund|funding|sponsor|sponsoring)\b|"
    r"\b(?:can(?:not|['’]?t)|could(?:not|n['’]?t)|(?:is|are|was|were|be|being)\s+unable\s+to|"
    r"(?:is|are|was|were|be|being)\s+not\s+able\s+to|unable\s+to|not\s+able\s+to|"
    r"won['’]?t\s+be\s+able\s+to|"
    r"will\s+not\s+be\s+able\s+to|refus(?:e|es|ed|ing)\s+to|declin(?:e|es|ed|ing)\s+to)"
    r".{0,24}\b(?:pay|cover|fund|sponsor)\b|"
    r"\b(?:can(?:not|['’]?t)|could(?:not|n['’]?t)|unable\s+to|not\s+able\s+to)"
    r".{0,12}\bafford\b|"
    r"(?:不|未|没有|沒有|没|沒|不会|不會|并非|並非).{0,8}"
    r"(?:资助|資助|支付|承担|承擔|负担|負擔|出钱)|"
    r"(?:拒绝|拒絕|不愿意|不願意|不肯|无法|無法|不能|没法|沒法|无力|無力|"
    r"没有能力|沒有能力|付不起).{0,10}"
    r"(?:资助|資助|支付|付款|承担|承擔|负担|負擔|出钱|旅费|旅費|费用|費用)",
    re.I,
)

_SPONSOR_REPLACEMENT = re.compile(
    r"\b(?:someone\s+else|somebody\s+else|another\s+person|a\s+different\s+person|"
    r"a\s+new\s+sponsor)\b.{0,36}\b(?:will\s+|is\s+going\s+to\s+|now\s+)?"
    r"(?:sponsor|fund|pay|cover)(?:s|ing)?\b.{0,32}\b(?:me|my\s+(?:trip|travel|costs?|expenses?))\b|"
    r"\b(?:my\s+)?sponsor\s+(?:has\s+changed|is\s+now\s+someone\s+else)\b|"
    r"\b(?:changed|switched)\s+(?:over\s+)?to\s+(?:someone\s+else|somebody\s+else|"
    r"another\s+person|a\s+different\s+(?:person|sponsor)|a\s+new\s+sponsor)\b|"
    r"\b(?:someone\s+else|somebody\s+else|another\s+(?:person|family\s+member|relative)|"
    r"a\s+different\s+(?:person|family\s+member|relative))\b.{0,40}"
    r"\b(?:sponsor|fund|pay|cover)(?:s|ed|ing)?\b"
    r".{0,24}\binstead\b|"
    r"(?:改由|改成由|现在由|現在由|换成|換成|更换为|更換為)"
    r"(?:其他人|别人|別人|另一个人|另一個人|新资助人|新資助人)"
    r".{0,18}(?:资助|資助|支付|承担|承擔|负担|負擔)|"
    r"(?:我的)?资助人(?:已经|已經|现在|現在)?(?:换了|換了|变成|變成)"
    r"(?:其他人|别人|別人|新资助人|新資助人)",
    re.I,
)

_QUESTION_OR_NONCOMMITTAL_REPLACEMENT = re.compile(
    r"\b(?:can|could|would|should|may|might)\b.{0,48}\b(?:someone\s+else|another\s+person|"
    r"different\s+sponsor|new\s+sponsor)\b|"
    r"(?:可以|能|能否|可否|是否|会不会|會不會).{0,20}"
    r"(?:其他人|别人|別人|新资助人|新資助人)|(?:吗|嗎|呢)$|[?？]",
    re.I,
)
_REPORTED_REPLACEMENT = re.compile(
    r"(?:朋友|同事|客户|客戶|别人|別人).{0,8}(?:说|說|表示|告诉我|告訴我)",
    re.I,
)


def explicit_personal_sponsor_replacement(text: str) -> bool:
    """Recognise an asserted change of payer without inventing the new identity.

    This signal is only for retiring stale sponsor identity/location.  It does
    not establish who the replacement is, where they live, or that they can
    actually provide funds.  Questions, hypotheticals, reported speech and
    uncertain plans therefore fail closed.
    """
    current = unicodedata.normalize("NFKC", text).strip()
    if not current or len(current) > 1200:
        return False
    # Preserve terminal question marks so a bare "My sponsor has changed?"
    # cannot be mistaken for an asserted correction.
    for sentence in (part.strip() for part in re.split(r"(?<=[.!?。！？;；])|\n", current)):
        if not sentence or (
            _HYPOTHETICAL_OR_REPORTED.search(sentence)
            or _REPORTED_REPLACEMENT.search(sentence)
            or _UNCERTAIN_OR_CHOICE.search(sentence)
            or _QUESTION_OR_NONCOMMITTAL_REPLACEMENT.search(sentence)
        ):
            continue
        if _SPONSOR_REPLACEMENT.search(sentence):
            return True
    return False


def funding_source_value_is_grounded(
    text: str,
    value: str,
    *,
    directly_requested: bool = False,
) -> bool:
    """Require the proposed funding enum to match an affirmative payer statement.

    Saying that one host will not pay is not evidence of self-funding: another
    person or organisation may still be the payer.  This validates a model's
    enum choice; it never creates a fact by itself.
    """
    pattern = _FUNDING_VALUE_PATTERNS.get(value)
    direct = _DIRECT_FUNDING_VALUE.get(value)
    if pattern is None or direct is None:
        return False
    current = unicodedata.normalize("NFKC", text).strip()
    if not current or len(current) > 1200:
        return False
    sentences = [part.strip() for part in re.split(r"[.!?。！？;；\n]", current) if part.strip()]
    for sentence in sentences:
        if _HYPOTHETICAL_OR_REPORTED.search(sentence):
            continue
        clauses = [part.strip() for part in re.split(
            r"[,，]|\b(?:but|whereas|while)\b|\band(?=\s+(?:i|he|she|they)\b)|但是|不过|不過|但|而(?=我|他|她)",
            sentence,
            flags=re.I,
        ) if part.strip()]
        cursor = 0
        for clause in clauses:
            start = sentence.find(clause, cursor)
            cursor = max(cursor, start + len(clause))
            # A choice or uncertainty before/in the payer statement governs
            # that statement. A later independent date deferral does not turn
            # an already-explicit payer into an uncertain funding arrangement.
            if _UNCERTAIN_OR_CHOICE.search(sentence[:start]) or _UNCERTAIN_OR_CHOICE.search(clause):
                continue
            if _NEGATED_FUNDING.search(clause):
                continue
            if pattern.search(clause):
                return True
    if directly_requested and len(sentences) == 1 and not (
        _HYPOTHETICAL_OR_REPORTED.search(current)
        or _UNCERTAIN_OR_CHOICE.search(current)
        or _NEGATED_FUNDING.search(current)
    ):
        return bool(direct.fullmatch(current.strip(" ,，:：")))
    return False


def explicitly_answers_current_enum(
    text: str,
    field: str,
    *,
    directly_requested: bool = False,
) -> bool:
    """Return whether current applicant prose plainly answers one enum field.

    A positive result suppresses only the same-turn question.  Uncertainty,
    negation, hypotheticals, reported speech and another person's circumstances
    fail closed.  A bare value is accepted only as a short direct answer.
    """
    patterns = _PATTERNS.get(field)
    if not patterns:
        return False
    value = unicodedata.normalize("NFKC", text).strip()
    if not value or len(value) > 500:
        return False
    message_has_other_applicant = bool(_OTHER_APPLICANT.search(value))
    sentences = [part.strip() for part in re.split(r"[.!?。！？;；\n]", value) if part.strip()]
    for sentence in sentences:
        if _HYPOTHETICAL_OR_REPORTED.search(sentence) or _UNCERTAIN_OR_CHOICE.search(sentence):
            continue
        for pattern in patterns:
            for match in pattern.finditer(sentence):
                prefix = sentence[max(0, match.start() - 32):match.start()]
                if re.search(
                    r"(?:\b(?:not|never|no\s+longer|isn['’]?t|aren['’]?t|no)\b|"
                    r"不是|并非|並非|不会|不會|没有|沒有)"
                    r"[^,;。；]{0,16}$",
                    prefix,
                    re.I,
                ):
                    continue
                # A relative funding this applicant is an allowed funding-source
                # answer; for every other enum, a named third person owns it.
                other_person = _OTHER_APPLICANT.search(sentence)
                if other_person and not (
                    field == "funding_source"
                    and ("my trip" in sentence.casefold() or re.search(r"资助我|資助我|承担我|承擔我|支付我", sentence))
                ):
                    continue
                if _CURRENT_FIELD_CONTEXT[field].search(sentence):
                    return True
                direct = _DIRECT_ANSWER[field].fullmatch(sentence.strip(" ,，:："))
                if direct and not message_has_other_applicant and (
                    directly_requested or len(sentences) == 1
                ):
                    return True
    return False
