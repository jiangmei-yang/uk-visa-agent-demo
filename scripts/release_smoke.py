"""Exercise a fresh, disposable local Demo through HTTP, without resetting any state.

No model, mailbox or real applicant data is used. Start a separate empty container
and explicitly pass its loopback URL. A used guided lab is rejected before writes.
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("Use the HTTP loopback origin of a disposable Demo container.")
    return value.rstrip("/")


def run(base_url: str) -> dict[str, Any]:
    base_url = local_url(base_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path: str, *, method: str = "GET", status: int = 200) -> bytes:
        req = urllib.request.Request(base_url + path, method=method)
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != status:
                raise RuntimeError(f"{method} {path}: expected {status}, got {response.status}")
            body = response.read(20_000_001)
        if len(body) > 20_000_000:
            raise RuntimeError("Unexpectedly large Demo response")
        return body

    def state() -> dict[str, Any]:
        return json.loads(request("/api/lab"))

    checks: dict[str, bool] = {}

    def check(name: str, passed: bool) -> None:
        checks[name] = bool(passed)
        if not passed:
            raise RuntimeError(f"Release check failed: {name}")

    check("healthy", json.loads(request("/health"))["status"] == "ok")
    request("/")
    request("/try")
    initial = state()
    check("fresh_synthetic_lab", initial["synthetic"] is True
          and initial["mode"] == "deterministic_fixture" and initial["processed_steps"] == 0
          and initial["conversation"] == [] and not initial["pack_available"])
    request("/api/lab/pack", status=404)
    request("/api/lab/steps/3", method="POST", status=409)
    check("cannot_skip_confirmation_steps", state() == initial)

    first = json.loads(request("/api/lab/steps/1", method="POST"))
    check("initial_evidence_blocks_delivery", first["processed_steps"] == 1
          and len(first["open_blockers"]) >= 2 and not first["gate"]["allowed"]
          and not first["pack_available"])
    request("/api/lab/pack", status=404)
    request("/api/lab/steps/1", method="POST", status=409)
    check("duplicate_click_preserves_state", state() == first)

    corrected = json.loads(request("/api/lab/steps/2", method="POST"))
    check("correction_still_requires_confirmation", corrected["processed_steps"] == 2
          and corrected["open_blockers"] == [] and not corrected["gate"]["allowed"]
          and not corrected["pack_available"])
    request("/api/lab/pack", status=404)

    final = json.loads(request("/api/lab/steps/3", method="POST"))
    check("confirmed_pack_released", final["processed_steps"] == 3 and final["pack_available"]
          and final["gate"]["allowed"] and all(final["gate"]["checks"].values()))
    content = request("/api/lab/pack")
    check("repeat_download_identical", request("/api/lab/pack") == content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        check("zip_crc_valid", archive.testzip() is None)
        check("pack_has_customer_deliverables", all(name in names for name in (
            "00_READ_ME_FIRST.pdf", "01_case_summary.pdf", "02_personalised_document_checklist.pdf",
            "03_document_index.pdf", "04_cover_letter_draft.pdf", "05_application_answers.json",
            "06_open_issues.pdf")) and any(name.startswith("supporting_documents/") for name in names))
        check("internal_audit_not_sent_to_customer", not any(name.endswith(suffix) for name in names
              for suffix in ("gate_result.json", "case_snapshot.json", "evidence_ledger.json")))
        answers = json.loads(archive.read("05_application_answers.json"))
        check("pack_matches_confirmed_case", answers["case_id"] == final["case_id"]
              and answers["status"] == "READY_FOR_HUMAN_REVIEW"
              and answers["submits_application"] is False
              and answers["profile"] == final["profile"] and len(answers["facts"]) > 0)
    return {"observed_at": datetime.now(UTC).isoformat(), "evidence": "offline HTTP fixture replay",
            "model_calls": 0, "mailbox_calls": 0, "checks": checks, "all_passed": all(checks.values()),
            "zip_sha256": hashlib.sha256(content).hexdigest(), "zip_members": names}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=local_url)
    parser.add_argument("--report", type=Path, help="Optional new report; never overwrites an existing file.")
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error("Report already exists; choose a new path.")
    report = run(args.base_url)
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        with args.report.open("x", encoding="utf-8") as output:
            output.write(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
