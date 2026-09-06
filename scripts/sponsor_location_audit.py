"""Read-only fictional location/residence ambiguity probe; no model or mailbox."""

import json
from datetime import UTC, datetime

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


def main() -> None:
    results = []
    for body in (
        "My sponsor is in the UK.",
        "My sponsor does not live in the UK.",
        "My sponsor does not live in the UK but is in the UK now.",
        "My sponsor lives in the UK but is not in the UK now.",
        "我的资助人不住在英国，但现在在英国。",
        "我的资助人住在英国，但现在不在英国。",
    ):
        for proposed in (False, True):
            event = InboundEvent(id="fictional-location", external_thread_id="fictional-location",
                sender="fictional@example.test", subject="Fictional location probe", body=body,
                received_at=datetime(2026, 9, 7, tzinfo=UTC),
                known_profile={"funding_source": "personal_sponsor", "sponsor_name": "Mina Example",
                               "sponsor_relationship": "mother", "sponsor_is_in_uk": False},
                requested_fields=["sponsor_is_in_uk"])
            patch = validate_case_patch(event, CasePatch(updates=[FactUpdate(
                field="sponsor_is_in_uk", value=proposed, source_excerpt=body, confidence=1)], ambiguities=[]))
            results.append({"body": body, "proposed": proposed,
                "accepted": [item.value for item in patch.updates],
                "requires_human_review": patch.requires_human_review,
                "ambiguities": patch.ambiguities})
    print(json.dumps({"scope": "fictional source-guard audit only; no gate or delivery execution",
                      "model_calls": 0, "mailbox_calls": 0, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
