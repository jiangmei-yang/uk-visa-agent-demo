"""Bounded location-name equivalence, not inference of citizenship or residence."""

import unicodedata

_ALIASES = {
    "中国": "china", "中國": "china", "中华人民共和国": "china", "中華人民共和國": "china",
    "香港": "hong kong", "中国香港": "hong kong", "中國香港": "hong kong",
    "新加坡": "singapore",
    "英国": "united kingdom", "英國": "united kingdom", "uk": "united kingdom",
}


def location_key(value: str | None) -> str | None:
    """Preserve unknown locations as distinct values; never fill a missing country."""
    if value is None:
        return None
    key = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return _ALIASES.get(key, key)
