"""Local operator review of held Gmail updates. Never authenticates an adviser or approves a visa."""

import argparse
import json
from pathlib import Path

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import (
    queue_finalized_revision,
    queue_review_retry,
    review_fingerprint,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "retry", "revise"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--event")
    parser.add_argument("--fingerprint")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--include-held-updates", action="store_true",
                        help="Revise: explicitly review all currently held updates, selecting the earliest --event")
    args = parser.parse_args()
    database = args.state_dir / "sandbox.db"
    if not database.is_file():
        parser.error("Existing Gmail state database required")
    if args.action != "inspect" and not all((args.event, args.fingerprint, args.actor, args.reason)):
        parser.error("Retry/revise requires --event, --fingerprint, --actor and --reason")
    with exclusive_state(args.state_dir):
        store = SQLiteStore(database)
        try:
            if args.action == "inspect":
                case = store.get_case(args.case)
                if case is None:
                    parser.error("Case not found")
                print(json.dumps({"case_id": case.id, "status": case.status.value,
                    "delivery_revision": case.delivery_revision,
                    "review_reason": case.human_review_reason, "fingerprint": review_fingerprint(case),
                    "held_updates": store.list_held_inbound(case.id)}, ensure_ascii=False, indent=2))
            else:
                if args.action == "revise":
                    identifier = queue_finalized_revision(store, case_id=args.case, held_event_id=args.event,
                        expected_fingerprint=args.fingerprint, actor=args.actor, reason=args.reason,
                        include_held_updates=args.include_held_updates)
                else:
                    if args.include_held_updates:
                        parser.error("--include-held-updates applies only to revise")
                    identifier = queue_review_retry(store, case_id=args.case, held_event_id=args.event,
                        expected_fingerprint=args.fingerprint, actor=args.actor, reason=args.reason)
                print("Queued normal validation:", identifier)
                print("No document approved or message sent. The running serve worker will process it.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
