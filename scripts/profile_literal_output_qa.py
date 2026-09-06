"""Fictional literal-value PDF evidence; never use a real customer profile."""

from pathlib import Path

from pypdf import PdfReader

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    path = Path("output/pdf/profile-literal-values/summary.pdf")
    if path.exists():
        raise SystemExit("Refusing to overwrite existing visual evidence")
    values = {"employer_name": "ACME_Labs LTD", "employer_address": "Unit A_B, 12 Sample Road",
              "current_address": "Building A_B, 34 Sample Road", "uk_accommodation": "Hotel A_B London",
              "full_name": "Alex_SAMPLE", "sponsor_name": "Mei_SAMPLE",
              "sponsor_address": "Unit C_D, 56 Sample Road"}
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
                policy_version="synthetic", profile=CaseProfile(funding_source="personal_sponsor", **values))
    rows = [row for row in _profile_rows(case) if any(row.endswith(": " + value) for value in values.values())]
    path.parent.mkdir(parents=True, exist_ok=True)
    _pdf(path, "Literal profile values", rows, "FICTIONAL QA - NOT A CUSTOMER PACK")
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    assert all(value in text for value in values.values()), "PDF must retain every literal value"
    print("All seven literal values preserved:", path)


if __name__ == "__main__":
    main()
