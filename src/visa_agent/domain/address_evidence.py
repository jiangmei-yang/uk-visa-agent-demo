"""Minimum residential-detail checks, not postal validation or address verification.

Country/city background remains useful evidence but cannot by itself identify a
home. Named houses, villages, dormitories and non-Latin premises are supported;
neither a house number nor a postcode is universally required. Unrecognised or
incomplete formats remain a normal missing detail, not a human-review verdict.
"""

import re
import unicodedata

from visa_agent.domain.locations import location_key


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _literal_key(value: str) -> str:
    return re.sub(r"[^\w]+", " ", _normal(value)).strip()


def address_value_is_grounded(value: str, excerpt: str) -> bool:
    """Require the actual proposed text, with bounded existing location aliases.

    A residential cue alone cannot ground model-invented street/house details.
    This accepts formatting differences, not translation or inferred locations.
    """
    needle = _literal_key(value)
    if needle and re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", _literal_key(excerpt)):
        return True
    # The cold-start failure included an English 'China' value for 中国 evidence.
    # Only existing explicit location aliases are compared; no geocoding occurs.
    pieces = re.split(r"[,，。;；\n]", _normal(excerpt))
    for piece in pieces:
        stripped = re.sub(
            r"^(?:我\s*)?(?:(?:现在|目前)\s*)?(?:居住在|居住于|住在|家在|现居|住址(?:是|为|[:：])?)\s*",
            "", piece.strip(),
        ).strip(" .。:：")
        if location_key(value) == location_key(stripped) and stripped:
            return True
    return False


def address_excerpt_is_other_location(excerpt: str, value: str) -> bool:
    """Exclude only explicit other-subject clauses grounding this exact value."""
    clauses = [clause.strip() for clause in re.split(r"[。！？!?;；\n]|\.(?:\s|$)", _normal(excerpt))
               if address_value_is_grounded(value, clause)]
    other_subject = (
        r"(?:(?:my|our|the)\s+)?(?:office|workplace|company|employer(?:'s|’s)?)\s+address\b|"
        r"(?:my|his|her|their)\s+(?:mother|father|sister|brother|friend|colleague|daughter|son)"
        r"(?:'s|’s)?\s+(?:(?:home|current)\s+)?(?:address|lives?|resides?)\b|"
        r"(?:我的?)?(?:公司|单位|單位|工作地点|工作地點)(?:的)?(?:地址|住址)|"
        r"(?:我的?)?(?:母亲|父亲|媽媽|妈妈|爸爸|姐姐|妹妹|哥哥|弟弟|朋友)(?:的)?(?:地址|住址|住在|居住)"
    )
    return bool(clauses and all(re.match(other_subject, clause, re.I) for clause in clauses))


def address_detail_is_sufficient(value: str | None) -> bool:
    """Does the supplied text have minimum home-location detail beyond geography?

    This intentionally makes no claim that an address exists, is deliverable, or
    belongs to the applicant. Provenance and applicant confirmation still apply.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    text = _normal(value)
    if re.search(r"\b(?:unknown|tbc|tbd|not (?:known|sure|decided)|to follow)\b|"
                 r"不确定|不清楚|未确定|待补|待定|还没(?:定|确定)", text):
        return False
    components = [part.strip() for part in re.split(r"[,，、،;；\n]", text) if part.strip()]
    # A street locator needs an actual house/building identifier and locality,
    # not merely 'High Street' or a city plus a postcode.
    street = re.search(r"\b[\w'-]+(?:\s+[\w'-]+){0,5}\s+"
                       r"(?:road|rd|street|st|lane|ln|avenue|ave|drive|close|way|terrace|court)\b", text)
    if street and re.search(r"\b\d+[a-z]?(?:[-/]\d+[a-z]?)?\b", text) and (
            len(components) >= 2 or bool(text[street.end():].strip())):
        return True
    # Named premises can identify a home even where no street number is used.
    named_home = re.search(r"\b[\w'-]+(?:\s+[\w'-]+){0,5}\s+"
                           r"(?:house|cottage|hall|lodge|farm|residence|residences|dormitory|apartments)\b", text)
    if named_home and (len(components) >= 2 or bool(text[named_home.end():].strip())):
        return True
    unit = re.search(r"\b(?:flat|room|apartment|apt|unit)\s+[\w-]+\b", text)
    building = re.search(r"\b(?:building|block|tower)\s+[\w-]+\b", text)
    if unit and building and len(components) >= 2:
        return True
    # CJK addresses commonly encode their components without commas or spaces.
    locality = re.search(r"省|市|区|區|县|縣|镇|鎮|村|郡|町|県|縣", text)
    numbered_street = re.search(r"(?:路|道|街|巷)\s*[\d一二三四五六七八九十百甲乙丙]+(?:号|號)", text)
    if numbered_street:
        return True
    premises = re.search(r"(?:[\d一二三四五六七八九十百甲乙丙]+(?:号楼|號樓)|"
                         r"[\d一二三四五六七八九十]+(?:室|栋|棟|座|番地)|"
                         r"[\u3400-\u9fff]{2,}(?:宅|邸|院|荘|莊|宿舍|公寓))", text)
    if premises and (locality or len(components) >= 2):
        return True
    # Named residences in a non-Latin script need a separately supplied locality.
    return bool(len(components) >= 2 and re.search(r"निवास|भवन|सदन|منزل|دار\s+\S+|\bдом\s+\S+", text))
