"""Local operator review of held Gmail updates. Never authenticates an adviser or approves a visa."""

import argparse
import json
from pathlib import Path

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.review import queue_review_retry, review_fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "retry"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--event")
    parser.add_argument("--fingerprint")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    args = parser.parse_args()
    database = args.state_dir / "sandbox.db"
    if not database.is_file():
        parser.error("Existing Gmail state database required")
    if args.action == "retry" and not all((args.event, args.fingerprint, args.actor, args.reason)):
        parser.error("Retry requires --event, --fingerprint, --actor and --reason")
    with exclusive_state(args.state_dir):
        store = SQLiteStore(database)
        try:
            if args.action == "inspect":
                case = store.get_case(args.case)
                if case is None:
                    parser.error("Case not found")
                print(json.dumps({"case_id": case.id, "status": case.status.value,
                    "review_reason": case.human_review_reason, "fingerprint": review_fingerprint(case),
                    "held_updates": store.list_held_inbound(case.id)}, ensure_ascii=False, indent=2))
            else:
                identifier = queue_review_retry(store, case_id=args.case, held_event_id=args.event,
                    expected_fingerprint=args.fingerprint, actor=args.actor, reason=args.reason)
                print("Queued normal validation:", identifier)
                print("No document approved or message sent. The running serve worker will process it.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
