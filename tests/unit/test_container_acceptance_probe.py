"""The destructive synthetic reset probe must not default to a live console."""

import runpy

import pytest

PROBE = runpy.run_path("scripts/container_acceptance_probe.py")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000", "http://127.0.0.1", "https://127.0.0.1:32768",
    "http://example.com:32768", "http://localhost:32768", "http://127.0.0.1:32768/api",
    "http://user:password@127.0.0.1:32768", "http://127.0.0.1:32768/?reset=true",
    "http://127.0.0.1:32768/#fragment",
])
def test_requires_explicit_nondefault_loopback_target(url):
    with pytest.raises(ValueError):
        PROBE["isolated_url"](url)


def test_accepts_explicit_isolated_port_and_normalizes_trailing_slash():
    assert PROBE["isolated_url"]("http://127.0.0.1:32768/") == "http://127.0.0.1:32768"
