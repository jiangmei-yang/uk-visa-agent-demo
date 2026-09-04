"""Display accepted funding facts without exposing a combined internal enum.

This is deliberately a wording helper, not another fact extractor. A short, direct
payment assertion in the *current funding evidence* can refine the display label.
Longer, qualified or otherwise ambiguous excerpts retain the stored broad category.
No caller should use the result to change requirements or release a case.
"""

from __future__ import annotations

import re
import unicodedata

from visa_agent.domain.models import Case, ProvenanceState

_ORGANISATION_FUNDING = "employer_or_school"
_MESSAGE_EXTRACTION = "bounded_structured_extraction"
_USABLE_PROVENANCE = {
    ProvenanceState.EXTRACTED_UNVERIFIED,
    ProvenanceState.VERIFIED,
    ProvenanceState.DEMO_SYNTHETIC,
}
_LABELS = {
    "school": ("学校", "school"),
    "university": ("大学", "university"),
    "employer": ("雇主", "employer"),
    "company": ("公司", "company"),
}
_ZH_PARTIES = {"学校": "school", "大学": "university", "雇主": "employer", "公司": "company"}
_ZH_PARTY = r"(?:我的?|我们(?:的)?)?(?P<party>学校|大学|雇主|公司)"
_ZH_COSTS = r"(?:(?:这次|此次|本次)?(?:旅行|出行|行程|旅程)?(?:的)?(?:费用|开销|花费))"
_ZH_ASSERTIONS = (
    re.compile(
        rf"(?:这次|此次|本次)?(?:由)?{_ZH_PARTY}(?:会)?"
        rf"(?:出钱|(?:承担|支付|报销){_ZH_COSTS}|资助(?:我|这次旅行|本次出行)?)"
    ),
    re.compile(rf"{_ZH_COSTS}(?:是)?由{_ZH_PARTY}(?:承担|支付|报销|提供)"),
)
_EN_ASSERTION = re.compile(
    r"(?:(?:my|our|the) )?(?P<party>school|university|employer|company) "
    r"(?:(?:is paying|will pay|pays)"
    r"(?: for (?:my |our |the |this )?(?:trip|travel|travel costs|trip costs|costs|expenses))?"
    r"|(?:is (?:covering|funding)|will (?:cover|fund)|covers|funds)"
    r" (?:my |our |the |this )?(?:trip|travel|travel costs|trip costs|costs|expenses))"
)


def _asserted_party(excerpt: str) -> str | None:
    # Full matching, rather than searching for an organisation name anywhere in a
    # message, prevents negation, quoted examples, historical plans and split/partial
    # funding from becoming a confident specific payer. Do not strip quote markers.
    text = re.sub(r"[.!。！]$", "", unicodedata.normalize("NFKC", excerpt).strip())
    compact = re.sub(r"\s+", "", text)
    for pattern in _ZH_ASSERTIONS:
        match = pattern.fullmatch(compact)
        if match:
            return _ZH_PARTIES[match["party"]]
    match = _EN_ASSERTION.fullmatch(re.sub(r"\s+", " ", text).casefold())
    return match["party"] if match else None


def _current_party(case: Case) -> str | None:
    evidence = case.active_evidence("funding_source")
    if not evidence:
        return None
    parties = set()
    for item in evidence:
        if (
            item.value != case.profile.funding_source
            or item.source_document_id is not None
            or item.extraction_method != _MESSAGE_EXTRACTION
            or item.provenance_state not in _USABLE_PROVENANCE
            or item.confidence < 0.8
        ):
            return None
        party = _asserted_party(item.source_excerpt)
        if party is None:
            return None
        parties.add(party)
    # More than one active interpretation is not permission to pick a favourite.
    return next(iter(parties)) if len(parties) == 1 else None


def funding_label(case: Case, *, language: str) -> str:
    """Return a display value for the existing fact, not an organisation name.

    Ordinary EXTRACTED_UNVERIFIED evidence means accepted customer-reported
    information, not verified funding. Document excerpts are intentionally not used
    to infer a more specific party here. No evidence, conflicting evidence or an
    unfamiliar assertion leaves the combined category intact.
    """
    zh = language == "zh"
    source = case.profile.funding_source
    if source == "self":
        return "本人" if zh else "self-funded"
    if source == "personal_sponsor":
        return "个人资助人" if zh else "a personal sponsor"
    if source == _ORGANISATION_FUNDING:
        party = _current_party(case)
        if party is not None:
            return _LABELS[party][0 if zh else 1]
        return "雇主或学校" if zh else "employer or school"
    return "尚未确认" if zh else "not yet confirmed"


def funding_wording(case: Case, *, language: str) -> str:
    """Return an unpunctuated acknowledgement clause, without mutating the case."""
    zh = language == "zh"
    source = case.profile.funding_source
    if source == "self":
        return "费用由你自己承担" if zh else "you're paying for the trip yourself"
    if source == "personal_sponsor":
        return "这次有个人资助" if zh else "someone is helping fund your trip"
    if source == _ORGANISATION_FUNDING:
        label = funding_label(case, language=language)
        if zh:
            return f"费用由{label}承担"
        subject = "your employer or school" if label == "employer or school" else f"the {label}"
        return f"{subject} is paying for the trip"
    return "费用由谁承担还待确认" if zh else "who will fund the trip is not yet confirmed"
