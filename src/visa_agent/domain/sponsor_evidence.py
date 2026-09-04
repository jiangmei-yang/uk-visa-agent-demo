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
    r"\b(?:my|our)\s+\w+['’]s\s+(?:sponsor|trip|travel|costs?|expenses?)\b|"
    r"我(?:的)?(?:朋友|姐姐|妹妹|哥哥|弟弟|母亲|父亲|父母)的?(?:资助人|旅行|旅费)|"
    r"\b(?:not|never|isn't|aren't|doesn't|won't)\b.{0,18}\b(?:sponsors?|pay|cover|fund)\b|"
    r"(?:不|未|并非|没有|无需|不用).{0,8}(?:资助|承担|支付|负担)|^[>\"“]",
    re.I,
)
OWN_SPONSOR = re.compile(r"\b(?:my|our) sponsors?\b|我的?资助人|资助我的人", re.I)
PAYING_FOR_APPLICANT = re.compile(
    r"\b(?:sponsor(?:ing|s)?|fund(?:ing|s)?|financ(?:e|es|ing)|pay(?:ing|s)?\s+for|cover(?:ing|s)?)\s+"
    r"(?:(?:all|some|part)\s+of\s+)?(?:me\b|my\s+(?:trips?|visits?|travel|flights?|accommodation|costs?|expenses?))|"
    r"资助我(?!的?(?:朋友|姐姐|妹妹|哥哥|弟弟|母亲|父亲|父母|爸妈|双亲))|"
    r"(?:为我|帮我|替我).{0,6}(?:支付|承担)|"
    r"(?:支付|承担|负担)我的?(?:旅行|旅费|住宿|机票|费用|开支)", re.I,
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


def _normal(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .。;；")


def _relations(text: str) -> set[str]:
    return {relationship for relationship, pattern in RELATIONSHIPS.items() if re.search(pattern, text)}


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
    return text in {"yes", "no", "true", "false", "是", "不是", "在", "不在", "在英国", "不在英国"}


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
    sentences = [part for part in re.split(r"[。!?！？;；\n]|\.(?:\s|$)", body) if part.strip()]
    # A leading condition governs its whole sentence, including clauses after a
    # comma. A later independent sentence can still establish a current role.
    safe_sentences = [part for part in sentences if not NONCURRENT_OR_OTHER.search(part)]
    clauses = [normal for sentence in safe_sentences for part in re.split(
        r"[,，]|\b(?:and|but)\b|但是|不过|但", sentence, flags=re.I,
    ) if (normal := _normal(part))]
    contexts = [part for part in clauses if needle in part]
    if not contexts and any(needle in _normal(part) for part in safe_sentences):
        contexts = [needle]  # A verbatim excerpt may itself span clauses.
    safe_contexts = [part for part in contexts if not NONCURRENT_OR_OTHER.search(part)]
    if not safe_contexts:
        return False
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
