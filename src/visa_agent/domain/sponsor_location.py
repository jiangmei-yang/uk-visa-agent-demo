"""Separate source-bound residence and presence observations, never legal status.

This bounded parser only accepts explicit current, single-sponsor statements.
It deliberately has no conversion from the historical sponsor_is_in_uk boolean.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LocationDimension = Literal["residence", "current_presence"]


class SponsorLocationStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: LocationDimension
    value: bool
    source_event_id: str = Field(min_length=1)
    source_excerpt: str = Field(min_length=1)
    # Caller-supplied current identity binding, not a name inferred from location.
    sponsor_name: str | None = None
    sponsor_relationship: str | None = None
    identity_epoch: int = 0
    context_question_event_id: str | None = None


_EN_OWNER = re.compile(r"my sponsor\s+(.+)", re.I)
_ZH_OWNER = re.compile(r"我的?资助人(.+)")
_EN_PREDICATES: tuple[tuple[LocationDimension, bool, str], ...] = (
    ("residence", True, r"(?:currently\s+)?(?:lives|resides|is living|is residing) in (?:the )?UK"),
    ("residence", False, r"(?:currently\s+)?(?:does not|doesn't|doesn’t) (?:live|reside) in (?:the )?UK"),
    ("current_presence", True, r"is (?:currently |now )?in (?:the )?UK(?: now| at the moment)?"),
    ("current_presence", False, r"(?:is not|isn't|isn’t) (?:currently |now )?in (?:the )?UK(?: now| at the moment)?"),
)
_ZH_PREDICATES: tuple[tuple[LocationDimension, bool, str], ...] = (
    ("residence", True, r"(?:目前)?(?:住在|居住在)英国"),
    ("residence", False, r"(?:目前)?(?:不住在|没有住在|不居住在)英国"),
    ("current_presence", True, r"(?:目前|现在)?在英国"),
    ("current_presence", False, r"(?:目前|现在)?不在英国"),
)


def parse_sponsor_location_statements(
    body: str, *, source_event_id: str, sponsor_name: str | None = None,
    sponsor_relationship: str | None = None,
) -> list[SponsorLocationStatement]:
    """Parse whole explicit statements, retaining both dimensions and conflicts.

    Conjuncts can inherit only the same explicit singular sponsor within the same
    sentence. Unsupported framing invalidates that entire sentence, not just its
    inconvenient clause. Source excerpts retain the original owner and wording.
    Callers must pass latest unquoted email text and apply normal event ordering.
    """
    if not source_event_id.strip():
        raise ValueError("A source event is required")
    # Do not turn quoted/reported examples into live facts. This deliberately
    # leaves mixed quoted/unquoted messages for a richer parser/clarification.
    if re.search(r'[>"“”‘「」『』?？]', body):
        return []
    result: list[SponsorLocationStatement] = []
    for raw in re.split(r"[。\n]|\.(?:\s|$)", body):
        sentence = raw.strip()
        if not sentence:
            continue
        en = _EN_OWNER.fullmatch(sentence)
        zh = _ZH_OWNER.fullmatch(sentence)
        if en:
            predicates = _EN_PREDICATES
            clauses = re.split(r"\s*,?\s+\b(?:but|and)\b\s+", en.group(1), flags=re.I)
        elif zh:
            predicates = _ZH_PREDICATES
            clauses = re.split(r"[，,]?(?:但是|但|而且|并且|而)", zh.group(1))
        else:
            continue
        parsed: list[tuple[LocationDimension, bool]] = []
        for clause in clauses:
            clause = clause.strip()
            # Only an exact repeated owner or same-sentence pronoun is allowed.
            if en:
                clause = re.sub(r"^(?:my sponsor|he|she)\s+", "", clause, flags=re.I)
            else:
                clause = re.sub(r"^(?:我的?资助人|他|她)", "", clause)
            match = next(((dimension, value) for dimension, value, pattern in predicates
                          if re.fullmatch(pattern, clause, re.I)), None)
            if match is None:
                parsed = []
                break
            parsed.append(match)
        result.extend(SponsorLocationStatement(dimension=dimension, value=value,
            source_event_id=source_event_id, source_excerpt=sentence,
            sponsor_name=sponsor_name, sponsor_relationship=sponsor_relationship)
            for dimension, value in parsed)
    return result


def conflicting_location_dimensions(statements: list[SponsorLocationStatement]) -> set[LocationDimension]:
    """Detect contradictory proposals within each identity; never pick a winner."""
    seen: dict[tuple[str | None, str | None, LocationDimension], set[bool]] = {}
    for item in statements:
        seen.setdefault((item.sponsor_name, item.sponsor_relationship, item.dimension), set()).add(item.value)
    return {key[2] for key, values in seen.items() if len(values) > 1}
