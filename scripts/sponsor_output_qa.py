"""Render fictional sponsor applicability states with the production formatter."""

from pathlib import Path

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    target = Path("output/pdf/sponsor-output-states/summary.pdf")
    if target.exists():
        raise SystemExit("Refusing to overwrite previous visual evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, funding, supplied in (
        ("Funding unknown", None, False),
        ("Self-funded", "self", False),
        ("Employer or school funded", "employer_or_school", False),
        ("Personal sponsor - missing details", "personal_sponsor", False),
        ("Legacy supplied details - retain for review", "self", True),
    ):
        profile = CaseProfile(funding_source=funding)
        if supplied:
            profile.sponsor_name = "Mina Example"
            profile.sponsor_address = "Unit_B, Example Road"
            profile.sponsor_relationship = "parent"
            profile.sponsor_is_in_uk = False
        case = Case(id="fictional", external_thread_id="fictional",
                    applicant_contact="fictional@example.test", policy_version="synthetic",
                    profile=profile)
        rows.append(label)
        rows.extend(row for row in _profile_rows(case) if row.startswith("Sponsor "))
    _pdf(target, "Sponsor output states", rows, "FICTIONAL QA - NOT A CUSTOMER PACK", compact=True)
    print(target)


if __name__ == "__main__":
    main()
