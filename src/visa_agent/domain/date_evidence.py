"""Literal date grounding, independent of the system clock and model inference."""

import re
import unicodedata
from datetime import date

YEAR_FIRST_DATE = re.compile(
    r"(?<![\d./-])(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d|[./-]\d)"
)


def _compact_date_text(excerpt: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", excerpt)).casefold()


def canonical_date_value(value: str) -> str:
    """Normalize an explicit year-first value, without guessing missing parts or order.

    Evidence grounding must still run against the original customer excerpt afterwards.
    Invalid or ambiguous values remain unchanged for the caller's validation to reject.
    """
    compact = _compact_date_text(value)
    numeric = YEAR_FIRST_DATE.fullmatch(compact)
    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", compact)
    parts = ((numeric[1], numeric[3], numeric[4]) if numeric else
             (chinese[1], chinese[2], chinese[3]) if chinese else None)
    if parts is None:
        return value
    try:
        return date(*(int(part) for part in parts)).isoformat()
    except ValueError:
        return value


def date_is_grounded(value: str, excerpt: str, *, allow_shared_year: bool = True) -> bool:
    try:
        expected = date.fromisoformat(value)
    except ValueError:
        return False
    compact = _compact_date_text(excerpt)
    y, m, d = expected.year, expected.month, expected.day
    if re.search(rf"(?<!\d){y}年0?{m}月0?{d}[日号]", compact):
        return True
    # Month/day may share an explicitly written year elsewhere in this same excerpt.
    # Do not borrow a year from the current date or an earlier conversation.
    years = set(re.findall(r"(?<!\d)(\d{4})(?:年|[-/])", compact))
    if allow_shared_year and years == {str(y)} and re.search(rf"(?<!\d)0?{m}月0?{d}[日号]", compact):
        return True
    # Accept unambiguous year-first dot notation too; never guess day/month order.
    # Use the same recognizer as has_calendar_day so a valid fact is not silently lost.
    if any((int(match[1]), int(match[3]), int(match[4])) == (y, m, d)
           for match in YEAR_FIRST_DATE.finditer(compact)):
        return True
    normal = re.sub(r"\s+", " ", excerpt).casefold()
    month = rf"(?:{expected.strftime('%B')}|{expected.strftime('%b')})"
    return bool(
        re.search(
            rf"(?<!\d)(?:0?{d}\s+{month}\s+{y}|{month}\s+0?{d},?\s+{y})(?!\d)",
            normal,
            re.I,
        )
    )


def has_calendar_day(excerpt: str) -> bool:
    """A month alone is insufficient, but a partially specified calendar day is useful."""
    if YEAR_FIRST_DATE.search(_compact_date_text(excerpt)):
        return True
    return bool(
        re.search(
            r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|"
            r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}",
            excerpt,
        )
    )
