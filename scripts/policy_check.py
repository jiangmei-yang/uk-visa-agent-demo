from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from visa_agent.domain.policy import load_policy


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail when the committed policy review is overdue.")
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    policy = load_policy(args.policy)
    if not policy.is_current(args.date):
        raise SystemExit(
            f"Policy {policy.version} is outside its reviewed window on {args.date}. "
            f"Recheck the official sources and update {args.policy}."
        )
    print(
        f"Policy {policy.version} is within its reviewed window on {args.date}; "
        f"next review boundary: {policy.review_after}."
    )


if __name__ == "__main__":
    main()
