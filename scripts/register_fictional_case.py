"""Register an independent fictional Gmail case; no mail, model or approval writes."""

import argparse
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NoReturn, cast

from visa_agent.channels.runtime_lock import exclusive_state
from visa_agent.documents.simulation import read_simulation_document
from visa_agent.domain.simulation import SpecimenEntry
from visa_agent.storage.simulation import register_simulation
from visa_agent.storage.sqlite import SQLiteStore


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case", "thread", "sender", "operator", "reason"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--specimen", action="append", required=True, help="passport=PATH or status_document=PATH")
    args = parser.parse_args(argv)
    database = args.state_dir / "sandbox.db"
    if not database.is_file():
        parser.error("An existing independent Gmail case database is required")
    entries = []
    paths = []
    for item in args.specimen:
        kind, separator, raw_path = item.partition("=")
        if not separator or kind not in {"passport", "status_document"}:
            parser.error("Only explicit identity specimen kinds can be registered")
        path = Path(raw_path)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            parser.error("Each identity specimen must be an existing PDF of at most 2 MB")
        paths.append(path)
        entries.append(SpecimenEntry(filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                     kind=cast(Literal["passport", "status_document"], kind)))
    with exclusive_state(args.state_dir):
        store = SQLiteStore(database)
        try:
            # Roll back registration if any specimen is malformed or unmarked.
            with store.atomic_write():
                registration = register_simulation(store, case_id=args.case, external_thread_id=args.thread,
                    applicant_contact=args.sender, operator=args.operator, reason=args.reason, specimens=tuple(entries))

                def forbidden(path: Path) -> NoReturn:
                    raise ValueError("Registration cannot use an ordinary reader or model")

                for path in paths:
                    read_simulation_document(path, registration, forbidden)
            print(f"Registered fictional case {registration.case_id}; no case approval or email sent.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
