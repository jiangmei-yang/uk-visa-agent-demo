"""All refresh probes use fictional credentials and fake HTTP; no live Google SDK required."""

from __future__ import annotations

import importlib.util
import json
import socket
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


class RefreshError(Exception):
    pass


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/gmail_refresh_probe.py"
    spec = importlib.util.spec_from_file_location("fictional_gmail_refresh_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No network")))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No network")))
    return module


@pytest.fixture
def fixture(probe, tmp_path, monkeypatch):
    client = tmp_path / "fictional-client.json"
    token = tmp_path / "fictional-token.json"
    client.write_text('{"installed":{"client_secret":"FICTIONAL_CLIENT_SECRET"}}')
    original = {"client_id": "FICTIONAL_CLIENT_ID", "client_secret": "FICTIONAL_CLIENT_SECRET",
                "refresh_token": "FICTIONAL_REFRESH_TOKEN", "token": "FICTIONAL_ACCESS_TOKEN",
                "expiry": "2099-01-01T00:00:00Z"}
    token.write_text(json.dumps(original))
    state = SimpleNamespace(
        calls=[], copies=[], http_closed=0, session_closed=0, invalid_code="invalid_grant",
        mailbox="fictional@example.test", profile_status=200, initial_error=None,
        profile_callback=None, refresh_count=0,
    )

    class Request:
        def __init__(self):
            self.session = SimpleNamespace(close=self.close)

        def close(self):
            state.session_closed += 1

        def __call__(self, url, method, body, headers, **kwargs):
            assert kwargs == {"timeout": 20, "allow_redirects": False}
            assert url == probe.TOKEN_ENDPOINT and method == "POST"
            state.calls.append("POST token")
            state.refresh_count += 1
            if state.refresh_count == 1 and state.initial_error:
                raise state.initial_error
            if json.loads(body)["refresh_token"] == probe.INVALID_REFRESH_TOKEN:
                data = {"error": state.invalid_code,
                        "error_description": "FICTIONAL_PRIVATE_PROVIDER_DESCRIPTION"}
                return SimpleNamespace(status=400, data=json.dumps(data).encode())
            return SimpleNamespace(status=200, data=b'{"access_token":"FICTIONAL_RENEWED_TOKEN"}')

    class Credentials:
        def __init__(self, value):
            self.value = value
            self.refresh_token = value["refresh_token"]
            self.expired = value["expiry"] == "2000-01-01T00:00:00Z"
            self.valid = not self.expired

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            path = Path(path)
            assert path != token
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE((path.parent / "client.json").stat().st_mode) == 0o600
            assert scopes == list(probe.gmail_auth.GMAIL_SCOPES)
            state.copies.append(path)
            return cls(json.loads(path.read_bytes()))

        def refresh(self, request):
            response = request(probe.TOKEN_ENDPOINT, method="POST",
                               body=json.dumps({"refresh_token": self.refresh_token}), headers={})
            if response.status != 200:
                raise RefreshError("FICTIONAL_PRIVATE_PROVIDER_DESCRIPTION", json.loads(response.data))
            self.expired, self.valid = False, True
            self.value.update(token="FICTIONAL_RENEWED_TOKEN", expiry="2099-01-01T00:00:00Z")

        def to_json(self):
            return json.dumps(self.value)

        def apply(self, headers):
            headers["Authorization"] = "Bearer " + self.value["token"]

    class Http:
        def __init__(self, *, timeout):
            assert timeout == 20

        def request(self, uri, *, method, headers, redirections):
            assert uri == "https://gmail.googleapis.com/gmail/v1/users/me/profile?alt=json"
            assert method == "GET" and redirections == 0
            assert headers["Authorization"] == "Bearer FICTIONAL_RENEWED_TOKEN"
            state.calls.append("GET profile")
            if state.profile_callback:
                state.profile_callback()
            return SimpleNamespace(status=state.profile_status), json.dumps({
                "emailAddress": state.mailbox, "messagesTotal": 999,
                "irrelevantPrivateData": "FICTIONAL_PRIVATE_PROFILE_DATA",
            }).encode()

        def close(self):
            state.http_closed += 1

    def build(service, version, *, http, **kwargs):
        assert (service, version) == ("gmail", "v1")
        assert kwargs == {"cache_discovery": False, "static_discovery": True, "num_retries": 0}

        def profile(*, userId):
            assert userId == "me"

            def execute(*, num_retries):
                assert num_retries == 0
                _, body = http.request("https://gmail.googleapis.com/gmail/v1/users/me/profile?alt=json")
                return json.loads(body)

            return SimpleNamespace(execute=execute)

        return SimpleNamespace(users=lambda: SimpleNamespace(getProfile=profile))

    def consent(*args, **kwargs):
        raise AssertionError("Probe must never open consent")

    modules = {
        "google.oauth2.credentials": SimpleNamespace(Credentials=Credentials),
        "google.auth.transport.requests": SimpleNamespace(Request=Request),
        "google_auth_oauthlib.flow": SimpleNamespace(InstalledAppFlow=SimpleNamespace(
            from_client_secrets_file=consent)),
        "googleapiclient.discovery": SimpleNamespace(build=build),
        "httplib2": SimpleNamespace(Http=Http),
    }
    monkeypatch.setattr(probe, "import_module", modules.__getitem__)
    monkeypatch.setattr(probe.gmail_auth, "import_module", modules.__getitem__)
    return SimpleNamespace(client=client, token=token, state=state, original=original,
                           report=tmp_path / "evidence.json")


