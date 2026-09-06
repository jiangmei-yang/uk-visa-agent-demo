"""Generate a fictional visual check using the production profile formatter."""

from pathlib import Path

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    target = Path("output/pdf/employer-output-states/summary.pdf")
    if target.exists():
        raise SystemExit("Refusing to overwrite previous visual evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, occupation, deferred in (
        ("Student", "student", False), ("Self-employed", "self_employed", False),
        ("Occupation unknown", None, False), ("Employed - missing", "employed", False),
        ("Employed - awaiting HR", "employed", True),
    ):
        case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
                    policy_version="synthetic", profile=CaseProfile(occupation_status=occupation))
        if deferred:
            case.profile.employer_name = "Northstar Ltd"
            case.deferred_fields = ["employer_phone"]
            case.employer_detail_deferrals = [{"field": "employer_phone", "employer_name": "Northstar Ltd",
                                              "source_event_id": "fictional-event"}]
        rows.append(label)
        rows.extend(row for row in _profile_rows(case)
                    if row.startswith(("Current employer:", "Employer address:", "Employer contact number:")))
    _pdf(target, "Employer output states", rows, "FICTIONAL QA - NOT A CUSTOMER PACK", compact=True)
    print(target)


if __name__ == "__main__":
    main()
