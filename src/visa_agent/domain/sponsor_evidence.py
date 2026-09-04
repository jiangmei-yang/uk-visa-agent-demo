"""Bounded sponsor-role grounding, not general entity or funding interpretation.

A host's identity/location is not evidence that they fund this applicant. Unknown
roles remain missing; this helper neither clears old facts nor changes funding.
"""

import re
from collections.abc import Mapping, Sequence

SPONSOR_FIELDS = {"sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"}
RELATIONSHIPS = {
    "parents": r"\bparents\b|父母|爸妈|双亲",
    "mother": r"\b(?:mother|mom|mum)\b|母亲|媽媽|妈妈",
    "father": r"\b(?:father|dad)\b|父亲|爸爸",
    "sister": r"\bsister\b|姐姐|妹妹",
    "brother": r"\bbrother\b|哥哥|弟弟",
    "spouse": r"\b(?:spouse|wife|husband)\b|配偶|妻子|丈夫",
    "partner": r"\bpartner\b|伴侣",
    "friend": r"\bfriend\b|朋友",
    "employer": r"\bemployer\b|雇主",
}
NONCURRENT_OR_OTHER = re.compile(
    r"\b(?:if|unless|maybe|might|could|would|whether|previously|formerly|example)\b|"
    r"\b(?:used to|last year|no longer)\b|如果|假如|若|除非|可能|也许|以前|之前|曾经|过去|例如|"
    r"\b(?:(?:my|our)\s+)?(?:friend|client|customer|colleague|coworker)\s+"
    r"(?:said|says|wrote|writes|asked|asks)\b|"
    r"(?:我的?|我们的?)?(?:朋友|客户|同事)(?:说|写道|问过|问道)|"
    r"\b(?:my|our)\s+\w+['’]s\s+(?:sponsor|trip|travel|costs?|expenses?)\b|"
    r"我(?:的)?(?:朋友|姐姐|妹妹|哥哥|弟弟|母亲|父亲|父母)的?(?:资助人|旅行|旅费)|"
    r"\b(?:not|never|isn't|aren't|doesn't|won't)\b.{0,18}\b(?:sponsors?|pay|cover|fund)\b|"
    r"(?:不|未|并非|没有|无需|不用).{0,8}(?:资助|承担|支付|负担)|^[>\"“]",
    re.I,
)
_DECLINED_OR_UNABLE_TO_FUND = re.compile(
    r"\b(?:can(?:not|['’]?t)|could(?:not|n['’]?t)|(?:is|are|was|were|be|being)\s+unable\s+to|"
    r"(?:is|are|was|were|be|being)\s+not\s+able\s+to|unable\s+to|not\s+able\s+to|"
    r"won['’]?t\s+be\s+able\s+to|"
    r"will\s+not\s+be\s+able\s+to|refus(?:e|es|ed|ing)\s+to|declin(?:e|es|ed|ing)\s+to)"
    r".{0,24}\b(?:pay|cover|fund|sponsor)\b|"
    r"\b(?:can(?:not|['’]?t)|could(?:not|n['’]?t)|unable\s+to|not\s+able\s+to)"
    r".{0,12}\bafford\b|"
    r"(?:拒绝|拒絕|不愿意|不願意|不肯|无法|無法|不能|没法|沒法|无力|無力|"
    r"没有能力|沒有能力|付不起).{0,10}"
    r"(?:资助|資助|支付|付款|承担|承擔|负担|負擔|出钱|旅费|旅費|费用|費用)",
    re.I,
)
OWN_SPONSOR = re.compile(r"\b(?:my|our) sponsors?\b|我的?资助人|资助我的人", re.I)
PAYING_FOR_APPLICANT = re.compile(
    r"\b(?:sponsor(?:ing|s)?|fund(?:ing|s)?|financ(?:e|es|ing)|pay(?:ing|s)?\s+for|cover(?:ing|s)?)\s+"
    r"(?:(?:all|some|part)\s+of\s+)?(?:me\b|(?:my|this|the)\s+"
    r"(?:trips?|visits?|travel|flights?|accommodation|costs?|expenses?))|"
    r"资助我(?!的?(?:朋友|姐姐|妹妹|哥哥|弟弟|母亲|父亲|父母|爸妈|双亲))|"
    r"(?:为我|帮我|替我).{0,6}(?:支付|承担)|"
    r"(?:支付|承担|负担)我的?(?:旅行|旅费|住宿|机票|费用|开支)|"
    r"我(?:的)?(?:母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|丈夫|妻子|伴侣|朋友|亲友|亲属|家人)"
    r".{0,24}(?:资助|支付|承担|负担)(?:这次|這次|此次|本次)"
    r"(?:旅行|出行|赴英|访问|旅费|费用|开支)", re.I,
)
# This passive form must identify the applicant/current trip and the parents in
# the same complete clause. A loose "费用由...资助" search would also match a
# friend's costs, someone else's parents, a quotation or a negated arrangement.
CURRENT_TRIP_FUNDED_BY_PARENTS = re.compile(
    r"(?:(?:我(?:的)?|我们(?:的)?)(?:(?:这次|此次|本次)的?)?|(?:这次|此次|本次)的?)"
    r"(?:旅行|出行|赴英|访问)?(?:费用|旅费|开支)(?:全部|全额|部分)?(?:是|将)?由"
    r"(?:我(?:的)?|我们(?:的)?)?(?:父母|爸妈|双亲)(?:共同|一起|全部|全额)?"
    r"(?:资助|承担|支付|负担)(?:的)?",
)
_QUOTED_MATERIAL = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"')
_PERSON_REFERENCE = re.compile(
    r"\bmy\s+(?:sponsor|mother|father|parents?|mum|mom|dad|sister|brother|spouse|wife|"
    r"husband|partner|friend|relative|colleague|host|organiser|organizer)\b|"
    r"我(?:的)?(?:资助人|母亲|父亲|妈妈|爸爸|父母|姐姐|妹妹|哥哥|弟弟|配偶|丈夫|妻子|伴侣|朋友|亲友|亲属|同事|接待人)",
    re.I,
)
_UNCERTAIN_SPONSOR_ROLE = re.compile(
    r"\b(?:may|might|could|would|perhaps|maybe|possibly)\b.{0,24}"
    r"\b(?:sponsor|fund|pay|cover)\b|"
    r"(?:可能|也许|或许|不确定).{0,12}(?:资助|支付|承担|负担)",
    re.I,
)
_SPONSOR_OUTSIDE_UK_PRONOUN = re.compile(
    r"\b(?:he|she)\s+(?:does not|doesn't|is not|isn't)\s+"
    r"(?:live|living|resident|residing|in)\s*(?:in\s+)?(?:the\s+)?UK\b|"
    r"(?:他|她)(?:目前)?(?:不住在|不在|没有住在|沒有住在)英国",
    re.I,
)
_SPONSOR_INSIDE_UK_PRONOUN = re.compile(
    r"\b(?:he|she)\s+(?:lives?|is living|resides?|is residing|is)\s+"
    r"(?:in\s+)?(?:the\s+)?UK\b|"
    r"(?:他|她)(?:目前)?(?:住在|在|居住在)英国",
    re.I,
)
_SPONSOR_OUTSIDE_UK = re.compile(
    r"\b(?:does\s+not|doesn['’]?t|is\s+not|isn['’]?t|not)\b.{0,18}"
    r"\b(?:live|living|resident|residing|in)\b.{0,8}\b(?:the\s+)?UK\b|"
    r"\b(?:lives?|resides?|is\s+living)\b.{0,12}\boutside\b.{0,8}\b(?:the\s+)?UK\b|"
    r"(?:不住在|不在|没有住在|沒有住在)英国|"
    r"(?:住在|居住在)英国以外",
    re.I,
)
_SPONSOR_INSIDE_UK = re.compile(
    r"\b(?:lives?|living|resides?|residing|resident|is)\b.{0,10}\b(?:in\s+)?(?:the\s+)?UK\b|"
    r"\bin\s+(?:the\s+)?UK\b|(?:住在|居住在|在)英国",
    re.I,
)