def run(probe, fixture, **kwargs):
    return probe.run_probe(credentials=fixture.client, token=fixture.token,
                           mailbox="fictional@example.test", report_path=fixture.report, **kwargs)


def assert_no_sensitive_evidence(report):
    content = json.dumps(report)
    assert "FICTIONAL_" not in content
    assert "example.test" not in content
    assert "messagesTotal" not in content
    assert "hash" not in content


def test_three_stages_use_production_factory_on_private_copies_only(probe, fixture):
    before = fixture.token.read_bytes(), fixture.client.read_bytes()
    report = run(probe, fixture)
    assert report["passed"] and report["state"] == "finished"
    assert all(stage["passed"] for stage in report["stages"])
    assert report["original_token_unchanged"] and report["original_client_unchanged"]
    assert report["sends"] == report["message_api_calls"] == report["revocation_requests"] == 0
    assert fixture.state.calls == ["POST token", "GET profile", "POST token", "POST token", "GET profile"]
    assert (fixture.token.read_bytes(), fixture.client.read_bytes()) == before
    assert len(fixture.state.copies) == 3 and all(not path.exists() for path in fixture.state.copies)
    assert fixture.state.http_closed == fixture.state.session_closed == 3
    assert stat.S_IMODE(fixture.report.stat().st_mode) == 0o600
    assert json.loads(fixture.report.read_bytes()) == report
    assert_no_sensitive_evidence(report)


@pytest.mark.parametrize("code", ["invalid_client", "FICTIONAL_PRIVATE_UNKNOWN_CODE", ["invalid_grant"]])
def test_wrong_oauth_failure_cannot_be_promoted_to_invalid_grant_pass(probe, fixture, code):
    fixture.state.invalid_code = code
    report = run(probe, fixture)
    assert not report["passed"]
    invalid = report["stages"][1]
    assert not invalid["passed"] and invalid["error_type"] == "RefreshError"
    assert invalid["oauth_error"] == (code if code == "invalid_client" else None)
    assert report["stages"][2]["passed"]
    assert_no_sensitive_evidence(report)


@pytest.mark.parametrize("failure", ["mailbox", "http_status"])
def test_readonly_profile_must_match_expected_account_and_status(probe, fixture, failure):
    if failure == "mailbox":
        fixture.state.mailbox = "other-fictional@example.test"
    else:
        fixture.state.profile_status = 403
    report = run(probe, fixture)
    assert not report["passed"]
    assert not report["stages"][0]["passed"] and not report["stages"][2]["passed"]
    assert report["stages"][1]["passed"]
    assert_no_sensitive_evidence(report)


def test_initial_transport_failure_preserved_even_when_later_recovery_succeeds(probe, fixture):
    fixture.state.initial_error = TimeoutError("FICTIONAL_PRIVATE_PROVIDER_DESCRIPTION")
    report = run(probe, fixture)
    assert not report["passed"]
    assert report["stages"][0]["error_type"] == "TimeoutError"
    assert report["stages"][0]["token_http_status"] is None
    assert report["stages"][0]["profile_requests"] == 0
    assert report["stages"][1]["passed"] and report["stages"][2]["passed"]
    assert json.loads(fixture.report.read_bytes()) == report
    assert_no_sensitive_evidence(report)


