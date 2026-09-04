"""Literal date grounding, independent of the system clock and model inference."""

import re
from datetime import date


def date_is_grounded(value: str, excerpt: str, *, allow_shared_year: bool = True) -> bool:
    try:
        expected = date.fromisoformat(value)
    except ValueError:
        return False
    compact = re.sub(r"\s+", "", excerpt).casefold()
    y, m, d = expected.year, expected.month, expected.day
    if re.search(rf"(?<!\d){y}年0?{m}月0?{d}[日号]", compact):
        return True
    # Month/day may share an explicitly written year elsewhere in this same excerpt.
    # Do not borrow a year from the current date or an earlier conversation.
    years = set(re.findall(r"(?<!\d)(\d{4})(?:年|[-/])", compact))
    if allow_shared_year and years == {str(y)} and re.search(rf"(?<!\d)0?{m}月0?{d}[日号]", compact):
        return True
    if re.search(rf"(?<!\d){y}[-/]0?{m}[-/]0?{d}(?!\d)", compact):
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
    return bool(
        re.search(
            r"\d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}|"
            r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|"
            r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}",
            excerpt,
        )
    )
