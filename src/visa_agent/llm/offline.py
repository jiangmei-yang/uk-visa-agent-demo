from __future__ import annotations

import re

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.ports import CasePatch, FactUpdate

BLOCK = re.compile(r"<!-- DEMO_FACTS\n(.*?)\n-->", re.DOTALL)


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
            text_value = value.strip()
            parsed: str | int | bool
            if text_value in {"true", "false"}:
                parsed = text_value == "true"
            elif text_value.isdigit():
                parsed = int(text_value)
            else:
                parsed = text_value
            updates.append(
                FactUpdate(
                    field=field.strip(),
                    value=parsed,
                    source_excerpt=line.strip(),
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
