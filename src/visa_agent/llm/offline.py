from __future__ import annotations

import re

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.ports import CasePatch, FactUpdate

BLOCK = re.compile(r"<!-- DEMO_FACTS\n(.*?)\n-->", re.DOTALL)

FUNDING_EXCERPTS = {
    "self": re.compile(
        r"\bI\s+(?:will\s+)?(?:pay|cover|fund)\s+(?:for\s+)?(?:my|the)\b[^.!?\n]*|"
        r"我(?:会|将)?(?:自费|自己|本人).{0,24}(?:承担|支付|负担)",
        re.I,
    ),
    "employer_or_school": re.compile(
        r"\b(?:my|our)\s+(?:university|school|employer|company)\s+"
        r"(?:will\s+)?(?:pay|cover|fund)\b[^.!?\n]*|"
        r"(?:我的)?(?:学校|大学|公司|雇主|单位).{0,24}(?:承担|支付|资助|负担)",
        re.I,
    ),
    "personal_sponsor": re.compile(
        r"\bmy\s+(?:mother|father|parent|sister|brother|spouse|partner|friend|relative)\b"
        r"[^.!?\n]{0,48}\b(?:sponsor|fund|pay|cover)\b[^.!?\n]*|"
        r"我的?(?:母亲|父亲|父母|姐姐|妹妹|哥哥|弟弟|配偶|朋友|亲属).{0,32}"
        r"(?:资助|承担|支付|负担)",
        re.I,
    ),
}


class OfflineFixtureLLM:
    """Deterministic substitute used only for reproducible synthetic fixtures."""

    version = "offline-fixture-v1"

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        match = BLOCK.search(event.body)
        if not match:
            return CasePatch(updates=[], ambiguities=[])
        updates: list[FactUpdate] = []
        for line in match.group(1).splitlines():
            field, value = line.split("=", 1)
            field = field.strip()
            text_value = value.strip()
            parsed: str | int | bool
            if text_value in {"true", "false"}:
                parsed = text_value == "true"
            elif text_value.isdigit():
                parsed = int(text_value)
            else:
                parsed = text_value
            source_excerpt = line.strip()
            if (
                field == "funding_source"
                and (pattern := FUNDING_EXCERPTS.get(text_value))
                and (funding_match := pattern.search(event.body))
            ):
                # The hidden block selects a deterministic fixture value, but
                # the production guard still requires visible payer evidence.
                # Use an exact visible sentence fragment when the fixture has
                # one; otherwise leave the metadata excerpt to be rejected.
                source_excerpt = funding_match.group(0).strip(" ,。、")
            updates.append(
                FactUpdate(
                    field=field,
                    value=parsed,
                    source_excerpt=source_excerpt,
                    confidence=1.0,
                )
            )
        return CasePatch(updates=updates, ambiguities=[])

    def render_message(self, case: Case, plan: str) -> str:
        if plan == "blocked":
            issue_titles = "; ".join(issue.title for issue in case.open_blockers())
            return (
                "Thank you — I have recorded the documents. I cannot prepare the review pack yet. "
                f"Please resolve: {issue_titles}. This service prepares documents and does not "
                "provide a legal conclusion or submit an application."
            )
        if plan == "awaiting_confirmation":
            return (
                "The current checks no longer show a document blocker. Please review the final "
                "facts summary and reply with the exact confirmation requested."
            )
        return "Your review pack has been prepared for human review. This is not an approval prediction."
