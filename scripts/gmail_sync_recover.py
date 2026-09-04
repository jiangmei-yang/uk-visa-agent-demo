"""Request a scoped discovery rescan without clearing case, candidate, or send history."""

import argparse
import json
from pathlib import Path

from visa_agent.channels.gmail_sync import GmailSyncJournal
from visa_agent.channels.runtime_lock import exclusive_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "rescan"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    args = parser.parse_args()
    database = args.state_dir / "sync.db"
    binding = args.state_dir / "binding.json"
    if not database.is_file() or not binding.is_file():
        parser.error("Existing sync.db and binding.json are required; no state was created")
    if args.action == "rescan" and (args.expected_revision is None or not args.actor or not args.reason):
        parser.error("Rescan requires --expected-revision, --actor and --reason")
    with exclusive_state(args.state_dir):
        journal = GmailSyncJournal(database, json.dumps(json.loads(binding.read_text()), sort_keys=True))
        try:
            checkpoint = journal.checkpoint()
            if args.action == "rescan":
                if checkpoint is None or checkpoint.revision != args.expected_revision:
                    parser.error("Checkpoint changed or is absent; inspect again before requesting a rescan")
                checkpoint = journal.request_rescan(checkpoint, actor=args.actor, reason=args.reason)
            print(json.dumps({"phase": checkpoint.phase if checkpoint else "not_started",
                "revision": checkpoint.revision if checkpoint else None,
                "pending_candidates": len(journal.pending_ids()),
                "recovery_actions": journal.connection.execute("SELECT COUNT(*) FROM recovery_actions").fetchone()[0]}))
            unavailable = journal.unavailable_metadata()
            if unavailable:
                print(json.dumps({"unavailable_metadata_count": len(unavailable),
                                  "unavailable_metadata_sample": unavailable[:20]}))
                print("These candidates remain pending and block dispatch. A 404 is not proof of deletion; "
                      "investigate the scoped message/access without clearing its history.")
            if args.action == "rescan":
                print("Rescan requested. The next running worker cycle will fetch a new anchor and scan the same scope.")
                print("No candidate cleared, case changed, mail read or message sent by this command.")
        finally:
            journal.close()


if __name__ == "__main__":
    main()
