"""Recover an omitted explicit first-person holiday statement, never route approval."""

import re

from visa_agent.workflow.advice_preferences import _current_clauses


def explicit_holiday_statement(body: str) -> str | None:
    clauses = _current_clauses(body)
    # Mixed purposes need interpretation; this fallback cannot select a winner.
    if any(re.search(r"\b(?:business|conference|work|study|family visit)\b|商务|会议|工作|探亲", c, re.I) for c in clauses):
        return None
    matches = [clause.strip() for clause in clauses if re.fullmatch(
        r"I(?:'m| am) preparing for a UK (?:holiday|vacation)|"
        r"I(?:'m| am) planning a (?:holiday|vacation) in the UK|"
        r"我(?:计划|打算|准备)去英国(?:旅游|度假)", clause.strip(), re.I)]
    return matches[0] if len(matches) == 1 else None
