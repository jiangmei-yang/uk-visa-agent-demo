import argparse
import io
import json
import runpy
from pathlib import Path

import pytest

SCRIPT = runpy.run_path(str(Path(__file__).parents[2] / "scripts/release_smoke.py"))


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000", "http://example.com", "http://127.0.0.1.example.com",
    "http://user:password@127.0.0.1", "http://127.0.0.1/api/lab",
    "http://127.0.0.1?reset=true", "http://127.0.0.1/#fragment", "file:///tmp/demo",
])
def test_smoke_rejects_nonlocal_or_ambiguous_origins(url):
    with pytest.raises(argparse.ArgumentTypeError):
        SCRIPT["local_url"](url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:18080", "http://localhost:8000/", "http://[::1]:8000"])
def test_smoke_accepts_explicit_loopback_origin(url):
    assert SCRIPT["local_url"](url) == url.rstrip("/")


def test_smoke_rejects_a_used_lab_before_any_post(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, req, timeout):
            calls.append((req.method, req.full_url))
            assert req.method == "GET"
            if req.full_url.endswith("/health"):
                data = {"status": "ok"}
            elif req.full_url.endswith("/api/lab"):
                data = {"synthetic": True, "mode": "deterministic_fixture", "processed_steps": 1}
            else:
                return Response(b"offline page")
            return Response(json.dumps(data).encode())

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    with pytest.raises(RuntimeError, match="fresh_synthetic_lab"):
        SCRIPT["run"]("http://127.0.0.1:18080")
    assert len(calls) == 4