def test_concurrent_original_change_fails_report_without_replacing_the_original(probe, fixture):
    fixture.state.profile_callback = lambda: fixture.token.write_text("new operator-managed credentials")
    report = run(probe, fixture)
    assert not report["passed"] and not report["original_token_unchanged"]
    assert all(stage["passed"] for stage in report["stages"])
    assert fixture.token.read_text() == "new operator-managed credentials"


def test_existing_report_refused_before_reading_credentials(probe, fixture, monkeypatch):
    fixture.report.write_text("preserve previous failure")
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("No credentials read")))
    with pytest.raises(FileExistsError):
        run(probe, fixture)
    assert fixture.report.read_text() == "preserve previous failure"
    assert not fixture.state.calls


@pytest.mark.parametrize("target", ["token", "client"])
def test_report_cannot_overwrite_source_credentials(probe, fixture, target):
    fixture.report = getattr(fixture, target)
    before = fixture.report.read_bytes()
    with pytest.raises(ValueError, match="distinct paths"):
        run(probe, fixture)
    assert fixture.report.read_bytes() == before
    assert not fixture.state.calls


@pytest.mark.parametrize("target", ["token", "client"])
def test_report_cannot_create_a_missing_source_credential_file(probe, fixture, target):
    source = fixture.report.parent / "missing-credentials.json"
    setattr(fixture, target, source)
    fixture.report = source
    with pytest.raises(ValueError, match="distinct paths"):
        run(probe, fixture)
    assert not source.exists() and not fixture.state.calls


def test_source_client_and_token_require_distinct_resolved_paths(probe, fixture):
    before = fixture.token.read_bytes()
    fixture.client = fixture.token.parent / "." / fixture.token.name
    with pytest.raises(ValueError, match="distinct paths"):
        run(probe, fixture)
    assert fixture.token.read_bytes() == before
    assert not fixture.report.exists() and not fixture.state.calls


def test_output_symlink_to_source_is_rejected_by_resolved_location(probe, fixture):
    fixture.report.symlink_to(fixture.token)
    with pytest.raises(ValueError, match="distinct paths"):
        run(probe, fixture)
    assert fixture.report.is_symlink() and not fixture.state.calls


@pytest.mark.parametrize("target", ["token", "client"])
def test_input_symlinks_fail_without_network_and_preserve_evidence(probe, fixture, target):
    path = getattr(fixture, target)
    link = path.with_suffix(".link")
    link.symlink_to(path)
    setattr(fixture, target, link)
    report = run(probe, fixture)
    assert report["error_type"] == "ValueError" and not report["passed"]
    assert fixture.report.exists() and link.is_symlink() and not fixture.state.calls


@pytest.mark.parametrize("content", ["not JSON FICTIONAL_PRIVATE_TOKEN", "{}", "[]",
                                     '{"refresh_token":"FICTIONAL_PRIVATE_TOKEN"}'])
def test_malformed_token_retains_sanitized_failure_without_network(probe, fixture, content):
    fixture.token.write_text(content)
    report = run(probe, fixture)
    assert not report["passed"] and report["original_token_unchanged"]
    assert report["error_type"] in {"ValueError", "JSONDecodeError"}
    assert not fixture.state.calls
    assert_no_sensitive_evidence(report)


@pytest.mark.parametrize("timeout", [0, 61, -1])
def test_timeout_bounds_are_validated_before_probe(probe, fixture, timeout):
    with pytest.raises(ValueError):
        run(probe, fixture, timeout=timeout)
    assert not fixture.report.exists() and not fixture.state.calls


def test_report_must_stay_outside_secrets_directory(probe, fixture):
    fixture.report = fixture.report.parent / ".secrets" / "report.json"
    with pytest.raises(ValueError):
        run(probe, fixture)
    assert not fixture.report.exists() and not fixture.state.calls


@pytest.mark.parametrize("url,method", [
    ("https://oauth2.googleapis.com/revoke", "POST"),
    ("https://oauth2.googleapis.com/token", "GET"),
    ("https://oauth2.googleapis.com/token?redirect=yes", "POST"),
    ("https://not-google.example/token", "POST"),
])
def test_refresh_endpoint_guard_blocks_other_actions(probe, url, method):
    transport = probe.BoundedTransport(20)
    transport._request = Mock(side_effect=AssertionError("Unexpected request"))
    with pytest.raises(RuntimeError):
        transport.refresh_request(url, method=method)
    transport._request.assert_not_called()


