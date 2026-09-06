"""Render fictional cover-letter contexts through the production formatter."""

from pathlib import Path

from visa_agent.delivery.pack import _cover_letter_context, _pdf
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    target = Path("output/pdf/cover-letter-context/contexts.pdf")
    if target.exists():
        raise SystemExit("Refusing to overwrite previous visual evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    profiles = [
        CaseProfile(occupation_status="student", funding_source="self"),
        CaseProfile(occupation_status="employed", employer_name="eBay_RESEARCH Ltd", funding_source="self"),
        CaseProfile(occupation_status="self_employed", funding_source="personal_sponsor", sponsor_name="Mina Example"),
        CaseProfile(occupation_status="student", funding_source="employer_or_school"),
        CaseProfile(),
    ]
    rows = []
    for profile in profiles:
        case = Case(id="fictional", external_thread_id="fictional",
                    applicant_contact="fictional@example.test", policy_version="synthetic", profile=profile)
        rows.append(_cover_letter_context(case))
    _pdf(target, "Cover letter context checks", rows, "FICTIONAL QA - NOT A CUSTOMER PACK")
    print(target)


if __name__ == "__main__":
    main()
