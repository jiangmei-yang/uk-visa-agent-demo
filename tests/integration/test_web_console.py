from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from visa_agent import web
from visa_agent.config import Settings
from visa_agent.demo import run_demo


def test_review_console_and_pack_download(tmp_path: Path) -> None:
    test_settings = Settings(
        database_path=tmp_path / "visa.db",
        output_dir=tmp_path / "output",
        policy_path=Path("knowledge/uk_standard_visitor_2026-02-25.yaml"),
    )
    result = run_demo(test_settings, reset=True)
    web.settings = test_settings
    with ThreadPoolExecutor(max_workers=1) as executor:
        page = executor.submit(web.index).result()
        download = executor.submit(web.get_pack, result.case.id).result()
    assert page.status_code == 200
    body = page.body.decode("utf-8")
    assert "The application pack is ready for adviser review" in body
    assert "From first submission to review pack" in body
    assert "Service response:" in body
    assert body.index("Current outcome") < body.index("Delivery gate")
    assert "<details>" in body
    assert "Delivery gate" in body
    assert "Active evidence ledger" in body
    assert download.media_type == "application/zip"
