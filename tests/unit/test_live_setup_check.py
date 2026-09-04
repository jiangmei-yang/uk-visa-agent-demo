import importlib.util
import json
from pathlib import Path

import pytest


def checker():
    spec = importlib.util.spec_from_file_location("live_setup", Path("scripts/check_live_setup.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.configuration_checks


def test_setup_inventory_never_outputs_values_or_reads_env_file(tmp_path):
    secret = "PRIVATE_TEST_VALUE_NOT_FOR_OUTPUT"
    (tmp_path / ".env").write_text(f"TWILIO_AUTH_TOKEN={secret}\n")
    check = checker()
    report = check(tmp_path, {"DEEPSEEK_API_KEY": secret})
    assert secret not in json.dumps(report)
    assert report["checks"]["deepseek_key_present"]
    assert not report["checks"]["twilio_auth_token_present"]


def test_file_check_reports_presence_not_validity(tmp_path):
    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    (secrets / "gmail_token.json").write_text("intentionally not valid credential JSON")
    report = checker()(tmp_path, {})
    assert report["checks"]["gmail_oauth_token_file_present"]
    assert not report["checks"]["gmail_oauth_client_file_present"]


@pytest.mark.parametrize("url, expected", [
    ("https://demo.example.test/webhooks/twilio/whatsapp/status", True),
    ("http://demo.example.test/webhooks/twilio/whatsapp/status", False),
    ("https://demo.example.test/webhooks/twilio/whatsapp", False),
    ("https://demo.example.test/webhooks/twilio/whatsapp/status?token=secret", False),
    ("https://user:secret@demo.example.test/webhooks/twilio/whatsapp/status", False),
])
def test_callback_check_requires_exact_https_endpoint(tmp_path, url, expected):
    report = checker()(tmp_path, {"TWILIO_STATUS_CALLBACK_PUBLIC_URL": url})
    assert report["checks"]["twilio_status_https_url_shaped_correctly"] == expected
    assert url not in json.dumps(report)
