"""One synthetic renderer QA page: supplied, deferred and cleared residence data."""

import argparse
from pathlib import Path

from visa_agent.delivery.pack import _pdf, _profile_rows
from visa_agent.domain.models import Case, CaseProfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new filename; retain prior QA evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for title, value, deferred, address in [
        ("Supplied approximate duration", "about two years", False, "1 Example Road, Hong Kong"),
        ("Supplied Chinese duration - original precision", "大概两年半", False, "香港虚构示例路1号"),
        ("Customer needs to check", None, True, "1 Example Road, Hong Kong"),
        ("Changed home - old uncertainty does not transfer", None, True, "2 Example Road, Hong Kong"),
    ]:
        case = Case(id="fictional-duration-render", external_thread_id="fictional-duration-render",
            applicant_contact="fictional@example.test", policy_version="2026-02-25",
            profile=CaseProfile(current_address=address, current_address_duration=value),
            deferred_fields=["current_address_duration"] if deferred else [],
            residence_duration_deferrals=[{"address": "1 Example Road, Hong Kong", "source_event_id": "fictional-qa",
                "question_event_id": "fictional-question", "source_excerpt": "I need to check."}] if deferred else [])
        rows.append(title)
        rows.extend(row for row in _profile_rows(case) if row.startswith(("Current home address:", "Time living at current home:")))
    _pdf(args.output, "Residence details - fictional QA", rows,
         "FICTIONAL RENDERING QA - NOT A COMPLETED APPLICATION")


if __name__ == "__main__":
    main()
