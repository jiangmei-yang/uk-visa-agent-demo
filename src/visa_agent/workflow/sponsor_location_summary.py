"""Applicant-facing facts only, never reviewer metadata or proof of legal status."""

from visa_agent.domain.models import Case
from visa_agent.domain.sponsor_location_review import (
    current_sponsor_location_statements,
    current_sponsor_location_values,
)


def sponsor_location_snapshot(case: Case) -> dict[str, object] | None:
    """Structured customer facts with explicit completeness, no review authority."""
    if case.profile.funding_source != "personal_sponsor" or not case.sponsor_location_statements:
        return None
    values = current_sponsor_location_values(case)
    try:
        sources = current_sponsor_location_statements(case)
    except ValueError:
        sources = current_sponsor_location_statements(case, selected={})
    return {
        "schema_version": 1,
        "basis": "applicant_reported_not_legal_status_verification",
        "sponsor_name": case.profile.sponsor_name,
        "sponsor_relationship": case.profile.sponsor_relationship,
        "dimensions": {dimension: {
            "state": "unknown" if not items else "conflicting" if len(items) > 1 else "reported",
            "value": next(iter(items)) if len(items) == 1 else None,
            "sources": [{"source_event_id": item.source_event_id, "source_excerpt": item.source_excerpt,
                         "context_question_event_id": item.context_question_event_id, "reported_value": item.value}
                        for item in sources if item.dimension == dimension],
        } for dimension, items in values.items()},
    }


def sponsor_location_summary_rows(case: Case) -> list[str]:
    if case.profile.funding_source != "personal_sponsor" or not case.sponsor_location_statements:
        return []
    zh = case.customer_language == "zh"
    values = current_sponsor_location_values(case)
    labels = {
        "residence": "资助人是否居住在英国（按你提供的信息）" if zh else "Sponsor lives in the UK (as reported by you)",
        "current_presence": "资助人目前是否人在英国（按你提供的信息）" if zh else "Sponsor currently physically in the UK (as reported by you)",
    }
    rows = []
    for dimension, label in labels.items():
        items = values[dimension]
        if not items:
            answer = "尚未确认" if zh else "Not yet confirmed"
        elif len(items) > 1:
            answer = "有不同说法，尚待核对" if zh else "Conflicting statements; needs clarification"
        else:
            answer = ("是" if True in items else "否") if zh else ("Yes" if True in items else "No")
        rows.append(f"- {label}{'：' if zh else ': '}{answer}")
    rows.append("- 上述所在地信息不代表已核实资助人的英国合法身份。" if zh else
                "- These location details do not establish that your sponsor has lawful UK status.")
    return rows
