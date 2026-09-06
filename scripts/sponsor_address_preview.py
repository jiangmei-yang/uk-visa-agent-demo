"""Production renderer QA for fictional personal-sponsor address states."""

import argparse
from pathlib import Path

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new path to preserve previous QA evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for title, funding, address in [
        ("Explicit personal sponsor address", "personal_sponsor", "12 Example Road, Hong Kong"),
        ("Original Chinese spelling", "personal_sponsor", "深圳市虚构示例路12号测试大厦5层501室"),
        ("Not yet supplied - never fabricated", "personal_sponsor", None),
        ("Self-funded - sponsor details not applicable", "self", None),
    ]:
        case = Case(id="fictional-sponsor-qa", external_thread_id="fictional-sponsor-qa",
                    applicant_contact="fictional@example.test", policy_version="2026-02-25",
                    profile=CaseProfile(funding_source=funding, sponsor_address=address))
        rows.append(title)
        rows.extend(row for row in _profile_rows(case) if row.startswith(("Sponsor address:", "Current home address:")))
    _pdf(args.output, "Sponsor address - fictional QA", rows,
         "FICTIONAL RENDERING QA - NOT A COMPLETED APPLICATION")


if __name__ == "__main__":
    main()
