"""Read-only live rejection probe; never loads or changes the owner's OAuth credentials."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from visa_agent.channels.gmail import _map_gmail_error
from visa_agent.channels.outbound import PermanentChannelError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    request = Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": "Bearer deliberately-invalid-visa-demo-probe"},
        method="GET",
    )
    status: int | None = None
    try:
        with urlopen(request, timeout=15) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    mapped = RuntimeError("No provider body or credentials recorded")
    mapped.resp = SimpleNamespace(status=status)  # type: ignore[attr-defined]
    classification = _map_gmail_error(mapped)
    passed = status == 401 and isinstance(classification, PermanentChannelError)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "evidence_class": "live provider read-only negative request",
        "endpoint": "GET /gmail/v1/users/me/profile",
        "credentials": "deliberately invalid token; no saved credentials loaded",
        "http_status": status,
        "classification": type(classification).__name__,
        "passed": passed,
        "limitations": [
            "Not an OAuth revocation or refresh-token recovery test.",
            "No message sent, no quota exhaustion and no live 429/5xx induced.",
            "Does not validate dispatch or recipient delivery.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit("Live rejection probe did not meet its narrow acceptance criterion")


if __name__ == "__main__":
    main()
