"""Exercise an explicitly isolated synthetic web lab over real HTTP, not Gmail.

Requires a caller-created disposable instance. Never point this at a customer's
console: reset replaces that instance's synthetic lab. No API keys are used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


def isolated_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.port == 8000 or parsed.path not in {"", "/"} or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        raise ValueError("Use an explicit isolated 127.0.0.1 HTTP port, not the normal 8000 service")
    return value.rstrip("/")


def probe(base_url: str) -> dict[str, object]:
    base_url = isolated_url(base_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path: str, method: str = "GET") -> tuple[int, bytes]:
        req = urllib.request.Request(base_url + path, method=method)
        try:
            with opener.open(req, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def state(path: str) -> dict:
        status, body = request(path, "POST")
        if status != 200:
            raise ValueError(f"Synthetic lab operation {path} returned HTTP {status}")
        return json.loads(body)

    checks = {}
    checks["health"] = request("/health")[0] == 200
    checks["home_and_lab"] = all(request(path)[0] == 200 for path in ("/", "/try"))
    initial = state("/api/lab/reset")
    checks["fresh_lab_withholds_pack"] = initial["processed_steps"] == 0 and not initial["pack_available"]
    checks["fresh_download_blocked"] = request("/api/lab/pack")[0] == 404
    first = state("/api/lab/steps/1")
    checks["initial_evidence_blocks"] = not first["pack_available"] and {
        item["code"] for item in first["open_blockers"]} == {"DATE_CONFLICT", "MISSING_CERTIFIED_TRANSLATION"}
    checks["out_of_order_confirmation_blocked"] = request("/api/lab/steps/3", "POST")[0] == 409
    second = state("/api/lab/steps/2")
    checks["correction_still_needs_confirmation"] = (not second["open_blockers"] and not second["pack_available"]
        and not second["gate"]["checks"]["applicant_explicitly_confirmed_final_summary"])
    final = state("/api/lab/steps/3")
    checks["confirmed_synthetic_case_ready"] = final["pack_available"] and all(final["gate"]["checks"].values())
    status, archive = request("/api/lab/pack")
    checks["download_available"] = status == 200
    with zipfile.ZipFile(io.BytesIO(archive)) as pack:
        names = pack.namelist()
        checks["zip_integrity"] = pack.testzip() is None
        checks["expected_deliverables"] = all(name in names for name in (
            "00_READ_ME_FIRST.pdf", "01_case_summary.pdf", "02_personalised_document_checklist.pdf",
            "03_document_index.pdf", "04_cover_letter_draft.pdf", "05_application_answers.json", "06_open_issues.pdf"))
        checks["has_supporting_documents"] = any(name.startswith("supporting_documents/") for name in names)
    return {"scope": "isolated Docker synthetic guided lab over HTTP; no model or mailbox; not visual or human usability QA",
            "base_url": base_url, "checks": checks, "all_passed": all(checks.values()),
            "archive_sha256": hashlib.sha256(archive).hexdigest(), "archive_entries": names}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allow-synthetic-state-changes", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_synthetic_state_changes:
        parser.error("Explicit --allow-synthetic-state-changes is required for the isolated lab reset")
    if args.output.exists():
        parser.error("Do not overwrite previous acceptance evidence")
    result = probe(args.base_url)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
    if not result["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