@pytest.mark.parametrize("uri,method,body", [
    ("https://gmail.googleapis.com/gmail/v1/users/me/messages", "GET", None),
    ("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", "POST", "message"),
    ("https://gmail.googleapis.com/gmail/v1/users/me/profile", "POST", None),
    ("https://gmail.googleapis.com/gmail/v1/users/me/profile", "GET", "message"),
    ("https://gmail.googleapis.com/gmail/v1/users/me/profile?alt=json&key=secret", "GET", None),
    ("https://gmail.googleapis.com/gmail/v1/users/me/profile#other", "GET", None),
    ("https://gmail.googleapis.com.other.test/gmail/v1/users/me/profile", "GET", None),
    ("https://gmail.googleapis.com/gmail/v1/users/other/profile", "GET", None),
])
def test_gmail_endpoint_guard_blocks_message_and_other_requests(probe, uri, method, body):
    transport = probe.BoundedTransport(20)
    transport._http = Mock()
    with pytest.raises(RuntimeError):
        transport.request(uri, method=method, body=body)
    transport._http.request.assert_not_called()


def test_no_implicit_retry_or_unbounded_caller_timeout(probe):
    transport = probe.BoundedTransport(7)
    transport._request = Mock(return_value=SimpleNamespace(status=503, data=b'{"error":"server_error"}'))
    transport.refresh_request(probe.TOKEN_ENDPOINT, method="POST", timeout=999, allow_redirects=True)
    assert transport.token_status == 503 and transport.oauth_error == "server_error"
    assert transport._request.call_args.kwargs["timeout"] == 7
    assert transport._request.call_args.kwargs["allow_redirects"] is False
    with pytest.raises(RuntimeError):
        transport.refresh_request(probe.TOKEN_ENDPOINT, method="POST")
    assert transport._request.call_count == 1


@pytest.mark.parametrize("data", [b"provider body FICTIONAL_SECRET", b"[]", b"null",
                                  b'{"error": null}', b"\xff"])
def test_provider_error_body_never_leaks_or_manufactures_error_code(probe, data):
    transport = probe.BoundedTransport(7)
    transport._request = Mock(return_value=SimpleNamespace(status=400, data=data))
    transport.refresh_request(probe.TOKEN_ENDPOINT, method="POST")
    assert transport.token_status == 400 and transport.oauth_error is None


def test_repeated_profile_request_is_not_dispatched(probe):
    transport = probe.BoundedTransport(7)
    transport.profile_requests = 1
    transport._http = Mock()
    with pytest.raises(RuntimeError):
        transport.request("https://gmail.googleapis.com/gmail/v1/users/me/profile")
    transport._http.request.assert_not_called()


def test_expired_profile_credentials_never_trigger_implicit_refresh(probe):
    transport = probe.BoundedTransport(7)
    transport._credentials = SimpleNamespace(valid=False)
    transport._http = Mock()
    with pytest.raises(RuntimeError):
        transport.request("https://gmail.googleapis.com/gmail/v1/users/me/profile")
    transport._http.request.assert_not_called()


def test_partial_transport_setup_still_closes_private_session(probe, monkeypatch):
    session = Mock()

    def modules(name):
        if name == "google.auth.transport.requests":
            return SimpleNamespace(Request=lambda: SimpleNamespace(session=session))
        raise ModuleNotFoundError("FICTIONAL_PRIVATE_PATH")

    monkeypatch.setattr(probe, "import_module", modules)
    with pytest.raises(ModuleNotFoundError), probe.BoundedTransport(7).install():
        raise AssertionError("Setup must not succeed")
    session.close.assert_called_once()


def test_main_failure_exits_nonzero_and_prints_only_sanitized_evidence(probe, fixture, monkeypatch, capsys):
    fixture.state.invalid_code = "invalid_client"
    monkeypatch.setattr("sys.argv", ["probe", "--credentials", str(fixture.client),
                                    "--token", str(fixture.token), "--mailbox", "fictional@example.test",
                                    "--report", str(fixture.report)])
    with pytest.raises(SystemExit, match="failed evidence preserved"):
        probe.main()
    assert_no_sensitive_evidence(json.loads(capsys.readouterr().out))
