"""Acceptance evidence must bind rule data, not only Python source."""

import hashlib
import runpy

import pytest

PROBE = runpy.run_path("scripts/consultant_journey_probe.py")


def fixture_tree(root):
    files = {
        "scripts/consultant_journey_probe.py": "# probe",
        str(PROBE["POLICY_PATH"]): "policy: original",
        "pyproject.toml": "# dependencies",
        "uv.lock": "# lock",
        "src/visa_agent/example.py": "# code",
        "src/visa_agent/assets/example.txt": "asset",
        ".secrets/key.txt": "must never enter report",
        ".env": "SECRET=not-for-report",
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)


@pytest.mark.parametrize("changed", [str(PROBE["POLICY_PATH"]), "uv.lock", "pyproject.toml"])
def test_rules_and_dependency_changes_invalidate_manifest(tmp_path, changed):
    fixture_tree(tmp_path)
    before = PROBE["evidence_sources"](tmp_path)
    (tmp_path / changed).write_text("changed content")
    after = PROBE["evidence_sources"](tmp_path)
    assert before != after
    assert after[changed] == hashlib.sha256(b"changed content").hexdigest()
    assert not any(name.startswith((".secrets", ".env")) for name in after)


def test_missing_policy_is_not_silently_omitted(tmp_path):
    fixture_tree(tmp_path)
    (tmp_path / PROBE["POLICY_PATH"]).unlink()
    with pytest.raises(FileNotFoundError):
        PROBE["evidence_sources"](tmp_path)


def test_runtime_versions_are_bounded_metadata_not_environment():
    versions = PROBE["runtime_versions"]()
    assert set(versions) == {"python", "openai", "pydantic", "PyYAML", "httpx", "reportlab", "pypdf"}
    assert versions["python"] != "not-installed"
