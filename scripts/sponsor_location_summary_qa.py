"""Fictional production-format PDF for distinct sponsor-location display states."""

from pathlib import Path

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements


def main() -> None:
    target = Path("output/pdf/sponsor-location-dimensions/summary.pdf")
    if target.exists():
        raise SystemExit("Refusing to overwrite existing visual evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for state in ("Different dimensions", "Missing current presence", "Conflicting residence", "Replaced sponsor"):
        case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
            policy_version="synthetic", customer_language="zh", profile=CaseProfile(funding_source="personal_sponsor",
                sponsor_name="Mina Example", sponsor_relationship="mother", sponsor_is_in_uk=False))
        case.sponsor_location_statements = parse_sponsor_location_statements(
            "My sponsor does not live in the UK but is in the UK now.", source_event_id="private-source",
            sponsor_name="Mina Example", sponsor_relationship="mother")
        if state == "Missing current presence":
            case.sponsor_location_statements.pop()
        elif state == "Conflicting residence":
            case.sponsor_location_statements.append(case.sponsor_location_statements[0].model_copy(update={"value": True}))
        elif state == "Replaced sponsor":
            case.sponsor_location_epoch += 1
        rows.append(state)
        rows.extend(row for row in _profile_rows(case) if row.startswith(("Sponsor lives", "Sponsor currently", "These location")))
    _pdf(target, "Sponsor location dimensions", rows, "FICTIONAL QA - NOT A CUSTOMER PACK", compact=True)
    print(target)


if __name__ == "__main__":
    main()
