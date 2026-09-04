"""Isolated OAuth refresh/rejection probe: token endpoint and Gmail profile only.

Never revokes authorization, opens consent, reads messages or uses the live token as
a write target. Local expiry is deliberately forced; it is not natural expiry.
Optional Gmail SDK dependencies are loaded only when a probe is executed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parseaddr
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from visa_agent.channels import gmail_auth

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
INVALID_REFRESH_TOKEN = "deliberately-invalid-isolated-visa-refresh-probe"
EXPIRED_ACCESS_TOKEN = "deliberately-expired-isolated-visa-access-probe"
ALLOWED_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_request",
                  "invalid_scope", "access_denied", "temporarily_unavailable", "server_error"}
ALLOWED_EXCEPTION_TYPES = {"RefreshError", "TransportError", "HttpError", "Timeout",
                           "ConnectTimeout", "ReadTimeout", "TimeoutError", "ConnectionError",
                           "OSError", "ValueError", "RuntimeError", "FileNotFoundError",
                           "PermissionError", "JSONDecodeError", "ModuleNotFoundError"}


def _safe_exception(error):
    name = type(error).__name__
    return name if name in ALLOWED_EXCEPTION_TYPES else "UnexpectedError"


class BoundedTransport:
    """Probe-only wrapper: one refresh and one profile operation per stage.

    SDK timeouts are configured, redirects and upper-layer retries/implicit refresh
    are disabled. Counters are wrapper operations, not a wire trace: httplib2 may
    internally retry a connection. This is not a hard wall-clock deadline. The normal
    production factory still loads, refreshes and atomically persists the copy.
    """

    def __init__(self, timeout):
        self.timeout = timeout
        self.token_requests = 0
        self.profile_requests = 0
        self.token_status = None
        self.profile_status = None
        self.oauth_error = None
        self._request = None
        self._http = None
        self._credentials = None

    def refresh_request(self, url, method="GET", body=None, headers=None, **kwargs):
        if url != TOKEN_ENDPOINT or method.upper() != "POST" or self.token_requests:
            raise RuntimeError("Probe blocked a non-allowlisted or repeated refresh request")
        self.token_requests += 1
        # Request's underlying Session receives allow_redirects=False. Do not copy
        # caller-provided retries/timeouts or log request bodies containing secrets.
        response = self._request(url, method="POST", body=body, headers=headers,
                                 timeout=self.timeout, allow_redirects=False)
        status = response.status
        self.token_status = status if type(status) is int and 100 <= status <= 599 else None
        if self.token_status != 200:
            try:
                value = json.loads(response.data)
                code = value.get("error") if isinstance(value, dict) else None
                if isinstance(code, str) and code in ALLOWED_ERRORS:
                    self.oauth_error = code
            except (ValueError, TypeError, UnicodeError):
                pass
        return response

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        parsed = urlsplit(uri)
        if (parsed.scheme != "https" or parsed.netloc != "gmail.googleapis.com"
                or parsed.path != "/gmail/v1/users/me/profile" or parsed.fragment
                or parse_qs(parsed.query, keep_blank_values=True) not in ({}, {"alt": ["json"]})
                or method.upper() != "GET" or body is not None or self.profile_requests):
            raise RuntimeError("Probe blocked a non-allowlisted or repeated Gmail request")
        if self._credentials is None or not self._credentials.valid:
            raise RuntimeError("Probe requires the explicitly refreshed credentials")
        self.profile_requests += 1
        request_headers = dict(headers or {})
        # apply() adds authorization without before_request()'s implicit refresh.
        self._credentials.apply(request_headers)
        response, content = self._http.request(uri, method="GET", headers=request_headers,
                                               redirections=0)
        status = response.status
        self.profile_status = status if type(status) is int and 100 <= status <= 599 else None
        return response, content

    @contextmanager
    def install(self):
        original_import = gmail_auth.import_module
        try:
            self._request = import_module("google.auth.transport.requests").Request()
            discovery = import_module("googleapiclient.discovery")
            self._http = import_module("httplib2").Http(timeout=self.timeout)

            def build(service, version, *, credentials, **kwargs):
                if service != "gmail" or version != "v1" or not credentials.valid:
                    raise RuntimeError("Unexpected production factory result")
                self._credentials = credentials
                # Static bundled discovery prevents an extra discovery/network request.
                return discovery.build(service, version, http=self, cache_discovery=False,
                                       static_discovery=True, num_retries=0)

            def resolver(name):
                if name == "google.auth.transport.requests":
                    return SimpleNamespace(Request=lambda: self.refresh_request)
                if name == "googleapiclient.discovery":
                    return SimpleNamespace(build=build)
                return original_import(name)

            with patch.object(gmail_auth, "import_module", resolver):
                yield self
        finally:
            try:
                if self._http is not None:
                    self._http.close()
            finally:
                session = getattr(self._request, "session", None)
                if session is not None:
                    session.close()


def _stage(name, client_copy, token_copy, source_token, mailbox, timeout):
    invalid = name == "invalid_refresh_rejected"
    value = dict(source_token)
    value.update(expiry="2000-01-01T00:00:00Z", token=EXPIRED_ACCESS_TOKEN)
    if invalid:
        value["refresh_token"] = INVALID_REFRESH_TOKEN
    gmail_auth._write_private_token(token_copy, json.dumps(value))
    before = token_copy.read_bytes()
    transport = BoundedTransport(timeout)
    result = {"stage": name, "forced_local_expiry": True, "mailbox_matches": False,
              "error_type": None, "passed": False}
    try:
        with transport.install():
            service = gmail_auth.build_gmail_service(client_copy, token_copy, interactive=False)
            if invalid:
                raise RuntimeError("Invalid refresh token unexpectedly accepted")
            profile = service.users().getProfile(userId="me").execute(num_retries=0)
            result["mailbox_matches"] = (
                isinstance(profile, dict) and profile.get("emailAddress", "").casefold()
                == mailbox.casefold()
            )
    except Exception as error:
        result["error_type"] = _safe_exception(error)
    result.update(token_requests=transport.token_requests, token_http_status=transport.token_status,
                  profile_requests=transport.profile_requests,
                  profile_http_status=transport.profile_status,
                  oauth_error=transport.oauth_error,
                  isolated_token_changed=token_copy.read_bytes() != before)
    if invalid:
        result["passed"] = (result["error_type"] == "RefreshError"
                            and result["token_requests"] == 1
                            and result["token_http_status"] == 400
                            and result["oauth_error"] == "invalid_grant"
                            and result["profile_requests"] == 0
                            and not result["isolated_token_changed"])
    else:
        result["passed"] = (result["error_type"] is None and result["token_requests"] == 1
                            and result["token_http_status"] == 200
                            and result["profile_requests"] == 1
                            and result["profile_http_status"] == 200
                            and result["mailbox_matches"] and result["isolated_token_changed"])
    return result


def run_probe(*, credentials, token, mailbox, report_path, timeout=20):
    """Return sanitized evidence; reserve output before reading any credentials."""
    if (parseaddr(mailbox)[1] != mailbox or "@" not in mailbox
            or any(character.isspace() or character in '\"()' for character in mailbox)):
        raise ValueError("Supply a single plain mailbox address")
    if not 1 <= timeout <= 60:
        raise ValueError("Timeout must be between 1 and 60 seconds")
    client_location, token_location = credentials.resolve(), token.resolve()
    output_location = report_path.resolve()
    if (client_location == token_location
            or output_location in (client_location, token_location)):
        raise ValueError("Input credentials and evidence require three distinct paths")
    if ".secrets" in output_location.parts:
        raise ValueError("Write evidence outside the private secrets directory")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "evidence_class": "isolated forced local expiry, real refresh and read-only profile",
        "state": "running", "timeout_seconds_per_request": timeout,
        "stages": [], "original_token_unchanged": None, "original_client_unchanged": None,
        "interactive_consent": False, "revocation_requests": 0, "message_api_calls": 0,
        "sends": 0, "passed": False,
        "limitations": [
            "Local expiry is forced; no naturally expired live-worker credential was exercised.",
            "Invalid isolated refresh token is not revocation of the authorized account.",
            "Recovery uses an isolated valid credential copy, not a live worker recovery.",
            "No mail bodies, dispatch, recipient delivery, live database or permission changes.",
            "Original-file comparison detects concurrent changes; it does not lock the live worker.",
        ],
    }
    original_token = None
    original_client = None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        def checkpoint():
            handle.seek(0)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())

        checkpoint()
        try:
            if credentials.is_symlink() or token.is_symlink():
                raise ValueError("Input credentials must not be symlinks")
            original_client = credentials.read_bytes()
            original_token = token.read_bytes()
            source_token = json.loads(original_token)
            if (not isinstance(source_token, dict)
                    or any(not isinstance(source_token.get(key), str) or not source_token[key]
                           for key in ("refresh_token", "client_id", "client_secret"))):
                raise ValueError("Expected reusable authorized-user credentials")
            with tempfile.TemporaryDirectory(prefix="visa-gmail-refresh-probe-") as directory:
                private = Path(directory)
                private.chmod(0o700)
                client_copy, token_copy = private / "client.json", private / "token.json"
                gmail_auth._write_private_token(client_copy, original_client.decode("utf-8"))
                for name in ("forced_expiry_refresh", "invalid_refresh_rejected",
                             "valid_copy_refresh_recovered"):
                    report["stages"].append(_stage(name, client_copy, token_copy, source_token,
                                                   mailbox, timeout))
                    checkpoint()
        except Exception as error:
            report["error_type"] = _safe_exception(error)
        finally:
            for path, original, field in (
                (token, original_token, "original_token_unchanged"),
                (credentials, original_client, "original_client_unchanged"),
            ):
                if original is not None:
                    try:
                        report[field] = not path.is_symlink() and path.read_bytes() == original
                    except OSError:
                        report[field] = False
            report["state"] = "finished"
            report["passed"] = bool(
                len(report["stages"]) == 3 and all(stage["passed"] for stage in report["stages"])
                and report["original_token_unchanged"] and report["original_client_unchanged"]
                and "error_type" not in report
            )
            checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--token", required=True, type=Path)
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    try:
        report = run_probe(credentials=args.credentials, token=args.token, mailbox=args.mailbox,
                           report_path=args.report, timeout=args.timeout)
    except (OSError, ValueError) as error:
        # Never print exception messages: they may contain private filesystem names.
        parser.exit(2, f"Probe did not start ({_safe_exception(error)}); no evidence overwritten.\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit("Narrow refresh criterion not met; failed evidence preserved")


if __name__ == "__main__":
    main()
