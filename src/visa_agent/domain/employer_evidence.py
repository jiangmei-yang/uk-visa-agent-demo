"""Literal, current applicant-employer details; no general entity resolution."""

import re

EMPLOYER_FIELDS = {"employer_name", "employer_address", "employer_phone"}


def employer_phone_is_sufficient(value: object) -> bool:
    return bool(isinstance(value, str) and re.fullmatch(r"\+?[\d ()\-.]+", value)
                and 7 <= len(re.sub(r"\D", "", value)) <= 15)

_MARKERS = {
    "employer_name": r"(?:\bmy\s+(?:current\s+)?employer\s*(?:is|:)|我的?(?:现任|现在的|目前的)?雇主(?:名称)?(?:是|为|[:：]))\s*",
    "employer_address": r"(?:\bmy\s+(?:current\s+)?employer['’]s\s+(?:current\s+)?address\s*(?:is|:)|我的?(?:现任|现在的|目前的)?雇主(?:的)?地址(?:是|为|[:：]))\s*",
    "employer_phone": r"(?:\bmy\s+(?:current\s+)?employer['’]s\s+(?:phone|telephone|contact)(?:\s+number)?\s*(?:is|:)|我的?(?:现任|现在的|目前的)?雇主(?:的)?(?:电话|电话号码|联系电话)(?:是|为|[:：]))\s*",
}
_NONCURRENT = re.compile(
    r"\b(?:if|unless|maybe|might|could|would|previously|formerly|not|never|unknown|unsure)\b|"
    r"\b(?:for example|used to|last year|no longer|please translate)\b|"
    r"\b(?:my friend|he|she|they)\s+(?:said|says|wrote|asked)\b|"
    r"如果|假如|以前|之前|曾经|可能|例如|不是|并非|不确定|不知道|不清楚|翻译|"
    r"(?:朋友|同事|他|她)(?:说|写道)|[\"“”‘’「」『』>]",
    re.I,
)


def employer_detail_is_grounded(field: str, value: str | int | bool, excerpt: str, body: str,
                               *, sent_question_verified: bool = False) -> bool:
    """Require a complete owner-identifying current statement in retained evidence.

    Missing/bare details remain missing; neither a previous company, the employer
    of someone else nor an address-looking string may silently become this record.
    Phone text stays literal; no country code, geocoding or translation is inferred.
    """
    if field not in EMPLOYER_FIELDS or not isinstance(value, str) or not value.strip() or len(value) > 400:
        return False
    if field == "employer_phone" and not employer_phone_is_sufficient(value):
        return False
    def normal(text: str) -> str:
        return " ".join(text.casefold().split()).strip(" .。")
    needle = normal(excerpt)
    if not needle or needle not in normal(body) or normal(value) not in needle:
        return False
    if (sent_question_verified and normal(value) == needle == normal(body)
            and not re.search(r"\b(?:my|our|his|her|their|if|maybe|unknown|not|sure|check)\b|"
                              r"[?？\"“”「」『』>]|我的|他的|她的|如果|不确定|不知道|不清楚|待定", body, re.I)):
        if field != "employer_address":
            return True
        from visa_agent.domain.address_evidence import address_detail_is_sufficient

        if address_detail_is_sufficient(value):
            return True
    # Preserve periods inside company names and phone numbers. A sentence boundary
    # requires punctuation followed by whitespace (or the end).
    for segment in re.finditer(r"(.+?)([。;；\n!?！？]|\.(?:\s|$)|$)", body):
        sentence, punctuation = segment.group(1, 2)
        sentence = sentence.strip()
        if not sentence or punctuation in {"?", "？"}:
            continue
        for match in re.finditer(_MARKERS[field], sentence, re.I):
            detail = sentence[match.end():].strip().rstrip(" .。")
            if normal(detail) != normal(value) or normal(sentence[match.start():]) not in needle:
                continue
            # Check framing separately; a business name can contain ordinary
            # words also used in hypothetical sentences (e.g. "Maybe Ltd").
            if _NONCURRENT.search(sentence[:match.start()]):
                continue
            if re.search(r"[\"“”「」『』>]", sentence):
                continue
            if re.match(r"(?:not\b|unknown\b|tbc\b|tbd\b|不确定|不知道|不是)", detail, re.I):
                continue
            if re.search(r"\b(?:not sure|need to check)\b|\b(?:but|and)\s+(?:I|we)\b|不确定|还需确认", detail, re.I):
                continue
            return True
    return False


def literal_employer_details(body: str, verified_field: str | None = None) -> list[tuple[str, str, str]]:
    """Return only literal grammar matches, never invented fields or permissions."""
    candidates = []
    if verified_field in EMPLOYER_FIELDS and employer_detail_is_grounded(
            verified_field, body.strip(), body.strip(), body, sent_question_verified=True):
        candidates.append((verified_field, body.strip(), body.strip()))
    for segment in re.finditer(r"(.+?)([。;；\n!?！？]|\.(?:\s|$)|$)", body):
        sentence = segment.group(1).strip()
        if segment.group(2) in {"?", "？"}:
            continue
        for field, marker in _MARKERS.items():
            for match in re.finditer(marker, sentence, re.I):
                value = sentence[match.end():].strip()
                if employer_detail_is_grounded(field, value, sentence, body):
                    candidates.append((field, value, sentence))
    # Two different values for a field are not a safe literal completion.
    return list(dict.fromkeys(item for item in candidates
        if len({candidate[1] for candidate in candidates if candidate[0] == item[0]}) == 1))