def _normal(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .。;；")


def _relations(text: str) -> set[str]:
    return {relationship for relationship, pattern in RELATIONSHIPS.items() if re.search(pattern, text)}


def _requested_combined_parent_answer(
    field: str,
    value: str | int | bool,
    excerpt: str,
    body: str,
    *,
    known_profile: Mapping[str, object],
    requested_fields: Sequence[str],
) -> bool:
    """Ground one compact answer to the actually-sent parent identity question.

    A prior ``personal_sponsor`` fact plus both SENT question fields supplies
    conversational context, but never the answer. The current message must still
    explicitly say the parents are sponsoring this applicant, identify both names
    for a combined name value, and/or explicitly locate those same parents. Inline
    quotations, conditions, history and other people's arrangements are excluded.
    """
    if (
        known_profile.get("funding_source") != "personal_sponsor"
        or not {"sponsor_relationship", "sponsor_name"} <= set(requested_fields)
    ):
        return False

    # A quoted example can contain every expected keyword. Remove it rather than
    # borrowing its role context for the current applicant.
    current = re.sub(
        r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|(?<!\w)\'[^\'\n]+\'(?!\w)',
        "",
        body,
    ).strip()
    needle = _normal(excerpt)
    if (
        not current
        or needle not in _normal(current)
        or NONCURRENT_OR_OTHER.search(current)
        or _DECLINED_OR_UNABLE_TO_FUND.search(current)
    ):
        return False
    sentences = [part.strip() for part in re.split(
        r"[。!?！？；;\n]|\.(?:\s|$)", current,
    ) if part.strip()]
    if len(sentences) != 1:
        return False
    sentence = _normal(sentences[0])
    parent_support = bool(re.search(
        r"(?:^|[,，]\s*)(?:实际)?(?:是|由)?(?:我(?:的)?)?(?:父母|爸妈|双亲)"
        r"(?:两位|二人)?(?:共同|一起|都)?(?:来|会|将)?(?:资助|承担|支付|负担)"
        r"(?:我|(?:我的|这次|本次)?(?:旅行|旅费|费用|机票|住宿))?|"
        r"\b(?:both\s+)?my parents\b.{0,18}\b(?:will\s+|are\s+|do\s+)?"
        r"(?:sponsor|fund|pay|cover)(?:s|ing)?\b(?:.{0,24}\b(?:me|my|this|the)\s+"
        r"(?:trip|travel|costs?|expenses?|flights?|accommodation)\b)?",
        sentence,
        re.I,
    ))
    if not parent_support:
        return False

    if field == "sponsor_relationship":
        return value == "parents" and bool(re.search(r"父母|爸妈|双亲|\bparents\b", needle, re.I))
    if field == "sponsor_name":
        named_both = bool(re.search(
            r"(?:父亲|爸爸|爸).{1,60}(?:母亲|妈妈|妈)|"
            r"(?:母亲|妈妈|妈).{1,60}(?:父亲|爸爸|爸)|"
            r"\b(?:father|dad)\b.{1,80}\b(?:mother|mum|mom)\b|"
            r"\b(?:mother|mum|mom)\b.{1,80}\b(?:father|dad)\b",
            current,
            re.I,
        ))
        if not isinstance(value, str) or not named_both:
            return False
        names = [item.strip() for item in re.split(
            r"[、,，/&]|\s+and\s+|及|和", value, flags=re.I,
        ) if item.strip()]
        return len(names) == 2 and all(_normal(name) in _normal(current) for name in names)
    if field == "sponsor_is_in_uk":
        outside = bool(re.search(
            r"(?:他们|她们|父母|爸妈|两位)(?:目前)?(?:都|均)?(?:不住在|不在|没有住在)英国|"
            r"\b(?:neither of them (?:lives|is living)|they (?:both )?(?:do not|don't|aren't) live) in the UK\b",
            current,
            re.I,
        ))
        inside = bool(re.search(
            r"(?:他们|她们|父母|爸妈|两位)(?:目前)?(?:都|均)?(?:住在|在|居住在)英国|"
            r"\bthey (?:both )?(?:live|are living) in the UK\b",
            current,
            re.I,
        ))
        return (value is False and outside) or (value is True and inside and not outside)
    return False


def _requested_single_sponsor_answer(
    field: str,
    value: str | int | bool,
    excerpt: str,
    body: str,
    *,
    known_profile: Mapping[str, object],
    requested_fields: Sequence[str],
) -> bool:
    """Ground a natural one-sponsor answer to the sent identity question.

    The preceding question may supply the omitted object in "my father Chen Jian
    will sponsor".  The current sentence must still name exactly one relationship
    and explicitly identify that person as this applicant's sponsor.  A location
    answer is accepted from the same sentence only when its pronoun is unambiguous.
    """
    if (
        known_profile.get("funding_source") != "personal_sponsor"
        or not {"sponsor_relationship", "sponsor_name"} <= set(requested_fields)
    ):
        return False
    current = re.sub(
        r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|(?<!\w)\'[^\'\n]+\'(?!\w)',
        "",
        body,
    ).strip()
    needle = _normal(excerpt)
    if (
        not current
        or needle not in _normal(current)
        or NONCURRENT_OR_OTHER.search(current)
        or _DECLINED_OR_UNABLE_TO_FUND.search(current)
    ):
        return False
    sentences = [part.strip() for part in re.split(
        r"[。!?！？；;\n]|\.(?:\s|$)", current,
    ) if part.strip()]
    if len(sentences) != 1:
        return False
    sentence = _normal(sentences[0])
    relations = _relations(sentence)
    if len(relations) != 1 or "parents" in relations:
        return False
    relationship = next(iter(relations))
    relationship_pattern = RELATIONSHIPS[relationship]
    role = bool(
        OWN_SPONSOR.search(sentence)
        or re.search(
            rf"(?:是|由)?我(?:的)?(?:{relationship_pattern}).{{0,32}}"
            r"(?:资助|承担|支付|负担)(?:我|我的|这次|本次|旅行|旅费|费用|机票|住宿)?|"
            rf"\bmy\s+(?:{relationship_pattern})\b.{{0,42}}\b"
            r"(?:sponsor|fund|pay|cover)(?:s|ed|ing)?\b",
            sentence,
            re.I,
        )
    )
    if not role:
        return False
    if field == "sponsor_relationship":
        return value == relationship and bool(re.search(relationship_pattern, needle, re.I))
    if field == "sponsor_name":
        return isinstance(value, str) and _normal(value) in sentence and _normal(value) in needle
    if field == "sponsor_is_in_uk":
        outside = bool(re.search(
            r"(?:他|她|资助人|父亲|母亲|爸爸|妈妈)(?:目前)?(?:不住在|不在|没有住在)英国|"
            r"\b(?:he|she|the sponsor)\s+(?:does not|doesn't|is not|isn't)\s+"
            r"(?:live|living|resident|residing|in)\s*(?:in\s+)?(?:the\s+)?UK\b",
            sentence,
            re.I,
        ))
        inside = bool(re.search(
            r"(?:他|她|资助人|父亲|母亲|爸爸|妈妈)(?:目前)?(?:住在|在|居住在)英国|"
            r"\b(?:he|she|the sponsor)\s+(?:lives?|is living|resides?|is residing|is)\s+"
            r"(?:in\s+)?(?:the\s+)?UK\b",
            sentence,
            re.I,
        ))
        return (value is False and outside) or (value is True and inside and not outside)
    return False


def _same_sentence_unique_sponsor_location_answer(
    field: str,
    value: str | int | bool,
    excerpt: str,
    body: str,
) -> bool:
    """Link a later he/she UK-location clause to one explicit current sponsor.

    This is intentionally narrower than general pronoun resolution: the sponsor
    role and location must be in the same unquoted, non-hypothetical sentence,
    the role must precede the location, and no second person may compete for the
    pronoun.  It accepts a natural first-turn answer without borrowing identity
    from a different sentence or from quoted/reported text.
    """
    if field != "sponsor_is_in_uk" or not isinstance(value, bool):
        return False
    needle = _normal(excerpt)
    if not needle:
        return False
    sentences = [part.strip() for part in re.split(
        r"[。!?！？；;\n]|\.(?:\s|$)", body,
    ) if part.strip()]
    for sentence in sentences:
        normal = _normal(sentence)
        if (
            needle not in normal
            or NONCURRENT_OR_OTHER.search(sentence)
            or _DECLINED_OR_UNABLE_TO_FUND.search(sentence)
            or _QUOTED_MATERIAL.search(sentence)
            or _UNCERTAIN_SPONSOR_ROLE.search(sentence)
            or len(_PERSON_REFERENCE.findall(sentence)) != 1
            or len(_relations(normal)) > 1
        ):
            continue
        clauses = [part.strip() for part in re.split(
            r"[,，]|\b(?:and|but)\b|但是|不过|不過|但",
            sentence,
            flags=re.I,
        ) if part.strip()]
        location_indexes = [
            index for index, clause in enumerate(clauses)
            if needle in _normal(clause)
            and (_SPONSOR_OUTSIDE_UK_PRONOUN.search(clause) or _SPONSOR_INSIDE_UK_PRONOUN.search(clause))
        ]
        if len(location_indexes) != 1:
            continue
        location_index = location_indexes[0]
        role_clauses = [
            clause for clause in clauses[:location_index]
            if (
                OWN_SPONSOR.search(clause)
                or PAYING_FOR_APPLICANT.search(clause)
                or CURRENT_TRIP_FUNDED_BY_PARENTS.fullmatch(_normal(clause))
                or _requested_single_sponsor_answer(
                    "sponsor_relationship",
                    next(iter(_relations(_normal(clause))), ""),
                    clause,
                    clause,
                    known_profile={"funding_source": "personal_sponsor"},
                    requested_fields=("sponsor_relationship", "sponsor_name"),
                )
            )
        ]
        if len(role_clauses) != 1:
            continue
        location_clause = clauses[location_index]
        outside = bool(_SPONSOR_OUTSIDE_UK_PRONOUN.search(location_clause))
        inside = bool(_SPONSOR_INSIDE_UK_PRONOUN.search(location_clause)) and not outside
        if (value is False and outside) or (value is True and inside):
            return True
    return False


def _direct_requested_answer(field: str, value: str | int | bool, text: str) -> bool:
    """A sent question supplies role context only for an actual short answer."""
    if field == "sponsor_relationship":
        pattern = RELATIONSHIPS.get(str(value))
        return bool(pattern and re.fullmatch(
            r"(?:(?:my|our) relationship is\s+|my\s+|our\s+|我(?:的)?|我们(?:的)?)?"
            r"(?:" + pattern + r")(?: and (?:child|children))?", text,
        ))
    if field == "sponsor_name":
        name = re.escape(_normal(str(value)))
        relative = "(?:" + "|".join(RELATIONSHIPS.values()) + ")"
        return bool(re.fullmatch(
            r"(?:(?:my|our)\s+" + relative + r"\s+(?:is|is called|name is)\s+|"
            r"(?:我(?:的)?|我们(?:的)?)" + relative + r"(?:叫|是|名字是))?" + name, text,
        ))
    if not isinstance(value, bool):
        return False
    affirmative = {"yes", "true", "是", "在", "在英国"}
    negative = {"no", "false", "不是", "不在", "不在英国"}
    return text in (affirmative if value else negative)


def _sponsor_location_polarity_matches(value: str | int | bool, text: str) -> bool:
    """Bind the proposed boolean to the polarity of its own location excerpt."""
    if not isinstance(value, bool):
        return False
    normal = _normal(text)
    if _direct_requested_answer("sponsor_is_in_uk", value, normal):
        return True
    outside = bool(_SPONSOR_OUTSIDE_UK.search(normal))
    inside = bool(_SPONSOR_INSIDE_UK.search(normal)) and not outside
    return (value is False and outside) or (value is True and inside)


def sponsor_role_is_grounded(
    field: str, value: str | int | bool, excerpt: str, body: str,
    *, known_profile: Mapping[str, object], requested_fields: Sequence[str],
) -> bool:
    """Require an explicit current role, or context from an asked sponsor question.

    The caller supplies latest-only text and performs ordinary excerpt, type and
    conflict checks. A model's personal_sponsor enum is never role evidence.
    Cross-clause support is limited to a single shared relative or literal name.
    """
    needle = _normal(excerpt)
    if not needle or needle not in _normal(body):
        return False
    if field == "sponsor_is_in_uk" and not _sponsor_location_polarity_matches(value, excerpt):
        return False
    sentences = [part for part in re.split(r"[。!?！？;；\n]|\.(?:\s|$)", body) if part.strip()]
    # A leading condition governs its whole sentence, including clauses after a
    # comma. A later independent sentence can still establish a current role.
    # Funding refusal is clause-local: "father cannot pay, but mother will"
    # must reject the father without discarding the mother's affirmative role.
    # Conditions/reported speech in NONCURRENT_OR_OTHER still govern the whole
    # sentence and remain sentence-scoped.
    safe_sentences = [part for part in sentences if not NONCURRENT_OR_OTHER.search(part)]
    clauses = [normal for sentence in safe_sentences for part in re.split(
        r"[,，]|\b(?:and|but)\b|但是|不过|但", sentence, flags=re.I,
    ) if (normal := _normal(part))]
    contexts = [part for part in clauses if needle in part]
    if not contexts and any(needle in _normal(part) for part in safe_sentences):
        contexts = [needle]  # A verbatim excerpt may itself span clauses.
    safe_contexts = [
        part for part in contexts
        if not NONCURRENT_OR_OTHER.search(part)
        and not _DECLINED_OR_UNABLE_TO_FUND.search(part)
    ]
    if not safe_contexts:
        return False
    if _requested_combined_parent_answer(
        field,
        value,
        excerpt,
        body,
        known_profile=known_profile,
        requested_fields=requested_fields,
    ):
        return True
    if _requested_single_sponsor_answer(
        field,
        value,
        excerpt,
        body,
        known_profile=known_profile,
        requested_fields=requested_fields,
    ):
        return True
    if _same_sentence_unique_sponsor_location_answer(field, value, excerpt, body):
        return True
    if (known_profile.get("funding_source") == "personal_sponsor" and field in requested_fields
            and len(sentences) == 1 and len(_relations(_normal(body))) <= 1
            and not NONCURRENT_OR_OTHER.search(sentences[0])
            and _direct_requested_answer(field, value, _normal(body))):
        # In Gmail, WorkflowService exposes only pending questions actually SENT.
        # The context supplies the role, never the new value or new evidence.
        return True
    if any(re.fullmatch(re.escape(field) + r"\s*[:=].+", part) for part in safe_contexts):
        return True  # An explicit field-labelled applicant answer, not an inferred role.
    roles = [part for part in clauses if not NONCURRENT_OR_OTHER.search(part)
             and not _DECLINED_OR_UNABLE_TO_FUND.search(part)
             and (OWN_SPONSOR.search(part) or PAYING_FOR_APPLICANT.search(part)
                  or CURRENT_TRIP_FUNDED_BY_PARENTS.fullmatch(part))]
    for context in safe_contexts:
        for role in roles:
            if field == "sponsor_name":
                if _normal(str(value)) in context and _normal(str(value)) in role:
                    return True
            elif field == "sponsor_relationship":
                if _relations(context) == _relations(role) == {str(value)}:
                    return True
            elif ((context == role and OWN_SPONSOR.search(context))
                  or len(_relations(context)) == 1 and _relations(context) == _relations(role)):
                return True
    return False
